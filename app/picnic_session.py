"""Holds the Picnic login for the web app. The password is never stored, only Picnic's session token."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from python_picnic_api2 import PicnicAPI
from python_picnic_api2.session import Picnic2FAError, Picnic2FARequired

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TOKEN_FILE = DATA_DIR / "picnic_token"
COUNTRY_FILE = DATA_DIR / "picnic_country"


class PicnicSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.api: PicnicAPI | None = None
        self._pending: PicnicAPI | None = None  # waiting for the SMS code
        self._load()

    def _load(self):
        if not TOKEN_FILE.exists():
            return
        token = TOKEN_FILE.read_text().strip()
        country = COUNTRY_FILE.read_text().strip() if COUNTRY_FILE.exists() else os.environ.get("PICNIC_COUNTRY", "DE")
        if token:
            self.api = PicnicAPI(country_code=country, auth_token=token)

    def _save(self, api: PicnicAPI, country: str):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(api.session.auth_token or "")
        TOKEN_FILE.chmod(0o600)
        COUNTRY_FILE.write_text(country)
        self.api = api

    def logged_in(self) -> bool:
        if not self.api:
            return False
        try:
            self.api.get_user()
            return True
        except Exception:
            self.api = None
            return False

    def login(self, username: str, password: str, country: str) -> dict:
        api = PicnicAPI(country_code=country)
        try:
            api.login(username, password)
        except Picnic2FARequired:
            api.generate_2fa_code("SMS")
            self._pending = api
            self._pending_country = country
            return {"need_2fa": True}
        self._save(api, country)
        return {"need_2fa": False}

    def verify_2fa(self, code: str):
        if not self._pending:
            raise Picnic2FAError("No login in progress, start again.")
        self._pending.verify_2fa_code(code.strip())
        self._save(self._pending, self._pending_country)
        self._pending = None

    def logout(self):
        self.api = None
        self._pending = None
        TOKEN_FILE.unlink(missing_ok=True)
