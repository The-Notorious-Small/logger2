"""
updater.py - self-updater for PyInstaller --onefile executables (stdlib only).

Download behaviour:
  * Transient network failures (dropped connection, reset, timeout, short read)
    are retried with backoff, resuming from where the last attempt stopped via
    an HTTP Range request (falls back to a full restart if the server doesn't
    support ranges).
  * The partial file has a fixed name tied to the update's checksum, so if every
    attempt fails it is resumed on the next launch instead of piling up random
    temp files. Partials for other versions are cleaned up automatically.
  * Nothing the updater does is allowed to crash the host program: run()
    catches every error and returns False (unless raise_errors=True).
"""

from __future__ import annotations

import glob
import hashlib
import http.client
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("updater")

ProgressFn = Callable[[int, Optional[int]], None]


class UpdateError(Exception):
    """Raised when an update can't be checked, downloaded or installed."""


class _RetryableDownloadError(Exception):
    """A transient download failure that is worth another attempt."""


# urllib.error.URLError, socket timeouts, ConnectionResetError (WinError 10054),
# ssl.SSLError are all OSError; IncompleteRead etc. are HTTPException.
_TRANSIENT_ERRORS = (OSError, http.client.HTTPException, _RetryableDownloadError)

_CHUNK = 1 << 16


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
        timeout: float = 60.0,  # per socket operation, not for the whole download
        exe_path: Optional[str] = None,
        user_agent: str = "pyi-updater",
        retries: int = 5,
    ):
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.timeout = timeout
        self.user_agent = user_agent
        self.retries = max(1, retries)
        self.exe_path = os.path.abspath(exe_path or sys.executable)
        self._exe_stem = os.path.splitext(os.path.basename(self.exe_path))[0]

    # ------------------------------------------------------------------ check

    def _open(self, url: str, headers: Optional[dict] = None):
        h = {"User-Agent": self.user_agent}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
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

    # --------------------------------------------------------------- download

    def _download_dir(self) -> str:
        """The exe's folder if writable, otherwise the temp folder."""
        exe_dir = os.path.dirname(self.exe_path)
        try:
            fd, test_path = tempfile.mkstemp(dir=exe_dir)
            os.close(fd)
            _silent_remove(test_path)
            return exe_dir
        except OSError:
            return tempfile.gettempdir()

    def _part_path(self, info: UpdateInfo) -> str:
        tag = info.sha256.strip().lower()[:16] or "update"
        return os.path.join(self._download_dir(), f"{self._exe_stem}.{tag}.part")

    def _remove_stale_parts(self, keep: str) -> None:
        dirs = {os.path.dirname(self.exe_path), tempfile.gettempdir()}
        for d in dirs:
            for p in glob.glob(os.path.join(d, glob.escape(self._exe_stem) + ".*.part")):
                if os.path.normcase(os.path.abspath(p)) != os.path.normcase(keep):
                    _silent_remove(p)

    def download(self, info: UpdateInfo, progress: Optional[ProgressFn] = None) -> str:
        part = self._part_path(info)
        self._remove_stale_parts(keep=os.path.abspath(part))
        progress = _safe_progress(progress)

        last_err: Optional[BaseException] = None
        for attempt in range(1, self.retries + 1):
            try:
                self._fetch(info.url, part, progress)
                last_err = None
                break
            except UpdateError:
                raise  # permanent failure (e.g. 404) - retrying won't help
            except _TRANSIENT_ERRORS as e:
                last_err = e
                log.warning("[updater] download attempt %d/%d failed: %s",
                            attempt, self.retries, e)
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 30))

        if last_err is not None:
            raise UpdateError(
                f"Download failed after {self.retries} attempts: {last_err} "
                f"(partial download kept and will resume next time)"
            ) from last_err

        try:
            digest = _sha256_file(part)
        except OSError as e:
            raise UpdateError(f"Could not read downloaded file: {e}") from e
        if digest != info.sha256.strip().lower():
            _silent_remove(part)
            raise UpdateError("Checksum mismatch - refusing to install update")
        return part

    def _fetch(self, url: str, part: str, progress: Optional[ProgressFn]) -> None:
        """One download attempt. Appends to `part` if the server supports Range."""
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={have}-"} if have else None

        try:
            r = self._open(url, headers)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 416 and have:
                return  # we already have every byte; the checksum decides
            if 400 <= e.code < 500 and e.code not in (408, 429):
                raise UpdateError(f"Server returned HTTP {e.code} for {url}") from e
            raise _RetryableDownloadError(f"server returned HTTP {e.code}") from e

        with r:
            status = getattr(r, "status", 200)
            if status == 206 and have:
                start = _content_range_start(r.headers.get("Content-Range"))
                if start != have:
                    open(part, "wb").close()  # unusable resume; start over next try
                    raise _RetryableDownloadError(
                        f"server resumed at byte {start}, expected {have}")
                mode = "ab"
            elif status == 200:
                have, mode = 0, "wb"  # full body (server ignored Range, or fresh start)
            else:
                raise _RetryableDownloadError(f"unexpected HTTP status {status}")

            length = r.headers.get("Content-Length")
            total = have + int(length) if length and length.strip().isdigit() else None
            done = have
            if progress:
                progress(done, total)

            with open(part, mode) as f:
                while True:
                    # read(amt) does NOT raise when the server hangs up early -
                    # it just returns b"" - so the byte count is checked below.
                    chunk = r.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                f.flush()
                os.fsync(f.fileno())

        if total is not None and done != total:
            raise _RetryableDownloadError(
                f"connection closed early ({done:,} of {total:,} bytes)")

    # ---------------------------------------------------------------- install

    def install_and_restart(self, downloaded_path: str) -> None:
        """
        Handles replacing the executable safely across platforms.
        Uses a detached background batch script on Windows to bypass file-locking.
        """
        if os.name == "nt":
            self._install_windows(downloaded_path)
        else:
            old = self.exe_path + ".old"
            try:
                _silent_remove(old)
                os.rename(self.exe_path, old)
                try:
                    os.rename(downloaded_path, self.exe_path)
                except OSError:
                    os.rename(old, self.exe_path)
                    raise
                os.chmod(self.exe_path, 0o755)
            except OSError as e:
                _silent_remove(downloaded_path)
                raise UpdateError(f"Could not replace executable: {e}") from e

            logging.shutdown()
            env = os.environ.copy()
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen([self.exe_path, *sys.argv[1:]], env=env, close_fds=True)
            sys.exit(0)

    def _install_windows(self, downloaded_path: str) -> None:
        """
        Hand the swap to a hidden batch script, then exit.

        In a --onefile build there are two processes: the bootloader (parent)
        and Python (child, os.getpid()). The parent keeps the .exe locked for a
        while after the child exits (it is deleting the _MEI temp folder), so
        the script keeps retrying the move instead of trying once and giving up.
        Everything the script does is appended to the updater log.
        """
        pid = os.getpid()
        log_path = self.log_path
        fd, bat_path = tempfile.mkstemp(prefix=f"{self._exe_stem}_update_", suffix=".bat")
        os.close(fd)

        bat = f"""@echo off
setlocal
set "LOG={log_path}"
set "NEW={downloaded_path}"
set "EXE={self.exe_path}"
>>"%LOG%" echo [%date% %time%] batch: waiting for process {pid} to exit
:waitpid
tasklist /fi "pid eq {pid}" /nh 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    ping -n 2 127.0.0.1 >NUL
    goto waitpid
)
set /a tries=0
:replace
move /y "%NEW%" "%EXE%" >>"%LOG%" 2>&1
if not errorlevel 1 goto launch
set /a tries+=1
if %tries% GEQ 90 goto failed
ping -n 2 127.0.0.1 >NUL
goto replace
:failed
>>"%LOG%" echo [%date% %time%] batch: FAILED to replace "%EXE%" after %tries% tries - relaunching old version
start "" "%EXE%"
goto done
:launch
>>"%LOG%" echo [%date% %time%] batch: replaced "%EXE%" after %tries% retries - launching
start "" "%EXE%"
:done
del "%~f0"
"""
        # "mbcs" = the ANSI code page cmd.exe reads .bat files in, so paths like
        # C:\\Users\\José\\... survive.
        with open(bat_path, "w", encoding="mbcs", errors="replace") as f:
            f.write(bat)

        # The relaunched exe must NOT inherit this process's PyInstaller
        # variables, or it tries to reuse the _MEI folder that is being deleted
        # and dies on startup ("Failed to load Python DLL").
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("_PYI_", "_MEIPASS"))}
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

        base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        last_err: Optional[OSError] = None
        for flags in (base | breakaway, base):  # breakaway is refused in some job objects
            try:
                # Explicit DEVNULL handles: in a --windowed build stdin/stdout are
                # invalid, and inheriting them makes Popen fail with WinError 6.
                subprocess.Popen(
                    ["cmd.exe", "/d", "/c", bat_path],
                    creationflags=flags, env=env, close_fds=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                last_err = None
                break
            except OSError as e:
                last_err = e
        if last_err is not None:
            _silent_remove(bat_path)
            raise UpdateError(f"Could not start the update script: {last_err}") from last_err

        log.info("[updater] update script started (%s); exiting so it can replace %s",
                 bat_path, self.exe_path)
        _hard_exit()

    @property
    def log_path(self) -> str:
        return os.path.join(tempfile.gettempdir(), f"{self._exe_stem}_updater.log")

    def _ensure_logging(self) -> None:
        """If the app hasn't configured logging, write the updater's own log file.
        (A --windowed build has no console, so otherwise errors vanish.)"""
        if log.handlers or logging.getLogger().handlers:
            return
        try:
            h = logging.FileHandler(self.log_path, encoding="utf-8")
        except OSError:
            return
        h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)

    def cleanup(self) -> None:
        _silent_remove(self.exe_path + ".old")
        # Leftovers from the previous version of this updater (random tmpXXXX.download names).
        for d in {os.path.dirname(self.exe_path), tempfile.gettempdir()}:
            for p in glob.glob(os.path.join(d, "tmp*.download")):
                _silent_remove(p)

    def run(
        self,
        ask: Optional[Callable[[UpdateInfo], bool]] = None,
        progress: Optional[ProgressFn] = None,
        raise_errors: bool = False,
    ) -> bool:
        if not is_frozen():
            return False
        self._ensure_logging()
        try:
            self.cleanup()
            log.info("[updater] %s v%s checking %s", self.exe_path,
                     self.current_version, self.manifest_url)
            info = self.check()
            if info is None:
                log.info("[updater] up to date")
                return False
            if ask and not ask(info):
                return False
            path = self.download(info, progress)
            log.info("[updater] downloaded and verified v%s -> %s", info.version, path)
            self.install_and_restart(path)
        except UpdateError as e:
            if raise_errors:
                raise
            log.error("[updater] %s", e)
            return False
        except Exception as e:  # never let the updater take the app down
            if raise_errors:
                raise
            log.exception("[updater] unexpected error: %s", e)
            return False
        return True


