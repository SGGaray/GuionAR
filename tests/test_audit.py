"""Regresiones de UI/UX, interacción, motion y performance.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_audit.py

Cada test nombra el hallazgo que protege. Se prueban invariantes
(estabilidad, visibilidad, foco, timers, costo por frame), no estética por
coordenadas fijas.
"""

import gc
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QAbstractAnimation, QPoint, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QFont, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import guionar
import ui_controls
from guionar import SCRIPT_MARGIN_PX, TeleprompterOverlay

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


def _archivo(nombre, contenido):
    ruta = os.path.join(_TMP.name, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def _overlay(ancho=520, alto=260, **cfg):
    ov = TeleprompterOverlay({"width": ancho, "height": alto, **cfg})
    ov.resize(ancho, alto)
    ov.show()
    _app.processEvents()
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()
    gc.collect()


def _drenar(segundos):
    fin = time.time() + segundos
    while time.time() < fin:
        _app.processEvents()
        time.sleep(0.01)


def _ticks(ov, n, dt=0.05):
    for _ in range(n):
        ov._last_tick = time.monotonic() - dt
        ov._tick()


class Grabador:
    """Painter mínimo: lo único que _paint_script puede usar."""

    def __init__(self):
        self.trazos = []
        self.pesos = set()
        self._color = None

    def setFont(self, fuente):
        self.pesos.add(int(fuente.weight()))

    def setPen(self, color):
        self._color = color.getRgb()

    def drawText(self, punto, texto):
        self.trazos.append((round(punto.x(), 3), round(punto.y(), 3), texto, self._color))


def _posiciones(ov):
    g = Grabador()
    ov._paint_script(g)
    return g


# ---------------------------------------------------------------- F01

def test_resaltado_no_corre_palabras():
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("f01.txt", ORACIONES))
        ov.set_speaking(True)
        maximo = 0.0
        for _ in range(15):
            antes = _posiciones(ov).trazos
            ov.append_text(ov.guion.originales[ov.guion.cursor][1])
            despues = _posiciones(ov).trazos
            if len(antes) == len(despues):
                maximo = max(maximo, max(abs(a[0] - b[0]) for a, b in zip(antes, despues)))
        check("F01 avanzar la palabra resaltada no corre ninguna palabra",
              maximo == 0.0, f"máximo {maximo}px")
        g = _posiciones(ov)
        check("F01 el guion usa un solo peso (sin negrita al resaltar)",
              g.pesos == {int(QFont.Weight.Normal)}, repr(g.pesos))
        rect = ov._rect_subrayado_cursor()
        li = ov._linea_por_indice[ov.guion.cursor]
        palabra = next(t for t in g.trazos
                       if abs(t[1] - ov._baseline_guion(li)) < 0.01
                       and t[2].strip() == ov.guion.originales[ov.guion.cursor][1])
        check("F01 el subrayado marca la palabra reconocida",
              rect is not None and abs(rect.x() - palabra[0]) < 0.01
              and rect.top() > palabra[1], repr((rect, palabra)))
        ov.set_voice_producers(0)
        check("F01 sin voz no hay subrayado de palabra (se destaca la línea)",
              ov._rect_subrayado_cursor() is None)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F02

def test_contorno_con_fondo_transparente():
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("f02.txt", ORACIONES))
        trazos = {}
        for opacidad in (0.1, 0.3, 0.5, 0.9):
            ov.set_opacidad(opacidad)
            trazos[opacidad] = _posiciones(ov).trazos
        check("F02 el texto no cambia de color ni posición con la opacidad",
              len({tuple(t) for t in trazos.values()}) == 1)
        ov.set_opacidad(0.9)
        alto = ov._alpha_contorno()
        ov.set_opacidad(0.5)
        limite = ov._alpha_contorno()
        ov.set_opacidad(0.3)
        medio = ov._alpha_contorno()
        ov.set_opacidad(0.1)
        bajo = ov._alpha_contorno()
        check("F02 contorno sólo con fondo poco opaco y más fuerte cuanto más transparente",
              alto == 0 and limite == 0 and 0 < medio < bajo <= 0.85,
              repr((alto, limite, medio, bajo)))
        imagen = ov.grab().toImage()
        oscuros = sum(1 for x in range(0, imagen.width(), 3)
                      for y in range(40, imagen.height() - 40, 3)
                      if imagen.pixelColor(x, y).alpha() > 120
                      and imagen.pixelColor(x, y).lightness() < 60)
        check("F02 con 10% de opacidad el texto tiene borde oscuro real", oscuros > 30,
              f"{oscuros} píxeles")
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F03 / F12

