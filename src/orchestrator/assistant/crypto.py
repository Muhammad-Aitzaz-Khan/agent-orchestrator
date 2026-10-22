"""Encryption for stored secrets (Google tokens), keyed by RELAY_SECRET_KEY.

Locally, when RELAY_SECRET_KEY is not set, a random key is created once and kept in
relay.secret next to the database, so tokens stay readable across restarts.
"""
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SECRET_FILE = Path("relay.secret")


def secret_key() -> str:
    key = os.getenv("RELAY_SECRET_KEY", "").strip()
    if key:
        return key
    if SECRET_FILE.is_file():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(key, encoding="utf-8")
    return key


def _fernet() -> Fernet:
    digest = hashlib.sha256(("relay-tokens:" + secret_key()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: dict) -> str:
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def decrypt(token: str) -> dict | None:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return None
