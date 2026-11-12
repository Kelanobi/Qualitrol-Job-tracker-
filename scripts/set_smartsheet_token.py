from __future__ import annotations

from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.phase2.credentials import SecureCredentialStore


def main() -> None:
    token = getpass("Paste the Smartsheet API token (input is hidden): ")
    SecureCredentialStore().set_smartsheet_token(token)
    print("Smartsheet token saved securely in the Windows credential store.")


if __name__ == "__main__":
    main()