def test_teclado_llega_a_los_controles():
    ov = _overlay()
    try:
        ov.activateWindow()
        ov.setFocus()
        ov.cargar_guion(_archivo("f03.txt", ORACIONES))
        _app.processEvents()
        check("F03 activar la ventana no despliega los controles",
              not ov.barra.visible_objetivo)
        QTest.keyClick(ov, Qt.Key.Key_Tab)
        _app.processEvents()
        foco = _app.focusWidget()
        check("F03 Tab con paneles ocultos los muestra y entra en un control",
              ov.barra.visible_objetivo and ov.ventana_controles.visible_objetivo
              and isinstance(foco, ui_controls.IconButton), repr(foco))
        ov._autohide._ocultar_todos()
        check("F12 con foco en un panel no se oculta ninguno",
              ov.barra.visible_objetivo and ov.ventana_controles.visible_objetivo)
        recorridos = set()
        for _ in range(16):
            QTest.keyClick(_app.focusWidget(), Qt.Key.Key_Tab)
            _app.processEvents()
            recorridos.add(getattr(_app.focusWidget(), "icono", None))
        check("F03 Tab recorre barra inferior y controles de ventana",
              {"open", "minus", "plus", "unlock", "settings", "close"} <= recorridos,
              repr(recorridos))
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F04

def test_sin_asignaciones_de_fuente_por_frame():
    ov = _overlay()
    cuenta = {"n": 0}
    real_font, real_fm = guionar.QFont, guionar.QFontMetrics

    class ContarFont(real_font):
        def __init__(self, *a):
            cuenta["n"] += 1
            super().__init__(*a)

    class ContarFM(real_fm):
        def __init__(self, *a):
            cuenta["n"] += 1
            super().__init__(*a)

    try:
        ov.cargar_guion(_archivo("f04.txt", ORACIONES))
        ov.set_speaking(True)
        ov.repaint()
        guionar.QFont, guionar.QFontMetrics = ContarFont, ContarFM
        cuenta["n"] = 0
        for _ in range(10):
            ov.repaint()
        por_frame = cuenta["n"] / 10
        cuenta["n"] = 0
        _ticks(ov, 10, 0.016)
        por_tick = cuenta["n"] / 10
        check("F04 ningún QFont/QFontMetrics nuevo por frame de guion",
              por_frame == 0, f"{por_frame}/frame")
        check("F04 ningún QFontMetrics nuevo por tick", por_tick == 0, f"{por_tick}/tick")
    finally:
        guionar.QFont, guionar.QFontMetrics = real_font, real_fm
    try:
        lineas = len(ov._lineas_guion)
        ov._change_font(+8)
        check("F04 la caché se invalida al cambiar la fuente",
              ov._metricas_guion()[0].pointSize() == ov.cfg["font_size_context"]
              and len(ov._lineas_guion) > lineas)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F05

