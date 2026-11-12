from __future__ import annotations

from datetime import datetime
from time import perf_counter

import pytest
from argon2 import PasswordHasher

from app.auth import AuthenticationError, AuthenticationService, WindowsIdentity
from app.services.history_service import HistoryService


TEST_PASSWORD_HASH = PasswordHasher().hash("Password123")


class IdentityProvider:
    def __init__(self, username="kelvin.obi", account="DHRQTC\\kelvin.obi"):
        self.identity = WindowsIdentity(username, account, "S-1-test", "test-device")

    def current(self):
        return self.identity


def service(tmp_path, username="kelvin.obi", **kwargs):
    return AuthenticationService(
        HistoryService(tmp_path / "history.db"), IdentityProvider(username),
        password_hash=TEST_PASSWORD_HASH, **kwargs,
    )


def test_valid_local_login_normalizes_email_and_is_fast(tmp_path):
    auth = service(tmp_path)
    started = perf_counter()
    session = auth.authenticate("  Kelvin.Obi@qualitrolcorp.com ", "Password123")
    elapsed = perf_counter() - started
    assert session.email == "kelvin.obi@qualitrolcorp.com"
    assert session.windows_username == "kelvin.obi"
    assert session.windows_identity == "DHRQTC\\kelvin.obi"
    assert session.windows_sid == "S-1-test"
    assert elapsed < 1.0


def test_firstname_lastname_login_expands_to_company_email(tmp_path):
    session = service(tmp_path).authenticate(" Kelvin.Obi ", "Password123")

    assert session.email == "kelvin.obi@qualitrolcorp.com"


@pytest.mark.parametrize("email,password,message", [
    ("kelvin.obi@example.com", "Password123", "@qualitrolcorp.com"),
    ("kelvin.obi@qualitrolcorp.com", "wrong", "incorrect"),
])
def test_invalid_domain_and_password_are_denied(tmp_path, email, password, message):
    with pytest.raises(AuthenticationError, match=message):
        service(tmp_path).authenticate(email, password)


def test_windows_username_must_match_email_prefix_without_domain(tmp_path):
    with pytest.raises(AuthenticationError, match="Windows account"):
        service(tmp_path, username="another.user").authenticate(
            "kelvin.obi@qualitrolcorp.com", "Password123"
        )


def test_explicit_username_mapping_is_supported(tmp_path):
    auth = service(tmp_path, username="kobi", approved_mappings={"kelvin.obi": "kobi"})
    assert auth.authenticate("kelvin.obi@qualitrolcorp.com", "Password123").windows_username == "kobi"


def test_login_and_logout_are_recorded_without_password(tmp_path):
    history = HistoryService(tmp_path / "history.db")
    auth = AuthenticationService(history, IdentityProvider(), password_hash=TEST_PASSWORD_HASH)
    session = auth.authenticate("kelvin.obi@qualitrolcorp.com", "Password123")
    auth.logout(session)
    with history._connect() as connection:
        row = connection.execute("SELECT * FROM login_events WHERE session_id=?", (session.session_id,)).fetchone()
        columns = {item[1] for item in connection.execute("PRAGMA table_info(login_events)")}
    assert row["success"] == 1
    assert row["logout_timestamp"] is not None
    assert not any("password" in column.casefold() for column in columns)


def test_too_many_failures_temporarily_lock_login(tmp_path):
    auth = service(tmp_path, max_failures=2, lockout_seconds=30)
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            auth.authenticate("kelvin.obi@qualitrolcorp.com", "wrong")
    with pytest.raises(AuthenticationError, match="Too many failed attempts"):
        auth.authenticate("kelvin.obi@qualitrolcorp.com", "Password123")
