"""Tests de integración de escritorio: Configuración, bandeja, controles
de ventana, alineación, opacidad, pausa con el puntero, bloqueo,
geometría, persistencia y lanzador Linux.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_desktop.py

La bandeja real no existe en offscreen: se prueba con disponibilidad
forzada (lógica y menú) y con el fallback sin bandeja.
"""

import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

import guionar_config
from desktop_shell import (
    BandejaGuionAR, VentanaConfiguracion, crear_bandeja, icono_app,
)
from guionar import (
    DEFAULTS, SCRIPT_MARGIN_PX, TeleprompterOverlay, geometria_visible,
)

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
_TMP = tempfile.TemporaryDirectory()

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
    """Redirige la config a un directorio temporal (nunca la del usuario)."""

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


def _overlay(cfg=None, persistir=False, ancho=520, alto=260):
    ov = TeleprompterOverlay({"width": ancho, "height": alto, **(cfg or {})},
                             persistir=persistir)
    ov.show()
    _app.processEvents()
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()
    gc.collect()


def _entrar(ov):
    ov.enterEvent(QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))


def _salir(ov):
    ov.leaveEvent(QEvent(QEvent.Type.Leave))


class Grabador:
    def __init__(self):
        self.llamadas = []

    def __call__(self, *args):
        self.llamadas.append(args)


class PainterRecorder:
    def __init__(self):
        self.draws = []
        self.color = None

    def setFont(self, _font):
        pass

    def setPen(self, color):
        self.color = color.getRgb()

    def drawText(self, point, text):
        self.draws.append((round(point.x(), 3), round(point.y(), 3), text, self.color))


# ---------------------------------------------------------------- config

def test_config_defaults_y_validacion():
    with ConfigTemporal() as archivo:
        archivo.write_text(json.dumps({"bg_opacity": 0.4, "font_size_current": 40,
                                       "font_size_context": 24}), encoding="utf-8")
        vieja = guionar_config.cargar()
        check("config vieja sigue cargando sus claves",
              vieja == {"bg_opacity": 0.4, "font_size_current": 40,
                        "font_size_context": 24}, repr(vieja))
        ov = TeleprompterOverlay(vieja)
        try:
            esperado = {"text_alignment": "center", "pause_on_hover": False,
                        "position_locked": False, "remember_geometry": True,
                        "auto_hide_controls": True}
            check("claves nuevas ausentes toman defaults",
                  all(ov.cfg[k] == v for k, v in esperado.items()),
                  repr({k: ov.cfg[k] for k in esperado}))
            check("config vieja conserva sus valores", ov.cfg["bg_opacity"] == 0.4)
        finally:
            _cerrar(ov)

    validos = {"text_alignment": "right", "pause_on_hover": True,
               "position_locked": True, "remember_geometry": False,
               "auto_hide_controls": False,
               "window_geometry": {"x": 10, "y": -20, "width": 600, "height": 240}}
    check("claves nuevas válidas se conservan",
          guionar_config.normalizar(validos) == validos)
    invalidos = [
        {"text_alignment": "middle"}, {"text_alignment": 1},
        {"pause_on_hover": "yes"}, {"pause_on_hover": 1},
        {"position_locked": None}, {"remember_geometry": 0},
        {"auto_hide_controls": "false"},
        {"window_geometry": {"x": 1}},
        {"window_geometry": {"x": 0, "y": 0, "width": 50, "height": 200}},
        {"window_geometry": {"x": 0, "y": 0, "width": True, "height": 200}},
        {"window_geometry": {"x": 10**9, "y": 0, "width": 600, "height": 200}},
        {"window_geometry": [0, 0, 600, 200]},
    ]
    for caso in invalidos:
        check(f"valor inválido se descarta: {caso}",
              guionar_config.normalizar(caso) == {})


