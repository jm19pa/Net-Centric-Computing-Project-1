"""CNT4713 Project 3 encrypted multi-client chat client.

The typed ``connect`` command creates the CONTROL connection and receives the
server's public key.  All later commands are RSA-OAEP/SHA-256 encrypted on the
CONTROL socket.  A listener thread receives encrypted responses and chat
messages on the DATA socket.
"""

from __future__ import annotations

import hashlib
import hmac
import queue
import socket
import struct
import threading
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


RSA_KEY_SIZE = 2048
RSA_PUBLIC_EXPONENT = 65537
FRAME_HEADER = struct.Struct("!I")
MAX_FRAME_SIZE = 1_048_576
MAX_HANDSHAKE_SIZE = 65_536
ENVELOPE_MAGIC = b"CNT4713-SHA256"
PUBLIC_KEY_END = b"-----END PUBLIC KEY-----"
RESPONSE_TIMEOUT_SECONDS = 30


class ProtocolError(Exception):
    """Raised when a peer sends a malformed or unverifiable protocol message."""


def oaep_padding() -> padding.OAEP:
    """Return the one padding configuration used by both sides."""

    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def build_envelope(message: str) -> bytes:
    """Add a SHA-256 digest to a UTF-8 message before encryption."""

    message_bytes = message.encode("utf-8")
    digest = hashlib.sha256(message_bytes).hexdigest().encode("ascii")
    return ENVELOPE_MAGIC + b"\n" + digest + b"\n" + message_bytes


def open_envelope(envelope: bytes) -> str:
    """Verify an envelope's digest and return its UTF-8 message."""

    try:
        magic, supplied_digest, message_bytes = envelope.split(b"\n", 2)
    except ValueError as exc:
        raise ProtocolError("Encrypted envelope is incomplete") from exc

    if magic != ENVELOPE_MAGIC:
        raise ProtocolError("Encrypted envelope has an invalid marker")

    expected_digest = hashlib.sha256(message_bytes).hexdigest().encode("ascii")
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise ProtocolError("Encrypted message failed SHA-256 verification")

    try:
        return message_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("Encrypted message is not valid UTF-8") from exc


def encrypt_message(public_key: rsa.RSAPublicKey, message: str) -> bytes:
    """Encrypt any supported message by splitting it into RSA-OAEP blocks."""

    envelope = build_envelope(message)
    key_bytes = public_key.key_size // 8
    hash_bytes = hashes.SHA256().digest_size
    maximum_plaintext_block = key_bytes - (2 * hash_bytes) - 2

    encrypted_blocks = []
    for start in range(0, len(envelope), maximum_plaintext_block):
        block = envelope[start : start + maximum_plaintext_block]
        encrypted_blocks.append(public_key.encrypt(block, oaep_padding()))

    return b"".join(encrypted_blocks)


def decrypt_message(private_key: rsa.RSAPrivateKey, ciphertext: bytes) -> str:
    """Decrypt fixed-size RSA blocks and verify the reconstructed envelope."""

    block_size = private_key.key_size // 8
    if not ciphertext or len(ciphertext) % block_size != 0:
        raise ProtocolError("Ciphertext does not contain complete RSA blocks")

    plaintext_blocks = []
    try:
        for start in range(0, len(ciphertext), block_size):
            block = ciphertext[start : start + block_size]
            plaintext_blocks.append(private_key.decrypt(block, oaep_padding()))
    except ValueError as exc:
        raise ProtocolError("RSA decryption failed") from exc

    return open_envelope(b"".join(plaintext_blocks))


def receive_exact(connection: socket.socket, byte_count: int) -> Optional[bytes]:
    """Receive exactly byte_count bytes, or None after an orderly disconnect."""

    received = bytearray()
    while len(received) < byte_count:
        chunk = connection.recv(byte_count - len(received))
        if not chunk:
            return None
        received.extend(chunk)
    return bytes(received)


def receive_frame(connection: socket.socket) -> Optional[bytes]:
    """Read one length-prefixed encrypted protocol frame."""

    header = receive_exact(connection, FRAME_HEADER.size)
    if header is None:
        return None

    (frame_size,) = FRAME_HEADER.unpack(header)
    if frame_size <= 0 or frame_size > MAX_FRAME_SIZE:
        raise ProtocolError("Encrypted frame length is invalid")

    payload = receive_exact(connection, frame_size)
    if payload is None:
        raise ProtocolError("Connection ended during an encrypted frame")
    return payload


