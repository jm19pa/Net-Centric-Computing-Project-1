import socket
import threading

def server_listener(data_socket, data_port  ):
    while True:
        try:
            server_message = data_socket.recv(1024)
            if not server_message:
                # Server closed data channel
                break

            print(server_message.decode())
        except Exception as e:
            break
            

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
            break
            
        if not user_input:
            continue

        parts = user_input.split()
        command = parts[0].lower()

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
                        data_port = int(response_lines[1])
                        print(f"200 status coded received. Starting data connection on port {data_port}")

                        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        data_socket.connect((server_ip, data_port))
                except Exception as e:
                    print(e)
                    control_socket = None
            case "login":
                if len(parts) < 2:
                    print("Usage: login <username>")
                    continue

                try:
                    control_socket.sendall(user_input.encode())

                    response = data_socket.recv(1024).decode()
                    if response == "200":
                        print("200 status code received. Login successful")
                    else:
                        print("500 status code received. Failed to login")

                    # Start thread to receive messages from server
                    listening_thread = threading.Thread(
                        target=server_listener,
                        args=(data_socket, data_port)
                    )

                    listening_thread.daemon = True
                    listening_thread.start()
                except Exception as e:
                    print(e)
                    break

