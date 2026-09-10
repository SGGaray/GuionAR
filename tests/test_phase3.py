"""Regresiones de remediación Fase 3: layout y viewport de Modo Script.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_phase3.py
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication

from guionar import TeleprompterOverlay


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
MARGEN_SCRIPT = 20


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _procesar_eventos():
    _app.processEvents()


def _overlay_con_guion(texto, ancho=720):
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(descriptor, texto.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        ov = TeleprompterOverlay({"width": ancho})
        ov.resize(ancho, ov.height())
        ov.show()
        _procesar_eventos()
        ov.cargar_guion(ruta)
        _procesar_eventos()
        return ov
    finally:
        os.unlink(ruta)


def _cerrar(ov):
    ov.close()
    _procesar_eventos()


def _linea_cursor(ov):
    if ov.guion.cursor == len(ov.guion.palabras_norm):
        return max(i for i, linea in enumerate(ov._lineas_guion)
                   if linea is not None)
    return ov._linea_por_indice[ov.guion.cursor]


def _target_esperado(ov):
    return max(0.0, (_linea_cursor(ov) - 1) * ov._line_advance_guion_px())


def _target_alineado(ov):
    return abs(ov.scroll_target - _target_esperado(ov)) < 0.01


def _viewport_alineado(ov):
    return _target_alineado(ov) and abs(ov.scroll_offset - ov.scroll_target) < 0.01


def _font_script(ov, negrita=False):
    fuente = QFont(ov.cfg["font_family"], ov.cfg["font_size_context"])
    if negrita:
        fuente.setWeight(QFont.Weight.Bold)
    return fuente


def _ancho_linea_pintada(ov, linea):
    if linea is None:
        return 0
    ancho = 0
    for idx, palabra in linea:
        fm = QFontMetrics(_font_script(ov, idx == ov.guion.cursor))
        ancho += fm.horizontalAdvance(palabra + " ")
    return ancho


def _lineas_caben(ov):
    disponible = ov.width() - 2 * MARGEN_SCRIPT
    return all(_ancho_linea_pintada(ov, linea) <= disponible
               for linea in ov._lineas_guion if linea is not None)


def _asentar_automatico(ov, max_ticks=1000):
    for _ in range(max_ticks):
        if abs(ov.scroll_target - ov.scroll_offset) <= 0.5:
            ov._tick()
            break
        ov._last_tick = time.monotonic() - 0.05
        ov._tick()
    return abs(ov.scroll_target - ov.scroll_offset) < 0.01


def _palabras(cantidad):
    return [f"palabra{i:03d}" for i in range(cantidad)]


def _guion_oraciones(cantidad=5):
    return " ".join(
        f"Inicio{i} contenido amplio para obligar varias líneas visuales "
        f"en una ventana angosta número {i}."
        for i in range(cantidad)
    )


def test_eof_resuelve_final_del_guion():
    corto = _overlay_con_guion("uno dos tres")
    try:
        corto.append_text("uno dos tres")
        check("guion corto llega al cursor terminal",
              corto.guion.cursor == len(corto.guion.palabras_norm))
        check("EOF corto resuelve su última línea", _target_alineado(corto))
    finally:
        _cerrar(corto)

    palabras = _palabras(90)
    activo = _overlay_con_guion(" ".join(palabras), ancho=320)
    try:
        activo.set_speaking(True)
        activo.append_text(" ".join(palabras))
        check("final exacto deja cursor N",
              activo.guion.cursor == len(activo.guion.palabras_norm))
        check("EOF multi-pantalla apunta a sección final",
              _target_alineado(activo) and activo.scroll_target > 0,
              f"target={activo.scroll_target:.1f}")
        check("animación automática puede asentarse en EOF",
              _asentar_automatico(activo))
        target_final = activo.scroll_target
        activo.append_text("texto posterior sin match")
        check("texto posterior a EOF conserva ancla final",
              activo.guion.cursor == len(activo.guion.palabras_norm)
              and activo.scroll_target == target_final)
    finally:
        _cerrar(activo)

    inactivo = _overlay_con_guion(" ".join(palabras), ancho=320)
    try:
        inactivo.set_speaking(True)
        inactivo.append_text(" ".join(palabras[:-1]))
        _asentar_automatico(inactivo)
        offset_antes = inactivo.scroll_offset
        inactivo.set_speaking(False)
        inactivo.append_text(palabras[-1])
        check("EOF con VAD inactivo mantiene target final",
              _target_alineado(inactivo) and inactivo.scroll_target > 0)
        check("EOF con VAD inactivo no salta al comienzo",
              inactivo.scroll_offset == offset_antes and offset_antes > 0)
    finally:
        _cerrar(inactivo)


def test_layout_por_pixeles_y_token_patologico():
    texto = (
        "Árbol pingüino acción ¿qué? WWWWWW iiiii programación audiovisual "
        "cámara corazón — puntuación amplia " * 8
    )
    mediciones = {}
    for ancho in (320, 720, 1000):
        ov = _overlay_con_guion(texto, ancho=ancho)
        try:
            mediciones[ancho] = len(ov._lineas_guion)
            check(f"líneas caben en {ancho}px", _lineas_caben(ov),
                  f"disponible={ancho - 40}, máximo="
                  f"{max(_ancho_linea_pintada(ov, l) for l in ov._lineas_guion):.1f}")
            check(f"layout {ancho}px conserva todos los índices",
                  len(ov._linea_por_indice) == len(ov.guion.palabras_norm))
        finally:
            _cerrar(ov)
    check("layout angosto produce más líneas que normal/ancho",
          mediciones[320] > mediciones[720] > mediciones[1000],
          repr(mediciones))

    token = "W" * 500
    ov = _overlay_con_guion(token, ancho=320)
    try:
        check("token patológico mantiene una palabra semántica",
              len(ov.guion.palabras_norm) == 1
              and ov.guion.originales[0][1] == token)
        check("token patológico conserva mapping", ov._linea_por_indice == {0: 0})
        check("fallback visual de token patológico cabe", _lineas_caben(ov),
              f"ancho={_ancho_linea_pintada(ov, ov._lineas_guion[0])}")
    finally:
        _cerrar(ov)


def test_resize_reflow_preserva_cursor_y_ancla():
    palabras = _palabras(70)
    ov = _overlay_con_guion(" ".join(palabras), ancho=320)
    try:
        ov.append_text(" ".join(palabras[:42]))
        cursor = ov.guion.cursor
        lineas_angostas = len(ov._lineas_guion)

        ov.resize(1000, ov.height())
        _procesar_eventos()
        lineas_anchas = len(ov._lineas_guion)
        check("resize angosto→ancho hace reflow",
              lineas_anchas < lineas_angostas,
              f"{lineas_angostas}→{lineas_anchas}")
        check("resize ancho conserva cursor semántico", ov.guion.cursor == cursor)
        check("resize ancho reancla viewport inmediatamente", _viewport_alineado(ov))
        check("líneas anchas caben", _lineas_caben(ov))

        ov.resize(320, ov.height())
        _procesar_eventos()
        check("resize ancho→angosto hace reflow",
              len(ov._lineas_guion) > lineas_anchas)
        check("resize angosto conserva cursor y ancla",
              ov.guion.cursor == cursor and _viewport_alineado(ov))
        check("líneas reenvueltas caben", _lineas_caben(ov))

        ov.append_text(" ".join(palabras[cursor:]))
        check("completar tras reflow mantiene EOF final",
              ov.guion.cursor == len(palabras) and _target_alineado(ov)
              and ov.scroll_target > 0)
    finally:
        _cerrar(ov)


def test_cambio_fuente_reflow_preserva_cursor_y_ancla():
    palabras = _palabras(70)
    ov = _overlay_con_guion(" ".join(palabras), ancho=520)
    try:
        ov.append_text(" ".join(palabras[:35]))
        cursor = ov.guion.cursor
        lineas_base = len(ov._lineas_guion)

        ov._change_font(-12)
        lineas_chicas = len(ov._lineas_guion)
        check("bajar fuente reduce líneas visuales", lineas_chicas < lineas_base,
              f"{lineas_base}→{lineas_chicas}")
        check("bajar fuente conserva cursor y ancla",
              ov.guion.cursor == cursor and _viewport_alineado(ov))
        check("líneas con fuente chica caben", _lineas_caben(ov))

        ov._change_font(+24)
        lineas_grandes = len(ov._lineas_guion)
        check("subir fuente aumenta líneas visuales", lineas_grandes > lineas_chicas,
              f"{lineas_chicas}→{lineas_grandes}")
        check("subir fuente conserva cursor y ancla",
              ov.guion.cursor == cursor and _viewport_alineado(ov))
        check("líneas con fuente grande caben", _lineas_caben(ov))

        cursor_antes = ov.guion.cursor
        ov.append_text(palabras[cursor_antes])
        check("matching continúa estable después de cambio de fuente",
              ov.guion.cursor == cursor_antes + 1)
    finally:
        _cerrar(ov)


def test_navegacion_manual_visible_bajo_pausas():
    estados = (
        (False, False, False, "VAD=false"),
        (True, True, False, "pausa manual"),
        (True, False, True, "pausa hover"),
        (False, True, True, "pausas combinadas"),
    )
    for speaking, paused, hover, nombre in estados:
        ov = _overlay_con_guion(_guion_oraciones(), ancho=320)
        try:
            ov.speaking = speaking
            ov.paused = paused
            ov.hover_paused = hover
            flags = (ov.speaking, ov.paused, ov.hover_paused)
            ov.saltar_oracion(1)
            cursor_abajo = ov.guion.cursor
            check(f"PageDown cambia cursor con {nombre}", cursor_abajo > 0)
            check(f"PageDown revela posición con {nombre}", _viewport_alineado(ov))
            check(f"PageDown conserva flags con {nombre}",
                  flags == (ov.speaking, ov.paused, ov.hover_paused))
            ov.saltar_oracion(-1)
            check(f"PageUp vuelve y revela posición con {nombre}",
                  ov.guion.cursor < cursor_abajo and _viewport_alineado(ov))
        finally:
            _cerrar(ov)

    ov = _overlay_con_guion(_guion_oraciones(), ancho=720)
    try:
        ov.saltar_oracion(-1)
        check("PageUp respeta borde inicial",
              ov.guion.cursor == 0 and _viewport_alineado(ov))
        for _ in range(20):
            ov.saltar_oracion(1)
        cursor_final = ov.guion.cursor
        ov.saltar_oracion(1)
        check("PageDown respeta borde final",
              ov.guion.cursor == cursor_final and _viewport_alineado(ov))
        ov.resize(320, ov.height())
        _procesar_eventos()
        ov.saltar_oracion(-1)
        check("navegación tras reflow mantiene cursor visible",
              ov.guion.cursor < cursor_final and _viewport_alineado(ov))
    finally:
        _cerrar(ov)


def test_clear_segun_modo():
    dictado = TeleprompterOverlay()
    try:
        dictado.append_text("contenido de dictado")
        dictado.set_partial("hipótesis")
        dictado.scroll_offset = dictado.scroll_target = 50.0
        dictado.clear()
        check("clear dictado conserva semántica existente",
              not dictado.lines and dictado.current_line == ""
              and dictado.partial_text == ""
              and dictado.scroll_offset == dictado.scroll_target == 0.0)
    finally:
        _cerrar(dictado)

    palabras = _palabras(75)
    ov = _overlay_con_guion(" ".join(palabras), ancho=320)
    try:
        guion_cargado = ov.guion
        ov.clear()
        check("clear script al inicio conserva guion/cursor",
              ov.guion is guion_cargado and ov.guion.cursor == 0)
        check("clear script al inicio conserva ancla", _viewport_alineado(ov))

        ov.append_text(" ".join(palabras[:50]))
        cursor = ov.guion.cursor
        ov.set_partial("hipótesis transitoria")
        ov.clear()
        check("clear script profundo conserva guion/cursor",
              ov.guion is guion_cargado and ov.guion.cursor == cursor)
        check("clear script elimina partial", ov.partial_text == "")
        check("clear script profundo conserva viewport", _viewport_alineado(ov))

        ov.resize(720, ov.height())
        _procesar_eventos()
        cursor_resize = ov.guion.cursor
        ov.clear()
        check("clear después de resize conserva ancla nueva",
              ov.guion.cursor == cursor_resize and _viewport_alineado(ov))

        ov.append_text(palabras[cursor_resize])
        check("siguiente final continúa desde cursor retenido",
              ov.guion.cursor == cursor_resize + 1)

        ov.append_text(" ".join(palabras[ov.guion.cursor:-1]))
        cursor_cerca_eof = ov.guion.cursor
        ov.clear()
        check("clear cerca de EOF conserva posición",
              cursor_cerca_eof == len(palabras) - 1
              and ov.guion.cursor == cursor_cerca_eof and _viewport_alineado(ov))
        ov.append_text(palabras[-1])
        ov.clear()
        check("clear en cursor terminal conserva EOF",
              ov.guion.cursor == len(palabras) and _viewport_alineado(ov)
              and ov.scroll_target > 0)
    finally:
        _cerrar(ov)


def main():
    test_eof_resuelve_final_del_guion()
    test_layout_por_pixeles_y_token_patologico()
    test_resize_reflow_preserva_cursor_y_ancla()
    test_cambio_fuente_reflow_preserva_cursor_y_ancla()
    test_navegacion_manual_visible_bajo_pausas()
    test_clear_segun_modo()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de Fase 3 pasaron.")


if __name__ == "__main__":
    main()
