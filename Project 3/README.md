# CNT4713 Project 3 - Encrypted Chat

Team members:

- Juan Pinero - Panther ID 6353764
- Bishal Ghosh - Panther ID 6404406
- Michael Estrada - Panther ID 6596109
- Alejandro Leon Prieto - Panther ID 6530978

## Required software

- Python 3.10 or newer
- The Python `cryptography` package listed in `requirements.txt`

Install the dependency:

```powershell
py -m pip install -r requirements.txt
```

## Run

Open three terminals in this folder.

Terminal 1:

```powershell
py server.py 8991
```

Terminal 2:

```powershell
py client.py
```

Then enter:

```text
connect 127.0.0.1 8991
login bob
```

Terminal 3:

```powershell
py client.py
```

Then enter:

```text
connect 127.0.0.1 8991
login alice
who
broadcast Hello all!
private bob Let's talk
quit
```

Return to Bob's terminal and enter:

```text
quit
```

Run the automated two-client verification:

```powershell
py tests/integration_test.py
```

The final line should begin with `PASS:`.

## Protocol choices

The assignment specifies RSA 2048, SHA-256, public-key exchange, and the
plaintext command/response formats, but it does not specify RSA padding,
serialization, large-message handling, or TCP framing. This implementation
uses:

- RSA-OAEP with SHA-256 and MGF1-SHA-256;
- PEM SubjectPublicKeyInfo public keys;
- fixed-size RSA ciphertext blocks so the PEM-bearing login can be encrypted;
- a SHA-256 digest inside each encrypted envelope; and
- a four-byte network-order length before each encrypted TCP frame.

The connect response remains plaintext so the server can send its data port and
public key. Every message after that exchange is encrypted.

## LMS submission

Follow the current Canvas submission fields. The assignment explicitly asks
for `server.py`, `client.py`, and `answers.txt` and says not to submit a ZIP or
IDE project files. `README.md` and `requirements.txt` are useful for GitHub and
team setup; submit them only if the LMS provides an appropriate place or the
instructor asks for them.