def test_actualizar_es_parcial_y_atomico():
    with ConfigTemporal() as archivo:
        archivo.write_text(json.dumps({"bg_opacity": 0.3, "font_size_current": 40}),
                           encoding="utf-8")
        guionar_config.actualizar({"text_alignment": "left", "inventada": 1})
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        check("actualizar agrega sin pisar lo guardado",
              datos == {"bg_opacity": 0.3, "font_size_current": 40,
                        "text_alignment": "left"}, repr(datos))
        check("no quedan temporales", sorted(p.name for p in archivo.parent.iterdir())
              == ["config.json"])
        anterior = archivo.read_text(encoding="utf-8")
        original = guionar_config.os.replace

        def falla(*_a):
            raise OSError("disco lleno")

        guionar_config.os.replace = falla
        try:
            try:
                guionar_config.actualizar({"bg_opacity": 0.9})
                lanzo = False
            except OSError:
                lanzo = True
        finally:
            guionar_config.os.replace = original
        check("falla de escritura conserva el archivo anterior",
              lanzo and archivo.read_text(encoding="utf-8") == anterior
              and sorted(p.name for p in archivo.parent.iterdir()) == ["config.json"])


def test_overlay_sin_persistir_no_escribe():
    with ConfigTemporal() as archivo:
        ov = _overlay()
        try:
            ov.set_alineacion("left")
            ov.set_opacidad(0.3)
            ov.guardar_preferencias()
            check("overlay de tests/integración no toca la config", not archivo.exists())
        finally:
            _cerrar(ov)


# ---------------------------------------------------------------- settings

def test_configuracion_instancia_unica_y_valores():
    with ConfigTemporal():
        ov = _overlay({"text_alignment": "right", "bg_opacity": 0.4,
                       "font_size_context": 22, "pause_on_hover": True,
                       "position_locked": True, "auto_hide_controls": False})
        try:
            ventana = ov.abrir_configuracion()
            otra = ov.abrir_configuracion()
            check("Configuración abre una sola instancia",
                  ventana is otra and len(ov.findChildren(VentanaConfiguracion)) == 1)
            check("Configuración es una ventana no modal",
                  ventana.isWindow() and not ventana.isModal() and ventana.isVisible())
            check("valores cargan desde la config",
                  ventana.botones_alineacion["right"].isChecked()
                  and ventana.slider_opacidad.value() == 40
                  and ventana.lbl_opacidad.text() == "40 %"
                  and ventana.spin_texto.value() == 22
                  and ventana.chk_pausa_hover.isChecked()
                  and ventana.chk_bloqueo.isChecked()
                  and ventana.chk_geometria.isChecked()
                  and not ventana.chk_autoocultar.isChecked())
            ventana.close()
            _app.processEvents()
            check("cerrar y reabrir reutiliza la ventana",
                  ov.abrir_configuracion() is ventana and ventana.isVisible())
            ov.hide()
            _app.processEvents()
            check("Configuración sigue visible con el overlay oculto",
                  ventana.isVisible())
        finally:
            _cerrar(ov)


