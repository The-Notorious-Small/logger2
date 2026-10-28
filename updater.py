"""
updater.py - self-updater for PyInstaller --onefile executables (stdlib only).

Put this file next to your main.py, then:

    from updater import Updater
    Updater("1.1.0", "https://example.com/myapp/latest.json").run()

Release helper (run from source, not the exe) to make the manifest:

    python updater.py dist/myapp.exe --version 1.2.0 \
        --url https://example.com/myapp/myapp-1.2.0.exe -o latest.json
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
    """'v1.10.2' -> (1, 10, 2). Non-numeric parts are ignored."""
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


class Updater:
    """
    Usage:
        updater = Updater(current_version="1.1.0",
                          manifest_url="https://example.com/myapp/latest.json")
        updater.run()   # check, download, swap, relaunch (no-op when not frozen)

    Manifest format (JSON):
        {"version": "1.2.0", "url": "https://.../myapp.exe",
         "sha256": "<hex>", "notes": "optional release notes"}
    """

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

    # ---------- network ----------
    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def check(self) -> Optional[UpdateInfo]:
        """Return UpdateInfo if a newer version is available, else None."""
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
        """Download and verify the new exe. Returns the temp file path."""
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
        if os.name != "nt":
            os.chmod(tmp, 0o755)
        return tmp

    # ---------- install ----------
    def install(self, downloaded_path: str) -> None:
        """Swap the running exe for the downloaded one (rename trick)."""
        old = self.exe_path + ".old"
        try:
            _silent_remove(old)
            os.rename(self.exe_path, old)
            try:
                os.rename(downloaded_path, self.exe_path)
            except OSError:
                os.rename(old, self.exe_path)  # roll back
                raise
        except OSError as e:
            _silent_remove(downloaded_path)
            raise UpdateError(
                f"Could not replace {self.exe_path} (no write permission?): {e}"
            ) from e

    def restart(self) -> None:
        """Launch the (new) exe as a fresh process and exit this one."""
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen([self.exe_path, *sys.argv[1:]], env=env, close_fds=True)
        sys.exit(0)

    def cleanup(self) -> None:
        """Remove the '.old' exe left behind by a previous update."""
        _silent_remove(self.exe_path + ".old")

    # ---------- all-in-one ----------
    def run(
        self,
        ask: Optional[Callable[[UpdateInfo], bool]] = None,
        progress: Optional[Callable[[int, Optional[int]], None]] = None,
        restart: bool = True,
        raise_errors: bool = False,
    ) -> bool:
        """
        Check -> (ask) -> download -> install -> restart.
        Returns True if an update was installed (only reached when restart=False).
        Does nothing when running from source rather than a frozen build.
        """
        if not is_frozen():
            return False
        self.cleanup()
        try:
            info = self.check()
            if info is None or (ask and not ask(info)):
                return False
            path = self.download(info, progress)
            self.install(path)
        except UpdateError as e:
            if raise_errors:
                raise
            print(f"[updater] {e}", file=sys.stderr)
            return False
        if restart:
            self.restart()
        return True


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------- release helper: python updater.py <exe> --version X --url Y ----------
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
