import os
import json
import logging
from typing import Optional, Dict
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _get_fernet() -> Optional[Fernet]:
    key = os.getenv("API_HEADERS_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def encrypt_headers(headers: Dict) -> str:
    f = _get_fernet()
    raw = json.dumps(headers).encode()
    if f:
        return f.encrypt(raw).decode()
    else:
        logger.warning("API_HEADERS_KEY not set — headers stored as plaintext")
        return json.dumps(headers)


def decrypt_headers(encrypted: str) -> Dict:
    f = _get_fernet()
    if f:
        try:
            return json.loads(f.decrypt(encrypted.encode()).decode())
        except InvalidToken:
            pass
    try:
        return json.loads(encrypted)
    except Exception:
        return {}