def test_configuracion_aplica_en_vivo_y_persiste():
    with ConfigTemporal() as archivo:
        ov = _overlay(persistir=True)
        try:
            ov.abrir_documento(_archivo("vivo.txt", ORACIONES))
            ventana = ov.abrir_configuracion()
            ventana.botones_alineacion["left"].click()
            check("alineación se aplica en vivo", ov.cfg["text_alignment"] == "left")
            ventana.slider_opacidad.setValue(30)
            check("opacidad se aplica en vivo",
                  ov.cfg["bg_opacity"] == 0.3 and ventana.lbl_opacidad.text() == "30 %")
            lineas = len(ov._lineas_guion)
            ventana.spin_texto.setValue(26)
            check("tamaño de texto se aplica en vivo con reflow",
                  ov.cfg["font_size_context"] == 26 and len(ov._lineas_guion) > lineas)
            ventana.chk_pausa_hover.setChecked(True)
            ventana.chk_bloqueo.setChecked(True)
            ventana.chk_autoocultar.setChecked(False)
            check("comportamiento se aplica en vivo",
                  ov.cfg["pause_on_hover"] and ov.cfg["position_locked"]
                  and not ov.cfg["auto_hide_controls"] and ov.barra.visible_objetivo)
            check("el guardado es diferido (agrupa cambios)",
                  not archivo.exists() and ov._timer_guardado.isActive())
            ov.guardar_preferencias()
            guardado = guionar_config.cargar()
            check("preferencias persisten",
                  guardado.get("text_alignment") == "left"
                  and guardado.get("bg_opacity") == 0.3
                  and guardado.get("font_size_context") == 26
                  and guardado.get("pause_on_hover") is True
                  and guardado.get("position_locked") is True
                  and guardado.get("auto_hide_controls") is False, repr(guardado))

            ov.set_bloqueo(False)  # desde otro lugar (bandeja / candado)
            check("Configuración se sincroniza con cambios externos",
                  not ventana.chk_bloqueo.isChecked())

            ventana.btn_restaurar.click()
            check("Restaurar vuelve sólo Apariencia",
                  ov.cfg["text_alignment"] == DEFAULTS["text_alignment"]
                  and ov.cfg["bg_opacity"] == DEFAULTS["bg_opacity"]
                  and ov.cfg["font_size_context"] == DEFAULTS["font_size_context"]
                  and ov.cfg["pause_on_hover"] is True
                  and not ov.cfg["auto_hide_controls"])
            check("Restaurar se refleja en la ventana",
                  ventana.botones_alineacion["center"].isChecked()
                  and ventana.slider_opacidad.value() == round(DEFAULTS["bg_opacity"] * 100))
        finally:
            _cerrar(ov)

        reinicio = TeleprompterOverlay(guionar_config.cargar())
        try:
            check("al reiniciar los valores persisten",
                  reinicio.cfg["pause_on_hover"] is True
                  and reinicio.cfg["auto_hide_controls"] is False
                  and reinicio.cfg["text_alignment"] == "center")
        finally:
            _cerrar(reinicio)


