"""Seguimiento visual por voz: zona de lectura, zona muerta y área útil.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_voice_viewport.py

Los asserts son invariantes (rangos y relaciones), no píxeles exactos.
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

from guionar import SCRIPT_TOP_PX, TeleprompterOverlay
from ui_controls import ControlBar


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _texto(palabras, por_oracion=12):
    partes = []
    for i in range(palabras):
        palabra = f"w{i:05d}"
        if (i + 1) % por_oracion == 0:
            palabra += "."
        partes.append(palabra)
    return " ".join(partes)


def _overlay(palabras=600, ancho=720, alto=360, voz=True):
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(descriptor, _texto(palabras).encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        ov = TeleprompterOverlay({"width": ancho, "height": alto})
        ov.resize(ancho, alto)
        ov.show()
        _app.processEvents()
        ov.cargar_guion(ruta)
        _app.processEvents()
    finally:
        os.unlink(ruta)
    if voz:
        ov.set_speaking(True)   # un productor de voz: Modo Script sigue la voz
        _asentar(ov)
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()


def _decir(ov, desde, hasta):
    # Tramos bajo max_input_chars, como frases finales reales.
    for inicio in range(desde, hasta, 100):
        ov.append_text(" ".join(
            f"w{i:05d}" for i in range(inicio, min(hasta, inicio + 100))))


def _asentar(ov, maximo=400):
    """Avanza frames simulados de 16 ms hasta que el timer se detiene."""
    ticks = 0
    while ov._timer.isActive() and ticks < maximo:
        ov._last_tick = time.monotonic() - 0.016
        ov._tick()
        ticks += 1
    return ticks


def _linea(ov, offset=None):
    """(arriba, abajo, centro) de la línea activa en el viewport."""
    _, fm, adv = ov._metricas_guion()
    offset = ov.scroll_offset if offset is None else offset
    arriba = SCRIPT_TOP_PX + ov._linea_visual_del_cursor() * adv - offset
    return arriba, arriba + fm.height(), arriba + fm.height() / 2


def _en_zona(ov):
    _, _, centro = _linea(ov)
    zona_min, _, zona_max = ov._zona_lectura()
    return zona_min - 0.5 <= centro <= zona_max + 0.5


def _visible_en_area(ov):
    arriba, abajo, _ = _linea(ov)
    area_arriba, area_abajo = ov._area_lectura()
    return arriba >= area_arriba - 0.5 and abajo <= area_abajo + 0.5


def _en_clamp(ov):
    return (ov.scroll_target <= 0.01
            or abs(ov.scroll_target - ov._scroll_maximo_voz()) < 0.01)


def _techo_barra(ov):
    return ov.height() - ControlBar.ALTO - ControlBar.MARGEN_INFERIOR


def _palabras_por_linea(ov, li):
    return [idx for idx, _ in ov._lineas_guion[li]]


# ---------------------------------------------------------------- casos

def test_bug_fisico_texto_final_despues_de_vad_false():
    ov = _overlay()
    try:
        ov.set_speaking(False)          # ParlAR cierra la frase...
        _decir(ov, 0, 120)              # ...y el final llega después
        _asentar(ov)
        check("texto final tras vad:false igual mueve el viewport",
              ov.scroll_offset > 0 and _en_zona(ov) and _visible_en_area(ov))
        ov.barra.mostrar()
        ov.hover_paused = True          # puntero encima: barra visible
        _decir(ov, 120, 200)
        _asentar(ov)
        check("con puntero encima y barra visible sigue acompañando",
              _visible_en_area(ov) and _linea(ov)[1] <= _techo_barra(ov))
    finally:
        _cerrar(ov)


def test_zona_muerta_y_limites():
    ov = _overlay()
    try:
        _decir(ov, 0, 60)
        _asentar(ov)
        check("tras avanzar la línea queda en la zona", _en_zona(ov))
        # Palabras de la misma línea: el viewport no se mueve.
        li = ov._linea_visual_del_cursor()
        idx = _palabras_por_linea(ov, li)
        objetivo = ov.scroll_target
        cambios = 0
        for i in idx[1:]:
            _decir(ov, i - 1, i)
            cambios += ov.scroll_target != objetivo
        check("1 dentro de la zona muerta no hay scroll", cambios == 0
              and not ov._timer.isActive())

        # 2. Avanzar línea por línea: sólo se mueve al salir por abajo.
        movimientos, lineas = 0, 0
        anterior = ov.scroll_target
        for _ in range(8):
            li = ov._linea_visual_del_cursor()
            siguiente = _palabras_por_linea(ov, li + 1)[0] if \
                ov._lineas_guion[li + 1] else _palabras_por_linea(ov, li + 2)[0]
            _decir(ov, ov.guion.cursor, siguiente + 1)
            lineas += 1
            if ov.scroll_target != anterior:
                movimientos += 1
                check("2 al cruzar el límite inferior el viewport sube",
                      ov.scroll_target > anterior)
                anterior = ov.scroll_target
            _asentar(ov)
            if not (_en_zona(ov) and _visible_en_area(ov)):
                check("2 la línea vuelve a la zona", False)
                break
        check("2 la zona muerta absorbe parte del avance",
              0 < movimientos <= lineas, f"{movimientos}/{lineas}")

        # 3. Corrección hacia atrás (límite superior).
        antes = ov.scroll_target
        ov.saltar_oracion(-3)
        check("3 al cruzar el límite superior el viewport baja",
              ov.scroll_target < antes and (_en_zona(ov) or _en_clamp(ov))
              and _visible_en_area(ov))
    finally:
        _cerrar(ov)


def test_barra_visible_y_controles():
    for alto in (200, 260, 360):
        ov = _overlay(alto=alto)
        try:
            ov.barra.mostrar()
            _decir(ov, 0, 300)
            _asentar(ov)
            check(f"4 alto {alto}: la línea no queda detrás de la barra",
                  _linea(ov)[1] <= _techo_barra(ov) and _visible_en_area(ov),
                  f"{_linea(ov)[1]:.1f} > {_techo_barra(ov)}")
        finally:
            _cerrar(ov)

    ov = _overlay(alto=260)
    try:
        # Línea en la parte baja de la zona con la barra oculta.
        _decir(ov, 0, 200)
        _asentar(ov)
        _, zona_max, _ = (None, ov._zona_lectura()[2], None)
        for _ in range(40):
            if _linea(ov)[2] >= zona_max - 4:
                break
            _decir(ov, ov.guion.cursor, ov.guion.cursor + 1)
            _asentar(ov)
        ov.barra.mostrar()
        ticks = _asentar(ov)
        check("5 la barra aparece: la línea sigue visible por encima",
              _linea(ov)[1] <= _techo_barra(ov) and _visible_en_area(ov))
        check("5 el ajuste es animado y corto", ticks <= 30, f"{ticks} ticks")
        objetivo, offset = ov.scroll_target, ov.scroll_offset
        ov.barra.ocultar()
        _app.processEvents()
        check("6 la barra desaparece: el texto no salta",
              ov.scroll_target == objetivo and ov.scroll_offset == offset
              and not ov._timer.isActive())
    finally:
        _cerrar(ov)


def test_clamps_inicio_final_y_documentos():
    ov = _overlay()
    try:
        check("7 inicio: sin espacio vacío artificial arriba",
              ov.scroll_target == 0.0 and ov.scroll_offset == 0.0)
        _decir(ov, 0, 3)
        _asentar(ov)
        check("7 primeras palabras no fuerzan el ancla",
              ov.scroll_target == 0.0 and _visible_en_area(ov))
        _decir(ov, 3, 600)
        _asentar(ov)
        _, abajo_area = ov._area_lectura()
        _, fm, adv = ov._metricas_guion()
        ultima = ov._ultima_linea_guion()
        fondo = SCRIPT_TOP_PX + ultima * adv + fm.height() - ov.scroll_offset
        check("8 final: clamp sin medio viewport vacío",
              ov.script_terminado() and _en_clamp(ov)
              and abs(fondo - abajo_area) <= 1.0, f"{fondo:.1f}/{abajo_area}")
        check("8 final: la última línea queda visible", _visible_en_area(ov))
    finally:
        _cerrar(ov)

    corto = _overlay(palabras=20)
    try:
        _decir(corto, 0, 20)
        _asentar(corto)
        check("9 documento corto no scrollea",
              corto.scroll_target == 0.0 and corto.scroll_offset == 0.0)
    finally:
        _cerrar(corto)

    largo = _overlay(palabras=50000)
    try:
        inicio = time.perf_counter()
        for paso in range(0, 3000, 30):
            _decir(largo, paso, paso + 30)
        duracion = time.perf_counter() - inicio
        _asentar(largo)
        check("10 documento largo sigue la voz",
              largo.guion.cursor == 3000 and _en_zona(largo)
              and _visible_en_area(largo))
        check("10 documento largo: 100 finales en tiempo acotado",
              duracion < 2.0, f"{duracion:.2f}s")
    finally:
        _cerrar(largo)


def test_parciales_y_avance_grande():
    ov = _overlay()
    try:
        _decir(ov, 0, 40)
        _asentar(ov)
        objetivo, offset = ov.scroll_target, ov.scroll_offset
        for i in range(200):
            ov.set_partial(f"w{40 + i % 7:05d} hipotesis {i}")
        _decir(ov, 38, 40)            # repetición: corrección sin avance
        check("11 parciales y correcciones no generan jitter",
              ov.scroll_target == objetivo and ov.scroll_offset == offset
              and not ov._timer.isActive())
        _decir(ov, 40, 340)
        ticks = _asentar(ov)
        check("12 avance grande reposiciona en la zona",
              _en_zona(ov) and _visible_en_area(ov))
        check("12 sin animación larga", ticks <= 30, f"{ticks} ticks")
    finally:
        _cerrar(ov)


def test_resize_y_alineacion():
    ov = _overlay(alto=360)
    try:
        _decir(ov, 0, 250)
        _asentar(ov)
        cursor = ov.guion.cursor
        for ancho, alto in ((720, 200), (720, 600), (360, 360),
                            (1200, 360), (720, 360)):
            ov.resize(ancho, alto)
            _app.processEvents()
            check(f"13 resize {ancho}x{alto} conserva la línea semántica",
                  ov.guion.cursor == cursor and _visible_en_area(ov)
                  and (_en_zona(ov) or _en_clamp(ov))
                  and not ov._timer.isActive())
        for alineacion in ("left", "center", "right"):
            objetivo = ov.scroll_target
            ov.set_alineacion(alineacion)
            check(f"14 alineación {alineacion}: viewport estable",
                  ov.guion.cursor == cursor and ov.scroll_target == objetivo
                  and _en_zona(ov))
    finally:
        _cerrar(ov)


def test_pin_lock_opacidad_y_reposo():
    ov = _overlay()
    try:
        _decir(ov, 0, 150)
        _asentar(ov)
        estado = (ov.guion.cursor, ov.scroll_target, ov.scroll_offset)
        for accion in (lambda: ov.set_siempre_encima(False),
                       lambda: ov.set_siempre_encima(True),
                       lambda: ov.set_bloqueo(True),
                       lambda: ov.set_bloqueo(False),
                       lambda: ov.set_opacidad(0.2),
                       lambda: ov.set_opacidad(0.9)):
            accion()
            _app.processEvents()
        check("15 pin/lock/opacidad no alteran el viewport",
              (ov.guion.cursor, ov.scroll_target, ov.scroll_offset) == estado)
        check("16 sin timer activo después de estabilizar",
              not ov._timer.isActive())
        ov.set_speaking(True)
        _asentar(ov)
        check("16 VAD en reposo no deja animación corriendo",
              not ov._timer.isActive())
    finally:
        _cerrar(ov)


def test_modos_y_navegacion():
    auto = _overlay(voz=False)
    try:
        auto.saltar_oracion(3)
        linea = auto._linea_visual_del_cursor()
        adv = auto._line_advance_guion_px()
        check("17 auto-scroll conserva su ancla", not auto._seguimiento_voz()
              and abs(auto.scroll_target - max(0.0, (linea - 1) * adv)) < 0.01)
        antes = auto.scroll_offset
        for _ in range(30):
            auto._last_tick = time.monotonic() - 0.05
            auto._tick()
        check("17 auto-scroll sigue avanzando", auto.scroll_offset > antes)
    finally:
        _cerrar(auto)

    ov = _overlay()
    try:
        _decir(ov, 0, 600)
        _asentar(ov)
        ov._reiniciar_script()
        check("18 replay vuelve al comienzo sin scroll residual",
              ov.guion.cursor == 0 and ov.scroll_offset == 0.0
              and ov.scroll_target == 0.0)
        for _ in range(6):
            ov.saltar_oracion(1)
            check("19 PageDown revela la línea en la zona al instante",
                  ov.scroll_offset == ov.scroll_target and _visible_en_area(ov)
                  and (_en_zona(ov) or _en_clamp(ov)))
        ov.saltar_oracion(-2)
        check("19 PageUp también", ov.scroll_offset == ov.scroll_target
              and _visible_en_area(ov) and (_en_zona(ov) or _en_clamp(ov)))

        cursor, offset = ov.guion.cursor, ov.scroll_offset
        ov.set_voice_producers(0)
        check("20 voz desconectada vuelve a avance automático sin salto",
              not ov._seguimiento_voz() and ov.scroll_offset == offset)
        recorrido = []
        for _ in range(40):
            ov._last_tick = time.monotonic() - 0.05
            ov._tick()
            recorrido.append(ov.scroll_offset)
        check("20 el avance automático continúa desde lo visible",
              recorrido[0] >= offset - 0.01
              and all(b >= a for a, b in zip(recorrido, recorrido[1:]))
              and ov.guion.cursor >= cursor)
    finally:
        _cerrar(ov)


def main():
    test_bug_fisico_texto_final_despues_de_vad_false()
    test_zona_muerta_y_limites()
    test_barra_visible_y_controles()
    test_clamps_inicio_final_y_documentos()
    test_parciales_y_avance_grande()
    test_resize_y_alineacion()
    test_pin_lock_opacidad_y_reposo()
    test_modos_y_navegacion()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de seguimiento de voz pasaron.")


if __name__ == "__main__":
    main()
