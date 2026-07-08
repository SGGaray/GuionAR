#!/usr/bin/env python3
"""
GuionAR bridge: connects a dictation pipeline (e.g. ParlAR)
to the teleprompter overlay.

Two integration modes, pick ONE:

1) IN-PROCESS (pipeline is Python and can import PyQt6):
       bridge = PipelineBridge(overlay)
       bridge.push_text("hello world")     # safe from ANY thread
       bridge.push_vad(True)               # safe from ANY thread

   Qt queued signal/slot delivery guarantees the UI thread is never
   blocked and the dictation thread never waits on rendering.

2) OUT-OF-PROCESS (recommended): run `python guionar.py --socket`, then
   have the pipeline write newline-delimited JSON to the Unix socket
   (path: $XDG_RUNTIME_DIR/guionar.sock):

       {"type": "text", "data": "hello world"}\n
       {"type": "partial", "data": "hel"}\n
       {"type": "vad", "data": true}\n
       {"type": "clear"}\n
       {"type": "toggle"}\n

   Sender side needs only the TeleprompterClient class below (no Qt).

All socket input is validated, size-capped, and rate-limited; malformed
or hostile input is dropped and can never crash the overlay.
"""

import json
import os
import socket
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal


def default_socket_path() -> str:
    """Per-user runtime dir when available (safer than world-writable /tmp)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, "guionar.sock")
    return f"/tmp/guionar-{os.getuid()}.sock"


SOCKET_PATH = default_socket_path()

# Hardening limits
MAX_LINE_BYTES = 64 * 1024    # a single JSON message may not exceed this
MAX_BUFFER_BYTES = 256 * 1024  # unterminated garbage gets dropped past this
MAX_TEXT_CHARS = 2000          # payload text is truncated to this
MAX_MSGS_PER_SEC = 200         # spam guard; excess messages are dropped


class PipelineBridge(QObject):
    """Thread-safe bridge. Call push_* from any thread; slots run on UI thread."""

    text_received = pyqtSignal(str)
    partial_received = pyqtSignal(str)
    vad_changed = pyqtSignal(bool)
    clear_requested = pyqtSignal()
    toggle_requested = pyqtSignal()

    def __init__(self, overlay=None):
        super().__init__()
        if overlay is not None:
            self.attach(overlay)

    def attach(self, overlay):
        # Cross-thread emits are automatically queued onto the UI thread.
        self.text_received.connect(overlay.append_text)
        self.partial_received.connect(overlay.set_partial)
        self.vad_changed.connect(overlay.set_speaking)
        self.clear_requested.connect(overlay.clear)
        self.toggle_requested.connect(overlay.toggle_visible)

    # -- call these from the dictation pipeline (non-blocking) -----------
    def push_text(self, text: str):
        self.text_received.emit(text)

    def push_partial(self, text: str):
        self.partial_received.emit(text)

    def push_vad(self, speaking: bool):
        self.vad_changed.emit(bool(speaking))

    def push_clear(self):
        self.clear_requested.emit()

    def push_toggle(self):
        self.toggle_requested.emit()


class SocketBridge(PipelineBridge):
    """Listens on a Unix domain socket and forwards JSON-line messages
    to the overlay. Runs in a daemon thread; never touches the UI thread
    directly (signals handle the hop)."""

    def __init__(self, overlay=None, path: str = SOCKET_PATH):
        super().__init__(overlay)
        self.path = path
        self._stop = threading.Event()
        self._rate_lock = threading.Lock()
        self._rate_window = 0.0
        self._rate_count = 0
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            # unblock accept()
            socket.socket(socket.AF_UNIX).connect(self.path)
        except OSError:
            pass

    def _serve(self):
        try:
            if os.path.lexists(self.path):
                os.unlink(self.path)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.path)
            os.chmod(self.path, 0o600)
            # Backlog > 1: un atajo de teclado (toggle) debe poder conectar
            # aunque el pipeline (ParlAR) ya tenga su conexión abierta.
            srv.listen(8)
        except OSError as e:
            # The overlay must survive without the socket (degraded mode).
            print(f"[bridge] socket unavailable ({e}); overlay runs standalone",
                  file=__import__("sys").stderr)
            return
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            # Un hilo por conexión: el pipeline mantiene la suya abierta
            # todo el tiempo, así que un segundo cliente (p. ej. un atajo
            # de teclado enviando {"type":"toggle"}) tiene que poder
            # conectar y desconectar sin esperar a que el primero se cierre.
            threading.Thread(target=self._handle_connection, args=(conn,),
                             daemon=True).start()
        try:
            srv.close()
            if os.path.lexists(self.path):
                os.unlink(self.path)
        except OSError:
            pass

    def _handle_connection(self, conn: socket.socket):
        try:
            self._read_connection(conn)
        except Exception:
            pass  # a broken client never kills the bridge
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _read_connection(self, conn: socket.socket):
        buf = b""
        while not self._stop.is_set():
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_BUFFER_BYTES:
                buf = b""  # unterminated garbage: drop and resync
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) <= MAX_LINE_BYTES:
                    self._handle(line)

    def _rate_ok(self) -> bool:
        """Cheap sliding-window spam guard (drops excess, never blocks).
        Locked: with one thread per connection, multiple clients can hit
        this concurrently."""
        with self._rate_lock:
            now = time.monotonic()
            if now - self._rate_window >= 1.0:
                self._rate_window = now
                self._rate_count = 0
            self._rate_count += 1
            return self._rate_count <= MAX_MSGS_PER_SEC

    def _handle(self, raw: bytes):
        try:
            msg = json.loads(raw.decode("utf-8"))
            if not isinstance(msg, dict) or not self._rate_ok():
                return
            kind = msg.get("type")
            data = msg.get("data")
            if kind == "text" and isinstance(data, str):
                self.push_text(data[:MAX_TEXT_CHARS])
            elif kind == "partial" and isinstance(data, str):
                self.push_partial(data[-MAX_TEXT_CHARS:])
            elif kind == "vad" and isinstance(data, (bool, int)):
                self.push_vad(bool(data))
            elif kind == "clear":
                self.push_clear()
            elif kind == "toggle":
                self.push_toggle()
            # unknown types are ignored on purpose (forward compatibility)
        except Exception:
            return  # malformed input must never crash the reader thread


# ---------------------------------------------------------------------------
# Sender helper for the pipeline side (out-of-process mode).
# Copy this class into your pipeline (ParlAR), or `from bridge import
# TeleprompterClient`. It never raises into the dictation pipeline and
# never blocks: if the overlay is closed, sends are silently dropped.
# ---------------------------------------------------------------------------
class TeleprompterClient:
    def __init__(self, path: str = SOCKET_PATH):
        self.path = path
        self._sock = None

    def _ensure(self) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            s.connect(self.path)
            self._sock = s
            return True
        except OSError:
            self._sock = None
            return False

    def _send(self, obj: dict):
        if not self._ensure():
            return
        try:
            self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        except (OSError, BlockingIOError):
            try:
                self._sock.close()
            finally:
                self._sock = None

    def send_text(self, text: str):
        self._send({"type": "text", "data": text})

    def send_partial(self, text: str):
        self._send({"type": "partial", "data": text})

    def send_vad(self, speaking: bool):
        self._send({"type": "vad", "data": bool(speaking)})

    def send_clear(self):
        self._send({"type": "clear"})


if __name__ == "__main__":
    print("Run the overlay with:  python guionar.py --socket")