def test_paneles_sin_costo_en_reposo():
    ov = _overlay()
    pintados = {"n": 0}
    original = ui_controls.IconButton.paintEvent

    def contar(self, e):
        pintados["n"] += 1
        return original(self, e)

    try:
        ov.cargar_guion(_archivo("f05.txt", ORACIONES))
        barra = ov.barra
        barra.mostrar()
        check("F05 durante la transición se usa el efecto de opacidad",
              barra._efecto.isEnabled() and not barra.quieto_y_opaco())
        _drenar(0.35)
        check("F05 opaco y quieto: el efecto se apaga",
              not barra._efecto.isEnabled() and barra.quieto_y_opaco())
        ov.ventana_controles.mostrar()
        _drenar(0.35)
        ui_controls.IconButton.paintEvent = contar
        _app.processEvents()
        pintados["n"] = 0
        for _ in range(30):
            ov.scroll_offset += 1.0
            ov._repintar_lectura()
            _app.processEvents()
        por_frame = pintados["n"] / 30
        check("F05 el scroll no repinta los 13 botones visibles en cada frame",
              por_frame <= 4, f"{por_frame:.1f} botones/frame")
        barra.ocultar()
        check("F05 al ocultar vuelve a animarse con efecto",
              barra._efecto.isEnabled() and not barra.quieto_y_opaco())
        _drenar(0.35)
        check("F05 oculto del todo al terminar", not barra.isVisible())
    finally:
        ui_controls.IconButton.paintEvent = original
        _cerrar(ov)


# ---------------------------------------------------------------- F06

def test_parcial_fuera_del_guion():
    ov = _overlay()
    llamadas = []
    original = ui_controls.pintar_parcial
    try:
        ov.cargar_guion(_archivo("f06.txt", ORACIONES))
        ov.set_partial("hipótesis en curso")
        textos = [t[2] for t in _posiciones(ov).trazos]
        check("F06 el parcial ya no se dibuja dentro del guion",
              not any("hipótesis" in t for t in textos))
        ui_controls.pintar_parcial = lambda *a: llamadas.append(a[-1])
        ov.toast_opacidad = 0.0
        imagen = QImage(ov.size(), QImage.Format.Format_ARGB32)
        p = QPainter(imagen)
        ov._paint_capas_superiores(p)
        ov.toast_opacidad = 1.0
        ov.toast_texto = "aviso"
        ov._paint_capas_superiores(p)
        p.end()
        check("F06 el parcial se muestra aparte y cede su lugar a los avisos",
              llamadas == ["hipótesis en curso"], repr(llamadas))
    finally:
        ui_controls.pintar_parcial = original
        _cerrar(ov)


# ---------------------------------------------------------------- F07

def test_fin_del_guion_conserva_contexto():
    ov = _overlay(alto=200)
    try:
        ov.abrir_documento(_archivo("f07.txt", ORACIONES))
        ov.speed_pps = ov.SPEED_MAX
        ov.toggle_pause()
        ultima = ov._ultima_linea_guion()
        ancla = max(0.0, (ultima - 1) * ov._line_advance_guion_px())
        llego = None
        for i in range(4000):
            _ticks(ov, 1)
            if llego is None and abs(ov.scroll_offset - ancla) < 0.01:
                llego = i
                check("F07 al llegar al ancla final todavía no terminó",
                      not ov.script_terminado())
            if ov.script_terminado():
                break
        check("F07 el scroll se detiene en el ancla del cursor terminal",
              ov.script_terminado() and abs(ov.scroll_offset - ancla) < 0.01,
              f"{ov.scroll_offset} vs {ancla}")
        check("F07 la última línea queda a la vista un rato antes de terminar",
              llego is not None and i > llego)
        offset = ov.scroll_offset
        ov.resize(ov.width() + 1, ov.height())
        ov.resize(ov.width() - 1, ov.height())
        _app.processEvents()
        check("F07 un resize después del fin no hace saltar la vista",
              abs(ov.scroll_offset - offset) < 0.01)

        corto = _overlay(alto=260)
        try:
            corto.abrir_documento(_archivo("f07b.txt", "Uno dos.\n\nTres cuatro.\n\nCinco seis."))
            corto.toggle_pause()
            cursores = []
            for _ in range(400):
                _ticks(corto, 1)
                cursores.append(corto.guion.cursor)
                if corto.script_terminado():
                    break
            check("F07 guion corto sin scroll igual avanza línea por línea",
                  len(set(cursores)) >= 3 and corto.script_terminado(), repr(sorted(set(cursores))))
        finally:
            _cerrar(corto)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F09 / F10 / F11

