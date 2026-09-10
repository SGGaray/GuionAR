#!/usr/bin/env python3
"""Cliente stdlib para enviar eventos JSONL a una instancia de GuionAR."""

import json
import os
import socket


def default_socket_path() -> str:
    """Usa el runtime dir del usuario o un fallback por UID en /tmp."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, "guionar.sock")
    return f"/tmp/guionar-{os.getuid()}.sock"


SOCKET_PATH = default_socket_path()


class TeleprompterClient:
    """Emisor no bloqueante y best effort, sin dependencias de Qt."""

    def __init__(self, path: str = SOCKET_PATH):
        self.path = path
        self._sock = None

    def _ensure(self) -> bool:
        if self._sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            sock.connect(self.path)
            self._sock = sock
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

    def send_toggle(self):
        self._send({"type": "toggle"})
