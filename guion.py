"""Modo Script: matching de texto dictado contra un guion cargado.

Módulo puro, sin Qt, testeable con entradas sintéticas (ver tests/test_guion.py).

Diseño:
- Normalización simétrica entre guion y voz: minúsculas, sin tildes, sin
  puntuación. Un token original da como mucho un token normalizado (nunca
  se parte en dos), así `palabras_norm` y `originales` quedan alineados
  por índice.
- Ventana de 8 palabras de lookahead: el cursor busca la próxima palabra
  reconocida solo cerca de donde está, nunca en todo el guion. Evita que
  palabras comunes ("que", "de") produzcan saltos falsos lejos del cursor.
- El cursor nunca retrocede solo. Retroceso es manual (saltar_oracion).
- Solo texto CONFIRMADO mueve el cursor (``cursor``). Los parciales mueven
  un cursor PROVISIONAL aparte (``provisional``), con evidencia mínima,
  ventana acotada e histéresis: es lo que se ve mientras se habla, pero el
  final siempre decide y lo descarta.
- Las líneas que empiezan con '>' se ignoran completamente: no participan del
  matching ni se renderizan.
- Párrafos separados por línea vacía.
"""

import re
import unicodedata

VENTANA = 8

# Parciales (seguimiento en vivo). Los parciales de ParlAR son una ventana
# móvil de los últimos segundos de la frase: se alinea su cola, no su inicio.
COLA_PARCIAL = 10          # tokens finales del parcial que se alinean
BUSQUEDA_ATRAS = 24        # contexto previo a la base para alinear la cola
BUSQUEDA_ADELANTE = 40     # máximo avance considerado por parcial
AVANCE_NORMAL = 10         # más que esto exige evidencia fuerte
RETROCESO_MAX = 4          # Whisper revisa sus últimas palabras
EVIDENCIA_MIN = (2, 3)     # (tokens alineados, puntaje ponderado)
EVIDENCIA_FUERTE = (4, 7)

# Palabras funcionales: alinean, pero solas no prueban nada.
_FUNCIONALES = frozenset("""
a al algo ante como con cual de del desde donde e el ella ellas ellos en
entre era es esa ese eso esta este esto fue ha hay la las le les lo los mas
me mi muy ni no nos o para pero por porque que se si sin sobre su sus te
tiene todo toda tu un una uno unos unas y ya yo the of and to in is it that
""".split())


def _peso(token: str) -> int:
    return 1 if token in _FUNCIONALES or len(token) < 3 else 2


def _iguales(a: str, b: str) -> bool:
    """Igualdad tolerante a la cola que Whisper suele recortar o alargar
    ("teleprompter"/"teleprompterá"); las palabras cortas exigen exactitud."""
    if a == b:
        return True
    # Sólo extensión/recorte de la cola: misma longitud con otra letra
    # ("tramo0"/"tramo1", "tiene"/"tiena") es otra palabra.
    if (len(a) < 6 or len(b) < 6 or len(a) == len(b)
            or abs(len(a) - len(b)) > 3):
        return False
    corto, largo = sorted((a, b), key=len)
    return largo.startswith(corto[:-1]) and len(corto) >= 6


