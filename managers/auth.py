import os
import json
import hashlib
import secrets
from typing import Optional
from fastapi import HTTPException, Header

from utils.helpers import PATHS


class AuthManager:
    SESSION_TOKEN = secrets.token_hex(16)

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None):
        salt = salt or secrets.token_hex(8)
        return hashlib.sha256((password + salt).encode()).hexdigest(), salt

    @staticmethod
    def save_auth(username, password):
        hashed, salt = AuthManager.hash_password(password)
        os.makedirs(os.path.dirname(PATHS["auth"]), exist_ok=True)
        with open(PATHS["auth"], "w") as f:
            json.dump({"username": username, "hashed": hashed, "salt": salt}, f)

    @staticmethod
    def get_auth():
        if os.path.exists(PATHS["auth"]):
            try:
                with open(PATHS["auth"], "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    @staticmethod
    def verify_token(authorization: str = Header(None)):
        if authorization != f"Bearer {AuthManager.SESSION_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")
        return True
