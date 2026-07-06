import os
import socket
import sys
import threading

if len(sys.argv) < 2:
    print("Error: Please include a port number to start the server")
    sys.exit(1)

PORT = int(sys.argv[1])

active_connections = {}
active_connections_lock = threading.Lock()
FILE_TRANSFER_MARKER = b"\n__EOF__\n"

def send_private_message(source_user, destination_user, message):
    with active_connections_lock:
        dest_socket = active_connections.get(destination_user)

    if dest_socket is None:
        return False

    outgoing = f"200\n\nPrivate\n{source_user}\n{message}".encode()
    try:
        dest_socket.sendall(outgoing)
        return True
    except Exception:
        return False


def broadcast_message_to_all(source_user, message):
    outgoing = f"200\n\nBroadcast\n{source_user}\n{message}".encode()
    with active_connections_lock:
        sockets = list(active_connections.values())

    for sock in sockets:
        try:
            sock.sendall(outgoing)
        except Exception:
            pass


def broadcast_user_join(joined_user):
    outgoing = f"200\n\njoin\n{joined_user}".encode()
    with active_connections_lock:
        for username in active_connections.keys():
            if username == joined_user:
                continue
            else:
                active_connections[username].sendall(outgoing)


def broadcast_user_disconnect(exited_user):
    outgoing = f"200\n\nquit\n{exited_user}".encode()
    with active_connections_lock:
        sockets = list(active_connections.values())

    for sock in sockets:
        try:
            sock.sendall(outgoing)
        except Exception:
            pass


def remove_connection(username):
    with active_connections_lock:
        active_connections.pop(username, None)


def resolve_server_file_path(file_name):
    candidates = [
        os.path.join("serverdirectory", file_name),
        file_name,
    ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return None


def transfer_file_to_user(file_name, destination_user):
    with active_connections_lock:
        dest_socket = active_connections.get(destination_user)

    if dest_socket is None:
        return

    file_path = resolve_server_file_path(file_name)
    if file_path is None:
        return

    try:
        with open(file_path, 'rb') as file:
            data = file.read()
        payload = data + FILE_TRANSFER_MARKER
        dest_socket.sendall(payload)
    except Exception:
        return

def handle_client_session(control_socket, client_address):
    thread_id = threading.get_ident()
    data_socket = None
    username = None

    data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_listener.bind(('0.0.0.0', 0)) 
    data_listener.listen(1) 

    data_port = data_listener.getsockname()[1]
    connection_response = f"200\n\n{data_port}"
    print("Connection requested. Creating data socket")
    control_socket.sendall(connection_response.encode())


    try:
        data_socket, data_address = data_listener.accept()
    except socket.timeout:
        print(f"Timeout occured when attempting to connect on port {data_port}.")
        return
    except OSError as e:
        print(e)
        return
    finally:
        data_listener.close()

    with control_socket:
        while True:
            try:
                data = control_socket.recv(1024).decode()

                parts = data.split()
                if not parts:
                    continue

                command = parts[0].lower()

                match command:
                    case "login":
                        username = parts[1]
                        print(f"Login requestd by: {username}")

                        login_code = "200"
                        with active_connections_lock:
                            if username not in active_connections:
                                active_connections[username] = data_socket
                            else:
                                login_code = "500"

                        login_response = f"{login_code}\n\njoin\n{username}"
                        data_socket.sendall(login_response.encode())
                        broadcast_user_join(username)                
                    case "who":
                        print("Who requested. Sending users.")
                        with active_connections_lock:
                            users = ", ".join(active_connections.keys())

                        response = f"200\n\nwho\n{users}"
                        data_socket.sendall(response.encode())
                    case "broadcast":
                        message = " ".join(parts[1:]).strip()
                        print(f"Broadcast requested by {username}\nMessage: {message}")

                        if not message:
                            data_socket.sendall("500".encode())
                            continue

                        data_socket.sendall("200".encode())
                        broadcast_message_to_all(username or "UNKNOWN", message) 
                    case "private":
                        if len(parts) < 3:
                            data_socket.sendall("500\nUsage: private <username> <message>".encode())
                            continue

                        destination_user = parts[1]
                        print(f"Private message from {username} to {destination_user}")
                        
                        message = " ".join(parts[2:]).strip()
                        if send_private_message(username or "SERVER", destination_user, message):
                            data_socket.sendall("200".encode())
                        else:
                            data_socket.sendall("500".encode())
                    case "quit":
                        if username:
                            remove_connection(username)
                        data_socket.sendall("200".encode())
                        print(f"Quit requested by {username}")
                        data_socket.close()
                        broadcast_user_disconnect(username)
                        return
                    case "stor":
                        if len(parts) < 2:
                            data_socket.sendall("500".encode())
                            continue

                        file_name = parts[1]
                        print(f"Stor {file_name} requested by {username}")

                        try:
                            os.makedirs('serverdirectory', exist_ok=True)
                            file_path = os.path.join('serverdirectory', file_name)
                            with open(file_path, 'wb') as f:
                                while True:
                                    chunk = data_socket.recv(1024)
                                    if not chunk:
                                        break
                                    if chunk.endswith(FILE_TRANSFER_MARKER):
                                        f.write(chunk[:-len(FILE_TRANSFER_MARKER)])
                                        break
                                    f.write(chunk)

                            data_socket.sendall("200".encode())
                        except Exception as e:
                            print(e)
                            try:
                                data_socket.sendall("500".encode())
                            except Exception:
                                pass
                    case "retr":
                        if not username:
                            data_socket.sendall("500\n".encode())
                            continue

                        file_request = parts[1]
                        print(f"Retr requested by {username}. Sending file: {file_request}")

                        if resolve_server_file_path(file_request):
                            transfer_file_to_user(file_request, username)
                            print("File sent.")
                        else:
                            data_socket.sendall("500\n".encode())
                    case "list":
                        try:
                            files = []
                            print(f"List requested by {username}. Sending files.")
                            if os.path.isdir('serverdirectory'):
                                files = [f for f in os.listdir('serverdirectory') if os.path.isfile(os.path.join('serverdirectory', f))]
                            file_list = ",".join(files)
                            response = f"200\n\nlist\n{file_list}"
                            data_socket.sendall(response.encode())
                        except Exception as e:
                            print(e)
                            try:
                                data_socket.sendall("500".encode())
                            except Exception:
                                pass
                    case "dele":
                        if len(parts) < 2:
                            data_socket.sendall("500".encode())
                            continue

                        file_name = parts[1]
                        file_path = f"serverdirectory/{file_name}"
                        print(f"Dele requested by {username}. Deleting file: {file_name}")
                        try:
                            if os.path.isfile(file_path):
                                os.remove(file_path)
                                data_socket.sendall("200".encode())
                                print(f"Delete complete")
                            else:
                                data_socket.sendall("500".encode())
                        except Exception as e:
                            print(e)
                            try:
                                data_socket.sendall("500".encode())
                            except Exception:
                                pass
            except ConnectionResetError:
                if username:
                    remove_connection(username)
                pass

def start_server():
    print("Starting server")
    print("Creating server socket")
    
    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serversocket.bind(('0.0.0.0', PORT))
    serversocket.listen(1)

    print("Awaiting connections...")

    try:
        while True:
            control_socket, client_address = serversocket.accept()

            client_thread = threading.Thread(
                target=handle_client_session,
                args=(control_socket, client_address)
            )
            client_thread.daemon = True
            client_thread.start()
    except Exception as e:
        print(f"{e}. Closing server...")
    finally:
        serversocket.close()

if __name__ == "__main__":
    start_server()