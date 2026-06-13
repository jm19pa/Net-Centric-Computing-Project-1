import random
import socket
import sys
import threading


if len(sys.argv) < 2:
    print("Error: Please include a port number to start the server")
    sys.exit(1)

PORT = int(sys.argv[1])

active_connections = {}

def handle_client_session(control_socket, data_port, client_address):
    thread_id = threading.get_ident()
    data_socket = None

    data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_listener.bind(('localhost', data_port))
    data_listener.listen(1)

    connection_response = f"200\n{data_port}"
    print("Connection requested. Creating data socket")
    control_socket.sendall(connection_response.encode())

    try:
        data_socket, data_address = data_listener.accept()
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

                        login_response = "200"
                        if username not in active_connections.keys():
                            active_connections[username] = (data_socket)
                        else:
                            login_response = "500"

                        data_socket.sendall(login_response.encode())

                        # TODO: Broadcast login to all connected client
                    case "who":
                        pass
                    case "broadcast":
                        pass
                    case "private":
                        pass
                    case "quit":
                        pass

            except ConnectionResetError:
                pass

def start_server():
    print("Starting server")
    print("Creating server socket")
    
    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serversocket.bind(('localhost', PORT))
    serversocket.listen(1)

    print("Awaiting connections...")

    try:
        while True:
            control_socket, client_address = serversocket.accept()
            data_port = random.randint(9000, 9100)

            client_thread = threading.Thread(
                target=handle_client_session,
                args=(control_socket, data_port, client_address)
            )

            client_thread.daemon = True
            client_thread.start()
    except KeyboardInterrupt:
        pass
    finally:
        serversocket.close()

if __name__ == "__main__":
    start_server()