def test_bloqueo_visible_y_controles_de_ventana():
    ov = _overlay()
    try:
        controles = ov.ventana_controles
        controles.mostrar()
        ov.set_bloqueo(True)
        check("F09 candado encendido cuando la posición está bloqueada",
              controles.btn_bloquear.activo)
        ov._iniciar_movimiento = lambda *_a: None
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=QPoint(200, 120))
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=QPoint(200, 120))
        check("F09 intentar mover una ventana bloqueada avisa por qué",
              "bloqueada" in ov.toast_texto, repr(ov.toast_texto))
        ov.set_bloqueo(False)
        check("F09 candado apagado al desbloquear", not controles.btn_bloquear.activo)
        check("F10 Cerrar es destructivo y está separado de Configuración",
              controles.btn_cerrar.destructivo
              and controles.btn_cerrar.x() - controles.btn_configuracion.geometry().right() > 4)
        centro_controles = controles._posicion_base()[1] + controles.ALTO / 2
        check("F11 chip, documento y controles comparten centro vertical",
              abs(centro_controles - ui_controls.FILA_SUPERIOR_CENTRO) <= 1)
        derecha = ov.width() - (controles._posicion_base()[0] + controles.width())
        check("F11 mismo margen lateral a ambos lados de la fila superior",
              derecha == ui_controls.MARGEN_LATERAL)
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- F13 / F14

def test_estado_vacio_y_carga_legibles():
    from PyQt6.QtGui import QFontMetrics
    for ancho in (320, 420, 720):
        ov = _overlay(ancho=ancho, alto=180)
        try:
            titulo, detalle, _ = ov._textos_estado_vacio()
            fm_t = QFontMetrics(ui_controls.fuente_ui(ov.cfg["font_family"], 14,
                                                      QFont.Weight.DemiBold))
            fm_d = QFontMetrics(ui_controls.fuente_ui(ov.cfg["font_family"], 9.5))
            check(f"F13 estado vacío a {ancho}px sin frases cortadas",
                  fm_t.horizontalAdvance(titulo) <= ancho - 40
                  and fm_d.horizontalAdvance(detalle) <= ancho - 40, repr((titulo, detalle)))
        finally:
            _cerrar(ov)
    ov = _overlay()
    original = guionar.document_loader.load_script
    try:
        import threading
        retener = threading.Event()

        def lento(ruta):
            retener.wait(5)
            return original(ruta)

        guionar.document_loader.load_script = lento
        ov.abrir_documento(_archivo("propuesta_final.txt", "Texto."), en_segundo_plano=True)
        titulo, detalle, _ = ov._textos_estado_vacio()
        check("F14 cargando muestra qué archivo, no la ayuda de arrastrar",
              titulo.startswith("Cargando") and detalle == "propuesta_final.txt",
              repr((titulo, detalle)))
        retener.set()
        fin = time.time() + 3
        while ov.cargando and time.time() < fin:
            _app.processEvents()
            time.sleep(0.01)
    finally:
        retener.set()
        guionar.document_loader.load_script = original
        ov._cargador.esperar(2.0)
        _cerrar(ov)


# ---------------------------------------------------------------- F15 / F16

