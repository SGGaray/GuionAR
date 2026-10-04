"""Transcript en vivo sin guion cargado (Phase 8E).

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_live_transcript.py

Invariantes (baselines, áreas, conteos), no píxeles exactos.
"""

import os
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

import guionar as g
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


def overlay(ancho=720, alto=320):
    ov = TeleprompterOverlay({"width": ancho, "height": alto})
    ov.resize(ancho, alto)
    ov.show()
    _app.processEvents()
    return ov


def asentar(ov, maximo=400):
    ticks = 0
    while ov._timer.isActive() and ticks < maximo:
        ov._last_tick = time.monotonic() - 0.016
        ov._tick()
        ticks += 1
    return ticks


def frase(ov, *parciales, final=None, vad=True):
    if vad:
        ov.set_speaking(True)
    for parcial in parciales:
        ov.set_partial(parcial)
    ov.set_speaking(False)
    if final is not None:
        ov.append_text(final)


def baselines(ov):
    fm = ov._metricas_guion()[1]
    return [ov._baseline_guion(li, fm) for li in range(len(ov._tx_lineas))]


def sin_solapamiento(ov):
    """Cada línea en su propia baseline, separadas por el avance completo,
    y ninguna más ancha que la medida."""
    fm, _, disponible = ov._medida_transcript()
    adv = ov._line_advance_guion_px()
    ys = baselines(ov)
    separadas = all(abs((b - a) - adv) < 1.01 for a, b in zip(ys, ys[1:]))
    anchos = all(fm.horizontalAdvance(t) <= disponible
                 for _, t, _ in ov._tx_lineas)
    return separadas and anchos


def linea_actual_visible(ov):
    fm = ov._metricas_guion()[1]
    ultima = len(ov._tx_lineas) - 1
    arriba = SCRIPT_TOP_PX + ultima * ov._line_advance_guion_px() - ov.scroll_offset
    area_arriba, area_abajo = ov._area_lectura()
    return area_arriba - 0.5 <= arriba and arriba + fm.height() <= area_abajo + 0.5


def textos(ov):
    return [t for _, t, _ in ov._tx_lineas]


LARGA = ("esta es una frase bastante larga que no entra en una sola línea "
         "del teleprompter y tiene que repartirse en varias líneas sin pisar "
         "nada de lo que ya estaba escrito arriba")


# ---------------------------------------------------------------- casos

def test_parcial_y_final():
    ov = overlay()
    try:
        ov.set_speaking(True)
        ov.set_partial("funciona")
        check("1 sin documento, el parcial aparece",
              not ov._estado_vacio() and textos(ov) == ["funciona"])
        ov.set_partial("funciona hablar")
        ov.set_partial("funciona hablar mientras")
        check("2/3 el parcial nuevo reemplaza, no concatena",
              textos(ov) == ["funciona hablar mientras"])
        ov.set_partial("funciona hablar mejor")
        check("24 corrección de Whisper sin restos",
              textos(ov) == ["funciona hablar mejor"])
        ov.set_speaking(False)
        check("13 vad:false conserva el parcial",
              textos(ov) == ["funciona hablar mejor"])
        ov.append_text("Funciona hablar mejor.")
        check("4/14 el final confirma al parcial en su lugar",
              textos(ov) == ["Funciona hablar mejor."]
              and ov.partial_text == "" and list(ov.transcript) == [
                  "Funciona hablar mejor."])
        frase(ov, "segunda frase", final="Segunda frase.")
        check("5/6 el final pasa al historial y la siguiente va debajo",
              textos(ov) == ["Funciona hablar mejor.", "Segunda frase."]
              and sin_solapamiento(ov))
        ov.set_speaking(True)
        ov.set_partial("")
        check("15 parcial vacío no borra confirmados",
              len(ov.transcript) == 2 and ov.partial_text == "")
    finally:
        ov.close()


def test_historial_sube_sin_solapar():
    ov = overlay(alto=260)
    try:
        primeras = []
        for i in range(12):
            frase(ov, f"frase numero {i} en curso",
                  final=f"Frase número {i} confirmada.")
            asentar(ov)
            primeras.append(baselines(ov)[0])
            if not (sin_solapamiento(ov) and linea_actual_visible(ov)):
                check("8 sin solapamiento con frase actual visible", False,
                      f"frase {i}")
                break
        else:
            check("8 nunca hay texto sobre texto y la frase actual se ve", True)
        check("7 el historial viejo sube",
              primeras[-1] < primeras[0] and all(
                  b <= a + 0.01 for a, b in zip(primeras, primeras[1:])))
        _, fm, adv = ov._metricas_guion()
        arriba, abajo = ov._area_lectura()
        centro = baselines(ov)[-1] - fm.ascent() + fm.height() / 2
        check("anclaje: frase actual en la mitad inferior, con espacio debajo",
              arriba + 0.45 * (abajo - arriba) <= centro
              <= arriba + 0.8 * (abajo - arriba))
    finally:
        ov.close()


