# DMS Order Tracker

DMS Order Tracker is a Windows desktop application for checking the required Specs, QA, DCL and Drawings documents for DMS manufacturing orders. It updates verification columns K–N and employee attribution column O of existing DMS rows in `T:\Temp\DMS\123.xlsx`, and records every verification attempt in a local SQLite history database.

## Install and run

Python 3.12 is recommended.

```powershell
python -m pip install -r requirements.txt
python main.py
```

The local login screen opens without accessing Excel, Smartsheet or network drives. The dashboard opens only after the employee's Qualitrol email, Windows username and shared application password have been verified. Normal users should run the packaged executable rather than Python.

## Employee authentication and attribution

- Login confirmation is required on every launch; authenticated sessions are never persisted.
- The login screen automatically fills the signed-in Windows username. After the first successful login,
  the application password is stored in that user's Windows Credential Manager and restored masked on
  later launches. Failed passwords are never saved, and stale saved credentials are removed after failure.
- Only `@qualitrolcorp.com` email addresses are accepted, and the email prefix must match the signed-in Windows username unless an approved mapping is configured in `app/config.py`.
- Windows account name and SID are read with Windows APIs. The in-memory session is cleared on Sign Out or application close.
- The shared password is verified locally against an Argon2id hash supplied through `DMS_APPLICATION_PASSWORD_HASH` or bundled securely during packaging; plaintext passwords are never stored or logged.
- When K–N or the Project/Flow classification genuinely changes, Column O (`Modified by`) is written in the same save operation. Unchanged rows retain their prior Column O value.
- Login/logout events and every Excel update attempt are retained in the existing local history database.

To change the shared password for a future deployment, generate a replacement hash and rebuild:

```powershell
python scripts\generate_password_hash.py
# Set DMS_APPLICATION_PASSWORD_HASH locally to the printed hash, then build.
build.bat
```

## Workflows

- **Automatic Search** reads `Sheet1`, finds DMS orders with no prior history, asks for confirmation, verifies them, safely updates Excel and records history.
- **Manual Search** creates the requested number of inputs, validates that each order exists and belongs to DMS, then supports first-time checks or rechecks.
- **History** retains every attempt, shows each order's latest status, supports filtering and permits multi-order rechecks.
- **Completion** separates document verification from Excel-save status.

## Phase 2 Project foundation

Phase 2 Project is integrated into the existing desktop navigation. It provides Project-only workbook eligibility, recursive document inventory, DCL/DCA/BOM/drawing evidence extraction, dependency and serial rules, live Smartsheet schema validation, preview and explicit confirmation, allowlisted create/update operations, readback verification, checkpoints and audit records. Initial inventory hashes are deferred until selected parsing to avoid network stalls.

The configured production sheet ID is `w9PHJHJXQCppp3xfXWQcGxhqXP43MJXcfJFp3q41`. The API token must never be placed in source, `.env`, logs, or SQLite. Secure credential setup will be documented when the read-only API client is enabled.

To store a token without exposing it in chat, source code, or PowerShell history, run this from the project directory and paste the token into the hidden prompt:

```powershell
python scripts\set_smartsheet_token.py
```

The token is stored through the operating-system credential backend. Initial API access is read-only and production row changes remain disabled.

## Production access

The signed-in Windows user must be able to access:

- Workbook: `T:\Temp\DMS\123.xlsx`
- Released job files: `S:\Manufacturing\Public\2 Jobfiles Released\`

The SQLite database and diagnostic log are stored under `%LOCALAPPDATA%\DMSOrderTracker\`. Close the workbook in Excel before a verification write. The application uses a same-directory temporary file, checks for concurrent changes and replaces the workbook only after a successful save.

## Test

```powershell
python -m pytest
```

Tests use temporary workbooks, document folders and databases; they do not modify the production workbook.

## Build for Windows

Run:

```bat
build.bat
```

The onedir executable is created at `dist\DMS Order Tracker\DMS Order Tracker.exe`. The build includes the Qualitrol logo, Q application icon, QSS and Qt runtime. It hides the Python console.

## Troubleshooting

- **Workbook unavailable:** confirm the T: drive is mapped and the workbook exists.
- **Workbook locked:** close Excel or ask the person using the workbook to close it, then retry.
- **Document-root error:** confirm the S: drive is connected and the released-job directory is accessible.
- **Missing documents:** confirm files are directly inside the required folder; nested files are intentionally ignored.
- **Unknown or IP order:** correct the entry; the application never creates rows or modifies IP orders.

## Known limitations

Scanned/image-only PDFs still require manual review because OCR is not enabled. Network-drive performance depends on the mapped S: and T: drives. Login and audit histories are local to each laptop and are not synchronised between installations.

