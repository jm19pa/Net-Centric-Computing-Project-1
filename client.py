import socket
import threading

response_ready = threading.Event()
lock = threading.Lock()

last_command = None
last_response = None
username = None

def data_listener(data_socket, data_port):
    while True:
        try:
            server_message = data_socket.recv(1024).decode()
            if not server_message:
                break
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

        except:
            break

def print_response():
    with lock:
        global last_command, last_response
        
        if not last_response:
            return

        parts = last_response.split('\n')
        status_code = parts[0]
        
        if last_command == "login":
            if status_code == "200":
                print("200 status code received. Login successful.")
            else:
                print("500 status code received. Failed to login.")
        elif last_command == "who":
            if status_code == "200":
                if len(parts) >= 4 and parts[2] == "who":
                    users = parts[3]
                else:
                    users = parts[2] if len(parts) > 2 else ""
                print(f"200 status code received. Users currently connected: {users}")
            else:
                print(f"500 status code received. Failed to retrieve active users.")
        elif last_command == "broadcast":
            if status_code == "200":
                print("200 status code received.")
            else:
                print("500 status code received. Failed to broadcast.")
        elif last_command == "private":
            if status_code == "200":
                print("200 status code received. Message sent.")
            else:
                print("500 status code received. Message failed to send.")
        elif last_command == "quit":
            if status_code == "200":
                print("200 status code received.")
            else:
                print("500 status code received. Failed to disconnect")

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

        if command not in ["connect", "login", "who", "broadcast", "private", "quit"]:
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