def test_wrap_y_resize():
    ov = overlay()
    try:
        frase(ov, final="Primera.")
        frase(ov, LARGA[:60], LARGA)
        check("9/10 una frase larga ocupa varias líneas sin solapar",
              sum(1 for i, _, _ in ov._tx_lineas if i == -1) > 1
              and sin_solapamiento(ov))
        ov.append_text(LARGA)
        for ancho, alto in ((360, 200), (1200, 400), (520, 600)):
            ov.resize(ancho, alto)
            _app.processEvents()
            check(f"11 resize {ancho}x{alto}: reflow sin solapar, frase visible",
                  sin_solapamiento(ov) and linea_actual_visible(ov)
                  and list(ov.transcript)[-1] == LARGA
                  and not ov._timer.isActive())
    finally:
        ov.close()


def test_barra_y_preferencias():
    ov = overlay(alto=240)
    try:
        ov.barra.mostrar()
        for i in range(8):
            frase(ov, f"hablando {i}", final=f"Línea dicha número {i}.")
            asentar(ov)
        fm = ov._metricas_guion()[1]
        fondo = baselines(ov)[-1] + fm.descent()
        check("12 con la barra visible la frase actual queda encima",
              fondo <= ov.height() - ControlBar.ALTO - ControlBar.MARGEN_INFERIOR
              and linea_actual_visible(ov))
        for numero, alineacion in ((22, "left"), (23, "center"), (24, "right")):
            ov.set_alineacion(alineacion)
            xs = [x for _, _, x in ov._tx_lineas]
            ok = sin_solapamiento(ov) and linea_actual_visible(ov)
            if alineacion == "left":
                ok = ok and all(x == g.SCRIPT_MARGIN_PX for x in xs)
            check(f"{numero} alineación {alineacion}", ok)
        lineas = len(ov._tx_lineas)
        ov.set_tamano_texto(ov.cfg["font_size_context"] + 10)
        check("25 tamaño de texto: reflow sin solapar",
              sin_solapamiento(ov) and linea_actual_visible(ov)
              and len(ov._tx_lineas) >= lineas)
        estado = (tuple(ov.transcript), ov.scroll_target)
        ov.set_opacidad(0.2)
        _app.processEvents()
        imagen = QImage(ov.size(), QImage.Format.Format_ARGB32)
        imagen.fill(0)
        pintor = QPainter(imagen)
        ov.render(pintor)
        pintor.end()
        check("26 opacidad baja: pinta con contorno, sin tocar el estado",
              (tuple(ov.transcript), ov.scroll_target) == estado
              and ov._buffer_contorno is not None)
        ov.set_siempre_encima(False)
        ov.set_bloqueo(True)
        ov.set_siempre_encima(True)
        ov.set_bloqueo(False)
        _app.processEvents()
        check("27 pin/lock sin efecto",
              (tuple(ov.transcript), ov.scroll_target) == estado)
    finally:
        ov.close()


