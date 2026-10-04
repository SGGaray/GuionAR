#!/usr/bin/env python3
"""Cliente stdlib para enviar eventos JSONL a una instancia de GuionAR.

Dos modos, ambos sin dependencias de Qt:

- Por defecto (histórico): best effort perezoso. Cada envío intenta
  conectar si hace falta; si GuionAR no está, el mensaje se descarta.

- ``auto_connect=True``: un único hilo mantiene la conexión. Espera sin
  consumir CPU (select bloqueante), detecta al instante que GuionAR se
  cerró, reintenta cada 1-2 s mientras no está y avisa los cambios de
  estado. Los envíos nunca intentan conectar ni bloquean: si no hay
  conexión, se descartan. Pensado para productores como ParlAR.

Con ``client_name`` el cliente se presenta al conectar con un mensaje
``hello`` (rol ``voice-producer``). GuionAR versiones anteriores lo ignoran.
"""

import json
import os
import select
import socket
import sys
import threading

PROTOCOLO = 1
REINTENTOS_S = (1.0, 1.5, 2.0)   # espera entre intentos mientras no está


def default_socket_path() -> str:
    """Usa el runtime dir del usuario o un fallback por UID en /tmp."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, "guionar.sock")
    return f"/tmp/guionar-{os.getuid()}.sock"


SOCKET_PATH = default_socket_path()


class TeleprompterClient:
    """Emisor no bloqueante y best effort, sin dependencias de Qt.

    ``on_state(conectado: bool)`` se llama desde el hilo de conexión en cada
    cambio (sólo con ``auto_connect``); la UI debe pasarlo a su propio hilo.
    """

    def __init__(self, path: str = SOCKET_PATH, *, auto_connect: bool = False,
                 client_name: str | None = None, on_state=None, log=None):
        self.path = path
        self._sock = None
        self._client_name = client_name
        self._on_state = on_state
        self._log = log if log is not None else (
            lambda texto: print(texto, file=sys.stderr))
        self._lock = threading.Lock()        # protege _sock y los envíos
        self._auto = auto_connect
        self._stop = threading.Event()
        self._hilo = None
        self._despertar_r = self._despertar_w = None
        self.connected = False
        if auto_connect:
            self.start()

    # ---------------------------------------------------------- conexión
    def _abrir(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            sock.connect(self.path)
            if self._client_name:
                hola = {"type": "hello", "client": self._client_name,
                        "role": "voice-producer", "protocol": PROTOCOLO}
                sock.sendall((json.dumps(hola) + "\n").encode("utf-8"))
            return sock
        except OSError:
            sock.close()
            return None

    def _ensure(self) -> bool:
        if self._sock is not None:
            return True
        if self._auto:
            return False   # sólo el hilo de conexión conecta
        self._sock = self._abrir()
        return self._sock is not None

    def _descartar(self, sock):
        try:
            sock.close()
        except OSError:
            pass

    def _send(self, obj: dict):
        linea = (json.dumps(obj) + "\n").encode("utf-8")
        with self._lock:
            if not self._ensure():
                return
            sock = self._sock
            try:
                sock.sendall(linea)
                return
            except OSError:   # incluye BlockingIOError: GuionAR saturado
                self._sock = None
        self._descartar(sock)
        if self._auto:
            self._despertar()   # el hilo registra la caída y reintenta

    # ---------------------------------------------------------- auto_connect
    def start(self):
        """Arranca el hilo de conexión (idempotente: como mucho uno)."""
        with self._lock:
            if self._hilo is not None or self._stop.is_set():
                return
            self._auto = True
            self._despertar_r, self._despertar_w = socket.socketpair()
            self._despertar_r.setblocking(False)
            self._despertar_w.setblocking(False)
            self._hilo = threading.Thread(target=self._mantener,
                                          name="guionar-conexion", daemon=True)
            self._hilo.start()

    def _despertar(self):
        try:
            self._despertar_w.send(b"\0")
        except (OSError, AttributeError):
            pass

    def _cambiar_estado(self, conectado: bool):
        if conectado == self.connected:
            return
        self.connected = conectado
        self._log("[guionar] conectado" if conectado else "[guionar] desconectado")
        if self._on_state is not None:
            try:
                self._on_state(conectado)
            except Exception:
                pass   # un callback roto nunca detiene la conexión

    def _mantener(self):
        intento = 0
        while not self._stop.is_set():
            sock = self._abrir()
            if sock is None:
                # GuionAR ausente: esperar sin consumir CPU y sin loguear.
                espera = REINTENTOS_S[min(intento, len(REINTENTOS_S) - 1)]
                intento += 1
                self._stop.wait(espera)
                continue
            intento = 0
            with self._lock:
                self._sock = sock
            self._cambiar_estado(True)
            self._esperar_cierre(sock)
            with self._lock:
                if self._sock is sock:
                    self._sock = None
            self._descartar(sock)
            self._cambiar_estado(False)

    def _esperar_cierre(self, sock):
        """Bloquea hasta que GuionAR cierre la conexión, un envío falle o se
        pida cerrar. GuionAR nunca escribe: legible == fin de la conexión."""
        while not self._stop.is_set():
            try:
                legibles, _, _ = select.select([sock, self._despertar_r], [], [])
            except (OSError, ValueError):
                return
            if self._despertar_r in legibles:
                try:
                    while self._despertar_r.recv(64):
                        pass
                except OSError:
                    pass
                with self._lock:
                    if self._sock is not sock:
                        return   # un envío falló y descartó el socket
            if sock in legibles:
                try:
                    if not sock.recv(4096):
                        return
                except BlockingIOError:
                    continue
                except OSError:
                    return

    def close(self, timeout: float = 2.0) -> bool:
        """Cierre limpio y acotado. Devuelve si el hilo terminó."""
        self._stop.set()
        self._despertar()
        hilo = self._hilo
        if hilo is not None and hilo is not threading.current_thread():
            hilo.join(timeout)
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            self._descartar(sock)
        for extremo in (self._despertar_r, self._despertar_w):
            if extremo is not None:
                self._descartar(extremo)
        return hilo is None or not hilo.is_alive()

    # ---------------------------------------------------------- mensajes
    def send_text(self, text: str):
        self._send({"type": "text", "data": text})

    def send_partial(self, text: str):
        self._send({"type": "partial", "data": text})

    def send_vad(self, speaking: bool):
        self._send({"type": "vad", "data": bool(speaking)})

    def send_clear(self):
        self._send({"type": "clear"})

    def send_toggle(self):
        self._send({"type": "toggle"})
