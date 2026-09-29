import hashlib
import os
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from .config import DATA


def cipher():
    key = os.getenv("SOURCE_SECRET_KEY")
    if not key:
        path = DATA / ".source-key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        key = path.read_bytes()
    return Fernet(key)


def validate_rtsp(uri):
    parsed = urlsplit(uri)
    if parsed.scheme not in ("rtsp", "rtsps", "https") or not parsed.hostname:
        raise ValueError("Нужен адрес rtsp://, rtsps:// или https://")
    return cipher().encrypt(uri.encode()).decode()


def decode_uri(encrypted):
    return cipher().decrypt(encrypted.encode()).decode()


def digest(content):
    return hashlib.sha256(content).hexdigest()
