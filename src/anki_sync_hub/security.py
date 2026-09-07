from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

OWNER_SCRYPT_N = 2**15
OWNER_SCRYPT_R = 8
OWNER_SCRYPT_P = 1
SYNC_PBKDF2_ITERATIONS = 600_000

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4))


def validate_sync_username(username: str) -> str:
    username = username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Use 1-128 letters, digits, dots, underscores, @, +, or hyphens; "
            "the first character must be alphanumeric."
        )
    if username in {".", ".."}:
        raise ValueError("Invalid sync username.")
    return username


def validate_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("Password is too long.")
    return password


def hash_owner_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=OWNER_SCRYPT_N,
        r=OWNER_SCRYPT_R,
        p=OWNER_SCRYPT_P,
        maxmem=64 * 1024 * 1024,
    )
    return (
        f"scrypt$n={OWNER_SCRYPT_N},r={OWNER_SCRYPT_R},p={OWNER_SCRYPT_P}"
        f"${_b64(salt)}${_b64(digest)}"
    )


def verify_owner_password(password: str, encoded: str) -> bool:
    try:
        algorithm, params, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "scrypt":
            return False
        parsed = dict(part.split("=", 1) for part in params.split(","))
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt_text),
            n=int(parsed["n"]),
            r=int(parsed["r"]),
            p=int(parsed["p"]),
            dklen=len(_unb64(digest_text)),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, _unb64(digest_text))
    except (ValueError, KeyError):
        return False


def hash_sync_password(password: str) -> str:
    """Return a PHC value accepted by RustCrypto's pbkdf2 verifier."""
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, SYNC_PBKDF2_ITERATIONS, dklen=32
    )
    return f"$pbkdf2-sha256$i={SYNC_PBKDF2_ITERATIONS},l=32${_b64(salt)}${_b64(digest)}"


def derive_sync_host_key(username: str, password_hash: str) -> str:
    """Mirror the official simple server's stable host-key derivation."""
    return hashlib.sha1(f"{username}:{password_hash}".encode(), usedforsecurity=False).hexdigest()


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_token() -> tuple[str, str, str]:
    token = f"ash_{secrets.token_urlsafe(32)}"
    return token, token[:12], hash_token(token)
