"""Tests del módulo guion.py: matching de voz contra guion cargado.

Puro, sin Qt, sin display. Corre:
    python tests/test_guion.py

Mismo estilo simple (check()) que el resto de los tests del proyecto.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guion import Guion, normalizar_palabra, normalizar_texto

FALLAS = []


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


# ---------------------------------------------------------------- normalización

def test_normalizar_palabra():
    check("minúsculas", normalizar_palabra("Hola") == "hola")
    check("sin tildes", normalizar_palabra("más") == "mas")
    check("sin puntuación", normalizar_palabra("¿qué?") == "que")
    check("token solo puntuación da vacío", normalizar_palabra("¡¡¡") == "")


def test_normalizar_texto_simetrico():
    guion_lado = normalizar_texto("¿Cómo estás?")
    voz_lado = normalizar_texto("como estas")
    check("guion y voz normalizan igual (simétrico)", guion_lado == voz_lado,
          f"{guion_lado} vs {voz_lado}")


# ---------------------------------------------------------------- carga

def test_carga_basica():
    g = Guion("Hola mundo, esto es una prueba.")
    check("guion válido", g.valido)
    check("palabras_norm y originales alineados",
          len(g.palabras_norm) == len(g.originales))
    check("cursor arranca en 0", g.cursor == 0)


def test_guion_vacio_no_crashea():
    for texto in ("", "   ", None):
        g = Guion(texto)
        check(f"guion vacío ({texto!r}) queda inválido", not g.valido)
        check(f"avanzar sobre inválido no rompe ({texto!r})",
              g.avanzar("hola") == 0)
        check(f"saltar_oracion sobre inválido no rompe ({texto!r})",
              g.saltar_oracion(1) == 0)


def test_notas_se_ignoran_en_v1():
    g = Guion("Primera línea.\n> esto es una nota\nSegunda línea.")
    originales_txt = [p for _, p in g.originales]
    check("la nota no aparece en originales", "nota" not in " ".join(originales_txt))
    check("solo quedan las palabras del guion real",
          originales_txt == ["Primera", "línea.", "Segunda", "línea."], originales_txt)


def test_parrafos_por_linea_vacia():
    g = Guion("Uno dos.\n\nTres cuatro.")
    parrafos = [p for p, _ in g.originales]
    check("primer párrafo es 0", parrafos[:2] == [0, 0], parrafos)
    check("segundo párrafo es 1", parrafos[2:] == [1, 1], parrafos)


# ---------------------------------------------------------------- avanzar

def test_avanzar_secuencial():
    g = Guion("uno dos tres cuatro cinco")
    g.avanzar("uno")
    check("avanza 1 con match exacto", g.cursor == 1)
    g.avanzar("dos tres")
    check("avanza con varias palabras", g.cursor == 3)


def test_avanzar_saltea_palabras_gratis():
    g = Guion("uno dos tres cuatro cinco")
    g.avanzar("tres")  # te comiste "uno dos"
    check("saltearse palabras reposiciona el cursor", g.cursor == 3)


def test_avanzar_no_matchea_fuera_de_ventana():
    g = Guion(" ".join(f"palabra{i}" for i in range(20)))
    g.avanzar("palabra15")  # está a 15 de distancia, ventana=8
    check("palabra fuera de la ventana no avanza nada", g.cursor == 0)


def test_cursor_nunca_retrocede_solo():
    g = Guion("uno dos tres cuatro cinco seis siete ocho nueve")
    g.avanzar("cinco")  # cursor=5, "uno" (índice 0) queda atrás
    cursor_antes = g.cursor
    g.avanzar("uno")  # zona=[seis..nueve], "uno" no está ahí: no matchea
    check("palabra que solo aparece atrás no teletransporta",
          g.cursor == cursor_antes, g.cursor)


def test_improvisar_no_rompe_nada():
    g = Guion("uno dos tres")
    g.avanzar("esto no está en el guion para nada")
    check("texto sin match no mueve el cursor", g.cursor == 0)
    g.avanzar("uno")
    check("volver al guion después de improvisar funciona", g.cursor == 1)


def test_avanzar_simetrico_con_tildes():
    g = Guion("Más allá de la duda.")
    g.avanzar("mas alla")
    check("voz sin tildes matchea guion con tildes", g.cursor == 2)


def test_solo_texto_confirmado_avanza():
    # avanzar() no distingue parcial/final por diseño: esa decisión es del
    # llamador (guionar.py). Acá solo confirmamos que avanzar() es la única
    # forma de mover el cursor (no hay API paralela para parciales).
    check("Guion no expone ningún método para parciales",
          not hasattr(Guion, "avanzar_parcial"))


# ---------------------------------------------------------------- saltar_oracion

def test_saltar_oracion_siguiente_anterior():
    g = Guion("Primera oración va acá. Segunda oración va acá. Tercera oración.")
    inicio_segunda = g.saltar_oracion(1)
    check("saltar +1 mueve a la segunda oración", inicio_segunda > 0)
    inicio_tercera = g.saltar_oracion(1)
    check("saltar +1 de nuevo mueve a la tercera", inicio_tercera > inicio_segunda)
    vuelta = g.saltar_oracion(-1)
    check("saltar -1 vuelve a la segunda", vuelta == inicio_segunda)


def test_saltar_oracion_no_pasa_los_bordes():
    g = Guion("Única oración acá.")
    check("saltar +1 sin más oraciones no rompe", g.saltar_oracion(1) == 0)
    check("saltar -1 sin oración previa no rompe", g.saltar_oracion(-1) == 0)


if __name__ == "__main__":
    test_normalizar_palabra()
    test_normalizar_texto_simetrico()
    test_carga_basica()
    test_guion_vacio_no_crashea()
    test_notas_se_ignoran_en_v1()
    test_parrafos_por_linea_vacia()
    test_avanzar_secuencial()
    test_avanzar_saltea_palabras_gratis()
    test_avanzar_no_matchea_fuera_de_ventana()
    test_cursor_nunca_retrocede_solo()
    test_improvisar_no_rompe_nada()
    test_avanzar_simetrico_con_tildes()
    test_solo_texto_confirmado_avanza()
    test_saltar_oracion_siguiente_anterior()
    test_saltar_oracion_no_pasa_los_bordes()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests pasaron.")
