"""Seguimiento en vivo por parciales: cursor provisional vs confirmado.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_partial_follow.py

Asserts por invariantes (posiciones relativas, rangos), no píxeles exactos.
"""

import os
import random
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import guion as guion_mod
from guion import Guion, normalizar_texto
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


FRASE = ("Todo ese andamiaje tiene un objetivo claro que no es otro que "
         "permitir que la persona que habla frente a la cámara pueda seguir "
         "su guion sin perder el hilo.")
REPETIDA = "El sistema obtiene una respuesta precisa del servidor remoto."


def relleno(n, semilla=0):
    """Oraciones con palabras de contenido únicas (nada se repite)."""
    oraciones = []
    for i in range(semilla, semilla + n):
        oraciones.append(
            f"Luego nodo{i} describe tramo{i} mientras pieza{i} sostiene "
            f"bloque{i} con calma.")
    return " ".join(oraciones)


def documento(*partes):
    return " ".join(partes)


def palabras(texto):
    return texto.split()


def decir(g, texto):
    """Parciales acumulados palabra a palabra, como ParlAR."""
    tokens = texto.split()
    posiciones = []
    for k in range(1, len(tokens) + 1):
        g.proponer_parcial(" ".join(tokens[:k]))
        posiciones.append(g.cursor_visible)
    return posiciones


def indice(g, palabra, desde=0):
    return g.palabras_norm.index(palabra, desde)


# ---------------------------------------------------------------- matcher

def test_evidencia():
    g = Guion(documento(FRASE, relleno(30)))
    for comun in ("el", "de", "un", "que", "la"):
        check(f"1 parcial de una palabra común ({comun}) no mueve",
              not g.proponer_parcial(comun) and g.provisional is None)
    check("2 parcial insuficiente ('todo ese') no mueve",
          not g.proponer_parcial("todo ese") and g.provisional is None)
    check("3 parcial válido crea provisional sin tocar el confirmado",
          g.proponer_parcial("todo ese andamiaje tiene")
          and g.provisional == 4 and g.cursor == 0)
    check("4 el siguiente parcial avanza el provisional",
          g.proponer_parcial("todo ese andamiaje tiene un objetivo claro")
          and g.provisional == 7 and g.cursor == 0)


def test_correcciones_y_retroceso():
    g = Guion(documento(relleno(3), REPETIDA, relleno(30, 100)))
    inicio = indice(g, "sistema")
    secuencia = []
    for parcial in ("el sistema tiene una", "el sistema obtiene una",
                    "el sistema obtiene una respuesta"):
        g.proponer_parcial(parcial)
        secuencia.append(g.cursor_visible)
    saltos = [b - a for a, b in zip(secuencia, secuencia[1:])]
    check("5/24 corrección de Whisper: ajuste estable, sin oscilar",
          secuencia[-1] == inicio + 4 and all(s >= -1 for s in saltos),
          repr(secuencia))

    g = Guion(documento(FRASE, relleno(10)))
    g.proponer_parcial("todo ese andamiaje tiene un objetivo claro que no")
    antes = g.provisional
    g.proponer_parcial("todo ese andamiaje tiene un objetivo claro quedó")
    check("6 pequeña corrección hacia atrás permitida",
          g.provisional is not None and antes - 4 <= g.provisional < antes,
          f"{antes} -> {g.provisional}")
    g.proponer_parcial("esto es completamente otra cosa sin relación")
    check("6 basura posterior no mueve", g.provisional is not None
          and g.provisional < antes + 1)


def test_saltos_e_histeresis():
    g = Guion(documento(FRASE, relleno(40)))
    destino = indice(g, "nodo0")      # >10 palabras adelante, en ventana
    check("7 salto grande con evidencia débil rechazado",
          not g.proponer_parcial("luego nodo0") and g.provisional is None)
    check("8 evidencia fuerte permite el avance",
          g.proponer_parcial("luego nodo0 describe tramo0 mientras pieza0")
          and g.provisional == destino + 5, f"{g.provisional} vs {destino + 5}")

    g = Guion(documento(FRASE, relleno(40)))
    errantes = []
    for parcial in ("todo ese andamiaje tiene", "luego nodo20",
                    "todo ese andamiaje tiene un objetivo", "pieza35 sostiene",
                    "todo ese andamiaje tiene un objetivo claro"):
        g.proponer_parcial(parcial)
        errantes.append(g.cursor_visible)
    check("7 matches débiles lejanos no producen saltos erráticos",
          max(errantes) <= 7 and errantes == sorted(errantes), repr(errantes))


