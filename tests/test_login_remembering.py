from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.auth import AuthenticationError
from app.controller import ApplicationController


class MemoryCredentials:
    def __init__(self):
        self.saved = []
        self.deleted = []

    def set_login_password(self, username, password):
        self.saved.append((username, password))

    def delete_login_password(self, username):
        self.deleted.append(username)

    def get_login_password(self, username):
        return "RememberedPassword"


def controller_with(auth):
    controller = ApplicationController.__new__(ApplicationController)
    controller.auth = auth
    controller.session = None
    controller.log = logging.getLogger("test-login-remembering")
    return controller


def test_successful_login_is_saved_to_credential_manager(monkeypatch):
    credentials = MemoryCredentials()
    session = SimpleNamespace(windows_username="kelvin.obi")
    auth = SimpleNamespace(authenticate=lambda _email, _password: session)
    controller = controller_with(auth)
    monkeypatch.setattr("app.controller.SecureCredentialStore", lambda: credentials)

    assert controller.authenticate("kelvin.obi", "Password123") is session
    assert credentials.saved == [("kelvin.obi", "Password123")]
    assert credentials.deleted == []


def test_failed_login_is_never_saved_and_removes_stale_password(monkeypatch):
    credentials = MemoryCredentials()

    class Auth:
        def authenticate(self, _email, _password):
            raise AuthenticationError("incorrect")

        def current_windows_username(self):
            return "kelvin.obi"

    controller = controller_with(Auth())
    monkeypatch.setattr("app.controller.SecureCredentialStore", lambda: credentials)

    with pytest.raises(AuthenticationError, match="incorrect"):
        controller.authenticate("kelvin.obi", "wrong")
    assert credentials.saved == []
    assert credentials.deleted == ["kelvin.obi"]


def test_login_defaults_prefill_windows_username_and_saved_password(monkeypatch):
    credentials = MemoryCredentials()
    auth = SimpleNamespace(current_windows_username=lambda: "kelvin.obi")
    controller = controller_with(auth)
    monkeypatch.setattr("app.controller.SecureCredentialStore", lambda: credentials)

    assert controller.login_defaults() == ("kelvin.obi", "RememberedPassword")
