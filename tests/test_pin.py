"""Tests del pin "Mantener sobre otras ventanas" (always_on_top).

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_pin.py

Offscreen no tiene gestor de ventanas: se verifican los flags de Qt (del
widget y de la ventana nativa), el estado lógico y que la ventana no se
recree ni se mueva. El efecto físico en X11 se valida aparte.
"""

import gc
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import guionar_config
from desktop_shell import BandejaGuionAR, VentanaConfiguracion, crear_bandeja
from guionar import TeleprompterOverlay

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
_TMP = tempfile.TemporaryDirectory()
ENCIMA = Qt.WindowType.WindowStaysOnTopHint

ORACIONES = " ".join(
    f"Oración número {i} con contenido suficiente para ocupar varias líneas "
    f"en la ventana del teleprompter." for i in range(30))


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


class ConfigTemporal:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir, self.file = guionar_config.CONFIG_DIR, guionar_config.CONFIG_FILE
        guionar_config.CONFIG_DIR = Path(self.tmp.name)
        guionar_config.CONFIG_FILE = Path(self.tmp.name) / "config.json"
        return guionar_config.CONFIG_FILE

    def __exit__(self, *_):
        guionar_config.CONFIG_DIR, guionar_config.CONFIG_FILE = self.dir, self.file
        self.tmp.cleanup()


def _archivo(nombre, contenido):
    ruta = os.path.join(_TMP.name, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def _overlay(cfg=None, persistir=False):
    ov = TeleprompterOverlay({"width": 600, "height": 240, **(cfg or {})},
                             persistir=persistir)
    ov.show()
    _app.processEvents()
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()
    gc.collect()


def _fijada(ov):
    """Flag del widget y de la ventana nativa, que deben coincidir."""
    widget = bool(ov.windowFlags() & ENCIMA)
    ventana = ov.windowHandle()
    nativa = bool(ventana.flags() & ENCIMA) if ventana is not None else widget
    return widget, nativa


def _ticks(ov, n, dt=0.05):
    for _ in range(n):
        ov._last_tick = time.monotonic() - dt
        ov._tick()


# ---------------------------------------------------------------- 1-3, 15

def test_defaults_config_y_arranque():
    ov = TeleprompterOverlay()
    try:
        check("1. por defecto siempre encima", ov.cfg["always_on_top"] is True
              and _fijada(ov) == (True, True))
    finally:
        _cerrar(ov)

    with ConfigTemporal() as archivo:
        archivo.write_text(json.dumps({"bg_opacity": 0.4, "position_locked": True}),
                           encoding="utf-8")
        vieja = TeleprompterOverlay(guionar_config.cargar())
        try:
            check("2. config vieja sin la clave queda siempre encima",
                  vieja.cfg["always_on_top"] is True and _fijada(vieja) == (True, True))
        finally:
            _cerrar(vieja)
        for invalido in ("no", 0, 1, None, [], "true"):
            archivo.write_text(json.dumps({"always_on_top": invalido}), encoding="utf-8")
            ov = TeleprompterOverlay(guionar_config.cargar())
            try:
                check(f"3. valor inválido {invalido!r} vuelve a siempre encima",
                      ov.cfg["always_on_top"] is True and _fijada(ov)[0])
            finally:
                _cerrar(ov)

    apagado = TeleprompterOverlay({"always_on_top": False})
    try:
        apagado.show()
        _app.processEvents()
        check("15. arranque con la preferencia apagada: ventana normal",
              _fijada(apagado) == (False, False))
    finally:
        _cerrar(apagado)
    prendido = _overlay({"always_on_top": True})
    try:
        check("15. arranque con la preferencia prendida: siempre encima",
              _fijada(prendido) == (True, True))
    finally:
        _cerrar(prendido)


def test_arranque_real_aplica_la_config():
    for valor in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "guionar"
            config.mkdir()
            (config / "config.json").write_text(
                json.dumps({"always_on_top": valor}), encoding="utf-8")
            codigo = (
                "import sys, guionar\n"
                "from PyQt6.QtCore import Qt\n"
                "from PyQt6.QtWidgets import QApplication\n"
                "def fake_exec(self):\n"
                "    ov = [w for w in QApplication.topLevelWidgets()\n"
                "          if isinstance(w, guionar.TeleprompterOverlay)][0]\n"
                "    T = Qt.WindowType.WindowStaysOnTopHint\n"
                "    print('PIN', bool(ov.windowFlags() & T),\n"
                "          bool(ov.windowHandle().flags() & T))\n"
                "    return 0\n"
                "QApplication.exec = fake_exec\n"
                "sys.argv = ['guionar.py']\n"
                "guionar.main()\n"
            )
            env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "XDG_CONFIG_HOME": tmp}
            proc = subprocess.run([sys.executable, "-c", codigo], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=20)
            check(f"15. main() arranca con always_on_top={valor} sin interacción",
                  proc.returncode == 0 and f"PIN {valor} {valor}" in proc.stdout,
                  f"{proc.stdout[-200:]!r} {proc.stderr[-300:]!r}")


