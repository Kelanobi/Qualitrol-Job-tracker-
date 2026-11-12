# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import atexit
import os
import shutil
import tempfile

import keyring

project = Path(SPECPATH)
datas = [
    (str(project / "assets"), "assets"),
    (str(project / "app" / "ui" / "styles.qss"), "app/ui"),
]

# The company requested a zero-setup portable build. Read the existing token
# from Windows Credential Manager at build time without printing it, stage it
# only for PyInstaller, and remove the staging directory when packaging exits.
smartsheet_token = (keyring.get_password("DMSOrderTracker", "SmartsheetApiToken") or "").strip()
if not smartsheet_token:
    raise RuntimeError("No Smartsheet token is available in Windows Credential Manager for this build.")
secure_stage = Path(tempfile.mkdtemp(prefix="dms-token-stage-"))
secure_token_file = secure_stage / "smartsheet.token"
secure_token_file.write_text(smartsheet_token, encoding="utf-8")
atexit.register(lambda: shutil.rmtree(secure_stage, ignore_errors=True))
datas.append((str(secure_token_file), "_secure"))

application_password_hash = os.getenv("DMS_APPLICATION_PASSWORD_HASH", "").strip()
if not application_password_hash:
    raise RuntimeError("Set DMS_APPLICATION_PASSWORD_HASH before building the standalone application.")
secure_hash_file = secure_stage / "application_password.hash"
secure_hash_file.write_text(application_password_hash, encoding="utf-8")
datas.append((str(secure_hash_file), "_secure"))

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="DMS Order Tracker Standalone",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, icon=str(project / "assets" / "app_icon.ico"),
)