def test_desconexion_clear_y_documento():
    ov = overlay()
    try:
        frase(ov, "uno", final="Uno.")
        ov.set_speaking(True)
        ov.set_partial("dos en curso")
        ov.set_voice_producers(0)
        check("16 desconexión: conserva finales y sólo cae el provisional",
              list(ov.transcript) == ["Uno."] and ov.partial_text == ""
              and textos(ov) == ["Uno."])
        frase(ov, "tres", final="Tres.")
        check("17 reconexión: la nueva frase sigue debajo, sin replay",
              textos(ov) == ["Uno.", "Tres."])
        ov.set_partial("provisional")
        ov.clear()
        check("18 clear limpia el transcript (semántica previa) y el provisional",
              not ov.transcript and ov.partial_text == "" and ov._estado_vacio()
              and ov.scroll_offset == ov.scroll_target == 0.0)

        frase(ov, final="Antes del documento.")
        descriptor, ruta = tempfile.mkstemp(suffix=".txt")
        os.write(descriptor, b"Este es el guion cargado. Tiene texto propio.")
        os.close(descriptor)
        try:
            ov.cargar_guion(ruta)
            _app.processEvents()
            with open(ruta, "rb") as f:
                intacto = f.read() == b"Este es el guion cargado. Tiene texto propio."
        finally:
            os.unlink(ruta)
        check("19 cargar documento sale del transcript sin mezclar",
              ov._hay_guion() and not ov.transcript and not ov._modo_transcript()
              and intacto)
        ov.append_text("Este es")
        check("30 con guion, el final usa el matching de siempre",
              ov.guion.cursor == 2 and not ov.transcript)

        nuevo = overlay()
        try:
            frase(nuevo, final="Transcript nuevo.")
            check("20 sin documento se arranca un transcript nuevo",
                  list(nuevo.transcript) == ["Transcript nuevo."])
        finally:
            nuevo.close()
    finally:
        ov.close()


def test_limite_de_memoria():
    ov = overlay()
    try:
        for i in range(g.TRANSCRIPT_MAX_FRASES + 50):
            ov.append_text(f"Frase {i} del dictado largo.")
        check("21 límite de frases: se descarta lo más viejo",
              len(ov.transcript) == g.TRANSCRIPT_MAX_FRASES
              and ov.transcript[-1].startswith(f"Frase {g.TRANSCRIPT_MAX_FRASES + 49}")
              and ov.transcript[0].startswith("Frase 50 "))
        asentar(ov)
        check("21 tras acotar sigue sin solapar y con la frase actual visible",
              sin_solapamiento(ov) and linea_actual_visible(ov))
        ov.clear()
        larga = "palabra " * 400
        for _ in range(30):
            ov.append_text(larga)
        check("21 límite de caracteres",
              sum(len(f) for f in ov.transcript) <= g.TRANSCRIPT_MAX_CARACTERES)
    finally:
        ov.close()


def test_rendimiento_y_timers():
    ov = overlay()
    try:
        inicio = time.perf_counter()
        for i in range(100):
            ov.set_speaking(True)
            for k in range(1, 4):
                ov.set_partial(" ".join(f"palabra{i}_{j}" for j in range(k * 3)))
            ov.set_speaking(False)
            ov.append_text(" ".join(f"Palabra{i}_{j}" for j in range(9)) + ".")
        duracion = time.perf_counter() - inicio
        asentar(ov)
        check("28 100 frases rápidas (400 eventos) en tiempo acotado",
              duracion < 2.0 and sin_solapamiento(ov), f"{duracion:.2f}s")
        check("29 sin timer residual tras estabilizar",
              not ov._timer.isActive())
        tiempos = []
        for n in (50, 500):
            ov.clear()
            for i in range(n):
                ov.append_text(f"Frase número {i} con algo de texto adicional.")
            asentar(ov)
            muestras = []
            for k in range(30):
                t0 = time.perf_counter()
                ov.set_partial(f"parcial en curso {k} con varias palabras")
                muestras.append(time.perf_counter() - t0)
            imagen = QImage(ov.size(), QImage.Format.Format_ARGB32)
            t0 = time.perf_counter()
            for _ in range(10):
                imagen.fill(0)
                pintor = QPainter(imagen)
                ov.render(pintor)
                pintor.end()
            tiempos.append((n, statistics.median(muestras) * 1000,
                            (time.perf_counter() - t0) / 10 * 1000))
        for n, parcial, pintura in tiempos:
            print(f"      {n} frases: parcial {parcial:.2f} ms, pintura {pintura:.2f} ms")
        check("parcial no escala con el historial (50 vs 500 frases)",
              tiempos[1][1] < max(2.0, tiempos[0][1] * 4), repr(tiempos))
        ov.set_speaking(False)
        asentar(ov)     # el último parcial pudo dejar una animación en curso
        activos = [t for t in ov.findChildren(QTimer) if t.isActive()]
        check("29 sin timers activos en reposo", not activos, repr(activos))
    finally:
        ov.close()


def main():
    test_parcial_y_final()
    test_historial_sube_sin_solapar()
    test_wrap_y_resize()
    test_barra_y_preferencias()
    test_desconexion_clear_y_documento()
    test_limite_de_memoria()
    test_rendimiento_y_timers()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de transcript en vivo pasaron.")


if __name__ == "__main__":
    main()