# -------------------------------------------------------------------- helpers

def _hard_exit() -> None:
    """Exit right now, from any thread.

    sys.exit() only ends the current thread if update ran in a background
    thread (the script would then wait forever), and GUI toolkits such as
    PyQt abort() on an exception escaping a slot. os._exit avoids both.
    """
    logging.shutdown()
    for s in (sys.stdout, sys.stderr):
        try:
            if s:
                s.flush()
        except Exception:
            pass
    os._exit(0)


def _safe_progress(cb: Optional[ProgressFn]) -> Optional[ProgressFn]:
    """Wrap a progress callback so an exception in it can't abort the download."""
    if cb is None:
        return None
    state = {"cb": cb}

    def wrapped(done: int, total: Optional[int]) -> None:
        fn = state["cb"]
        if fn is None:
            return
        try:
            fn(done, total)
        except Exception:
            log.exception("[updater] progress callback failed; disabling it")
            state["cb"] = None

    return wrapped


def _content_range_start(value: Optional[str]) -> Optional[int]:
    # "bytes 1000-4999/5000" -> 1000
    try:
        return int(value.split()[1].split("-")[0])
    except (AttributeError, IndexError, ValueError):
        return None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _silent_remove(path: str, attempts: int = 5) -> None:
    """Delete a file, retrying briefly: on Windows, antivirus or the indexer
    often holds a just-written file open for a moment."""
    for i in range(attempts):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if i == attempts - 1:
                log.warning("[updater] could not delete %s", path)
                return
            time.sleep(0.2 * (i + 1))


def make_manifest(exe: str, version: str, url: str, notes: str = "") -> dict:
    return {"version": version, "url": url, "sha256": _sha256_file(exe), "notes": notes}


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
