"""CNT4713 Project 3 encrypted multi-client chat server.

The application deliberately keeps the assignment's two-connection design:

* client -> server commands use the CONTROL socket;
* server -> client responses use that client's DATA socket.

The initial connect response is plaintext because it carries the server's
public key.  Every later logical message is protected with RSA-OAEP using
SHA-256.  A SHA-256 digest is included inside each encrypted envelope and is
verified after decryption.
"""

from __future__ import annotations

import hashlib
import hmac
import socket
import struct
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


CONTROL_HOST = "0.0.0.0"
RSA_KEY_SIZE = 2048
RSA_PUBLIC_EXPONENT = 65537
FRAME_HEADER = struct.Struct("!I")
MAX_FRAME_SIZE = 1_048_576
ENVELOPE_MAGIC = b"CNT4713-SHA256"


class ProtocolError(Exception):
    """Raised when a peer sends a malformed or unverifiable protocol message."""


@dataclass
class ClientRecord:
    """Information the server needs to route an encrypted response."""

    username: str
    data_socket: socket.socket
    public_key: rsa.RSAPublicKey
    send_lock: threading.Lock = field(default_factory=threading.Lock)


active_clients: dict[str, ClientRecord] = {}
active_clients_lock = threading.Lock()


def oaep_padding() -> padding.OAEP:
    """Return the one padding configuration used by both sides."""

    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def build_envelope(message: str) -> bytes:
    """Add the assignment-required SHA-256 digest to a UTF-8 message."""

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
    send_lock: Optional[threading.Lock] = None,
) -> None:
    """Encrypt and send one logical message without interleaving frames."""

    ciphertext = encrypt_message(public_key, message)
    if send_lock is None:
        send_frame(connection, ciphertext)
        return

    with send_lock:
        send_frame(connection, ciphertext)


def send_to_client(client: ClientRecord, message: str) -> bool:
    """Send a message to one active client, returning whether it succeeded."""

    try:
        send_encrypted(
            client.data_socket,
            client.public_key,
            message,
            client.send_lock,
        )
        return True
    except (OSError, ProtocolError, ValueError):
        return False


def client_snapshot() -> list[ClientRecord]:
    """Take a stable snapshot so network I/O never happens under the map lock."""

    with active_clients_lock:
        return list(active_clients.values())


def broadcast_to_clients(
    message: str,
    excluded_username: Optional[str] = None,
) -> None:
    """Encrypt the same logical message separately for every recipient."""

    for client in client_snapshot():
        if client.username == excluded_username:
            continue
        send_to_client(client, message)


def remove_client(username: str, expected_record: ClientRecord) -> bool:
    """Remove username only if it still refers to this exact connection."""

    with active_clients_lock:
        current = active_clients.get(username)
        if current is not expected_record:
            return False
        del active_clients[username]
        return True


def parse_login_message(message: str) -> tuple[str, rsa.RSAPublicKey]:
    """Parse: login, an empty line, username, then a PEM public key."""

    normalized = message.replace("\r\n", "\n")
    command, separator, body = normalized.partition("\n\n")
    if not separator or command.strip().lower() != "login":
        raise ProtocolError("Login message is missing its empty separator line")

    username, separator, public_key_pem = body.partition("\n")
    username = username.strip()
    public_key_pem = public_key_pem.strip() + "\n"

    if (
        not separator
        or not username
        or any(character.isspace() for character in username)
        or len(username) > 128
        or "-----BEGIN PUBLIC KEY-----" not in public_key_pem
        or "-----END PUBLIC KEY-----" not in public_key_pem
    ):
        raise ProtocolError("Login message is missing a username or public key")

    try:
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode("ascii")
        )
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise ProtocolError("Client public key is invalid") from exc

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ProtocolError("Client key is not an RSA public key")
    if public_key.key_size != RSA_KEY_SIZE:
        raise ProtocolError("Client RSA public key is not 2048 bits")

    return username, public_key


def try_extract_public_key(message: str) -> Optional[rsa.RSAPublicKey]:
    """Recover a public key from a malformed login so a 500 can be encrypted."""

    begin = message.find("-----BEGIN PUBLIC KEY-----")
    end_marker = "-----END PUBLIC KEY-----"
    end = message.find(end_marker)
    if begin == -1 or end == -1:
        return None

    pem = message[begin : end + len(end_marker)] + "\n"
    try:
        public_key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return None

    if isinstance(public_key, rsa.RSAPublicKey):
        return public_key
    return None


def command_and_remainder(message: str) -> tuple[str, str]:
    """Return a lowercase command and all text that follows its first space."""

    first_line = message.replace("\r\n", "\n").split("\n", 1)[0].strip()
    if not first_line:
        return "", ""
    pieces = first_line.split(maxsplit=1)
    command = pieces[0].lower()
    remainder = pieces[1] if len(pieces) == 2 else ""
    return command, remainder


def close_socket(connection: Optional[socket.socket]) -> None:
    """Close a socket without obscuring the original connection outcome."""

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