# ---------------------------------------------------------------- 4-6, 12, 13

def test_toggle_preserva_ventana_y_estado():
    ov = _overlay()
    try:
        ov.abrir_documento(_archivo("pin.txt", ORACIONES))
        ov.move(70, 80)
        ov.resize(640, 250)
        _app.processEvents()
        for _ in range(4):
            ov.saltar_oracion(1)
        ov.toggle_pause()
        _ticks(ov, 15)
        ov.speed_up()
        ov.set_opacidad(0.4)
        ov.set_alineacion("left")
        ov.set_bloqueo(True)
        ov.set_ghost_recovery_available(True)
        ventana = ov.windowHandle()
        antes = {
            "geometria": ov.geometry(), "visible": ov.isVisible(),
            "winid": int(ov.winId()), "documento": ov.documento_nombre,
            "guion": ov.guion, "cursor": ov.guion.cursor,
            "scroll": ov.scroll_offset, "pausa": ov.paused,
            "velocidad": ov.speed_pps, "opacidad": ov.cfg["bg_opacity"],
            "alineacion": ov.cfg["text_alignment"], "bloqueo": ov.cfg["position_locked"],
            "ghost": ov.hidden,
        }

        def actual():
            return {
                "geometria": ov.geometry(), "visible": ov.isVisible(),
                "winid": int(ov.winId()), "documento": ov.documento_nombre,
                "guion": ov.guion, "cursor": ov.guion.cursor,
                "scroll": ov.scroll_offset, "pausa": ov.paused,
                "velocidad": ov.speed_pps, "opacidad": ov.cfg["bg_opacity"],
                "alineacion": ov.cfg["text_alignment"],
                "bloqueo": ov.cfg["position_locked"], "ghost": ov.hidden,
            }

        ov.set_siempre_encima(False)
        _app.processEvents()
        check("5. toggle OFF quita el flag del widget y de la ventana",
              _fijada(ov) == (False, False) and ov.cfg["always_on_top"] is False)
        check("12-13. OFF conserva geometría, ventana nativa y todo el estado",
              actual() == antes, repr({k: (antes[k], v) for k, v in actual().items()
                                       if antes[k] != v}))
        ov.set_siempre_encima(True)
        _app.processEvents()
        check("4. toggle ON vuelve a poner el flag en widget y ventana",
              _fijada(ov) == (True, True) and ov.cfg["always_on_top"] is True)
        for _ in range(6):
            ov.alternar_siempre_encima()
            _app.processEvents()
        check("12-13. varios toggles: misma ventana nativa, sin moverse ni ocultarse",
              actual() == antes and ov.windowHandle() is ventana and _fijada(ov) == (True, True))

        controles = ov.ventana_controles
        controles.mostrar()
        check("6. botón refleja ON (activo, chinche derecha)",
              controles.btn_fijar.activo and controles.btn_fijar.icono == "pin"
              and controles.btn_fijar.toolTip() == "Dejar de mantener sobre otras ventanas")
        controles.btn_fijar.click()
        check("6. botón alterna a OFF y lo refleja",
              not ov.cfg["always_on_top"] and not controles.btn_fijar.activo
              and controles.btn_fijar.icono == "pin-off"
              and controles.btn_fijar.toolTip() == "Mantener sobre otras ventanas")
        controles.btn_fijar.click()
        check("6. botón vuelve a ON", ov.cfg["always_on_top"] and controles.btn_fijar.activo)
        ov.set_siempre_encima(True)
        check("set con el mismo valor no hace nada", _fijada(ov) == (True, True))
    finally:
        _cerrar(ov)


def test_sin_ventana_nativa_todavia():
    ov = TeleprompterOverlay({"always_on_top": True})
    try:
        ov.set_siempre_encima(False)
        check("antes de mostrarse el cambio se aplica al crear la ventana",
              not (ov.windowFlags() & ENCIMA))
        ov.show()
        _app.processEvents()
        check("al mostrarse arranca como ventana normal", _fijada(ov) == (False, False))
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- 7, 8, 14