def test_opacidad_no_afecta_texto():
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("opacidad.txt", ORACIONES))
        trazos = []
        for opacidad in (0.2, 1.0):
            ov.set_opacidad(opacidad)
            grabador = PainterRecorder()
            ov._paint_script(grabador)
            trazos.append(grabador.draws)
        check("opacidad del fondo no cambia color ni posición del texto",
              trazos[0] == trazos[1] and trazos[0])
        ov.set_opacidad(0.2)
        claro = ov.grab().toImage().pixelColor(4, ov.height() // 2).alpha()
        ov.set_opacidad(1.0)
        oscuro = ov.grab().toImage().pixelColor(4, ov.height() // 2).alpha()
        check("opacidad cambia realmente el fondo", oscuro > claro + 100,
              f"{claro} -> {oscuro}")
        ov.set_opacidad(7)
        check("opacidad se acota a 0-1", ov.cfg["bg_opacity"] == 1.0)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- alineación

def test_alineacion_afecta_layout_y_dibujo():
    ov = _overlay(ancho=520)
    try:
        ov.cargar_guion(_archivo("alinear.txt", ORACIONES))
        for _ in range(3):
            ov.saltar_oracion(1)
        cursor = ov.guion.cursor
        disponible = ov.width() - 2 * SCRIPT_MARGIN_PX
        resultados = {}
        for alineacion in ("left", "center", "right"):
            ov.set_alineacion(alineacion)
            lineas = [(li, ln) for li, ln in enumerate(ov._lineas_guion) if ln]
            xs = [ov._x_lineas_guion[li] for li, _ in lineas]
            grabador = PainterRecorder()
            ov._paint_script(grabador)
            primeros = {}
            for x, y, _texto, _c in grabador.draws:
                primeros.setdefault(y, x)
            resultados[alineacion] = (xs, sorted(primeros.values()))
            check(f"{alineacion}: cursor semántico se conserva", ov.guion.cursor == cursor)
            linea = ov._linea_visual_del_cursor()
            check(f"{alineacion}: viewport anclado al cursor",
                  abs(ov.scroll_target - max(0.0, (linea - 1)
                                             * ov._line_advance_guion_px())) < 0.01)
            check(f"{alineacion}: el dibujo empieza donde dice el layout",
                  set(round(x, 3) for x in primeros.values())
                  <= set(round(x, 3) for x in xs))

        izquierda, centro, derecha = (resultados[a][0] for a in ("left", "center", "right"))
        check("izquierda: todas las líneas arrancan en el margen",
              all(x == SCRIPT_MARGIN_PX for x in izquierda))
        check("centro: las líneas quedan corridas hacia el medio",
              all(c >= i for c, i in zip(centro, izquierda)) and max(centro) > SCRIPT_MARGIN_PX)
        check("derecha: más corridas que centro",
              all(d >= c for d, c in zip(derecha, centro)) and max(derecha) > max(centro))
        check("centro es el punto medio entre izquierda y derecha",
              all(abs((d + i) / 2 - c) < 0.51
                  for i, c, d in zip(izquierda, centro, derecha)))
        fm_lineas = [ln for ln in ov._lineas_guion if ln]
        check("derecha: ninguna línea se sale del área útil",
              all(x <= SCRIPT_MARGIN_PX + disponible for x in derecha)
              and len(derecha) == len(fm_lineas))

        ov.set_alineacion("centro")  # valor inválido: se ignora
        check("alineación inválida se ignora", ov.cfg["text_alignment"] == "right")
        ov.set_alineacion("center")
        x_antes = list(ov._x_lineas_guion)
        ov.append_text(" ".join(w for _, w in ov.guion.originales[cursor:cursor + 4]))
        check("el resaltado al avanzar no corre las líneas",
              ov._x_lineas_guion == x_antes)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- hover

def test_hover_no_pausa_por_defecto():
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("hover.txt", ORACIONES))
        _entrar(ov)
        check("por defecto el puntero no pausa", ov.hover_paused is False)
        check("el puntero muestra los controles",
              ov.barra.visible_objetivo and ov.ventana_controles.visible_objetivo)
        check("con el puntero encima sigue leyendo",
              not ov.paused and ov._estado()[0] == "Leyendo")
        _salir(ov)
        ov.set_pausa_hover(True)
        _entrar(ov)
        check("con la opción activa el puntero pausa", ov.hover_paused is True
              and ov._estado()[0] == "En pausa (puntero)")
        ov.set_pausa_hover(False)
        check("desactivar la opción con el puntero encima reanuda",
              ov.hover_paused is False)
        _salir(ov)
        check("salir del panel nunca deja pausa de puntero", ov.hover_paused is False)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- lock

def test_bloqueo():
    ov = _overlay(ancho=520, alto=260)
    try:
        movimientos = Grabador()
        ov._iniciar_movimiento = movimientos
        centro = QPoint(260, 130)
        grip = QPoint(ov.width() - 5, ov.height() - 5)
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=centro)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=centro)
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=grip)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=grip)
        check("desbloqueado mueve y redimensiona",
              movimientos.llamadas == [(False,), (True,)], repr(movimientos.llamadas))

        ov.ventana_controles.mostrar()
        ov.ventana_controles.btn_bloquear.click()
        check("candado bloquea la posición", ov.cfg["position_locked"] is True)
        check("candado refleja el estado", ov.ventana_controles.btn_bloquear.icono == "lock")
        movimientos.llamadas.clear()
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=centro)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=centro)
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=grip)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=grip)
        check("bloqueado no mueve ni redimensiona", movimientos.llamadas == [])
        QTest.mouseMove(ov, grip)
        check("bloqueado no ofrece cursor de mover/redimensionar",
              ov.cursor().shape() == Qt.CursorShape.ArrowCursor)

        ov.abrir_documento(_archivo("lock.txt", ORACIONES))
        ov.barra.mostrar()
        ov.barra.btn_play.click()
        check("bloqueado: Play/Pausa sigue funcionando", ov.paused is False)
        llamadas = Grabador()
        ov._selector_archivo = lambda *a: (llamadas(), ("", ""))[1]
        ov.barra.btn_abrir.click()
        check("bloqueado: abrir archivo sigue funcionando", len(llamadas.llamadas) == 1)
        ov.ventana_controles.btn_configuracion.click()
        check("bloqueado: Configuración sigue funcionando",
              ov._configuracion is not None and ov._configuracion.isVisible())
        salidas = Grabador()
        ov._salir_app = salidas
        ov.ventana_controles.btn_cerrar.click()
        check("bloqueado: cerrar sigue funcionando", len(salidas.llamadas) == 1)
        ov.alternar_bloqueo()
        check("desbloquear vuelve a permitir mover", ov.cfg["position_locked"] is False)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- tray

