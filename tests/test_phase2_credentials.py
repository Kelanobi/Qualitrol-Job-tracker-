import pytest

from app.phase2.credentials import (
    LOGIN_PASSWORD_ACCOUNT_PREFIX, SERVICE_NAME, TOKEN_ACCOUNT, TOKEN_ENVIRONMENT_VARIABLE,
    SecureCredentialStore,
)


class FakeBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        del self.values[(service, username)]


def test_token_is_kept_in_credential_backend_not_application_storage():
    backend = FakeBackend()
    store = SecureCredentialStore(backend)
    store.set_smartsheet_token("  secret-token  ")
    assert backend.values[(SERVICE_NAME, TOKEN_ACCOUNT)] == "secret-token"
    assert store.get_smartsheet_token() == "secret-token"
    store.delete_smartsheet_token()
    assert store.get_smartsheet_token() is None


def test_empty_token_is_rejected():
    with pytest.raises(ValueError, match="cannot be empty"):
        SecureCredentialStore(FakeBackend()).set_smartsheet_token("  ")


def test_login_password_is_scoped_to_normalized_windows_username():
    backend = FakeBackend()
    store = SecureCredentialStore(backend)
    store.set_login_password(" Kelvin.Obi ", "Password123")
    account = f"{LOGIN_PASSWORD_ACCOUNT_PREFIX}kelvin.obi"
    assert backend.values[(SERVICE_NAME, account)] == "Password123"
    assert store.get_login_password("KELVIN.OBI") == "Password123"
    store.delete_login_password("kelvin.obi")
    assert store.get_login_password("kelvin.obi") is None


def test_empty_login_password_is_rejected():
    with pytest.raises(ValueError, match="cannot be empty"):
        SecureCredentialStore(FakeBackend()).set_login_password("kelvin.obi", "")


def test_centrally_deployed_token_is_used_on_any_laptop(monkeypatch):
    backend = FakeBackend()
    backend.values[(SERVICE_NAME, TOKEN_ACCOUNT)] = "local-token"
    monkeypatch.setenv(TOKEN_ENVIRONMENT_VARIABLE, " shared-company-token ")

    assert SecureCredentialStore(backend).get_smartsheet_token() == "shared-company-token"


def test_standalone_bundle_uses_company_token_without_prompt(tmp_path, monkeypatch):
    secure = tmp_path / "_secure"
    secure.mkdir()
    (secure / "smartsheet.token").write_text(" bundled-company-token ", encoding="utf-8")
    monkeypatch.delenv(TOKEN_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr("app.phase2.credentials.sys._MEIPASS", str(tmp_path), raising=False)

    assert SecureCredentialStore(FakeBackend()).get_smartsheet_token() == "bundled-company-token"
