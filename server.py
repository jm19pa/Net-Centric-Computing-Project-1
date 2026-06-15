import socket
import sys
import threading

if len(sys.argv) < 2:
    print("Error: Please include a port number to start the server")
    sys.exit(1)

PORT = int(sys.argv[1])

# TODO: Active connections dictionary must be thread safe
active_connections = {}
active_connections_lock = threading.Lock()

# TODO: Client->Server->Client communication functions
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


def remove_connection(username):
    with active_connections_lock:
        active_connections.pop(username, None)


def handle_client_session(control_socket, client_address):
    thread_id = threading.get_ident()
    data_socket = None
    username = None

    # Create a temporary listener to establish data socket
    data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_listener.bind(('0.0.0.0', 0)) # Let the OS decide the port number
    data_listener.listen(1) # Ensure port is bound before sending to client

    data_port = data_listener.getsockname()[1]
    connection_response = f"200\n\n{data_port}"
    print("Connection requested. Creating data socket")
    control_socket.sendall(connection_response.encode())

    # Ensure client connects to data socket
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

                # TODO: Finish commands
                match command:
                    case "login":
                        username = parts[1]
                        print(f"Login requestd by: {username}")

                        login_response = "200"
                        with active_connections_lock:
                            if username not in active_connections:
                                active_connections[username] = data_socket
                            else:
                                login_response = "500"

                        data_socket.sendall(login_response.encode())                
                    case "who":
                        print("Who requested. Sending users.")
                        with active_connections_lock:
                            users = ", ".join(active_connections.keys())

                        response = f"200\n\n{users}"
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
                        data_socket.close()
                        return
            except ConnectionResetError:
                if username:
                    remove_connection(username)
                pass

def start_server():
    print("Starting server")
    print("Creating server socket")
    
    # Create the control socket which allows for client->server communication ONLY
    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serversocket.bind(('0.0.0.0', PORT))
    serversocket.listen(1)

    print("Awaiting connections...")

    try:
        while True:
            control_socket, client_address = serversocket.accept()

            # Create a data socket in its own thread for each server->client commumnication
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