def test_parcial_recortado_y_vacio():
    g = Guion(documento("Esta es una frase bastante larga para probar.",
                        relleno(10)))
    g.proponer_parcial("esta es una frase bastante larga")
    antes = g.provisional
    g.proponer_parcial("esta es una frase")
    check("23 parcial que se acorta no retrocede", g.provisional == antes)
    check("12 parcial vacío no mueve", not g.proponer_parcial("")
          and g.provisional == antes)


def test_texto_repetido():
    # 21: la misma frase aparece cerca y muy lejos.
    g = Guion(documento(relleno(2), REPETIDA, relleno(200, 10), REPETIDA,
                        relleno(5, 900)))
    primera = indice(g, "sistema")
    segunda = indice(g, "sistema", primera + 1)
    g.proponer_parcial("el sistema obtiene una respuesta precisa")
    check("21 texto repetido: gana la aparición cercana",
          g.provisional == primera + 5)
    g.avanzar(REPETIDA)
    g.cursor = segunda - 4
    g.proponer_parcial("el sistema obtiene una respuesta precisa")
    check("21 desde cerca de la segunda aparición: gana la segunda",
          g.provisional == segunda + 5)

    g = Guion(documento(relleno(2), relleno(300, 10), REPETIDA, relleno(3, 900)))
    g.proponer_parcial("el sistema obtiene una respuesta precisa del servidor")
    check("21 frase que sólo existe lejos: fuera de la ventana, no salta",
          g.provisional is None)

    # 22: frase repetida dos veces seguidas dentro de la ventana.
    g = Guion(documento(REPETIDA, REPETIDA, relleno(10)))
    pos = decir(g, REPETIDA)
    check("22 frase repetida: la primera lectura sigue la primera aparición",
          pos[-1] == len(palabras(REPETIDA)))
    g.avanzar(REPETIDA)
    pos = decir(g, REPETIDA)
    check("22 la segunda lectura sigue la segunda aparición",
          pos[-1] == 2 * len(palabras(REPETIDA)))


def test_partials_rapidos_y_bordes():
    g = Guion(documento(FRASE, relleno(20)))
    posiciones = decir(g, FRASE)
    total = len(palabras(FRASE))
    check("25 varios parciales rápidos siguen de corrido y sin retroceder",
          posiciones[-1] >= total - 1
          and all(b >= a for a, b in zip(posiciones, posiciones[1:])),
          repr(posiciones[-5:]))
    check("26 inicio de documento", posiciones[0] == 0
          and min(p for p in posiciones if p) >= 2)

    g = Guion(documento(relleno(3), FRASE))
    g.cursor = indice(g, "todo")
    decir(g, FRASE)
    check("27 final de documento: provisional llega al final",
          g.cursor_visible == len(g.palabras_norm))

    g = Guion("Hola mundo querido.")
    check("28 documento corto", g.proponer_parcial("hola mundo querido")
          and g.provisional == 3 and g.cursor == 0)


def test_ventana_movil_de_parlar():
    """Los parciales reales son una ventana de los últimos segundos: su
    comienzo se desplaza. Se alinea la cola."""
    texto = documento(FRASE, relleno(20))
    g = Guion(texto)
    tokens = FRASE.split()
    posiciones = []
    for k in range(4, len(tokens) + 1):
        ventana = tokens[max(0, k - 12):k]
        g.proponer_parcial(" ".join(ventana))
        posiciones.append(g.cursor_visible)
    check("ventana móvil: sigue hasta el final de la frase",
          posiciones[-1] >= len(tokens) - 1
          and all(b >= a for a, b in zip(posiciones, posiciones[1:])))


