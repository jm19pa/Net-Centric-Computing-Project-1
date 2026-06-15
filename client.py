import socket
import threading
import queue

message_queue = queue.Queue()
last_command = None
response_ready = threading.Event()

def data_listener(data_socket, data_port):
    while True:
        try:
            server_message = data_socket.recv(1024).decode()
            if not server_message:
                break
            
            message_queue.put(server_message)

            parts = server_message.split('\n')
            if len(parts) < 5:
                response_ready.set()

        except:
            break


def handle_messages():
    try:
        while True:
            server_message = message_queue.get_nowait()
            parts = server_message.split('\n')
            status_code = parts[0]

            # Brodadcast/Private
            if len(parts) == 5 and status_code == "200":
                if parts[2] == "Broadcast":
                    broadcast_message = " ".join(parts[4:])
                    print(f"Broadcast message from {parts[3]}: {broadcast_message}")
                else:
                    private_message = " ".join(parts[4:])
                    print(f"{parts[3]}: {private_message}")
            
            # Command response
            elif last_command:
                match last_command:
                    case "who":
                        if status_code == "200":
                            print(f"200 status code received. Users currently connected:{parts[2]}")
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
                            data_socket.close()
                            control_socket.close()
                        else:
                            print("500 status code received. Failed to disconnect")
    except queue.Empty:
        return


if __name__ == "__main__":
    print("Starting client...")
    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    data_port = None
    data_socket = None
    server_ip = None

    while True:
        handle_messages()

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

                    # Force event to prevent yielding
                    response_ready.set()

                    if status_code == "200" and len(response_lines) > 1:
                        data_port = int(response_lines[2])
                        print(f"200 status coded received. Starting data connection on port {data_port}")

                        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        data_socket.connect((server_ip, data_port))
                except Exception as e:
                    print(e)
            case "login":
                if len(parts) < 2:
                    print("Usage: login <username>")
                    continue
                last_command = "login"

                try:
                    # Send command to server for processing
                    control_socket.sendall(user_input.encode())

                    response = data_socket.recv(1024).decode()
                    if response == "200":
                        print("200 status code received. Login successful.")
                    else:
                        print("500 status code received. Failed to login.")

                    # Start thread to receive messages from server
                    listening_thread = threading.Thread(
                        target=data_listener,
                        args=(data_socket, data_port)
                    )
                    listening_thread.daemon = True
                    listening_thread.start()

                    # Force event to prevent yielding
                    response_ready.set()
                except Exception as e:
                    print(e)
            case "who":
                last_command = "who"  
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
            case "broadcast":
                if len(parts) < 2:
                    print("Usage: broadcast <message>")
                    continue
                last_command = "broadcast"

                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
            case "private":
                if len(parts) < 3:
                    print("Usage: private <username> <message>")
                    continue
                last_command = "private"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
            case "quit":
                last_command = "quit"
                try:
                    control_socket.sendall(user_input.encode())
                except Exception as e:
                    print(e)
            

        # Wait for server command response before continuing
        response_ready.wait()
        response_ready.clear()