def handle_client_session(
    control_socket: socket.socket,
    client_address: tuple[str, int],
    server_private_key: rsa.RSAPrivateKey,
    server_public_pem: bytes,
) -> None:
    """Create one data socket and process this client's control commands."""

    del client_address  # The protocol identifies clients by login username.

    data_listener: Optional[socket.socket] = None
    data_socket: Optional[socket.socket] = None
    client: Optional[ClientRecord] = None
    graceful_quit = False
    temporary_send_lock = threading.Lock()

    try:
        data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        data_listener.bind((CONTROL_HOST, 0))
        data_listener.listen(1)
        data_listener.settimeout(30)

        data_port = data_listener.getsockname()[1]
        connect_response = b"200\n\n" + str(data_port).encode("ascii")
        connect_response += b"\n" + server_public_pem

        print("Connection requested. Creating data socket", flush=True)
        control_socket.sendall(connect_response)

        data_socket, _ = data_listener.accept()
        data_socket.settimeout(None)

        while True:
            encrypted_request = receive_frame(control_socket)
            if encrypted_request is None:
                break

            print("Received encrypted message", flush=True)

            try:
                request = decrypt_message(server_private_key, encrypted_request)
            except ProtocolError:
                if client is not None:
                    send_to_client(client, "500")
                    continue
                break

            command, remainder = command_and_remainder(request)

            if command == "login":
                if client is not None:
                    send_to_client(client, "500")
                    continue

                try:
                    username, client_public_key = parse_login_message(request)
                except ProtocolError:
                    fallback_key = try_extract_public_key(request)
                    if fallback_key is not None and data_socket is not None:
                        send_encrypted(
                            data_socket,
                            fallback_key,
                            "500",
                            temporary_send_lock,
                        )
                    continue

                print(f"Login requested by: {username}", flush=True)
                candidate = ClientRecord(
                    username=username,
                    data_socket=data_socket,
                    public_key=client_public_key,
                )

                with active_clients_lock:
                    if username in active_clients:
                        accepted = False
                    else:
                        active_clients[username] = candidate
                        accepted = True

                if not accepted:
                    send_encrypted(
                        data_socket,
                        client_public_key,
                        "500",
                        temporary_send_lock,
                    )
                    continue

                client = candidate
                if not send_to_client(client, f"200\n\njoin\n{username}"):
                    break
                broadcast_to_clients(
                    f"200\n\njoin\n{username}",
                    excluded_username=username,
                )
                continue

            if client is None:
                # There is no client key available for an encrypted error yet.
                break

            if command == "who":
                print("Who requested. Sending users.", flush=True)
                with active_clients_lock:
                    users = [
                        username
                        for username in active_clients
                        if username != client.username
                    ]
                send_to_client(client, f"200\n\nwho\n{', '.join(users)}")

            elif command == "broadcast":
                message = remainder.strip()
                if not message:
                    send_to_client(client, "500")
                    continue

                print(f"Broadcast requested by {client.username}", flush=True)
                print(f"Message: {message}", flush=True)
                broadcast_to_clients(
                    f"200\n\nBroadcast\n{client.username}\n{message}"
                )

            elif command == "private":
                pieces = remainder.split(maxsplit=1)
                if len(pieces) != 2 or not pieces[1].strip():
                    send_to_client(client, "500")
                    continue

                destination_username = pieces[0]
                private_message = pieces[1].strip()
                print(
                    "Private message from "
                    f"{client.username} to {destination_username}",
                    flush=True,
                )

                with active_clients_lock:
                    destination = active_clients.get(destination_username)

                delivered = False
                if destination is not None:
                    delivered = send_to_client(
                        destination,
                        "200\n\nPrivate\n"
                        f"{client.username}\n{private_message}",
                    )

                send_to_client(client, "200" if delivered else "500")

            elif command == "quit":
                print(f"Quit requested by {client.username}", flush=True)
                send_to_client(client, "200")
                graceful_quit = True
                removed = remove_client(client.username, client)
                if removed:
                    broadcast_to_clients(
                        f"200\n\nquit\n{client.username}",
                        excluded_username=client.username,
                    )
                return

            else:
                send_to_client(client, "500")

    except (OSError, ProtocolError, ValueError):
        pass
    finally:
        if data_listener is not None:
            close_socket(data_listener)

        if client is not None and not graceful_quit:
            removed = remove_client(client.username, client)
            if removed:
                broadcast_to_clients(
                    f"200\n\nquit\n{client.username}",
                    excluded_username=client.username,
                )

        close_socket(data_socket)
        close_socket(control_socket)


def parse_control_port(arguments: list[str]) -> int:
    """Validate the required command-line TCP port."""

    if len(arguments) != 2:
        raise ValueError("Usage: python server.py <control_port>")

    port = int(arguments[1])
    if not 1 <= port <= 65535:
        raise ValueError("Control port must be between 1 and 65535")
    return port


def start_server(control_port: int) -> None:
    """Generate the server keys, listen, and create one thread per client."""

    print("Starting server...", flush=True)
    print("Creating RSA keypair", flush=True)
    server_private_key = rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT,
        key_size=RSA_KEY_SIZE,
    )
    server_public_pem = server_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    print("RSA keypair created", flush=True)
    print("Creating server socket", flush=True)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((CONTROL_HOST, control_port))
    server_socket.listen()

    print("Awaiting connections...", flush=True)

    try:
        while True:
            control_socket, client_address = server_socket.accept()
            session_thread = threading.Thread(
                target=handle_client_session,
                args=(
                    control_socket,
                    client_address,
                    server_private_key,
                    server_public_pem,
                ),
                daemon=True,
            )
            session_thread.start()
    except KeyboardInterrupt:
        pass
    finally:
        close_socket(server_socket)


def main() -> None:
    """Command-line entry point."""

    try:
        control_port = parse_control_port(sys.argv)
        start_server(control_port)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
