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

   Sender side can import TeleprompterClient from guionar_client (no Qt).

All socket input is validated, size-capped, and rate-limited; malformed
or hostile input is dropped and can never crash the overlay.
"""

import errno
import json
import os
import socket
import stat
import sys
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal
from guionar_client import SOCKET_PATH, TeleprompterClient, default_socket_path

# Hardening limits
MAX_LINE_BYTES = 64 * 1024    # a single JSON message may not exceed this
MAX_BUFFER_BYTES = 256 * 1024  # unterminated garbage gets dropped past this
MAX_TEXT_CHARS = 2000          # payload text is truncated to this
MAX_CLIENT_CONNECTIONS = 8     # pipeline + local controls, without thread growth
MAX_PARTIAL_MSGS_PER_SEC = 200  # partial/unknown traffic is lossy
MAX_FINAL_TEXT_MSGS_PER_SEC = 200  # preserves the former final-text ceiling
MAX_CONTROL_MSGS_PER_SEC = 32  # VAD/clear/toggle reserve, still bounded
SHUTDOWN_TIMEOUT = 2.0


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
        self._dispatch_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._rate_state = {}
        self._resources_lock = threading.Lock()
        self._server = None
        self._connections = set()
        self._workers = set()
        self._ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._listening = False
        self._startup_error = None
        self._owned_endpoint = None
        self._thread = threading.Thread(
            target=self._serve, name="guionar-listener", daemon=True)

    def start(self) -> bool:
        """Inicia el listener y devuelve si el socket quedó disponible."""
        if self._stop.is_set():
            return False
        if self._thread.ident is not None:
            return self._listening
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            self._startup_error = TimeoutError("socket startup timed out")
            print("[bridge] socket startup timed out; overlay runs standalone",
                  file=sys.stderr)
            return False
        if not self._listening:
            self._thread.join(timeout=0.2)
        return self._listening

    def stop(self) -> bool:
        """Cierra listener/clientes y espera workers hasta un límite acotado.

        La publicación de ``_stop`` bajo ``_dispatch_lock`` es la frontera:
        ningún mensaje nuevo puede emitirse después de ese punto.
        """
        with self._dispatch_lock:
            self._stop.set()

        limite = time.monotonic() + SHUTDOWN_TIMEOUT
        with self._resources_lock:
            servidor = self._server
            conexiones = tuple(self._connections)

        if servidor is not None:
            self._cerrar_socket(servidor)
        for conexion in conexiones:
            self._cerrar_socket(conexion)

        actual = threading.current_thread()
        if (self._thread.ident is not None and self._thread.is_alive()
                and actual is not self._thread):
            self._thread.join(timeout=max(0.0, limite - time.monotonic()))

        # Una vez terminado el listener ya no puede registrar más workers.
        with self._resources_lock:
            conexiones = tuple(self._connections)
            workers = tuple(self._workers)
        for conexion in conexiones:
            self._cerrar_socket(conexion)
        for worker in workers:
            if worker is not actual and worker.is_alive():
                worker.join(timeout=max(0.0, limite - time.monotonic()))

        with self._resources_lock:
            completo = (not self._thread.is_alive()
                        and not self._connections and not self._workers)
        if completo:
            self._shutdown_complete.set()
        return completo

    @property
    def active_connection_count(self) -> int:
        with self._resources_lock:
            return len(self._connections)

    @property
    def worker_count(self) -> int:
        with self._resources_lock:
            return len(self._workers)

    @staticmethod
    def _cerrar_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def _identidad(st):
        return st.st_dev, st.st_ino

    def _preparar_path(self):
        """Rechaza entradas ajenas y recupera solo sockets sin listener."""
        try:
            inicial = os.lstat(self.path)
        except FileNotFoundError:
            return

        if not stat.S_ISSOCK(inicial.st_mode):
            raise OSError(errno.EADDRINUSE,
                          "path exists and is not a Unix socket", self.path)

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(self.path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return  # desapareció entre lstat() y connect()
            if e.errno != errno.ECONNREFUSED:
                raise OSError(
                    errno.EADDRINUSE,
                    "existing Unix socket cannot be safely reclaimed",
                    self.path,
                ) from e
        else:
            raise OSError(errno.EADDRINUSE,
                          "an active Unix socket already uses this path",
                          self.path)
        finally:
            probe.close()

        # ECONNREFUSED aporta evidencia de socket stale. Revalidamos tipo e
        # identidad inmediatamente antes de quitarlo para no borrar un path
        # distinto observado durante el probe.
        try:
            actual = os.lstat(self.path)
        except FileNotFoundError:
            return
        if (not stat.S_ISSOCK(actual.st_mode)
                or self._identidad(actual) != self._identidad(inicial)):
            raise OSError(errno.EADDRINUSE,
                          "socket path changed while checking it", self.path)
        os.unlink(self.path)

    def _es_endpoint_propio(self) -> bool:
        if self._owned_endpoint is None:
            return False
        try:
            actual = os.lstat(self.path)
        except OSError:
            return False
        return (stat.S_ISSOCK(actual.st_mode)
                and self._identidad(actual) == self._owned_endpoint)

    def _limpiar_endpoint_propio(self):
        if not self._es_endpoint_propio():
            return
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _serve(self):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._preparar_path()
            srv.bind(self.path)
            self._owned_endpoint = self._identidad(os.lstat(self.path))
            os.chmod(self.path, 0o600)
            # Backlog > 1: un atajo de teclado (toggle) debe poder conectar
            # aunque el pipeline (ParlAR) ya tenga su conexión abierta.
            srv.listen(MAX_CLIENT_CONNECTIONS)
            srv.settimeout(0.2)
        except OSError as e:
            self._startup_error = e
            srv.close()
            self._limpiar_endpoint_propio()
            # The overlay must survive without the socket (degraded mode).
            print(f"[bridge] socket unavailable ({e}); overlay runs standalone",
                  file=sys.stderr)
            self._ready.set()
            return
        with self._resources_lock:
            self._server = srv
        self._listening = True
        self._ready.set()
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with self._resources_lock:
                    rechazado = (self._stop.is_set()
                                 or len(self._connections)
                                 >= MAX_CLIENT_CONNECTIONS)
                    if not rechazado:
                        worker = threading.Thread(
                            target=self._handle_connection,
                            args=(conn,),
                            name="guionar-client",
                            daemon=True,
                        )
                        self._connections.add(conn)
                        self._workers.add(worker)
                        worker.start()
                if rechazado:
                    self._cerrar_socket(conn)
        finally:
            self._cerrar_socket(srv)
            with self._resources_lock:
                if self._server is srv:
                    self._server = None
            self._limpiar_endpoint_propio()
            self._listening = False

    def _handle_connection(self, conn: socket.socket):
        try:
            self._read_connection(conn)
        except Exception:
            pass  # a broken client never kills the bridge
        finally:
            self._cerrar_socket(conn)
            actual = threading.current_thread()
            with self._resources_lock:
                self._connections.discard(conn)
                self._workers.discard(actual)

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
                if self._stop.is_set():
                    return
                if len(line) <= MAX_LINE_BYTES:
                    self._handle(line)

    def _rate_ok(self, clase: str, limite: int) -> bool:
        """Cuota global por clase, compartida por todos los clientes."""
        with self._rate_lock:
            now = time.monotonic()
            ventana, cantidad = self._rate_state.get(clase, (now, 0))
            if now - ventana >= 1.0:
                ventana, cantidad = now, 0
            cantidad += 1
            self._rate_state[clase] = ventana, cantidad
            return cantidad <= limite

    def _handle(self, raw: bytes):
        try:
            msg = json.loads(raw.decode("utf-8"))
            if not isinstance(msg, dict):
                return
            kind = msg.get("type")
            data = msg.get("data")

            if kind == "text":
                clase, limite = "final", MAX_FINAL_TEXT_MSGS_PER_SEC
            elif kind in {"vad", "clear", "toggle"}:
                clase, limite = "control", MAX_CONTROL_MSGS_PER_SEC
            else:
                # partial y desconocidos comparten la cuota lossy; un tipo
                # desconocido nunca obtiene la reserva de controles.
                clase, limite = "lossy", MAX_PARTIAL_MSGS_PER_SEC

            with self._dispatch_lock:
                if self._stop.is_set() or not self._rate_ok(clase, limite):
                    return
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


if __name__ == "__main__":
    print("Run the overlay with:  python guionar.py --socket")
