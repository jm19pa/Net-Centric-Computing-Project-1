"""Launch the real server and two real clients through the graded workflow.

Run from the project root:

    python tests/integration_test.py

This file is for team verification and should not be submitted to Canvas unless
the instructor explicitly requests test code.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TIMEOUT_SECONDS = 15


class ManagedProcess:
    """A subprocess whose output can be searched while it is still running."""

    def __init__(self, name: str, command: list[str]) -> None:
        self.name = name
        self.process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        self.output = ""
        self.condition = threading.Condition()
        self.reader_thread = threading.Thread(target=self._read, daemon=True)
        self.reader_thread.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        while True:
            character = self.process.stdout.read(1)
            if character == "":
                break
            with self.condition:
                self.output += character
                self.condition.notify_all()
        with self.condition:
            self.condition.notify_all()

    def mark(self) -> int:
        """Return the current output length for a later non-stale search."""

        with self.condition:
            return len(self.output)

    def send_line(self, line: str) -> int:
        """Write one interactive command and return its output start marker."""

        start = self.mark()
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return start

    def wait_for(
        self,
        expected: str,
        start: int = 0,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Wait for expected text produced after start, or fail with all output."""

        deadline = time.monotonic() + timeout
        with self.condition:
            while expected not in self.output[start:]:
                if self.process.poll() is not None:
                    raise AssertionError(
                        f"{self.name} exited before producing {expected!r}.\n"
                        f"Complete output:\n{self.output}"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"{self.name} timed out waiting for {expected!r}.\n"
                        f"Complete output:\n{self.output}"
                    )
                self.condition.wait(remaining)

    def stop(self) -> None:
        """Terminate a process left running after success or failure."""

        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)


def unused_local_port() -> int:
    """Ask the OS for a currently unused local TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    """Exercise success and failure behavior with two interactive clients."""

    control_port = unused_local_port()
    processes: list[ManagedProcess] = []

    try:
        server = ManagedProcess(
            "server",
            [sys.executable, "-u", "server.py", str(control_port)],
        )
        processes.append(server)
        server.wait_for("Awaiting connections...")

        bob = ManagedProcess(
            "bob client",
            [sys.executable, "-u", "client.py"],
        )
        processes.append(bob)
        bob.wait_for("Starting client...")

        start = bob.send_line(f"connect 127.0.0.1 {control_port}")
        bob.wait_for("Starting data connection on port", start)
        start = bob.send_line("login bob")
        bob.wait_for("Login successful", start)

        alice = ManagedProcess(
            "alice client",
            [sys.executable, "-u", "client.py"],
        )
        processes.append(alice)
        alice.wait_for("Starting client...")

        start = alice.send_line(f"connect 127.0.0.1 {control_port}")
        alice.wait_for("Starting data connection on port", start)

        start = alice.send_line("login bob")
        alice.wait_for("Failed to login.", start)

        bob_join_start = bob.mark()
        start = alice.send_line("login alice")
        alice.wait_for("Login successful", start)
        bob.wait_for("alice has logged in.", bob_join_start)

        start = alice.send_line("who")
        alice.wait_for("Users currently connected: bob", start)

        bob_broadcast_start = bob.mark()
        start = alice.send_line("broadcast Hello all!")
        alice.wait_for("Broadcast message from alice: Hello all!", start)
        bob.wait_for(
            "Broadcast message from alice: Hello all!",
            bob_broadcast_start,
        )

        start = alice.send_line("private nobody Test")
        alice.wait_for("Message failed to send.", start)

        bob_private_start = bob.mark()
        start = alice.send_line("private bob Let's talk")
        alice.wait_for("Message sent.", start)
        bob.wait_for("alice: Let's talk", bob_private_start)

        bob_logout_start = bob.mark()
        start = alice.send_line("quit")
        alice.wait_for("200 status code received.", start)
        alice.process.wait(timeout=5)
        bob.wait_for("alice has logged out.", bob_logout_start)

        start = bob.send_line("quit")
        bob.wait_for("200 status code received.", start)
        bob.process.wait(timeout=5)

        server.wait_for("Who requested. Sending users.")
        server.wait_for("Broadcast requested by alice")
        server.wait_for("Private message from alice to bob")
        server.wait_for("Quit requested by alice")
        server.wait_for("Quit requested by bob")

        print("PASS: encrypted two-client integration test")
        print(f"Temporary control port: {control_port}")

    finally:
        for managed_process in reversed(processes):
            managed_process.stop()


if __name__ == "__main__":
    main()