def test_bandeja():
    ov = _overlay()
    try:
        check("sin bandeja del sistema no se crea ícono",
              crear_bandeja(ov, disponible=False) is None)
        ov.set_tray_disponible(False)
        check("sin bandeja, Ocultar queda deshabilitado",
              not ov.ventana_controles.btn_ocultar.isEnabled())
        ov.ocultar_ventana()
        check("sin bandeja, ocultar no deja la ventana inaccesible", ov.isVisible())

        bandeja = crear_bandeja(ov, disponible=True)
        ov.set_tray_disponible(True)
        check("con bandeja se crea el ícono",
              isinstance(bandeja, BandejaGuionAR) and not bandeja.icon().isNull())
        textos = [a.text() for a in bandeja.contextMenu().actions() if not a.isSeparator()]
        check("menú mínimo completo",
              textos == ["GuionAR", "Ocultar", "Bloquear posición",
                         "Pausar al pasar el mouse", "ParlAR conectado",
                         "Configuración…", "Salir"], repr(textos))
        check("sin ParlAR el estado no se muestra", not bandeja.accion_parlar.isVisible())

        bandeja.accion_mostrar.trigger()
        check("Ocultar desde la bandeja oculta la ventana", not ov.isVisible())
        bandeja.sincronizar()
        check("el menú ofrece Mostrar cuando está oculta",
              bandeja.accion_mostrar.text() == "Mostrar")
        bandeja.accion_mostrar.trigger()
        check("Mostrar desde la bandeja la recupera", ov.isVisible())
        ov.ocultar_ventana()
        bandeja.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        check("click en el ícono restaura la ventana", ov.isVisible())
        ov.ocultar_ventana()
        bandeja.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        check("doble click restaura la ventana", ov.isVisible())

        ov.set_ghost_recovery_available(True)
        ov.toggle_visible()
        check("Ghost oculta", ov.hidden and not ov.isVisible())
        bandeja.accion_mostrar.trigger()
        check("la bandeja también recupera Ghost", ov.isVisible() and not ov.hidden)
        ov.ocultar_ventana()
        ov.toggle_visible()  # atajo de escritorio por socket
        check("toggle externo trae una ventana ocultada por el usuario",
              ov.isVisible() and not ov.hidden)

        bandeja.accion_bloquear.trigger()
        check("Bloquear desde la bandeja", ov.cfg["position_locked"] is True)
        ov.set_bloqueo(False)
        check("acción de bloqueo sincronizada", not bandeja.accion_bloquear.isChecked())
        bandeja.accion_pausa.trigger()
        check("Pausar con el puntero desde la bandeja", ov.cfg["pause_on_hover"] is True)
        ov.set_pausa_hover(False)
        check("acción de pausa sincronizada", not bandeja.accion_pausa.isChecked())

        bandeja.accion_configuracion.trigger()
        check("Configuración desde la bandeja",
              ov._configuracion is not None and ov._configuracion.isVisible())
        ov.voz_conectada = True
        bandeja.sincronizar()
        check("estado ParlAR conectado leído del overlay", bandeja.accion_parlar.isVisible())
        salidas = Grabador()
        ov._salir_app = salidas
        bandeja.accion_salir.trigger()
        check("Salir termina la aplicación", len(salidas.llamadas) == 1)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- window controls

