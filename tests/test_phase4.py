"""Regresiones de remediación Fase 4.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_phase4.py
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

import bridge as bridge_module
from bridge import SocketBridge, TeleprompterClient as CompatibleClient
from guionar import TeleprompterOverlay

try:
    import guionar_client
except ImportError:
    guionar_client = None


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _drenar(segundos=0.2):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        _app.processEvents()
        time.sleep(0.01)


def _esperar(predicado, timeout=1.5):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if predicado():
            return True
        _app.processEvents()
        time.sleep(0.01)
    return predicado()


def _cerrar_overlay(overlay):
    overlay.close()
    _app.processEvents()


def _cerrar_cliente(cliente):
    sock = getattr(cliente, "_sock", None)
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
        cliente._sock = None


def _habilitar_recuperacion(overlay, disponible):
    setter = getattr(overlay, "set_ghost_recovery_available", None)
    if setter is None:
        return False
    setter(disponible)
    return True


class RepaintProbe(TeleprompterOverlay):
    def __init__(self):
        self.update_calls = 0
        super().__init__()

    def update(self, *args):
        self.update_calls += 1
        return super().update(*args)


class RecordingBridge(SocketBridge):
    def __init__(self, path):
        super().__init__(path=str(path))
        self.records = []
        self.records_lock = threading.Lock()

    def _record(self, kind, data=None):
        with self.records_lock:
            self.records.append((kind, data))

    def push_text(self, text):
        self._record("text", text)

    def push_partial(self, text):
        self._record("partial", text)

    def push_vad(self, speaking):
        self._record("vad", speaking)

    def push_clear(self):
        self._record("clear")

    def push_toggle(self):
        self._record("toggle")


def test_ghost_requiere_recuperacion_externa():
    standalone = TeleprompterOverlay()
    try:
        standalone.show()
        _app.processEvents()
        check("Ghost expone capacidad de recuperación explícita",
              hasattr(standalone, "ghost_recovery_available"))
        standalone.toggle_visible()
        check("standalone rechaza ocultamiento irrecuperable",
              standalone.hidden is False and standalone.isVisible())
    finally:
        _cerrar_overlay(standalone)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "recovery.sock"
        recuperable = TeleprompterOverlay()
        bridge = SocketBridge(recuperable, path=str(path))
        cliente = None
        try:
            inicio = bridge.start()
            cableado = _habilitar_recuperacion(recuperable, inicio)
            recuperable.show()
            _app.processEvents()
            recuperable.toggle_visible()
            check("socket iniciado habilita Ghost real",
                  inicio and cableado and recuperable.hidden
                  and not recuperable.isVisible())

            cliente = CompatibleClient(str(path))
            cliente.send_toggle()
            restaurado = _esperar(
                lambda: not recuperable.hidden and recuperable.isVisible())
            check("toggle externo restaura ventana oculta", restaurado)

            recuperable.toggle_visible()
            recuperable.toggle_visible()
            check("ciclos Ghost repetidos conservan estado lógico y visual",
                  not recuperable.hidden and recuperable.isVisible())
        finally:
            if cliente is not None:
                _cerrar_cliente(cliente)
            bridge.stop()
            _cerrar_overlay(recuperable)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ocupado"
        path.write_text("no borrar", encoding="utf-8")
        degradado = TeleprompterOverlay()
        bridge = SocketBridge(degradado, path=str(path))
        try:
            inicio = bridge.start()
            cableado = _habilitar_recuperacion(degradado, inicio)
            degradado.show()
            degradado.toggle_visible()
            check("startup rechazado no habilita Ghost",
                  not inicio and cableado
                  and not degradado.hidden and degradado.isVisible())
            check("conflicto de socket permanece intacto",
                  path.read_text(encoding="utf-8") == "no borrar")
        finally:
            bridge.stop()
            _cerrar_overlay(degradado)


def test_vad_invalida_live_e_idle_sin_reactivar_pausa():
    overlay = RepaintProbe()
    try:
        overlay.update_calls = 0
        overlay.set_speaking(True)
        check("VAD true invalida la UI",
              overlay.speaking is True and overlay.update_calls > 0)

        overlay._timer.stop()
        overlay.update_calls = 0
        overlay.set_speaking(False)
        check("VAD false invalida LIVE a IDLE con timer detenido",
              overlay.speaking is False and overlay.update_calls > 0
              and not overlay._timer.isActive())

        overlay.paused = True
        overlay._timer.stop()
        overlay.set_speaking(True)
        check("VAD no reinicia scroll durante pausa manual",
              overlay.speaking is True and not overlay._timer.isActive())

        overlay.set_speaking(True)
        check("VAD repetido conserva estado sin timer persistente",
              overlay.speaking is True and not overlay._timer.isActive())

        _habilitar_recuperacion(overlay, True)
        overlay.show()
        overlay.toggle_visible()
        overlay.set_speaking(False)
        check("VAD y Ghost mantienen estados independientes",
              overlay.hidden and not overlay.isVisible()
              and overlay.speaking is False)
    finally:
        _cerrar_overlay(overlay)


def test_cliente_standalone_sin_qt_y_compatibilidad():
    codigo = (
        "import sys; sys.path.insert(0, '.'); import guionar_client; "
        "assert not any(n == 'PyQt6' or n.startswith('PyQt6.') "
        "for n in sys.modules); print(guionar_client.TeleprompterClient.__module__)"
    )
    resultado = subprocess.run(
        [sys.executable, "-S", "-c", codigo], cwd=ROOT,
        capture_output=True, text=True, timeout=4.0)
    check("cliente standalone importa con python -S sin Qt",
          resultado.returncode == 0 and resultado.stdout.strip() == "guionar_client",
          resultado.stderr.strip())

    disponible = guionar_client is not None
    check("módulo guionar_client existe", disponible)
    if not disponible:
        return

    check("bridge conserva re-export compatible",
          bridge_module.TeleprompterClient is guionar_client.TeleprompterClient)

    emisor, receptor = socket.socketpair()
    cliente = guionar_client.TeleprompterClient("sin-uso")
    cliente._sock = emisor
    try:
        cliente.send_text("final")
        cliente.send_partial("parcial")
        cliente.send_vad(True)
        cliente.send_clear()
        cliente.send_toggle()
        receptor.settimeout(1.0)
        recibido = b""
        while recibido.count(b"\n") < 5:
            recibido += receptor.recv(4096)
        mensajes = [json.loads(linea) for linea in recibido.splitlines()]
        check("cliente conserva JSONL y tipos de protocolo", mensajes == [
            {"type": "text", "data": "final"},
            {"type": "partial", "data": "parcial"},
            {"type": "vad", "data": True},
            {"type": "clear"},
            {"type": "toggle"},
        ], repr(mensajes))
    finally:
        emisor.close()
        receptor.close()

    ausente = guionar_client.TeleprompterClient(
        f"/tmp/guionar-phase4-ausente-{os.getpid()}.sock")
    try:
        ausente.send_text("descartado")
        check("cliente standalone falla de forma best effort", True)
    except Exception as exc:
        check("cliente standalone falla de forma best effort", False, repr(exc))


def test_cliente_standalone_entrega_al_socketbridge():
    if guionar_client is None:
        check("cliente standalone llega a SocketBridge", False,
              "guionar_client ausente")
        return
    with tempfile.TemporaryDirectory() as tmp:
        bridge = RecordingBridge(Path(tmp) / "cliente.sock")
        cliente = None
        try:
            iniciado = bridge.start()
            cliente = guionar_client.TeleprompterClient(bridge.path)
            cliente.send_partial("hipótesis")
            cliente.send_text("final")
            cliente.send_vad(False)
            cliente.send_clear()
            cliente.send_toggle()
            esperado = [
                ("partial", "hipótesis"), ("text", "final"),
                ("vad", False), ("clear", None), ("toggle", None),
            ]
            entregado = _esperar(lambda: bridge.records == esperado)
            check("cliente standalone llega a SocketBridge",
                  iniciado and entregado, repr(bridge.records))
        finally:
            if cliente is not None:
                _cerrar_cliente(cliente)
            bridge.stop()


def test_final_vacio_limpia_partial_sin_contenido_nuevo():
    overlay = TeleprompterOverlay()
    try:
        overlay.set_partial("normal pendiente")
        overlay.append_text("confirmado")
        check("partial más final normal conserva semántica",
              overlay.partial_text == "" and overlay.current_line == "confirmado")

        estado = (tuple(overlay.lines), overlay.current_line,
                  overlay.scroll_offset, overlay.scroll_target)
        overlay.set_partial("vacío pendiente")
        overlay.append_text("")
        check("final vacío termina hipótesis",
              overlay.partial_text == "")
        check("final vacío no agrega ni resetea contenido",
              estado == (tuple(overlay.lines), overlay.current_line,
                         overlay.scroll_offset, overlay.scroll_target))

        overlay.set_partial("espacios pendientes")
        overlay.append_text("   \t\n")
        check("final whitespace termina hipótesis sin línea blanca",
              overlay.partial_text == ""
              and estado == (tuple(overlay.lines), overlay.current_line,
                             overlay.scroll_offset, overlay.scroll_target))

        overlay.append_text("")
        check("final vacío sin partial es no-op", overlay.partial_text == "")
        overlay.set_partial("hipótesis nueva")
        check("partial posterior empieza limpio",
              overlay.partial_text == "hipótesis nueva")
        overlay.append_text("siguiente final")
        check("final válido posterior se procesa normalmente",
              overlay.partial_text == ""
              and "siguiente final" in overlay.current_line)
    finally:
        _cerrar_overlay(overlay)


def test_final_vacio_preserva_cursor_y_viewport_script():
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False) as archivo:
        archivo.write("Uno dos tres. Cuatro cinco seis. Siete ocho nueve.")
        ruta = archivo.name
    overlay = TeleprompterOverlay({"width": 360})
    try:
        overlay.resize(360, overlay.height())
        overlay.cargar_guion(ruta)
        overlay.append_text("uno dos tres")
        overlay._scroll_a_cursor(inmediato=True)
        overlay.set_partial("cuatro pendiente")
        cursor = overlay.guion.cursor
        viewport = (overlay.scroll_offset, overlay.scroll_target)
        overlay.append_text("   ")
        check("final vacío Script limpia partial sin avanzar cursor",
              overlay.partial_text == "" and overlay.guion.cursor == cursor)
        check("final vacío Script conserva viewport alineado",
              (overlay.scroll_offset, overlay.scroll_target) == viewport)
    finally:
        os.unlink(ruta)
        _cerrar_overlay(overlay)


def test_final_vacio_por_socket():
    with tempfile.TemporaryDirectory() as tmp:
        overlay = TeleprompterOverlay()
        bridge = SocketBridge(overlay, path=str(Path(tmp) / "empty.sock"))
        cliente = None
        try:
            iniciado = bridge.start()
            cliente = CompatibleClient(bridge.path)
            cliente.send_partial("socket pendiente")
            check("partial por socket llega",
                  iniciado and _esperar(
                      lambda: overlay.partial_text == "socket pendiente"))
            cliente.send_text("   ")
            check("final vacío por socket limpia partial",
                  _esperar(lambda: overlay.partial_text == ""))
            check("final vacío por socket no agrega contenido",
                  not overlay.lines and overlay.current_line == "")
        finally:
            if cliente is not None:
                _cerrar_cliente(cliente)
            bridge.stop()
            _cerrar_overlay(overlay)


def test_ci_ejecuta_todas_las_suites():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8")
    suites = [
        "tests/test_guionar.py", "tests/test_guion.py",
        "tests/test_phase1.py", "tests/test_phase2.py",
        "tests/test_phase3.py", "tests/test_phase4.py",
    ]
    posiciones = [workflow.find(suite) for suite in suites]
    check("workflow nombra las seis suites", all(pos >= 0 for pos in posiciones),
          repr(dict(zip(suites, posiciones))))
    check("workflow configura Qt offscreen", "QT_QPA_PLATFORM: offscreen" in workflow)


def main():
    test_ghost_requiere_recuperacion_externa()
    test_vad_invalida_live_e_idle_sin_reactivar_pausa()
    test_cliente_standalone_sin_qt_y_compatibilidad()
    test_cliente_standalone_entrega_al_socketbridge()
    test_final_vacio_limpia_partial_sin_contenido_nuevo()
    test_final_vacio_preserva_cursor_y_viewport_script()
    test_final_vacio_por_socket()
    test_ci_ejecuta_todas_las_suites()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de Fase 4 pasaron.")


if __name__ == "__main__":
    main()
