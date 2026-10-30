"""
updater.py - self-updater for PyInstaller --onefile executables (stdlib only).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional


class UpdateError(Exception):
    """Raised when an update can't be checked, downloaded or installed."""


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str
    notes: str = ""


def parse_version(v: str) -> tuple:
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


class Updater:
    def __init__(
        self,
        current_version: str,
        manifest_url: str,
        timeout: float = 10,
        exe_path: Optional[str] = None,
        user_agent: str = "pyi-updater",
    ):
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.timeout = timeout
        self.user_agent = user_agent
        self.exe_path = os.path.abspath(exe_path or sys.executable)

    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def check(self) -> Optional[UpdateInfo]:
        try:
            with self._open(self.manifest_url) as r:
                data = json.load(r)
            info = UpdateInfo(
                version=data["version"],
                url=data["url"],
                sha256=data["sha256"],
                notes=data.get("notes", ""),
            )
        except Exception as e:
            raise UpdateError(f"Could not read update manifest: {e}") from e

        if parse_version(info.version) > parse_version(self.current_version):
            return info
        return None

    def download(
        self,
        info: UpdateInfo,
        progress: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> str:
        exe_dir = os.path.dirname(self.exe_path)
        fd, tmp = tempfile.mkstemp(dir=exe_dir, suffix=".download")
        os.close(fd)
        h = hashlib.sha256()
        try:
            with self._open(info.url) as r, open(tmp, "wb") as f:
                total = r.headers.get("Content-Length")
                total = int(total) if total else None
                done = 0
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        except Exception as e:
            _silent_remove(tmp)
            raise UpdateError(f"Download failed: {e}") from e

        if h.hexdigest().lower() != info.sha256.lower():
            _silent_remove(tmp)
            raise UpdateError("Checksum mismatch - refusing to install update")
        return tmp

    def install_and_restart(self, downloaded_path: str) -> None:
        """
        Spawns a batch script on Windows to handle replacing the locked executable
        after this process exits.
        """
        if os.name == "nt":
            bat_path = os.path.join(tempfile.gettempdir(), "update_app.bat")
            pid = os.getpid()
            
            # Create a batch script that waits for the process to die, replaces the exe, and restarts it
            bat_content = f"""@echo off
echo Waiting for process {pid} to exit...
:waitloop
tasklist /fi "pid eq {pid}" 2>NUL | find /i "{pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto waitloop
)

echo Replacing executable...
move /y "{downloaded_path}" "{self.exe_path}"
if errorlevel 1 (
    echo Update failed! Restoring or exiting...
    exit /b 1
)

echo Launching updated application...
start "" "{self.exe_path}"
del "%~f0"
"""
            with open(bat_path, "w") as bfile:
                bfile.write(bat_content)

            # Launch the batch script detached
            subprocess.Popen([bat_path], shell=True, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
            sys.exit(0)
        else:
            # Fallback/Unix logic
            old = self.exe_path + ".old"
            try:
                _silent_remove(old)
                os.rename(self.exe_path, old)
                os.rename(downloaded_path, self.exe_path)
                os.chmod(self.exe_path, 0o755)
            except OSError as e:
                _silent_remove(downloaded_path)
                raise UpdateError(f"Could not replace executable: {e}") from e
            
            env = os.environ.copy()
            subprocess.Popen([self.exe_path, *sys.argv[1:]], env=env, close_fds=True)
            sys.exit(0)

    def cleanup(self) -> None:
        _silent_remove(self.exe_path + ".old")

    def run(
        self,
        ask: Optional[Callable[[UpdateInfo], bool]] = None,
        progress: Optional[Callable[[int, Optional[int]], None]] = None,
        raise_errors: bool = False,
    ) -> bool:
        if not is_frozen():
            return False
        self.cleanup()
        try:
            info = self.check()
            if info is None or (ask and not ask(info)):
                return False
            path = self.download(info, progress)
            self.install_and_restart(path)
        except UpdateError as e:
            if raise_errors:
                raise
            print(f"[updater] {e}", file=sys.stderr)
            return False
        return True


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def make_manifest(exe: str, version: str, url: str, notes: str = "") -> dict:
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return {"version": version, "url": url, "sha256": h.hexdigest(), "notes": notes}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Generate an update manifest.")
    p.add_argument("exe", help="path to the built executable")
    p.add_argument("--version", required=True)
    p.add_argument("--url", required=True, help="public download URL of the exe")
    p.add_argument("--notes", default="")
    p.add_argument("-o", "--output", help="write to file instead of stdout")
    a = p.parse_args()
    text = json.dumps(make_manifest(a.exe, a.version, a.url, a.notes), indent=2)
    if a.output:
        with open(a.output, "w") as f:
            f.write(text + "\n")
    else:
        print(text)