# Colas (48 caracteres) de los parciales que ParlAR (Whisper small
# en GPU) produjo en vivo leyendo FRASE_LARGA con voz sintética.
FRASE_LARGA = (
    "Todo ese andamiaje tiene un objetivo claro que no es otro que permitir "
    "que la persona que habla frente a la cámara pueda seguir su guion sin "
    "perder el hilo, incluso cuando la frase se extiende mucho más de lo "
    "habitual y no hay ninguna pausa que permita al detector de actividad de "
    "voz cerrar la unidad antes de tiempo, porque la idea es que el "
    "teleprompter acompañe cada palabra mientras todavía se está "
    "pronunciando.")
PARCIALES_REALES = [
    '¡Todo es en!',
    'Todo ese andamiaje tiene un objetivo claro.',
    'do ese andamiaje tiene un objetivo claro que no.',
    'ndamiaje tiene un objetivo claro que no es otro.',
    'ne un objetivo claro que no es otro que permiso.',
    'bjetivo claro que no es otro que permitir que...',
    ' claro que no es otro que permitir que la perso.',
    'ue no es otro que permitir que la persona quede.',
    'ro que permitir que la persona que habla frente.',
    'rmitir que la persona que habla frente a la cara',
    'la persona que habla frente a la cámara pueda...',
    'la persona que habla frente a la cámara vuelase.',
    ' habla frente a la cámara pueda seguir subiendo.',
    'abla frente a la cámara pueda seguir su niñoncí.',
    'nte a la cámara pueda seguir su mion sin perder.',
    ' cámara pueda seguir su nión sin perder el hilo.',
    'cámara pueda seguir su niñon sin perder el hilo.',
    'eguir su niñon sin perder el hilo, incluso cu...',
    'su mion sin perder el hilo, incluso cuando la...',
    'in perder el hilo, incluso cuando la frase se...',
    'er el hilo, incluso cuando la frase se extiende.',
    'hilo, incluso cuando la frase se extiende mucho.',
    'cluso cuando la frase se extiende mucho más lej.',
    'do la frase se extiende mucho más de lo que hay.',
    'la frase se extiende mucho más de lo igual y no.',
    'extiende mucho más de lo que hay, no hay ningún.',
    'ho más de lo que lo cual y no hay ninguna pausa.',
    ' lo habitual y no hay ninguna pausa que permita.',
    'ay cual y no hay ninguna pausa que permita al...',
    'al y no hay ninguna pausa que permita al efecto.',
    'y ninguna pausa que permita al detector de acto.',
    'guna pausa que permita al detector de actividad.',
    'ausa que permita al defector de actividad nuevo.',
    'sa que permita al defector de actividad nervosa.',
    'al defector de actividad de vos, herrala unidad.',
    ' defector de actividad de voz, será un inanante.',
    'vidad me voy a esperar a unir a antes de tiempo.',
    'vidad, no va a cerrar la unidad antes de tiempo.',
    ' a cerrar la unidad antes de tiempo porque la...',
    ' la unidad antes de tiempo porque la línea es...',
    ' antes de tiempo, porque la idea es que el te...',
    'es de tiempo Porque la línea es que el teleproma',
    'tiempo, porque la línea es que el teleprompterá.',
    ' porque la idea es que el teleprompter acompañe.',
    ' la idea es que el teleprompter acompañe canapa.',
    'es que el teleprompter acompañe cada palabra mí.',
    ' el teleprompter acompañe cada palabra mientras.',
    'mpter acompañe cada palabra mientras toma miras.',
    'recompañe cada palabra mientras todavía se está.',
    'a palabra mientras todavía se está pronunciando.',
    ' palabra mientras Toma Mia se está pronunciando.',
]


