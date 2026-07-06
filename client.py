import os
import socket
import threading

response_ready = threading.Event()
lock = threading.Lock()
FILE_TRANSFER_MARKER = b"\n__EOF__\n"

last_command = None
last_response = None
username = None

requested_file = None
data_socket = None

def data_listener(data_socket, data_port):
    global requested_file
    
    while True:
        try:
            server_message = data_socket.recv(1024)
            if not server_message:
                break

            if requested_file:
                if server_message.startswith(b"500\n"):
                    print("500 status code received.")
                    requested_file = None
                    response_ready.set()
                    continue

                payload = server_message
                if payload.endswith(FILE_TRANSFER_MARKER):
                    payload = payload[:-len(FILE_TRANSFER_MARKER)]
                elif FILE_TRANSFER_MARKER in payload:
                    payload = payload.split(FILE_TRANSFER_MARKER, 1)[0]

                with open(requested_file, 'wb') as file:
                    file.write(payload)

                print("File retrieved.")
                requested_file = None
                response_ready.set()
                continue 

            server_message = server_message.decode()
            parts = server_message.split('\n')

            # Print messages from other users immediately
            if len(parts) >= 5:
                if parts[2] == "Broadcast":
                    broadcast_message = " ".join(parts[4:])
                    print(f"\nBroadcast message from {parts[3]}: {broadcast_message}", end="\n> ")
                else:
                    private_message = " ".join(parts[4:])
                    print(f"\n{parts[3]}: {private_message}", end="\n> ")

            # Otherwise for command response or join/quit notifications
            elif len(parts) == 4 and parts[2] in ("join", "quit") and parts[3] != username:
                if parts[2] == "join":
                    print(f"\n{parts[3]} has logged in.", end="\n> ")
                else:
                    print(f"\n{parts[3]} has logged out.", end="\n> ")
            else:
                with lock:
                    global last_response
                    last_response = server_message
                    response_ready.set()

        except Exception as e:
            print(e)
            response_ready.set()

def print_response():
    with lock:
        global last_command, last_response
        
        if not last_response:
            return

        parts = last_response.split('\n')
        status_code = parts[0]
        
        match last_command:
            case "login":
                if status_code == "200":
                    print("200 status code received. Login successful.")
                else:
                    print("500 status code received. Failed to login.")
            case "who":
                if status_code == "200":
                    if len(parts) >= 4 and parts[2] == "who":
                        users = parts[3]
                    else:
                        users = parts[2] if len(parts) > 2 else ""
                    print(f"200 status code received. Users currently connected: {users}")
                else:
                    print(f"500 status code received. Failed to retrieve active users.")
            case "broadcast":
                if status_code == "200":
                    print("200 status code received.")
                else:
                    print("500 status code received. Failed to broadcast.")
            case "private":
                if status_code == "200":
                    print("200 status code received. Message sent.")
                else:
                    print("500 status code received. Message failed to send.")
            case "quit":
                if status_code == "200":
                    print("200 status code received.")
                else:
                    print("500 status code received. Failed to disconnect")
            case "stor":
                if status_code == "200":
                    print("200 status code received. File sent.")
                else:
                    print("500 status code received. Failed to store file.")
            case "retr":
                if status_code == "200":
                    print("File retrieved.")
                else:
                    print("500 status code received. Failed to retrieve file.")
            case "list":
                if status_code == "200":
                    if len(parts) >= 4 and parts[2] == "list":
                        files = parts[3]
                    else:
                        files = parts[2] if len(parts) > 2 else ""
                    print(f"200 status code received. Files: {files}")
                else:
                    print("500 status code received. Failed to list files.")
            case "dele":
                if status_code == "200":
                    print("200 status code received. File deleted.")
                else:
                    print("500 status code received. Failed to delete file.")
        last_response = None


if __name__ == "__main__":
    print("Starting client...")
    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    data_port = None
    data_socket = None
    server_ip = None

    while True:
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("Exiting")
            break
            
        if not user_input:
            continue

        parts = user_input.split()
        command = parts[0].lower()

        if command not in ["connect", "login", "who", "broadcast", "private", "quit", "retr", "stor", "list", "dele"]:
            print("Invalid command.")
            continue

        match command:
            case "connect":
                if len(parts) < 3:
                    print("Usage: connect <ip> <port>")
                    continue

                server_ip = parts[1]
                control_port = int(parts[2])

                try:
                    control_socket.connect((server_ip, control_port))

                    response = control_socket.recv(1024).decode()
                    response_lines = response.split('\n')
                    status_code = response_lines[0]

                    if status_code == "200" and len(response_lines) > 1:
                        data_port = int(response_lines[2])
                        print(f"200 status coded received. Starting data connection on port {data_port}")

                        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        data_socket.connect((server_ip, data_port))

                         # Start thread to receive messages from server
                        listening_thread = threading.Thread(
                            target=data_listener,
                            args=(data_socket, data_port)
                        )
                        listening_thread.daemon = True
                        listening_thread.start()
                except Exception as e:
                    print(e)
                    continue
            case "login":
                if len(parts) < 2:
                    print("Usage: login <username>")
                    continue
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "login"
                    username = parts[1]
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue
            case "who":
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "who"
                try:
                    control_socket.sendall(user_input.encode())
                    
                except Exception as e:
                    print(e)
                    continue
            case "broadcast":
                if len(parts) < 2:
                    print("Usage: broadcast <message>")
                    continue
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "broadcast"

                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue
            case "private":
                if len(parts) < 3:
                    print("Usage: private <username> <message>")
                    continue
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "private"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue
            case "quit":
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "quit"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue
            case "stor":
                if len(parts) < 2:
                    print("Usage: stor <file_name>")
                    continue

                filename = parts[1]
                if not os.path.isfile(filename):
                    print("File does not exist locally.")
                    continue

                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "stor"

                try:
                    control_socket.sendall(user_input.encode())

                    with open(filename, 'rb') as f:
                        data = f.read()
                    data_socket.sendall(data + FILE_TRANSFER_MARKER)
                except Exception as e:
                    print(e)
                    continue
            case "retr":
                if len(parts) < 2:
                    print("Usage: retr <file_name>")
                    continue

                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue

                requested_file = parts[1]
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "retr"
            case "list":
                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "list"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue
            case "dele":
                if len(parts) < 2:
                    print("Usage: dele <file_name>")
                    continue

                with lock:
                    last_response = None
                    response_ready.clear()
                    last_command = "dele"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
                    continue

        # Wait for server command response before continuing
        if command != "connect":
            response_ready.wait()
            response_ready.clear()
            print_response()

            if command == "quit":
                if data_socket: data_socket.close()
                if control_socket: control_socket.close()
                print("Disconnected.")
                break