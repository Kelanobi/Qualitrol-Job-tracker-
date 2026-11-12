from __future__ import annotations

import logging
import subprocess
import sys
import winreg


TASK_NAME = "DMS Order Tracker Monthly Report"
STARTUP_VALUE_NAME = "DMSOrderTrackerMonthlyReportCatchUp"


def install_monthly_task() -> None:
    """Register a silent noon check; the report service decides when one is due."""
    if not getattr(sys, "frozen", False):
        return
    executable = sys.executable
    command = f'"{executable}" --monthly-report'
    try:
        subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
             "/SC", "DAILY", "/ST", "12:00", "/F", "/RL", "LIMITED"],
            check=False, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # ONLOGON scheduled tasks require administrator rights on many company
        # laptops. The current-user Run key provides the same silent login
        # catch-up without elevation.
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError:
        logging.getLogger(__name__).exception("Could not register monthly report task")