def test_controles_de_ventana():
    with ConfigTemporal():
        ov = _overlay(persistir=True)
        try:
            controles = ov.ventana_controles
            ov.set_tray_disponible(True)
            controles.mostrar()
            check("controles de ventana arriba, separados de la barra inferior",
                  controles is not ov.barra
                  and controles.geometry().bottom() < ov.height() / 2
                  and ov.barra.geometry().top() > ov.height() / 2)
            check("botón ocultar disponible con bandeja", controles.btn_ocultar.isEnabled())
            controles.btn_ocultar.click()
            check("Ocultar mantiene el proceso y oculta la ventana", not ov.isVisible())
            ov.mostrar_ventana()
            controles.btn_configuracion.click()
            check("Configuración desde los controles de ventana",
                  ov._configuracion is not None and ov._configuracion.isVisible())
            ov.set_opacidad(0.33)
            salidas = Grabador()
            ov._salir_app = salidas
            controles.btn_cerrar.click()
            check("Cerrar termina la aplicación", len(salidas.llamadas) == 1)
            check("Cerrar guarda lo pendiente",
                  guionar_config.cargar().get("bg_opacity") == 0.33)
            cerradas = Grabador()
            ov.cerrada.connect(cerradas)
            ov.close()
            check("cerrar la ventana avisa a la aplicación", len(cerradas.llamadas) == 1)
        finally:
            _cerrar(ov)


def test_autoocultar_controles():
    ov = _overlay({"auto_hide_controls": False})
    try:
        check("sin autoocultar, los controles quedan visibles",
              ov.barra.visible_objetivo and ov.ventana_controles.visible_objetivo)
        ov._autohide.salida()
        ov._autohide._ocultar_todos()
        check("sin autoocultar, salir del panel no los oculta", ov.barra.visible_objetivo)
        ov.set_autoocultar_controles(True)
        ov._autohide._ocultar_todos()
        check("con autoocultar vuelven a ocultarse", not ov.barra.visible_objetivo)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- geometry

def test_geometria_visible():
    minimo = QSize(320, 140)
    pantalla = [QRect(0, 0, 1920, 1080)]
    dentro = QRect(100, 100, 700, 260)
    check("geometría válida no se toca", geometria_visible(dentro, pantalla, minimo) == dentro)
    fuera = geometria_visible(QRect(5000, -3000, 700, 260), pantalla, minimo)
    check("geometría fuera de pantalla vuelve adentro",
          pantalla[0].contains(fuera) and fuera.size() == QSize(700, 260), repr(fuera))
    parcial = geometria_visible(QRect(1800, 900, 700, 260), pantalla, minimo)
    check("geometría a medio salir se corre hacia adentro",
          pantalla[0].contains(parcial), repr(parcial))
    enorme = geometria_visible(QRect(0, 0, 5000, 4000), pantalla, minimo)
    check("geometría más grande que la pantalla se achica",
          enorme == QRect(0, 0, 1920, 1080), repr(enorme))
    dos = [QRect(0, 0, 1920, 1080), QRect(1920, 0, 1280, 1024)]
    segunda = geometria_visible(QRect(2500, 100, 600, 200), dos, minimo)
    check("con dos monitores se respeta el monitor donde estaba",
          dos[1].contains(segunda) and segunda.topLeft() == QPoint(2500, 100))
    desconectado = geometria_visible(QRect(4000, 100, 600, 200), dos, minimo)
    check("monitor desconectado: vuelve a uno disponible",
          any(p.contains(desconectado) for p in dos), repr(desconectado))


def test_geometria_persiste_y_se_restaura():
    with ConfigTemporal():
        ov = _overlay(persistir=True, ancho=500, alto=200)
        try:
            ov.move(60, 70)
            ov.resize(540, 220)
            _app.processEvents()
            ov.guardar_preferencias()
            guardada = guionar_config.cargar().get("window_geometry")
            check("posición y tamaño se guardan",
                  guardada == {"x": ov.x(), "y": ov.y(), "width": 540, "height": 220},
                  repr(guardada))
        finally:
            _cerrar(ov)
        restaurado = TeleprompterOverlay(guionar_config.cargar())
        try:
            check("al reabrir se restaura posición y tamaño",
                  restaurado.size() == QSize(540, 220)
                  and restaurado.pos() == QPoint(guardada["x"], guardada["y"]),
                  repr((restaurado.pos(), restaurado.size())))
        finally:
            _cerrar(restaurado)

        perdido = TeleprompterOverlay({"window_geometry": {
            "x": 9000, "y": 9000, "width": 600, "height": 200}})
        try:
            disponible = _app.primaryScreen().availableGeometry()
            check("geometría guardada fuera de pantalla se corrige al abrir",
                  disponible.contains(perdido.geometry()), repr(perdido.geometry()))
        finally:
            _cerrar(perdido)

        sin_recordar = TeleprompterOverlay({"remember_geometry": False, "window_geometry": {
            "x": 30, "y": 30, "width": 610, "height": 210}})
        try:
            check("sin Recordar no se restaura", sin_recordar.width() != 610)
        finally:
            _cerrar(sin_recordar)