def alinear_cola(parcial: list, documento: list, desde: int, hasta: int,
                 fin_min: int, fin_max: int, base: int):
    """Alineación local de la cola del parcial contra ``documento[desde:hasta]``.

    Devuelve (posición, tokens_alineados, puntaje) donde ``posición`` es el
    índice siguiente a la última palabra alineada (misma semántica que el
    cursor), o None. Sólo se consideran alineaciones que terminan en uno de
    los últimos tres tokens del parcial y en una posición entre ``fin_min`` y
    ``fin_max``. Ante empate gana la más cercana a ``base``.
    """
    cola = parcial[-COLA_PARCIAL:]
    tramo = documento[desde:hasta]
    n, m = len(cola), len(tramo)
    if not n or not m:
        return None
    pesos = [_peso(t) for t in cola]
    # puntaje, tokens alineados y peso alineado por celda (fila anterior).
    previo = [(0.0, 0, 0)] * (m + 1)
    mejor = None
    for i in range(1, n + 1):
        actual = [(0.0, 0, 0)] * (m + 1)
        token, peso = cola[i - 1], pesos[i - 1]
        for j in range(1, m + 1):
            diag = previo[j - 1]
            if _iguales(token, tramo[j - 1]):
                cand = (diag[0] + peso, diag[1] + 1, diag[2] + peso)
            else:
                cand = (diag[0] - 1.0, diag[1], diag[2])
            arriba, izq = previo[j], actual[j - 1]
            celda = max(cand, (arriba[0] - 1.0, arriba[1], arriba[2]),
                        (izq[0] - 1.0, izq[1], izq[2]), key=lambda c: c[0])
            if celda[0] <= 0:
                celda = (0.0, 0, 0)
            actual[j] = celda
            if (i >= n - 2 and celda[1]
                    and _iguales(token, tramo[j - 1])):
                posicion = desde + j
                if fin_min <= posicion <= fin_max:
                    # Cola sin alinear: cada token final perdido resta.
                    puntaje = celda[0] - 0.5 * (n - i)
                    clave = (puntaje, -abs(posicion - base))
                    if mejor is None or clave > mejor[0]:
                        mejor = (clave, posicion, celda[1], celda[2])
        previo = actual
    if mejor is None:
        return None
    return mejor[1], mejor[2], mejor[3]


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
        self.cursor = 0           # confirmado: sólo final/navegación/auto
        self.provisional = None   # posición propuesta por parciales
        self._ultimo_parcial = []
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
        self.descartar_provisional()   # el final manda
        return self.cursor

    @property
    def cursor_visible(self) -> int:
        """Lo que se pinta y sigue el viewport: provisional si existe."""
        return self.cursor if self.provisional is None else self.provisional

    def descartar_provisional(self):
        self.provisional = None
        self._ultimo_parcial = []

    def proponer_parcial(self, texto_parcial: str) -> bool:
        """Mueve el cursor provisional según un parcial; nunca el confirmado.

        Cada parcial reemplaza al anterior (no se concatenan). Devuelve True
        si la posición visible cambió. Reglas, en orden:
        - evidencia mínima: sin ella no se mueve nada;
        - el provisional nunca queda antes del confirmado;
        - un parcial que es versión recortada del anterior no retrocede;
        - retroceso de hasta RETROCESO_MAX palabras (Whisper corrige);
        - avance mayor que AVANCE_NORMAL sólo con evidencia fuerte.
        """
        if not self.valido:
            return False
        tokens = normalizar_texto(texto_parcial)
        anterior, self._ultimo_parcial = self._ultimo_parcial, tokens
        if len(tokens) < EVIDENCIA_MIN[0]:
            return False
        base = self.cursor_visible
        total = len(self.palabras_norm)
        desde = max(0, min(self.cursor, base) - BUSQUEDA_ATRAS)
        hasta = min(total, base + BUSQUEDA_ADELANTE)
        fin_min = max(self.cursor, base - RETROCESO_MAX)
        resultado = alinear_cola(tokens, self.palabras_norm, desde, hasta,
                                 fin_min, hasta, base)
        if resultado is None:
            return False
        posicion, alineados, puntaje = resultado
        if alineados < EVIDENCIA_MIN[0] or puntaje < EVIDENCIA_MIN[1]:
            return False
        delta = posicion - base
        if delta > AVANCE_NORMAL and (alineados < EVIDENCIA_FUERTE[0]
                                      or puntaje < EVIDENCIA_FUERTE[1]):
            return False
        if delta < 0 and tokens == anterior[:len(tokens)]:
            return False   # parcial recortado: no es una corrección
        if posicion == base:
            return False
        self.provisional = posicion
        return True

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
        self.descartar_provisional()   # la navegación manual gana
        return self.cursor