def send_frame(connection: socket.socket, payload: bytes) -> None:
    """Send one complete length-prefixed frame."""

    if not payload or len(payload) > MAX_FRAME_SIZE:
        raise ProtocolError("Encrypted frame length is invalid")
    connection.sendall(FRAME_HEADER.pack(len(payload)) + payload)


def send_encrypted(
    connection: socket.socket,
    public_key: rsa.RSAPublicKey,
    message: str,
) -> None:
    """Encrypt and send one logical command."""

    send_frame(connection, encrypt_message(public_key, message))


def receive_connect_response(
    control_socket: socket.socket,
) -> tuple[int, rsa.RSAPublicKey]:
    """Receive the plaintext status, data port, and complete server PEM key."""

    response = bytearray()
    while PUBLIC_KEY_END not in response:
        chunk = control_socket.recv(4096)
        if not chunk:
            raise ProtocolError("Server closed during the connect response")
        response.extend(chunk)
        if len(response) > MAX_HANDSHAKE_SIZE:
            raise ProtocolError("Connect response is too large")

    normalized = bytes(response).replace(b"\r\n", b"\n")
    status, separator, body = normalized.partition(b"\n\n")
    if not separator or status.strip() != b"200":
        raise ProtocolError("Server did not return a 200 connect response")

    port_line, separator, public_key_pem = body.partition(b"\n")
    if not separator:
        raise ProtocolError("Connect response is missing the server public key")

    try:
        data_port = int(port_line.strip())
    except ValueError as exc:
        raise ProtocolError("Connect response has an invalid data port") from exc
    if not 1 <= data_port <= 65535:
        raise ProtocolError("Connect response data port is outside TCP range")

    key_end = public_key_pem.find(PUBLIC_KEY_END)
    if key_end == -1:
        raise ProtocolError("Connect response has an incomplete public key")
    public_key_pem = public_key_pem[: key_end + len(PUBLIC_KEY_END)] + b"\n"

    try:
        server_public_key = serialization.load_pem_public_key(public_key_pem)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("Connect response has an invalid public key") from exc

    if not isinstance(server_public_key, rsa.RSAPublicKey):
        raise ProtocolError("Server key is not an RSA public key")
    if server_public_key.key_size != RSA_KEY_SIZE:
        raise ProtocolError("Server RSA public key is not 2048 bits")

    return data_port, server_public_key


def close_socket(connection: Optional[socket.socket]) -> None:
    """Close a socket without masking the application's original outcome."""

    if connection is None:
        return
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        connection.close()
    except OSError:
        pass


