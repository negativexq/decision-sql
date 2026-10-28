"""On-demand installation of allowlisted connector drivers.

Installing a package runs its code on the server, so this is guarded three
ways: it is off unless DECISION_SQL_ALLOW_DRIVER_INSTALL is set, it only passes
requirement specs declared in ``CONNECTOR_SPECS`` to pip, and one install runs
at a time. In production it belongs behind administrator authentication.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import threading

from pydantic import BaseModel

from app.metadata.connectors import connector_spec, driver_installed

_LOCK = threading.Lock()
_TIMEOUT_S = 600


class DriverInstallResult(BaseModel):
    ok: bool
    message: str
    packages: list[str]
    log_tail: str = ""


def install_driver(kind: str) -> DriverInstallResult:
    spec = connector_spec(kind)
    if spec is None or not spec.packages:
        return DriverInstallResult(
            ok=False, message="No driver is defined for this connector.", packages=[]
        )
    packages = list(spec.packages)
    if driver_installed(spec):
        return DriverInstallResult(
            ok=True, message=f"{spec.label} driver is already installed.", packages=packages
        )
    if not _LOCK.acquire(blocking=False):
        return DriverInstallResult(
            ok=False,
            message="Another driver is being installed. Try again shortly.",
            packages=packages,
        )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                *packages,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DriverInstallResult(
            ok=False, message="Driver installation timed out.", packages=packages
        )
    finally:
        _LOCK.release()
    log_tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-12:])
    importlib.invalidate_caches()
    if completed.returncode != 0 or not driver_installed(spec):
        return DriverInstallResult(
            ok=False,
            message=f"pip could not install the {spec.label} driver.",
            packages=packages,
            log_tail=log_tail,
        )
    return DriverInstallResult(
        ok=True,
        message=f"{spec.label} driver installed. You can connect now.",
        packages=packages,
        log_tail=log_tail,
    )