def test_sincronizacion_settings_y_bandeja():
    with ConfigTemporal():
        ov = _overlay(persistir=True)
        try:
            ventana = ov.abrir_configuracion()
            bandeja = crear_bandeja(ov, disponible=True)
            ov.set_tray_disponible(True)
            check("7. Settings arranca reflejando ON", ventana.chk_siempre_encima.isChecked())
            check("8. bandeja arranca reflejando ON", bandeja.accion_siempre_encima.isChecked())

            ov.ventana_controles.mostrar()
            ov.ventana_controles.btn_fijar.click()
            check("7-8. botón -> Settings y bandeja",
                  not ventana.chk_siempre_encima.isChecked()
                  and not bandeja.accion_siempre_encima.isChecked())
            ventana.chk_siempre_encima.setChecked(True)
            check("7. Settings -> overlay y botón",
                  ov.cfg["always_on_top"] and _fijada(ov) == (True, True)
                  and ov.ventana_controles.btn_fijar.activo
                  and bandeja.accion_siempre_encima.isChecked())
            bandeja.accion_siempre_encima.trigger()
            check("8. bandeja -> overlay, botón y Settings",
                  not ov.cfg["always_on_top"] and _fijada(ov) == (False, False)
                  and not ov.ventana_controles.btn_fijar.activo
                  and not ventana.chk_siempre_encima.isChecked())
            ov.guardar_preferencias()
            check("persistencia: el estado se guarda",
                  guionar_config.cargar().get("always_on_top") is False)

            for _ in range(5):
                ov.abrir_configuracion()
            check("14. Settings no se duplica",
                  len(ov.findChildren(VentanaConfiguracion)) == 1)
            check("14. una sola bandeja", len(ov.findChildren(BandejaGuionAR)) == 1)
            check("Settings no se fija por su cuenta: con pin OFF es ventana normal",
                  not (ventana.windowFlags() & ENCIMA)
                  and ventana.parentWidget() is ov and ventana.isVisible())
            ov.set_siempre_encima(True)
            check("con pin ON Settings sigue al overlay (no queda atrapada detrás)",
                  bool(ventana.windowFlags() & ENCIMA)
                  and bool(ventana.windowHandle().flags() & ENCIMA) and ventana.isVisible())
            ov.set_siempre_encima(False)
            check("al apagar el pin Settings vuelve a ser normal y sigue visible",
                  not (ventana.windowFlags() & ENCIMA) and ventana.isVisible())
        finally:
            _cerrar(ov)
        reinicio = TeleprompterOverlay(guionar_config.cargar())
        try:
            check("persistencia: al reiniciar recuerda OFF",
                  reinicio.cfg["always_on_top"] is False and _fijada(reinicio)[0] is False)
        finally:
            _cerrar(reinicio)


# ---------------------------------------------------------------- 9-11

def test_ocultar_mostrar_y_ghost():
    for valor in (True, False):
        ov = _overlay({"always_on_top": valor})
        try:
            ov.set_tray_disponible(True)
            geometria = ov.geometry()
            ov.ocultar_ventana()
            _app.processEvents()
            check(f"{9 if valor else 10}. oculta con pin {'ON' if valor else 'OFF'}",
                  not ov.isVisible())
            ov.mostrar_ventana()
            _app.processEvents()
            check(f"{9 if valor else 10}. al mostrar desde la bandeja sigue "
                  f"{'ON' if valor else 'OFF'}",
                  ov.isVisible() and ov.cfg["always_on_top"] is valor
                  and _fijada(ov) == (valor, valor) and ov.geometry() == geometria)

            ov.set_ghost_recovery_available(True)
            ov.toggle_visible()
            check(f"Ghost sigue ocultando de verdad con pin {'ON' if valor else 'OFF'}",
                  ov.hidden and not ov.isVisible())
            ov.toggle_visible()
            _app.processEvents()
            check(f"después de Ghost el pin sigue {'ON' if valor else 'OFF'}",
                  ov.isVisible() and _fijada(ov) == (valor, valor))
        finally:
            _cerrar(ov)

    ov = _overlay()
    try:
        ov.set_tray_disponible(True)
        ov.ocultar_ventana()
        ov.set_siempre_encima(False)   # desde la bandeja con la ventana oculta
        check("cambiar el pin con la ventana oculta no la muestra", not ov.isVisible())
        ov.mostrar_ventana()
        _app.processEvents()
        check("al mostrar aplica el cambio hecho mientras estaba oculta",
              _fijada(ov) == (False, False))
    finally:
        _cerrar(ov)


def test_pin_y_candado_independientes():
    ov = _overlay()
    movimientos = []
    try:
        ov._iniciar_movimiento = lambda redimensionar: movimientos.append(redimensionar)
        combinaciones = ((True, False), (True, True), (False, True), (False, False))
        for pin, candado in combinaciones:
            ov.set_siempre_encima(pin)
            ov.set_bloqueo(candado)
            movimientos.clear()
            QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=QPoint(300, 120))
            QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=QPoint(300, 120))
            check(f"11. pin {'ON' if pin else 'OFF'} + candado {'ON' if candado else 'OFF'}",
                  _fijada(ov) == (pin, pin) and ov.cfg["position_locked"] is candado
                  and movimientos == ([] if candado else [False]),
                  repr((_fijada(ov), movimientos)))
        ov.ventana_controles.mostrar()
        check("11. pin y candado son botones distintos y contiguos",
              ov.ventana_controles.btn_fijar is not ov.ventana_controles.btn_bloquear
              and ov.ventana_controles.btn_bloquear.x()
              - ov.ventana_controles.btn_fijar.geometry().right() <= 4)
    finally:
        _cerrar(ov)


def main():
    test_defaults_config_y_arranque()
    test_arranque_real_aplica_la_config()
    test_toggle_preserva_ventana_y_estado()
    test_sin_ventana_nativa_todavia()
    test_sincronizacion_settings_y_bandeja()
    test_ocultar_mostrar_y_ghost()
    test_pin_y_candado_independientes()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests del pin pasaron.")


if __name__ == "__main__":
    main()