class ClientState:
    """All mutable state shared by the input loop and data listener."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT,
            key_size=RSA_KEY_SIZE,
        )
        self.public_key_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        self.control_socket: Optional[socket.socket] = None
        self.data_socket: Optional[socket.socket] = None
        self.server_public_key: Optional[rsa.RSAPublicKey] = None
        self.username: Optional[str] = None
        self.login_candidate: Optional[str] = None

        self.state_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.response_queue: queue.Queue[Optional[str]] = queue.Queue()
        self.pending_command: Optional[str] = None
        self.pending_response_queued = False
        self.stop_event = threading.Event()

    @property
    def connected(self) -> bool:
        return (
            self.control_socket is not None
            and self.data_socket is not None
            and self.server_public_key is not None
        )

    def begin_request(self, command: str) -> None:
        """Mark one command pending; the client issues only one at a time."""

        while True:
            try:
                self.response_queue.get_nowait()
            except queue.Empty:
                break

        with self.state_lock:
            self.pending_command = command
            self.pending_response_queued = False

    def finish_request(self) -> None:
        """Clear the pending-command marker after a reply or timeout."""

        with self.state_lock:
            self.pending_command = None
            self.pending_response_queued = False

    def deliver_direct_response(self, response: Optional[str]) -> bool:
        """Queue a message only if a command is currently awaiting a reply."""

        with self.state_lock:
            if self.pending_command is None or self.pending_response_queued:
                return False
            self.pending_response_queued = True

        with self.print_lock:
            print("Received encrypted message", flush=True)
        self.response_queue.put(response)
        return True

    def display_asynchronous(self, lines: list[str]) -> None:
        """Print an incoming event and redraw the interactive prompt."""

        with self.print_lock:
            print()
            print("Received encrypted message")
            for line in lines:
                print(line)
            print("> ", end="", flush=True)

    def close(self) -> None:
        """Stop the listener and release both TCP connections."""

        self.stop_event.set()
        close_socket(self.data_socket)
        close_socket(self.control_socket)
        self.data_socket = None
        self.control_socket = None
        self.server_public_key = None


def response_fields(message: str) -> tuple[str, str, list[str]]:
    """Return status, response type, and data lines from a server message."""

    normalized = message.replace("\r\n", "\n")
    lines = normalized.split("\n")
    status = lines[0].strip() if lines else ""

    data_lines = lines[2:] if len(lines) >= 2 and lines[1] == "" else lines[1:]
    response_type = data_lines[0] if data_lines else ""
    return status, response_type, data_lines[1:]


def data_listener(state: ClientState) -> None:
    """Receive, decrypt, classify, and display messages from the DATA socket."""

    assert state.data_socket is not None

    try:
        while not state.stop_event.is_set():
            ciphertext = receive_frame(state.data_socket)
            if ciphertext is None:
                break

            message = decrypt_message(state.private_key, ciphertext)
            status, response_type, data_lines = response_fields(message)
            response_type_lower = response_type.lower()

            with state.state_lock:
                username = state.username
                login_candidate = state.login_candidate

            if response_type_lower == "broadcast" and len(data_lines) >= 2:
                source = data_lines[0]
                chat_message = "\n".join(data_lines[1:])
                if source == username and state.deliver_direct_response(message):
                    continue
                state.display_asynchronous(
                    [
                        f"{status} status code received.",
                        f"Broadcast message from {source}: {chat_message}",
                    ]
                )

            elif response_type_lower == "private" and len(data_lines) >= 2:
                source = data_lines[0]
                private_message = "\n".join(data_lines[1:])
                state.display_asynchronous(
                    [
                        f"{status} status code received.",
                        f"{source}: {private_message}",
                    ]
                )

            elif response_type_lower == "join" and data_lines:
                joined_username = data_lines[0]
                if (
                    joined_username == login_candidate
                    and state.deliver_direct_response(message)
                ):
                    continue
                state.display_asynchronous(
                    [
                        f"{status} status code received.",
                        f"{joined_username} has logged in.",
                    ]
                )

            elif response_type_lower == "quit" and data_lines:
                exited_username = data_lines[0]
                state.display_asynchronous(
                    [
                        f"{status} status code received.",
                        f"{exited_username} has logged out.",
                    ]
                )

            elif response_type_lower == "who":
                if not state.deliver_direct_response(message):
                    state.display_asynchronous([f"{status} status code received."])

            elif not state.deliver_direct_response(message):
                state.display_asynchronous([f"{status} status code received."])

    except (OSError, ProtocolError, ValueError):
        pass
    finally:
        if not state.stop_event.is_set():
            state.deliver_direct_response(None)


def connect_to_server(state: ClientState, server_ip: str, control_port: int) -> bool:
    """Create the CONTROL socket, parse the key exchange, then open DATA."""

    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_socket: Optional[socket.socket] = None

    try:
        control_socket.connect((server_ip, control_port))
        data_port, server_public_key = receive_connect_response(control_socket)

        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_socket.connect((server_ip, data_port))

        state.control_socket = control_socket
        state.data_socket = data_socket
        state.server_public_key = server_public_key

        print(
            "200 status code received. "
            f"Starting data connection on port {data_port}"
        )

        listener_thread = threading.Thread(
            target=data_listener,
            args=(state,),
            daemon=True,
        )
        listener_thread.start()
        return True

    except (OSError, ProtocolError, ValueError) as exc:
        print(f"500 status code received. Connection failed: {exc}")
        close_socket(data_socket)
        close_socket(control_socket)
        return False


def send_command_and_wait(
    state: ClientState,
    command_name: str,
    wire_message: str,
) -> Optional[str]:
    """Encrypt one command, send it, and wait for its DATA-socket response."""

    assert state.control_socket is not None
    assert state.server_public_key is not None

    state.begin_request(command_name)
    try:
        send_encrypted(
            state.control_socket,
            state.server_public_key,
            wire_message,
        )
        response = state.response_queue.get(timeout=RESPONSE_TIMEOUT_SECONDS)
        return response
    except queue.Empty:
        print("500 status code received. Server response timed out.")
        return None
    except (OSError, ProtocolError, ValueError) as exc:
        print(f"500 status code received. Command failed: {exc}")
        return None
    finally:
        state.finish_request()


def print_command_response(
    state: ClientState,
    command: str,
    response: Optional[str],
) -> bool:
    """Print the assignment's required output for one command response."""

    if response is None:
        print("500 status code received. Connection closed.")
        return False

    status, response_type, data_lines = response_fields(response)
    success = status == "200"

    if command == "login":
        if success:
            state.username = state.login_candidate
            print("200 status code received. Login successful")
        else:
            state.username = None
            print("500 status code received. Failed to login.")
        state.login_candidate = None

    elif command == "who":
        if success:
            users = "\n".join(data_lines) if response_type.lower() == "who" else ""
            print(f"200 status code received. Users currently connected: {users}")
        else:
            print("500 status code received. Failed to retrieve active users.")

    elif command == "broadcast":
        if success:
            print("200 status code received.")
            if response_type.lower() == "broadcast" and len(data_lines) >= 2:
                source = data_lines[0]
                message = "\n".join(data_lines[1:])
                print(f"Broadcast message from {source}: {message}")
        else:
            print("500 status code received. Failed to broadcast.")

    elif command == "private":
        if success:
            print("200 status code received. Message sent.")
        else:
            print("500 status code received. Message failed to send.")

    elif command == "quit":
        if success:
            print("200 status code received.")
        else:
            print("500 status code received. Failed to disconnect.")

    return success


