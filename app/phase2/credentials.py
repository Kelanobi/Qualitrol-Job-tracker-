from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Protocol


SERVICE_NAME = "DMSOrderTracker"
TOKEN_ACCOUNT = "SmartsheetApiToken"
LOGIN_PASSWORD_ACCOUNT_PREFIX = "LoginPassword:"
TOKEN_ENVIRONMENT_VARIABLE = "DMS_SMARTSHEET_TOKEN"
BUNDLED_TOKEN_PATH = Path("_secure") / "smartsheet.token"


class CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class SecureCredentialStore:
    def __init__(self, backend: CredentialBackend | None = None):
        if backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError("Secure credential support is not installed.") from exc
            backend = keyring
        self._backend = backend

    def get_smartsheet_token(self) -> str | None:
        centrally_deployed = os.getenv(TOKEN_ENVIRONMENT_VARIABLE, "").strip()
        if centrally_deployed:
            return centrally_deployed
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            try:
                bundled = (Path(bundle_root) / BUNDLED_TOKEN_PATH).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                bundled = ""
            if bundled:
                return bundled
        token = self._backend.get_password(SERVICE_NAME, TOKEN_ACCOUNT)
        return token.strip() if token and token.strip() else None

    def set_smartsheet_token(self, token: str) -> None:
        cleaned = token.strip()
        if not cleaned:
            raise ValueError("The Smartsheet API token cannot be empty.")
        self._backend.set_password(SERVICE_NAME, TOKEN_ACCOUNT, cleaned)

    def delete_smartsheet_token(self) -> None:
        self._backend.delete_password(SERVICE_NAME, TOKEN_ACCOUNT)

    @staticmethod
    def _login_account(username: str) -> str:
        normalized = username.strip().casefold()
        if not normalized:
            raise ValueError("The Windows username cannot be empty.")
        return f"{LOGIN_PASSWORD_ACCOUNT_PREFIX}{normalized}"

    def get_login_password(self, username: str) -> str | None:
        password = self._backend.get_password(SERVICE_NAME, self._login_account(username))
        return password if password else None

    def set_login_password(self, username: str, password: str) -> None:
        if not password:
            raise ValueError("The login password cannot be empty.")
        self._backend.set_password(SERVICE_NAME, self._login_account(username), password)

    def delete_login_password(self, username: str) -> None:
        self._backend.delete_password(SERVICE_NAME, self._login_account(username))