def test_parciales_reales_de_parlar():
    g = Guion(documento(relleno(3), FRASE_LARGA, relleno(20, 40)))
    inicio = indice(g, "todo")
    g.cursor = inicio
    posiciones = []
    for parcial in PARCIALES_REALES:
        g.proponer_parcial(parcial)
        posiciones.append(g.cursor_visible - inicio)
    total = len(normalizar_texto(FRASE_LARGA))
    retrocesos = [a - b for a, b in zip(posiciones, posiciones[1:]) if b < a]
    saltos = [b - a for a, b in zip(posiciones, posiciones[1:])]
    check("parciales reales: sigue la frase de corrido hasta el final",
          posiciones[-1] >= total - 1 and g.cursor == inicio,
          f"{posiciones[-1]}/{total}")
    check("parciales reales: sin retrocesos mayores a 1 ni saltos > 10",
          all(r <= 1 for r in retrocesos) and max(saltos) <= 10,
          f"retrocesos={retrocesos} max={max(saltos)}")
    mitad = posiciones[len(posiciones) // 2]
    check("parciales reales: a mitad de la lectura va por la mitad",
          total * 0.35 <= mitad <= total * 0.7, f"{mitad}/{total}")


def test_variantes_de_whisper():
    g = Guion(documento("Que el teleprompter acompañe cada palabra.", relleno(5)))
    check("variante de cola (teleprompterá) cuenta como la misma palabra",
          g.proponer_parcial("que el teleprompterá acompañe")
          and g.provisional == 4)
    g = Guion(documento(relleno(3), relleno(3, 10)))
    check("tramo1 no se confunde con tramo11 ni con tramo2",
          not guion_mod._iguales("tramo1", "tramo2")
          and not guion_mod._iguales("tiene", "tiena"))


def test_rendimiento():
    resultados = {}
    for total in (5_000, 50_000):
        n = total // 7
        g = Guion(relleno(n))
        g.cursor = len(g.palabras_norm) // 2
        base = g.cursor
        texto = g.originales
        tiempos = []
        for k in range(200):
            fin = base + 3 + k // 2
            parcial = " ".join(w for _, w in texto[max(0, fin - 12):fin])
            t0 = time.perf_counter()
            g.proponer_parcial(parcial)
            tiempos.append(time.perf_counter() - t0)
        tiempos.sort()
        resultados[total] = (statistics.median(tiempos) * 1000,
                             tiempos[int(len(tiempos) * 0.95)] * 1000,
                             tiempos[-1] * 1000)
        print(f"      {total} palabras: mediana {resultados[total][0]:.2f} ms "
              f"p95 {resultados[total][1]:.2f} ms máx {resultados[total][2]:.2f} ms")
    check("29 5k palabras: matching de pocos ms",
          resultados[5_000][1] < 15, repr(resultados[5_000]))
    check("30 50k palabras: mismo costo que 5k (no escala con el total)",
          resultados[50_000][1] < 15
          and resultados[50_000][0] < resultados[5_000][0] * 3 + 1,
          repr(resultados))


def _avanzar_original(palabras_norm, cursor, texto):
    for palabra in normalizar_texto(texto):
        zona = palabras_norm[cursor:cursor + guion_mod.VENTANA]
        if palabra in zona:
            cursor += zona.index(palabra) + 1
    return cursor


def test_final_intacto():
    rng = random.Random(7)
    vocab = "el la de que sistema obtiene una respuesta todo ese andamiaje tiene".split()
    iguales = True
    for _ in range(300):
        doc = " ".join(rng.choice(vocab) for _ in range(80))
        g = Guion(doc)
        g.cursor = rng.randrange(0, 40)
        g.provisional = g.cursor + rng.randrange(0, 5)
        dicho = " ".join(rng.choice(vocab) for _ in range(rng.randrange(1, 15)))
        esperado = _avanzar_original(g.palabras_norm, g.cursor, dicho)
        iguales = iguales and g.avanzar(dicho) == esperado \
            and g.provisional is None
    check("40 el matching final anterior queda intacto (300 casos)", iguales)


# ---------------------------------------------------------------- overlay

def _overlay(texto, ancho=720, alto=360):
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(descriptor, texto.encode("utf-8"))
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
    ov.set_speaking(True)
    _asentar(ov)
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()


def _asentar(ov, maximo=400):
    ticks = 0
    while ov._timer.isActive() and ticks < maximo:
        ov._last_tick = time.monotonic() - 0.016
        ov._tick()
        ticks += 1
    return ticks


def _linea_visible(ov):
    _, fm, adv = ov._metricas_guion()
    arriba = SCRIPT_TOP_PX + ov._linea_visual_del_cursor() * adv - ov.scroll_offset
    area_arriba, area_abajo = ov._area_lectura()
    return area_arriba - 0.5 <= arriba and arriba + fm.height() <= area_abajo + 0.5


def _parciales(ov, texto, desde_palabra=0):
    tokens = texto.split()
    for k in range(desde_palabra + 1, len(tokens) + 1):
        ov.set_partial(" ".join(tokens[:k]))


LARGO = documento(relleno(4), FRASE, relleno(80, 50))


def test_ciclo_de_vida_overlay():
    ov = _overlay(LARGO)
    try:
        inicio = indice(ov.guion, "todo")
        ov.guion.cursor = inicio
        ov._scroll_a_cursor(inmediato=True)
        _parciales(ov, FRASE)
        _asentar(ov)
        provisional = ov.guion.provisional
        offset = ov.scroll_offset
        check("31 el viewport sigue al provisional",
              provisional >= inicio + 20 and ov.guion.cursor == inicio
              and _linea_visible(ov))
        ov.set_speaking(False)
        check("13 vad:false conserva el provisional",
              ov.guion.provisional == provisional)
        ov.set_partial("")
        check("12 parcial vacío no retrocede (overlay)",
              ov.guion.provisional == provisional and ov.partial_text == "")
        ov.append_text(FRASE)
        _asentar(ov)
        check("9 final igual al provisional: sin salto",
              ov.guion.cursor == provisional and ov.guion.provisional is None
              and abs(ov.scroll_offset - offset) < 0.5)
        check("14 el final limpia el provisional", ov.guion.provisional is None)

        # 15: nueva utterance parte del confirmado.
        confirmado = ov.guion.cursor
        ov.set_speaking(True)
        ov.set_partial("luego nodo50 describe tramo50")
        check("15 nueva utterance busca desde el confirmado",
              ov.guion.provisional == indice(ov.guion, "tramo50") + 1
              and ov.guion.cursor == confirmado)

        # 10: final unas palabras distinto corrige.
        ov.set_partial("luego nodo50 describe tramo50 mientras pieza50 sostiene")
        ov.set_speaking(False)
        ov.append_text("luego nodo50 describe tramo50")
        check("10 final distinto corrige al provisional",
              ov.guion.cursor == indice(ov.guion, "tramo50") + 1
              and ov.guion.provisional is None and _linea_visible(ov))

        # 11: parcial equivocado, el final manda.
        ov.set_speaking(True)
        antes = ov.guion.cursor
        ov.set_partial("nada que ver con este guion en absoluto")
        ov.append_text("mientras pieza50 sostiene bloque50")
        check("11 parcial erróneo: el final manda",
              ov.guion.provisional is None
              and ov.guion.cursor == indice(ov.guion, "bloque50") + 1
              and ov.guion.cursor > antes)

        # 16/17: disconnect y reconnect.
        ov.set_speaking(True)
        ov.set_partial("con calma luego nodo51 describe tramo51")
        hubo = ov.guion.provisional is not None
        confirmado = ov.guion.cursor
        ov.set_voice_producers(0)
        check("16 disconnect limpia el provisional y conserva el confirmado",
              hubo and ov.guion.provisional is None
              and ov.guion.cursor == confirmado)
        ov.set_speaking(True)   # reconexión: voz nueva
        check("17 reconnect sin replay", ov.guion.provisional is None
              and ov.guion.cursor == confirmado)

        # 18: clear.
        ov.set_partial("con calma luego nodo51 describe tramo51")
        ov.clear()
        check("18 clear limpia el provisional y no reaparece",
              ov.guion.provisional is None and ov.partial_text == "")
    finally:
        _cerrar(ov)


def test_navegacion_manual():
    for delta, nombre in ((-1, "19 PageUp"), (1, "20 PageDown")):
        ov = _overlay(LARGO)
        try:
            ov.guion.cursor = indice(ov.guion, "todo")
            ov._scroll_a_cursor(inmediato=True)
            _parciales(ov, FRASE[:60])
            check(f"{nombre}: había provisional", ov.guion.provisional is not None)
            ov.saltar_oracion(delta)
            destino = ov.guion.cursor
            check(f"{nombre} invalida el provisional",
                  ov.guion.provisional is None)
            ov.set_partial(FRASE[:80])   # parcial viejo de la misma frase
            check(f"{nombre}: un parcial viejo no deshace la navegación",
                  ov.guion.provisional is None and ov.guion.cursor == destino
                  and ov.scroll_offset == ov.scroll_target)
            ov.set_speaking(False)
            ov.set_speaking(True)      # próxima unidad: vuelve el vivo
            ov.set_partial(" ".join(
                w for _, w in ov.guion.originales[destino:destino + 6]))
            check(f"{nombre}: la próxima unidad reanuda el seguimiento",
                  ov.guion.provisional == destino + 6)
        finally:
            _cerrar(ov)


def test_viewport_y_ventana():
    ov = _overlay(LARGO)
    try:
        ov.guion.cursor = indice(ov.guion, "todo")
        ov._scroll_a_cursor(inmediato=True)
        _parciales(ov, " ".join(FRASE.split()[:8]))
        _asentar(ov)
        objetivo = ov.scroll_target
        cambios = 0
        for parcial in ("ese andamiaje tiene un objetivo claro que",
                        "ese andamiaje tiene un objetivo claro quedó",
                        "ese andamiaje tiene un objetivo claro que no"):
            ov.set_partial(parcial)
            cambios += ov.scroll_target != objetivo
        check("32 la zona muerta absorbe cambios pequeños (sin jitter)",
              cambios == 0 and not ov._timer.isActive())

        ov.barra.mostrar()
        _parciales(ov, FRASE)
        _asentar(ov)
        _, fm, adv = ov._metricas_guion()
        fondo = (SCRIPT_TOP_PX + ov._linea_visual_del_cursor() * adv
                 + fm.height() - ov.scroll_offset)
        check("33 con controles visibles la línea provisional queda encima",
              fondo <= ov.height() - ControlBar.ALTO - ControlBar.MARGEN_INFERIOR
              and _linea_visible(ov))

        provisional = ov.guion.provisional
        for ancho, alto in ((720, 200), (420, 360), (1100, 500)):
            ov.resize(ancho, alto)
            _app.processEvents()
            check(f"34 resize {ancho}x{alto} conserva el provisional visible",
                  ov.guion.provisional == provisional and _linea_visible(ov))
        for numero, alineacion in ((35, "left"), (36, "center"), (37, "right")):
            ov.set_alineacion(alineacion)
            check(f"{numero} alineación {alineacion}",
                  ov.guion.provisional == provisional and _linea_visible(ov))
        estado = (ov.guion.cursor, ov.guion.provisional, ov.scroll_target)
        ov.set_siempre_encima(False)
        ov.set_bloqueo(True)
        ov.set_siempre_encima(True)
        ov.set_bloqueo(False)
        _app.processEvents()
        check("38 pin/lock sin efecto",
              (ov.guion.cursor, ov.guion.provisional, ov.scroll_target) == estado)
        _asentar(ov)
        check("39 sin timers residuales", not ov._timer.isActive())
    finally:
        _cerrar(ov)


def main():
    test_evidencia()
    test_correcciones_y_retroceso()
    test_saltos_e_histeresis()
    test_parcial_recortado_y_vacio()
    test_texto_repetido()
    test_partials_rapidos_y_bordes()
    test_ventana_movil_de_parlar()
    test_variantes_de_whisper()
    test_parciales_reales_de_parlar()
    test_rendimiento()
    test_final_intacto()
    test_ciclo_de_vida_overlay()
    test_navegacion_manual()
    test_viewport_y_ventana()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de seguimiento por parciales pasaron.")


if __name__ == "__main__":
    main()
