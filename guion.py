"""Modo Script: matching de texto dictado contra un guion cargado.

Módulo puro, sin Qt, testeable con entradas sintéticas (ver tests/test_guion.py).

Diseño (ver docs/DISENO-EVOLUCION-v2.md sección 4):
- Normalización simétrica entre guion y voz: minúsculas, sin tildes, sin
  puntuación. Un token original da como mucho un token normalizado (nunca
  se parte en dos), así `palabras_norm` y `originales` quedan alineados
  por índice.
- Ventana de 8 palabras de lookahead: el cursor busca la próxima palabra
  reconocida solo cerca de donde está, nunca en todo el guion. Evita que
  palabras comunes ("que", "de") produzcan saltos falsos lejos del cursor.
- El cursor nunca retrocede solo. Retroceso es manual (saltar_oracion).
- Solo texto CONFIRMADO mueve el cursor. Los parciales son inestables por
  definición: matchearlos produce jitter visible. Eso lo decide el llamador
  (guionar.py), acá avanzar() asume que ya le llega texto confirmado.
- Líneas que empiezan con '>' son notas (Fase 2): en v1 se ignoran
  completamente, ni matchean ni se renderizan.
- Párrafos separados por línea vacía.
"""

import re
import unicodedata

VENTANA = 8


def normalizar_palabra(palabra: str) -> str:
    """minúsculas, sin tildes, sin puntuación. Un token da como mucho un
    token de salida (posiblemente vacío si era solo puntuación)."""
    p = palabra.lower()
    p = unicodedata.normalize("NFD", p)
    p = "".join(c for c in p if unicodedata.category(c) != "Mn")
    p = re.sub(r"[^\w]", "", p, flags=re.UNICODE)
    return p


def normalizar_texto(texto: str) -> list:
    """Tokeniza por espacios y normaliza cada palabra, descartando las que
    quedan vacías (puntuación suelta)."""
    return [n for n in (normalizar_palabra(w) for w in texto.split()) if n]


class Guion:
    """Carga un guion y trackea la posición del cursor según lo dictado."""

    def __init__(self, texto: str):
        self.valido = bool(texto and texto.strip())
        self.cursor = 0
        self.palabras_norm = []   # alineado por índice con originales
        self.originales = []      # (parrafo_idx, palabra_original)
        self._inicios_oracion = [0] if self.valido else []  # indices de cursor
        if not self.valido:
            return

        parrafos = re.split(r"\n\s*\n", texto)
        for parrafo_idx, parrafo in enumerate(parrafos):
            lineas = [l for l in parrafo.split("\n")
                     if not l.strip().startswith(">")]  # notas: Fase 2
            for linea in lineas:
                for palabra in linea.split():
                    norm = normalizar_palabra(palabra)
                    if not norm:
                        continue
                    idx = len(self.originales)
                    self.originales.append((parrafo_idx, palabra))
                    self.palabras_norm.append(norm)
                    if idx > 0 and re.search(r"[.!?]$", self.originales[idx - 1][1]):
                        self._inicios_oracion.append(idx)

        self.valido = bool(self.palabras_norm)

    # ---------------------------------------------------------------- avance

    def avanzar(self, texto_reconocido: str) -> int:
        """Mueve el cursor según lo reconocido (ya confirmado, no parcial).
        Devuelve el nuevo cursor. Palabras fuera de la ventana no avanzan
        nada (improvisación)."""
        if not self.valido:
            return self.cursor
        for palabra in normalizar_texto(texto_reconocido):
            zona = self.palabras_norm[self.cursor:self.cursor + VENTANA]
            if palabra in zona:
                self.cursor += zona.index(palabra) + 1
        return self.cursor

    def saltar_oracion(self, delta: int) -> int:
        """Corrección manual: +1/-1 = oración siguiente/anterior. No hay
        límite de ventana acá, es explícito y a propósito del usuario."""
        if not self.valido or not self._inicios_oracion or delta == 0:
            return self.cursor
        pos = 0
        for i, inicio in enumerate(self._inicios_oracion):
            if inicio <= self.cursor:
                pos = i
            else:
                break
        nueva_pos = max(0, min(len(self._inicios_oracion) - 1, pos + delta))
        self.cursor = self._inicios_oracion[nueva_pos]
        return self.cursor
