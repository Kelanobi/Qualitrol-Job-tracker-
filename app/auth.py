from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta
import getpass
import platform
import re
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from app.config import APPLICATION_PASSWORD_HASH


QUALITROL_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@qualitrolcorp\.com$", re.IGNORECASE)
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)


class AuthenticationError(RuntimeError):
    pass


class LoginHistoryError(AuthenticationError):
    pass


@dataclass(frozen=True)
class WindowsIdentity:
    username: str
    account_name: str
    sid: str | None
    device_identifier: str


@dataclass(frozen=True)
class AuthenticatedSession:
    email: str
    windows_username: str
    windows_identity: str
    windows_sid: str | None
    device_identifier: str
    login_timestamp: datetime
    session_id: str


class WindowsIdentityProvider:
    """Read the signed-in Windows account with Win32 APIs and safe fallbacks."""

    @staticmethod
    def _account_name() -> str:
        if platform.system() != "Windows":
            return getpass.getuser()
        size = wintypes.ULONG(0)
        secur32 = ctypes.WinDLL("secur32", use_last_error=True)
        secur32.GetUserNameExW(2, None, ctypes.byref(size))  # NameSamCompatible
        if size.value:
            buffer = ctypes.create_unicode_buffer(size.value)
            if secur32.GetUserNameExW(2, buffer, ctypes.byref(size)):
                return buffer.value
        size = wintypes.DWORD(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
            return buffer.value
        return getpass.getuser()

    @staticmethod
    def _sid() -> str | None:
        if platform.system() != "Windows":
            return None
        TOKEN_QUERY, TOKEN_USER = 0x0008, 1
        token = wintypes.HANDLE()
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        class TOKEN_USER_STRUCT(ctypes.Structure):
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            needed = wintypes.DWORD()
            advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
            if not needed.value:
                return None
            data = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(token, TOKEN_USER, data, needed, ctypes.byref(needed)):
                return None
            sid_pointer = ctypes.cast(data, ctypes.POINTER(TOKEN_USER_STRUCT)).contents.User.Sid
            string_sid = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(string_sid)):
                return None
            try:
                return string_sid.value
            finally:
                kernel32.LocalFree(string_sid)
        finally:
            kernel32.CloseHandle(token)

    def current(self) -> WindowsIdentity:
        account = self._account_name().strip()
        username = account.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        device = f"{platform.node() or 'unknown'}-{uuid.getnode():012x}"
        return WindowsIdentity(username, account, self._sid(), device)


class AuthenticationService:
    def __init__(
        self, history, identity_provider=None, approved_mappings=None, max_failures=5,
        lockout_seconds=30, password_hash: str | None = None,
    ):
        self.history = history
        self.identity_provider = identity_provider or WindowsIdentityProvider()
        self.approved_mappings = {
            key.casefold(): value.casefold() for key, value in (approved_mappings or {}).items()
        }
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.password_hash = password_hash if password_hash is not None else APPLICATION_PASSWORD_HASH
        self._failures = 0
        self._locked_until: datetime | None = None

    def authenticate(self, email: str, password: str) -> AuthenticatedSession:
        started = datetime.now()
        entered_identity = email.strip().lower()
        normalized = (
            entered_identity
            if "@" in entered_identity
            else f"{entered_identity}@qualitrolcorp.com"
        )
        identity = self.identity_provider.current()
        if self._locked_until and started < self._locked_until:
            self._record(normalized, identity, False, "too_many_failed_attempts", started)
            remaining = max(1, int((self._locked_until - started).total_seconds()))
            raise AuthenticationError(f"Too many failed attempts. Try again in {remaining} seconds.")
        reason = None
        if not QUALITROL_EMAIL.fullmatch(normalized):
            reason = "invalid_email_domain"
        else:
            email_user = normalized.split("@", 1)[0]
            expected_user = self.approved_mappings.get(email_user, email_user)
            if identity.username.casefold() != expected_user:
                reason = "windows_username_mismatch"
            else:
                try:
                    PASSWORD_HASHER.verify(self.password_hash, password)
                except (VerifyMismatchError, VerificationError):
                    reason = "incorrect_password"
        if reason:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._locked_until = started + timedelta(seconds=self.lockout_seconds)
            self._record(normalized, identity, False, reason, started)
            messages = {
                "invalid_email_domain": (
                    "Enter firstname.lastname or your full @qualitrolcorp.com work email address."
                ),
                "windows_username_mismatch": "The work email does not match the signed-in Windows account.",
                "incorrect_password": "The email or password is incorrect.",
            }
            raise AuthenticationError(messages[reason])
        session = AuthenticatedSession(
            email=normalized, windows_username=identity.username,
            windows_identity=identity.account_name, windows_sid=identity.sid,
            device_identifier=identity.device_identifier, login_timestamp=started,
            session_id=str(uuid.uuid4()),
        )
        self._record(normalized, identity, True, None, started, session.session_id)
        self._failures = 0
        self._locked_until = None
        return session

    def _record(self, email, identity, success, reason, timestamp, session_id=None):
        try:
            self.history.record_login(
                timestamp, email, identity.username, identity.sid, identity.device_identifier,
                success, reason, session_id,
            )
        except Exception as exc:
            raise LoginHistoryError("Unable to record the login event safely.") from exc

    def logout(self, session: AuthenticatedSession) -> None:
        self.history.record_logout(session.session_id, datetime.now())