# ---------------------------------------------------------------- ícono y lanzador

def test_icono():
    icono = icono_app()
    check("ícono propio disponible", not icono.isNull())
    check("ícono legible en tamaños de bandeja/ventana",
          all(not icono.pixmap(l, l).isNull() for l in (16, 24, 32, 64)))
    svg = (ROOT / "assets" / "guionar.svg").read_text(encoding="utf-8")
    check("ícono SVG con fondo transparente", "<svg" in svg and "<rect width=\"64\"" not in svg)


def test_lanzador_y_desktop():
    plantilla = (ROOT / "packaging" / "linux" / "guionar.desktop.in").read_text(encoding="utf-8")
    for linea in ("[Desktop Entry]", "Type=Application", "Name=GuionAR",
                  "Icon=guionar", "Terminal=false", "Exec=@EXEC@"):
        check(f"plantilla .desktop contiene {linea}", linea in plantilla)
    check("plantilla sin rutas de usuario", "/home/" not in plantilla)

    lanzador = ROOT / "bin" / "guionar"
    check("lanzador ejecutable", os.access(lanzador, os.X_OK))
    proc = subprocess.run([str(lanzador), "--help"], capture_output=True, text=True,
                          timeout=20)
    check("lanzador arranca GuionAR", proc.returncode == 0 and "--guion" in proc.stdout,
          proc.stderr[-300:])

    datos = tempfile.mkdtemp()
    try:
        env = {**os.environ, "XDG_DATA_HOME": datos}
        instalador = ROOT / "packaging" / "linux" / "install-desktop-entry.sh"
        proc = subprocess.run(["sh", str(instalador)], env=env, capture_output=True,
                              text=True, timeout=20)
        desktop = Path(datos) / "applications" / "guionar.desktop"
        icono = Path(datos) / "icons" / "hicolor" / "scalable" / "apps" / "guionar.svg"
        contenido = desktop.read_text(encoding="utf-8") if desktop.exists() else ""
        exec_esperado = f'Exec="{lanzador}" --socket'
        check("instalador genera la entrada de menú",
              proc.returncode == 0 and exec_esperado in contenido,
              f"{proc.stderr[-200:]} {contenido!r}")
        check("instalador copia el ícono", icono.exists())
        validador = shutil.which("desktop-file-validate")
        if validador:
            v = subprocess.run([validador, str(desktop)], capture_output=True, text=True)
            check("entrada .desktop válida", v.returncode == 0, v.stdout + v.stderr)
        proc = subprocess.run(["sh", str(instalador), "--uninstall"], env=env,
                              capture_output=True, text=True, timeout=20)
        check("desinstalar quita entrada e ícono",
              proc.returncode == 0 and not desktop.exists() and not icono.exists())
    finally:
        shutil.rmtree(datos, ignore_errors=True)


def main():
    test_config_defaults_y_validacion()
    test_actualizar_es_parcial_y_atomico()
    test_overlay_sin_persistir_no_escribe()
    test_configuracion_instancia_unica_y_valores()
    test_configuracion_aplica_en_vivo_y_persiste()
    test_opacidad_no_afecta_texto()
    test_alineacion_afecta_layout_y_dibujo()
    test_hover_no_pausa_por_defecto()
    test_bloqueo()
    test_bandeja()
    test_controles_de_ventana()
    test_autoocultar_controles()
    test_geometria_visible()
    test_geometria_persiste_y_se_restaura()
    test_icono()
    test_lanzador_y_desktop()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de escritorio pasaron.")


if __name__ == "__main__":
    main()