def test_medida_de_linea_y_pixeles():
    texto = " ".join(["palabra"] * 600)
    normal = _overlay(ancho=720)
    ancha = _overlay(ancho=1600)
    try:
        normal.cargar_guion(_archivo("f15.txt", texto))
        ancha.cargar_guion(_archivo("f15b.txt", texto))

        def max_caracteres(ov):
            return max(len(" ".join(w for _, w in ln)) for ln in ov._lineas_guion if ln)

        check("F15 en ventana ancha la línea no supera ~75 caracteres",
              max_caracteres(ancha) <= 75, str(max_caracteres(ancha)))
        check("F15 en ventana normal la medida no cambia el corte",
              max_caracteres(normal) <= max_caracteres(ancha))
        xs = [x for x in ancha._x_lineas_guion if x is not None]
        mitad = ancha.width() / 2
        check("F15 con centro, el bloque queda centrado en la ventana ancha",
              all(x > SCRIPT_MARGIN_PX + 100 for x in xs) and min(xs) < mitad)

        ancha.scroll_offset = 37.4
        dpr = ancha.devicePixelRatioF()
        baselines = [ancha._baseline_guion(li) for li in range(5)]
        check("F16 baselines alineadas a píxeles de dispositivo",
              all(abs(b * dpr - round(b * dpr)) < 1e-6 for b in baselines))
        g = _posiciones(ancha)
        filas = {t[1] for t in g.trazos}
        check("F16 el texto se dibuja exactamente en esas baselines",
              filas <= {ancha._baseline_guion(li) for li in range(len(ancha._lineas_guion))})
    finally:
        _cerrar(normal)
        _cerrar(ancha)


# ---------------------------------------------------------------- motion / timers

def test_sin_timers_ni_animaciones_perpetuas():
    ov = _overlay()
    try:
        ov.abrir_documento(_archivo("motion.txt", ORACIONES))
        ov._autohide.actividad()
        ov.toggle_pause()
        _ticks(ov, 20)
        ov.toggle_pause()
        ov.set_bloqueo(True)
        ov._notificar("aviso")
        ov._autohide.salida()
        ov.abrir_configuracion().close()
        _drenar(3.2)
        activos = [t for t in ov.findChildren(QTimer) if t.isActive()]
        corriendo = [a for a in ov.findChildren(QVariantAnimation)
                     if a.state() == QAbstractAnimation.State.Running]
        check("motion: en reposo no queda ningún timer activo", not activos,
              repr([t.interval() for t in activos]))
        check("motion: en reposo no queda ninguna animación corriendo", not corriendo)
        check("motion: aviso y paneles terminan en su estado final",
              ov.toast_opacidad == 0.0 and not ov.barra.isVisible()
              and not ov.ventana_controles.isVisible())
    finally:
        _cerrar(ov)


def test_interrupcion_de_animaciones():
    ov = _overlay()
    try:
        barra = ov.barra
        barra.mostrar()
        _drenar(0.05)
        intermedio = barra._progreso
        barra.ocultar()
        _drenar(0.02)
        check("motion: ocultar a mitad de la entrada revierte desde donde estaba",
              0 < intermedio < 1 and barra._progreso <= intermedio + 0.01)
        for _ in range(20):
            barra.mostrar()
            barra.ocultar()
        barra.mostrar()
        _drenar(0.4)
        check("motion: spam de mostrar/ocultar termina en el último pedido",
              barra.isVisible() and barra._progreso == 1.0 and barra.visible_objetivo)
        ov.resize(400, 200)
        _app.processEvents()
        x, y = barra._posicion_base()
        check("motion: resize con panel visible lo reubica",
              barra.pos() == QPoint(x, y))
    finally:
        _cerrar(ov)


def main():
    test_resaltado_no_corre_palabras()
    test_contorno_con_fondo_transparente()
    test_teclado_llega_a_los_controles()
    test_sin_asignaciones_de_fuente_por_frame()
    test_paneles_sin_costo_en_reposo()
    test_parcial_fuera_del_guion()
    test_fin_del_guion_conserva_contexto()
    test_bloqueo_visible_y_controles_de_ventana()
    test_estado_vacio_y_carga_legibles()
    test_medida_de_linea_y_pixeles()
    test_sin_timers_ni_animaciones_perpetuas()
    test_interrupcion_de_animaciones()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de UI/UX pasaron.")


if __name__ == "__main__":
    main()
