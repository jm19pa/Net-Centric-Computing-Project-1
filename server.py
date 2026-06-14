import socket
import sys
import threading

if len(sys.argv) < 2:
    print("Error: Please include a port number to start the server")
    sys.exit(1)

PORT = int(sys.argv[1])

# TODO: Active connections dictionary must be thread safe
active_connections = {}

# TODO: Client->Server->Client communication functions
def send_private_message(source_user, destination_user, message):
    pass
def broadcast_message_to_all(source_user, message):
    pass

def handle_client_session(control_socket, client_address):
    thread_id = threading.get_ident()
    data_socket = None

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
                        if username not in active_connections.keys():
                            active_connections[username] = (data_socket)
                        else:
                            login_response = "500"

                        data_socket.sendall(login_response.encode())                
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