def valid_port(value: str) -> Optional[int]:
    """Convert a user-supplied TCP port, returning None when invalid."""

    try:
        port = int(value)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def run_client() -> None:
    """Interactive command loop."""

    print("Starting client...")
    state = ClientState()

    try:
        while True:
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting")
                break

            if not user_input:
                continue

            pieces = user_input.split()
            command = pieces[0].lower()

            if command == "connect":
                if len(pieces) != 3:
                    print("Usage: connect <ip> <port>")
                    continue
                if state.connected:
                    print("500 status code received. Client is already connected.")
                    continue

                control_port = valid_port(pieces[2])
                if control_port is None:
                    print("Usage: connect <ip> <port>")
                    continue
                connect_to_server(state, pieces[1], control_port)
                continue

            if command not in {
                "login",
                "who",
                "broadcast",
                "private",
                "quit",
            }:
                print("Invalid command.")
                continue

            if not state.connected:
                print("500 status code received. Connect to a server first.")
                continue

            if command == "login":
                if len(pieces) != 2:
                    print("Usage: login <username>")
                    continue
                if state.username is not None:
                    print("500 status code received. Client is already logged in.")
                    continue

                username = pieces[1]
                state.login_candidate = username
                login_message = (
                    f"login\n\n{username}\n"
                    + state.public_key_pem.decode("ascii")
                )
                response = send_command_and_wait(state, "login", login_message)
                print_command_response(state, "login", response)
                continue

            if state.username is None:
                print("500 status code received. Login first.")
                continue

            if command == "who":
                if len(pieces) != 1:
                    print("Usage: who")
                    continue
                response = send_command_and_wait(state, "who", "who")
                print_command_response(state, "who", response)

            elif command == "broadcast":
                message = user_input.partition(" ")[2].strip()
                if not message:
                    print("Usage: broadcast <message>")
                    continue
                response = send_command_and_wait(
                    state,
                    "broadcast",
                    f"broadcast {message}",
                )
                print_command_response(state, "broadcast", response)

            elif command == "private":
                if len(pieces) < 3:
                    print("Usage: private <username> <message>")
                    continue
                response = send_command_and_wait(state, "private", user_input)
                print_command_response(state, "private", response)

            elif command == "quit":
                if len(pieces) != 1:
                    print("Usage: quit")
                    continue
                response = send_command_and_wait(state, "quit", "quit")
                if print_command_response(state, "quit", response):
                    break

    finally:
        state.close()


if __name__ == "__main__":
    run_client()
