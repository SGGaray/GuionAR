#!/usr/bin/env python3
"""
GuionAR: professional teleprompter overlay for live dictation
--------------------------------------------------------------
Floating, always-on-top teleprompter window driven by a speech pipeline
(e.g. ParlAR) over a Unix socket or in-process Qt signals.

Usage:
    python guionar.py                        # abrir y cargar un guion desde la UI
    python guionar.py --guion charla.pdf     # abrir con un documento cargado
    python guionar.py --demo                 # standalone demo, no pipeline
    python guionar.py --socket               # listen for ParlAR messages
    python guionar.py --socket --opacity 0.4 --font-size 36

Requires: PyQt6  (pip install PyQt6)
Works on X11 and Wayland (see INTEGRATION.md for Wayland notes).
"""

import argparse
import math
import os
import signal
import sys
import threading
import time
from collections import deque

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPointF, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
    QPainterPath, QGuiApplication, QKeySequence, QShortcut, QCursor,
)
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget

import document_loader
import ui_controls as ui
from ui_controls import (
    ACENTO, COLOR_ACTIVO, COLOR_INACTIVO, TEXTO, AutoHide, ControlBar,
    TextButton, con_alpha,
)


# ---------------------------------------------------------------------------
# Configuration (tweak freely, no over-engineering: plain constants)
# ---------------------------------------------------------------------------
DEFAULTS = {
    "width": 720,
    "height": 260,
    "top_margin": 40,            # px below top of screen (camera area)
    "bg_opacity": 0.55,          # 0.0 - 1.0 panel background
    "corner_radius": 14,
    "font_family": "DejaVu Sans",
    "font_size_current": 30,     # pt, current line
    "font_size_context": 18,     # pt, previous/next lines
    "max_history_lines": 2,      # lines shown above current
    "max_next_lines": 1,         # buffer lines shown below current
    "line_char_limit": 42,       # wrap live text into lines at ~this width
    "scroll_pps": 120.0,         # scroll speed, pixels per second
    "scroll_pps_step": 30.0,     # speed change per keypress
    "fps": 60,
    "resize_grip": 18,           # px hit-zone at bottom-right corner
    # Hardening limits
    "max_input_chars": 2000,     # max chars accepted per append_text call
    "max_word_chars": 60,        # a single "word" longer than this is chunked
}

SCRIPT_MARGIN_PX = 20
# Primera línea del guion debajo de la fila de estado (chip + documento).
SCRIPT_TOP_PX = SCRIPT_MARGIN_PX + 14

# Avance automático (Modo Script sin voz): la misma velocidad px/s del
# scroll se expresa en líneas por segundo, así no depende de la fuente.
# 120 px/s (nivel 4) = media línea por segundo.
AUTO_PPS_POR_LINEA_SEG = 240.0

TOAST_ENTRADA_MS = 120
TOAST_SALIDA_MS = 180
AVISO_CARGA_MS = 150   # "Cargando…" sólo si la carga se nota


class _CargadorDocumentos(QObject):
    """Extrae documentos fuera del hilo de UI, acotado y "gana la última".

    Un único worker (creado con la primera carga) procesa una extracción a
    la vez. Hay como mucho una petición pendiente: una nueva la reemplaza,
    así una ráfaga de selecciones nunca encola extracciones viejas. El
    resultado vuelve por una señal encolada sólo si sigue siendo la carga
    vigente; el overlay además conserva su propia comprobación de token.
    """

    terminado = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condicion = threading.Condition()
        self._pendiente = None      # (token, ruta) o None
        self._vigente = None        # token de la última carga pedida
        self._cerrado = False
        self._hilo = None

    def iniciar(self, token: int, ruta: str) -> bool:
        with self._condicion:
            if self._cerrado:
                return False
            self._vigente = token
            self._pendiente = (token, ruta)
            if self._hilo is None:
                self._hilo = threading.Thread(
                    target=self._trabajar, name="guionar-loader", daemon=True)
                self._hilo.start()
            self._condicion.notify()
        return True

    def _trabajar(self):
        while True:
            with self._condicion:
                while self._pendiente is None and not self._cerrado:
                    self._condicion.wait()
                if self._cerrado:
                    return
                token, ruta = self._pendiente
                self._pendiente = None
            resultado = document_loader.load_script(ruta)
            with self._condicion:
                if self._cerrado:
                    return
                if token != self._vigente:
                    continue  # reemplazada mientras se extraía: se descarta
            try:
                self.terminado.emit(token, resultado)
            except RuntimeError:
                return  # el overlay ya se cerró

    def esperar(self, timeout: float = 2.0) -> bool:
        """Cierre limpio y acotado: descarta lo pendiente, despierta al
        worker y lo espera como mucho ``timeout`` segundos. Una extracción
        en curso no se interrumpe; si excede el plazo, el hilo daemon no
        bloquea la salida. Devuelve si el worker terminó."""
        with self._condicion:
            self._cerrado = True
            self._pendiente = None
            self._condicion.notify_all()
            hilo = self._hilo
        if hilo is None:
            return True
        hilo.join(max(0.0, timeout))
        return not hilo.is_alive()


class TeleprompterOverlay(QWidget):
    """Frameless, translucent, always-on-top teleprompter overlay."""

    SPEED_MIN = 30.0
    SPEED_MAX = 600.0
    FONT_MIN = 10        # fuente del guion (font_size_context)
    FONT_MAX = 96

    def __init__(self, cfg: dict | None = None):
        super().__init__()
        self.cfg = {**DEFAULTS, **(cfg or {})}

        # --- Phase 1: window flags -------------------------------------
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 140)
        self.resize(self.cfg["width"], self.cfg["height"])
        self._position_top_center()

        # --- Text model (Phase 2/3) ------------------------------------
        self.lines: deque[str] = deque(maxlen=200)   # committed lines
        self.current_line: str = ""                  # committed, in-progress line
        self.partial_text: str = ""                  # ephemeral hypothesis (dim
                                                     # suffix), replaced by each
                                                     # partial, cleared on final

        # --- Modo Script (opcional): ver guion.py -----------------------
        self.guion = None            # instancia de Guion si hay uno cargado
        self.documento_nombre = None # nombre visible del documento activo
        self.documento_error = None  # último error de carga, para el usuario
        self.cargando = False
        self._carga_token = 0
        self._ultimo_directorio = ""
        self._selector_archivo = QFileDialog.getOpenFileName
        self._nombre_en_carga = ""
        self.arrastre = None         # None | "ok" | "no" durante drag & drop
        self._lineas_guion = []      # líneas ya envueltas para pintar/scrollear
        self._linea_por_indice = {}  # índice de palabra global -> línea
        self._guion_layout_width = None
        self._anchos_clave = None    # caché de anchos de palabra por fuente
        self._anchos = {}

        # --- Scrolling state (Phase 4/6) --------------------------------
        self.scroll_offset = 0.0     # px, animates toward target
        self.scroll_target = 0.0
        self.speed_pps = self.cfg["scroll_pps"]
        self.speaking = False        # VAD signal
        self.paused = False          # user pause (Space)
        self.hover_paused = False    # pause on hover (Phase 5)
        self.hidden = False          # ghost mode (T or socket "toggle"):
                                     # window is truly hidden (hide()),
                                     # guaranteeing no click interception
                                     # regardless of WM/compositor
        self.ghost_recovery_available = False
        self.voz_conectada = False   # un pipeline (ParlAR) está enviando:
                                     # Modo Script sigue la voz; sin él,
                                     # avanza solo a la velocidad elegida

        # Single repaint timer, only ticks while animation is needed
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.cfg["fps"]))
        self._timer.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

        # --- Drag / resize state ----------------------------------------
        self._dragging = False

        # --- Phase 5: keyboard shortcuts --------------------------------
        self._make_shortcuts()

        # --- Feedback breve de estado (carga, errores, pausa, velocidad) --
        self.toast_texto = ""
        self.toast_tipo = "info"
        self.toast_opacidad = 0.0
        self._toast_anim = QVariantAnimation(self)
        self._toast_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._toast_anim.valueChanged.connect(self._set_toast_opacidad)
        self._toast_espera = QTimer(self)
        self._toast_espera.setSingleShot(True)
        # Slots como métodos (no lambdas que capturen self): PyQt no retiene
        # el overlay y no se forman ciclos que el GC libere a destiempo.
        self._toast_espera.timeout.connect(self._ocultar_toast)
        self._aviso_carga = QTimer(self)
        self._aviso_carga.setSingleShot(True)
        self._aviso_carga.timeout.connect(self._mostrar_aviso_carga)

        self._cargador = _CargadorDocumentos(self)
        self._cargador.terminado.connect(self._carga_terminada)

        # --- Controles visuales (ui_controls.py) ------------------------
        self.btn_vacio = TextButton("Abrir guion", "Abrir guion (Ctrl+O)",
                                    self, icono="open")
        self.btn_vacio.clicked.connect(self.elegir_documento)
        self.barra = ControlBar(self)   # creada después: queda por encima
        self._autohide = AutoHide(self.barra)
        self._buffer_script = None

        self.setWindowTitle("GuionAR")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._refrescar_ui()

    # ------------------------------------------------------------------
    # Public API (thread-safe when driven via PipelineBridge signals)
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def append_text(self, text: str):
        """Append final transcribed text. Wraps into lines automatically.

        Si hay un guion cargado (modo script), el consumidor cambia: en vez
        de acumular texto en pantalla, esto mueve el cursor del guion y
        re-renderiza. El productor (ParlAR) no se entera de nada."""
        if not isinstance(text, str):
            return
        self._marcar_voz()

        # Todo evento final aceptado termina la hipótesis, incluso si su
        # payload no contiene palabras utilizables.
        self.partial_text = ""
        if not text.strip():
            self._refrescar_ui()
            self.update()
            return
        text = text[: self.cfg["max_input_chars"]]

        if self.guion is not None and self.guion.valido:
            self.guion.avanzar(text)
            self._scroll_a_cursor()
            if self.script_terminado():
                self.barra.sincronizar()
            self.update()
            return

        limit = self.cfg["line_char_limit"]
        max_word = self.cfg["max_word_chars"]
        for raw in text.split():
            # Chunk pathological unbroken strings so a line can always commit
            chunks = [raw[i:i + max_word] for i in range(0, len(raw), max_word)]
            for word in chunks:
                candidate = (self.current_line + " " + word).strip()
                if len(candidate) > limit and self.current_line:
                    self._commit_current_line()
                    self.current_line = word
                else:
                    self.current_line = candidate
        self._request_animation()
        self._refrescar_ui()
        self.update()

    @pyqtSlot(str)
    def set_partial(self, text: str):
        """Show an in-flight hypothesis as a dim suffix after the committed
        text. Replaced wholesale by each partial; cleared by final text."""
        if not isinstance(text, str):
            return
        self._marcar_voz()
        self.partial_text = text[-self.cfg["max_input_chars"]:].strip()
        self._refrescar_ui()
        self.update()

    @pyqtSlot(bool)
    def set_speaking(self, speaking: bool):
        """VAD hook: True while user is speaking (Phase 4)."""
        speaking = bool(speaking)
        self._marcar_voz()
        changed = self.speaking != speaking
        self.speaking = speaking
        if speaking and not (self.paused or self.hover_paused):
            self._request_animation()
        if changed:
            self.update()

    @pyqtSlot()
    def clear(self):
        self.lines.clear()
        self.current_line = ""
        self.partial_text = ""
        if self.guion is not None and self.guion.valido:
            # En Modo Script, clear borra solo estado transitorio: no reinicia
            # ni el guion ni su posición semántica/visual.
            self._scroll_a_cursor(inmediato=True)
        else:
            self.scroll_offset = self.scroll_target = 0.0
        self._refrescar_ui()
        self.update()

    @pyqtSlot(int)
    def set_voice_producers(self, cantidad: int):
        """El SocketBridge informa productores de voz activos (conexiones
        que enviaron text/partial/vad). Sin ninguno, Modo Script vuelve al
        avance automático aunque sigan abiertas conexiones de control."""
        if cantidad <= 0 and self.voz_conectada:
            self.voz_conectada = False
            self._refrescar_ui()
            self.update()

    def _marcar_voz(self):
        if not self.voz_conectada:
            self.voz_conectada = True
            self.update()

    # ------------------------------------------------------------------
    # Documentos: un único camino para CLI, selector y drag & drop
    # ------------------------------------------------------------------
    def elegir_documento(self):
        """Selector nativo de archivos (botón Abrir / Ctrl+O)."""
        ruta, _ = self._selector_archivo(
            self, "Abrir guion", self._ultimo_directorio,
            document_loader.filtro_dialogo())
        if ruta:
            self.abrir_documento(ruta, en_segundo_plano=True)

    def abrir_documento(self, ruta: str, en_segundo_plano: bool = False):
        """Carga un documento para Modo Script.

        Documento nuevo: cursor al comienzo, viewport arriba, parcial
        descartado y teleprompter en pausa, listo para empezar con Play.
        Si la carga falla, el documento anterior (o el estado vacío) queda
        intacto y el error se informa en la UI.

        En segundo plano la extracción corre en un hilo; devuelve None y
        aplica el resultado al terminar. Sincrónico devuelve si cargó.
        """
        ruta = os.fspath(ruta)
        self._carga_token += 1
        token = self._carga_token
        self._ultimo_directorio = os.path.dirname(os.path.abspath(ruta))
        if not en_segundo_plano:
            return self._aplicar_documento(
                token, document_loader.load_script(ruta), preparar=True)
        self.cargando = True
        self.documento_error = None
        self._nombre_en_carga = os.path.basename(ruta)
        self._aviso_carga.start(AVISO_CARGA_MS)
        self._cargador.iniciar(token, ruta)
        self._refrescar_ui()
        self.update()
        return None

    def cargar_guion(self, ruta: str):
        """API programática histórica, también usada por --guion.

        Usa el mismo pipeline que abrir_documento pero no toca la pausa.
        Si falla, nunca crashea: avisa y conserva el estado anterior (Modo
        Dictado si no había guion)."""
        self._carga_token += 1
        return self._aplicar_documento(
            self._carga_token, document_loader.load_script(ruta),
            preparar=False)

    @pyqtSlot(int, object)
    def _carga_terminada(self, token, resultado):
        self._aplicar_documento(token, resultado, preparar=True)

    def _aplicar_documento(self, token, resultado, preparar) -> bool:
        if token != self._carga_token:
            return False  # una carga posterior reemplazó a esta
        self.cargando = False
        self._aviso_carga.stop()
        if not resultado.ok:
            self.documento_error = resultado.error
            print(f"[guion] {resultado.path}: {resultado.error}", file=sys.stderr)
            if self._hay_guion():
                # Sin guion el estado vacío ya muestra el error en línea.
                self._notificar(resultado.error, "error")
            self._refrescar_ui()
            self.update()
            return False

        self.guion = resultado.guion
        self.documento_nombre = resultado.display_name
        self.documento_error = None
        self.partial_text = ""
        self.scroll_offset = self.scroll_target = 0.0
        if preparar:
            self.paused = True
        self._reflow_y_anclar_guion()
        palabras = len(self.guion.palabras_norm)
        print(f"[guion] cargado: {resultado.path} ({palabras} palabras)")
        self._notificar(f"{resultado.display_name} · {palabras:,} palabras"
                        .replace(",", "."), "ok")
        self._refrescar_ui()
        self.update()
        return True

    def _mostrar_aviso_carga(self):
        if self.cargando:
            self._notificar(f"Cargando {self._nombre_en_carga}…", "info", ms=60_000)

    # ------------------------------------------------------------------
    # Modo Script: estado
    # ------------------------------------------------------------------
    def _hay_guion(self) -> bool:
        return self.guion is not None and bool(getattr(self.guion, "valido", False))

    def script_terminado(self) -> bool:
        return (self._hay_guion()
                and self.guion.cursor >= len(self.guion.palabras_norm))

    def _modo_auto(self) -> bool:
        """Sin voz conectada, Modo Script avanza solo (teleprompter clásico)."""
        return self._hay_guion() and not self.voz_conectada

    def _en_inicio(self) -> bool:
        return (self._hay_guion() and self.guion.cursor == 0
                and self.scroll_offset <= 0.5)

    def nivel_velocidad(self) -> int:
        return int(round(self.speed_pps / self.SPEED_MIN))

    def _reflow_guion(self):
        """Envuelve según ancho dibujable y métricas de la fuente actual.

        Cada línea guarda pares (índice_global, palabra_visual); el índice
        sigue perteneciendo a la palabra semántica original. ``None`` marca
        una separación de párrafo.
        """
        self._lineas_guion = []
        self._linea_por_indice = {}
        if self.guion is None or not self.guion.valido:
            return

        disponible = max(1, self.width() - 2 * SCRIPT_MARGIN_PX)
        fm_normal = QFontMetrics(self._font_context())
        fuente_bold = self._font_context()
        fuente_bold.setWeight(QFont.Weight.Bold)
        fm_bold = QFontMetrics(fuente_bold)

        def ancho(texto):
            return max(fm_normal.horizontalAdvance(texto),
                       fm_bold.horizontalAdvance(texto))

        # Mismo ancho exacto, medido una sola vez por palabra distinta y por
        # fuente: un resize sólo recalcula cortes de línea, no métricas.
        clave = (self.cfg["font_family"], self.cfg["font_size_context"], id(self.guion))
        if self._anchos_clave != clave:
            self._anchos_clave, self._anchos = clave, {}
        anchos = self._anchos

        def ancho_con_espacio(texto):
            valor = anchos.get(texto)
            if valor is None:
                valor = anchos[texto] = ancho(texto + " ")
            return valor

        def palabra_visual(palabra):
            if ancho_con_espacio(palabra) <= disponible:
                return palabra
            limite_palabra = max(1, disponible - ancho(" "))
            visual = fm_bold.elidedText(
                palabra, Qt.TextElideMode.ElideRight, limite_palabra)
            # La métrica bold suele ser la mayor. Esta reducción conserva el
            # fallback seguro también con fuentes donde no lo sea.
            while len(visual) > 1 and ancho(visual + " ") > disponible:
                base = visual[:-1] if visual.endswith("…") else visual
                visual = base[:-1] + "…"
            return visual

        linea = []
        ancho_linea = 0
        parrafo_anterior = None
        for idx, (parrafo_idx, palabra) in enumerate(self.guion.originales):
            if parrafo_anterior is not None and parrafo_idx != parrafo_anterior:
                if linea:
                    self._lineas_guion.append(linea)
                    linea, ancho_linea = [], 0
                self._lineas_guion.append(None)
            parrafo_anterior = parrafo_idx

            visual = palabra_visual(palabra)
            ancho_palabra = ancho_con_espacio(visual)
            if linea and ancho_linea + ancho_palabra > disponible:
                self._lineas_guion.append(linea)
                linea, ancho_linea = [], 0
            linea.append((idx, visual))
            ancho_linea += ancho_palabra
        if linea:
            self._lineas_guion.append(linea)
        for li, ln in enumerate(self._lineas_guion):
            if ln is None:
                continue
            for idx, _ in ln:
                self._linea_por_indice[idx] = li
        self._guion_layout_width = self.width()

    def _line_advance_guion_px(self) -> float:
        return QFontMetrics(self._font_context()).height() * 1.35

    def _linea_visual_del_cursor(self):
        if not self._lineas_guion or self.guion is None:
            return None
        if self.guion.cursor >= len(self.guion.palabras_norm):
            return next((i for i in range(len(self._lineas_guion) - 1, -1, -1)
                         if self._lineas_guion[i] is not None), None)
        return self._linea_por_indice.get(self.guion.cursor)

    def _scroll_a_cursor(self, inmediato=False):
        li = self._linea_visual_del_cursor()
        if li is None:
            return
        adv = self._line_advance_guion_px()
        # deja una línea de contexto arriba del cursor, no lo pega al borde
        self.scroll_target = max(0.0, (li - 1) * adv)
        if inmediato:
            self.scroll_offset = self.scroll_target
            self._timer.stop()
        else:
            self._request_animation()

    def _reflow_y_anclar_guion(self):
        """Reconstruye geometría y alinea el viewport al cursor semántico."""
        self._reflow_guion()
        self._scroll_a_cursor(inmediato=True)

    def saltar_oracion(self, delta: int):
        """Corrección manual: PageUp/PageDown en el overlay."""
        if self.guion is None:
            return
        self.guion.saltar_oracion(delta)
        # Navegación explícita: revela el destino aunque VAD/pausas bloqueen
        # el seguimiento automático posterior.
        self._scroll_a_cursor(inmediato=True)
        if self._modo_auto() and not self._effective_paused():
            self._request_animation()  # el avance automático sigue desde acá
        self.barra.sincronizar()
        self.update()

    # ------------------------------------------------------------------
    # Modo Script sin voz: avance automático a velocidad constante
    # ------------------------------------------------------------------
    def _ultima_linea_guion(self):
        return next((i for i in range(len(self._lineas_guion) - 1, -1, -1)
                     if self._lineas_guion[i] is not None), None)

    def _tick_auto(self, dt: float):
        ultima = self._ultima_linea_guion()
        adv = self._line_advance_guion_px()
        if ultima is None or adv <= 0:
            self._timer.stop()
            return
        # Termina cuando la última línea pasó la marca de lectura.
        maximo = ultima * adv
        paso = adv * self.speed_pps / AUTO_PPS_POR_LINEA_SEG * dt
        self.scroll_offset = min(maximo, self.scroll_offset + paso)
        self.scroll_target = self.scroll_offset
        if self.scroll_offset >= maximo - 0.01:
            self.guion.cursor = len(self.guion.palabras_norm)
            self.paused = True
            self._timer.stop()
            self._notificar("Fin del guion")
            self.barra.sincronizar()
        else:
            self._cursor_desde_lectura()
        self.update()

    def _cursor_desde_lectura(self):
        """La línea que llega a la marca de lectura pasa a ser la actual.
        El avance automático nunca mueve el cursor hacia atrás."""
        adv = self._line_advance_guion_px()
        lineas_avanzadas = self.scroll_offset / adv
        if lineas_avanzadas < 0.5:
            return  # la primera línea sigue legible arriba
        li = int(lineas_avanzadas) + 1
        while li < len(self._lineas_guion) and self._lineas_guion[li] is None:
            li += 1
        if li >= len(self._lineas_guion):
            return
        actual = self._linea_visual_del_cursor()
        if actual is not None and li <= actual:
            return
        self.guion.cursor = self._lineas_guion[li][0][0]

    def _reiniciar_script(self):
        self.guion.cursor = 0
        self._scroll_a_cursor(inmediato=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _commit_current_line(self):
        self.lines.append(self.current_line)
        self.current_line = ""
        # New line entered: scroll up by one line-height
        self.scroll_target += self._line_advance_px()
        self._request_animation()

    def _line_advance_px(self) -> float:
        fm = QFontMetrics(self._font_current())
        return fm.height() * 1.25

    def _font_current(self) -> QFont:
        f = QFont(self.cfg["font_family"], self.cfg["font_size_current"])
        f.setWeight(QFont.Weight.Bold)
        return f

    def _font_context(self) -> QFont:
        return QFont(self.cfg["font_family"], self.cfg["font_size_context"])

    def _position_top_center(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + self.cfg["top_margin"]
        self.move(x, y)  # no-op on some Wayland compositors, harmless

    def _effective_paused(self) -> bool:
        if self._modo_auto():
            return self.paused or self.hover_paused
        return self.paused or self.hover_paused or not self.speaking

    def _request_animation(self):
        if not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.05)
        self._last_tick = now

        # Paused (user, hover or VAD silence): stop ticking entirely.
        # Every resume path calls _request_animation(), so this is safe.
        if self._effective_paused():
            self._timer.stop()
            self.update()
            return

        if self._modo_auto():
            self._tick_auto(dt)
            return

        remaining = self.scroll_target - self.scroll_offset
        if remaining > 0.5:
            self.scroll_offset += min(self.speed_pps * dt, remaining)
            self.update()
            return

        # Animation settled: snap and stop (idle CPU ~0)
        self.scroll_offset = self.scroll_target
        self._timer.stop()
        self.update()

    # ------------------------------------------------------------------
    # Painting (Phase 2 + 6: single paintEvent, double-buffered by Qt)
    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Panel background
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()), self.cfg["corner_radius"], self.cfg["corner_radius"]
        )
        bg = QColor(10, 12, 16)
        bg.setAlphaF(self.cfg["bg_opacity"])
        p.fillPath(path, bg)

        if self._hay_guion():
            self._paint_script_desvanecido(p)
            self._paint_marca_lectura(p)
            self._draw_status(p)
            self._paint_capas_superiores(p)
            p.end()
            return

        if self._estado_vacio():
            self._paint_estado_vacio(p)
            self._paint_capas_superiores(p)
            p.end()
            return

        w = self.width()
        cy = self.height() * 0.55  # baseline zone for current line
        frac = self._scroll_fraction()

        fm_cur = QFontMetrics(self._font_current())
        fm_ctx = QFontMetrics(self._font_context())
        adv = self._line_advance_px()
        ctx_h = fm_ctx.height() * 1.2

        # Previous lines (faded, smaller), drawn bottom-up above current
        p.setFont(self._font_context())
        n_hist = self.cfg["max_history_lines"]
        history = list(self.lines)[-n_hist:]
        y = cy - fm_cur.ascent() - 14 + (adv * frac)  # slide with scroll
        for i, line in enumerate(reversed(history)):
            alpha = max(0.15, 0.55 - i * 0.2)
            p.setPen(QColor(255, 255, 255, int(alpha * 255)))
            ly = y - i * ctx_h
            if ly < fm_ctx.height() * 0.5:
                break
            self._draw_centered(p, fm_ctx, line, w, ly)

        # Current line: committed text bright, pending hypothesis dim after it
        p.setFont(self._font_current())
        cur = self.current_line if (self.current_line or self.partial_text) else "…"
        suffix = (" " + self.partial_text) if self.partial_text else ""
        full = fm_cur.elidedText(cur + suffix, Qt.TextElideMode.ElideLeft,
                                 self.width() - 40)
        # after eliding, split back into committed/pending parts
        n_suffix = min(len(suffix), len(full))
        bright, dim = (full[:-n_suffix], full[-n_suffix:]) if n_suffix else (full, "")
        x = (w - fm_cur.horizontalAdvance(full)) / 2
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(QPointF(x, cy), bright)
        if dim:
            p.setPen(QColor(255, 255, 255, 110))
            p.drawText(QPointF(x + fm_cur.horizontalAdvance(bright), cy), dim)

        # Status chip
        self._draw_status(p)
        self._paint_capas_superiores(p)
        p.end()

    def _paint_script(self, p: QPainter):
        """Modo Script: tres zonas de color sobre el texto original del
        guion (leído gris, actual+2 próximas brillante/bold, resto blanco
        normal). Scroll suave reutilizado: el objetivo ya lo fija
        _scroll_a_cursor() cada vez que el cursor se mueve."""
        fm = QFontMetrics(self._font_context())
        adv = self._line_advance_guion_px()
        w = self.width()
        cursor = self.guion.cursor
        # Sin voz el cursor avanza por línea: se destaca la línea completa.
        # Con voz el cursor es una palabra reconocida: se destaca la palabra.
        linea_actual = (self._linea_visual_del_cursor()
                        if self._modo_auto() and not self.script_terminado()
                        else None)
        inicio, fin = self._rango_lineas_guion_visibles()
        for li in range(inicio, fin):
            linea = self._lineas_guion[li]
            y = SCRIPT_TOP_PX + fm.ascent() + li * adv - self.scroll_offset
            if y < -adv or y > self.height() + adv:
                continue
            if linea is None:
                continue
            x = SCRIPT_MARGIN_PX
            for idx, palabra in linea:
                if linea_actual is not None:
                    if li == linea_actual:
                        color, negrita = QColor(255, 255, 255, 255), False
                    elif li < linea_actual:
                        color, negrita = QColor(255, 255, 255, 88), False
                    else:
                        color, negrita = QColor(255, 255, 255, 178), False
                elif idx < cursor:
                    color, negrita = QColor(255, 255, 255, 88), False
                elif idx == cursor:
                    color, negrita = QColor(255, 255, 255, 255), True
                elif idx <= cursor + 2:
                    color, negrita = QColor(255, 255, 255, 228), False
                else:
                    color, negrita = QColor(255, 255, 255, 178), False
                f = self._font_context()
                if negrita:
                    f.setWeight(QFont.Weight.Bold)
                p.setFont(f)
                p.setPen(color)
                texto = palabra + " "
                p.drawText(QPointF(x, y), texto)
                x += QFontMetrics(f).horizontalAdvance(texto)
        if self.partial_text:
            p.setFont(self._font_context())
            p.setPen(QColor(255, 255, 255, 110))
            self._draw_centered(p, fm, self.partial_text, w, self.height() - 24)

    def _paint_script_desvanecido(self, p: QPainter):
        """Pinta el guion en un buffer reutilizado y lo desvanece en los
        bordes: el texto entra y sale suave, sin pasar por debajo de la
        fila de estado. El buffer sólo se recrea si cambia el tamaño."""
        dpr = self.devicePixelRatioF()
        tam = QSize(max(1, round(self.width() * dpr)),
                    max(1, round(self.height() * dpr)))
        if self._buffer_script is None or self._buffer_script.size() != tam:
            self._buffer_script = QImage(
                tam, QImage.Format.Format_ARGB32_Premultiplied)
            self._buffer_script.setDevicePixelRatio(dpr)
        self._buffer_script.fill(Qt.GlobalColor.transparent)
        bp = QPainter(self._buffer_script)
        bp.setRenderHint(QPainter.RenderHint.Antialiasing)
        bp.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._paint_script(bp)
        h = max(1.0, float(self.height()))
        mascara = QLinearGradient(0, 0, 0, h)
        transparente, opaco = QColor(0, 0, 0, 0), QColor(0, 0, 0, 255)
        mascara.setColorAt(0.0, transparente)
        mascara.setColorAt(min(0.45, 12 / h), transparente)
        mascara.setColorAt(min(0.48, (SCRIPT_TOP_PX - 2) / h), opaco)
        mascara.setColorAt(max(0.52, 1 - 22 / h), opaco)
        mascara.setColorAt(1.0, transparente)
        bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        bp.fillRect(QRectF(0, 0, self.width(), h), mascara)
        bp.end()
        p.drawImage(QPointF(0, 0), self._buffer_script)

    def _rango_lineas_guion_visibles(self):
        """Índices candidatos a pintura para el viewport, con overscan."""
        cantidad = len(self._lineas_guion)
        avance = self._line_advance_guion_px()
        if cantidad == 0 or avance <= 0:
            return 0, 0

        fm = QFontMetrics(self._font_context())
        base = SCRIPT_TOP_PX + fm.ascent() - self.scroll_offset
        limite_inferior = (-avance - base) / avance
        limite_superior = (self.height() + avance - base) / avance

        # Una línea adicional por lado protege fronteras subpíxel. El filtro
        # geométrico de _paint_script sigue decidiendo qué se dibuja realmente.
        inicio = max(0, math.floor(limite_inferior) - 1)
        fin = min(cantidad, math.ceil(limite_superior) + 2)
        inicio = min(inicio, cantidad)
        return inicio, max(inicio, fin)

    def _scroll_fraction(self) -> float:
        adv = self._line_advance_px()
        if adv <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.scroll_target - self.scroll_offset) / adv))

    @staticmethod
    def _draw_centered(p: QPainter, fm: QFontMetrics, text: str, width: int, baseline: float):
        text = fm.elidedText(text, Qt.TextElideMode.ElideLeft, width - 40)
        x = (width - fm.horizontalAdvance(text)) / 2
        p.drawText(QPointF(x, baseline), text)

    def _estado_vacio(self) -> bool:
        return (not self._hay_guion() and not self.lines
                and not self.current_line and not self.partial_text)

    def _estado(self):
        """Etiqueta y color del chip de estado: un único lugar decide qué
        se comunica, a partir de las banderas existentes."""
        if self._hay_guion():
            if self.script_terminado():
                return "Fin del guion", COLOR_INACTIVO
            if self.paused:
                if self._en_inicio():
                    return "Listo para empezar", ACENTO
                return "En pausa", ACENTO
            if self.hover_paused:
                return "En pausa (puntero)", ACENTO
            if self.voz_conectada:
                if self.speaking:
                    return "Siguiendo la voz", COLOR_ACTIVO
                return "Esperando voz", COLOR_INACTIVO
            return "Leyendo", COLOR_ACTIVO
        if self.paused:
            return "En pausa", ACENTO
        if self.hover_paused:
            return "En pausa (puntero)", ACENTO
        if self.speaking:
            return "En vivo", COLOR_ACTIVO
        return "En espera", COLOR_INACTIVO

    def _draw_status(self, p: QPainter):
        etiqueta, color = self._estado()
        derecha = ui.pintar_chip_estado(p, self.cfg["font_family"], etiqueta, color)
        if self.documento_nombre and self._hay_guion():
            ui.pintar_nombre_documento(p, self.cfg["font_family"],
                                       self.documento_nombre, derecha + 8,
                                       self.width())

    def _paint_marca_lectura(self, p: QPainter):
        """Marca fina a la izquierda de la línea actual: la referencia de
        lectura que se mantiene quieta mientras el texto se mueve."""
        li = self._linea_visual_del_cursor()
        if li is None or self.script_terminado():
            return
        fm = QFontMetrics(self._font_context())
        y = (SCRIPT_TOP_PX + fm.ascent() + li * self._line_advance_guion_px()
             - self.scroll_offset)
        if y < 0 or y > self.height():
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(con_alpha(ACENTO, 0.9))
        alto = fm.ascent() * 0.8
        p.drawRoundedRect(QRectF(7, y - alto + 1, 3, alto), 1.5, 1.5)

    def _paint_estado_vacio(self, p: QPainter):
        """Sin guion ni dictado: qué hacer, con la acción a mano."""
        if self.cargando:
            titulo = "Cargando documento…"
        else:
            titulo = "Cargá un guion para comenzar"
        if self.documento_error and not self.cargando:
            detalle, color = self.documento_error, con_alpha(ui.COLOR_ERROR, 0.95)
        elif self.voz_conectada:
            detalle = "ParlAR conectado · el dictado aparece acá"
            color = con_alpha(TEXTO, 0.55)
        else:
            detalle = (f"Arrastrá un archivo o usá Abrir · "
                       f"{document_loader.formatos_legibles()}")
            color = con_alpha(TEXTO, 0.5)
        ui.pintar_estado_vacio(
            p, self.cfg["font_family"], self.width(), self.height(), titulo,
            detalle, color, 0 if self.cargando else self.btn_vacio.height())

    def _paint_capas_superiores(self, p: QPainter):
        familia = self.cfg["font_family"]
        if self.toast_opacidad > 0.01 and self.toast_texto:
            base = self.height() - 14
            if self.barra.visible_objetivo:
                base -= ControlBar.ALTO + 8   # el aviso no tapa la barra
            ui.pintar_aviso(p, familia, self.width(), base, self.toast_texto,
                            self.toast_tipo == "error", self.toast_opacidad)
        if self.arrastre is not None:
            aceptable = self.arrastre == "ok"
            texto = ("Soltá para abrir el guion" if aceptable else
                     f"Formato no soportado · {document_loader.formatos_legibles()}")
            ui.pintar_arrastre(p, familia, QRectF(self.rect()).adjusted(6, 6, -6, -6),
                               max(4, self.cfg["corner_radius"] - 4), aceptable, texto)
        ui.pintar_grip(p, self.width(), self.height(), self.barra._progreso)

    # ------------------------------------------------------------------
    # Feedback breve de estado
    # ------------------------------------------------------------------
    def _notificar(self, texto: str, tipo: str = "info", ms: int | None = None):
        self.toast_texto = texto
        self.toast_tipo = tipo
        self._toast_espera.start(ms if ms is not None
                                 else (4500 if tipo == "error" else 1200))
        self._animar_toast(1.0, TOAST_ENTRADA_MS)

    def _ocultar_toast(self):
        self._animar_toast(0.0, TOAST_SALIDA_MS)

    def _animar_toast(self, destino: float, duracion: int):
        self._toast_anim.stop()
        self._toast_anim.setDuration(duracion)
        self._toast_anim.setStartValue(self.toast_opacidad)
        self._toast_anim.setEndValue(destino)
        self._toast_anim.start()

    def _set_toast_opacidad(self, valor):
        self.toast_opacidad = float(valor)
        self.update()

    def _feedback_teclado(self, texto: str):
        """Con la barra visible su propio estado ya responde; con la barra
        oculta (atajo de teclado) el cambio se confirma con un aviso breve."""
        if not self.barra.visible_objetivo:
            self._notificar(texto)

    def _refrescar_ui(self):
        """Sincroniza widgets hijos con el estado (nunca desde paintEvent)."""
        if getattr(self, "barra", None) is None:
            return
        self.barra.sincronizar()
        vacio = (self._estado_vacio() and not self.cargando
                 and self.arrastre is None)
        if vacio:
            self._ubicar_boton_vacio()
        if self.btn_vacio.isVisible() != vacio:
            self.btn_vacio.setVisible(vacio)

    def _ubicar_boton_vacio(self):
        _, _, y = ui.layout_estado_vacio(self.cfg["font_family"], self.height(),
                                         self.btn_vacio.height())
        self.btn_vacio.move((self.width() - self.btn_vacio.width()) // 2, int(y))

    # ------------------------------------------------------------------
    # Phase 1: drag to move, corner drag to resize (X11 + Wayland safe)
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        g = self.cfg["resize_grip"]
        in_grip = (
            e.position().x() >= self.width() - g
            and e.position().y() >= self.height() - g
        )
        wh = self.windowHandle()
        if wh is not None:
            if in_grip:
                wh.startSystemResize(
                    Qt.Edge.RightEdge | Qt.Edge.BottomEdge
                )
            else:
                wh.startSystemMove()
        e.accept()

    def mouseMoveEvent(self, e):
        g = self.cfg["resize_grip"]
        in_grip = (
            e.position().x() >= self.width() - g
            and e.position().y() >= self.height() - g
        )
        self.setCursor(
            QCursor(Qt.CursorShape.SizeFDiagCursor if in_grip
                    else Qt.CursorShape.OpenHandCursor)
        )
        self._autohide.actividad()
        super().mouseMoveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (getattr(self, "guion", None) is not None and self._hay_guion()
                and self.width() != self._guion_layout_width):
            self._reflow_y_anclar_guion()
        if getattr(self, "barra", None) is not None:
            self.barra.reubicar()
            self._ubicar_boton_vacio()

    # ------------------------------------------------------------------
    # Drag & drop: mismo camino que el selector (abrir_documento)
    # ------------------------------------------------------------------
    @staticmethod
    def _ruta_arrastrada(mime):
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, e):
        ruta = self._ruta_arrastrada(e.mimeData())
        if ruta is None:
            e.ignore()
            return
        self.arrastre = "ok" if document_loader.es_soportado(ruta) else "no"
        e.acceptProposedAction()
        self.barra.ocultar()
        self._refrescar_ui()
        self.update()

    def dragMoveEvent(self, e):
        if self.arrastre is not None:
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.arrastre = None
        self._refrescar_ui()
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        ruta = self._ruta_arrastrada(e.mimeData())
        self.arrastre = None
        self._refrescar_ui()
        self.update()
        if ruta is None:
            e.ignore()
            return
        e.acceptProposedAction()
        self.abrir_documento(ruta, en_segundo_plano=True)

    # ------------------------------------------------------------------
    # Phase 5: hover pause
    # ------------------------------------------------------------------
    def enterEvent(self, e):
        self.hover_paused = True
        self._autohide.actividad()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover_paused = False
        self._autohide.salida()
        self._request_animation()
        self.update()
        super().leaveEvent(e)

    # ------------------------------------------------------------------
    # Phase 5: keyboard shortcuts (active while overlay has focus;
    # for global hotkeys bind these actions in your DE, see INTEGRATION.md)
    # ------------------------------------------------------------------
    def _make_shortcuts(self):
        binds = {
            "+": self.speed_up, "=": self.speed_up,
            "-": self.speed_down,
            "Space": self.toggle_pause,
            "T": self.toggle_visible,
            "Ctrl+Q": QApplication.instance().quit,
            "Up": self.agrandar_texto,
            "Down": self.achicar_texto,
            "C": self.clear,
            "PgUp": self.oracion_anterior,
            "PgDown": self.oracion_siguiente,
            "Ctrl+O": self.elegir_documento,
        }
        for key, fn in binds.items():
            QShortcut(QKeySequence(key), self, activated=fn)

    def oracion_anterior(self):
        self.saltar_oracion(-1)

    def oracion_siguiente(self):
        self.saltar_oracion(1)

    def agrandar_texto(self):
        self._change_font(+2)

    def achicar_texto(self):
        self._change_font(-2)

    def speed_up(self):
        self._set_speed(self.speed_pps + self.cfg["scroll_pps_step"])

    def speed_down(self):
        self._set_speed(self.speed_pps - self.cfg["scroll_pps_step"])

    def _set_speed(self, valor: float):
        valor = max(self.SPEED_MIN, min(self.SPEED_MAX, valor))
        if valor != self.speed_pps:
            self.speed_pps = valor
            self._feedback_teclado(f"Velocidad {self.nivel_velocidad()}")
        self.barra.sincronizar()
        self.update()

    def toggle_pause(self):
        """Play/Pausa único (Espacio, botón). Al final del guion, Play
        vuelve a empezar desde el comienzo."""
        if self.script_terminado():
            self._reiniciar_script()
            self.paused = False
        else:
            self.paused = not self.paused
        if not self.paused:
            self._request_animation()
        self.barra.sincronizar()
        self._feedback_teclado(self._estado()[0])
        self.update()

    @pyqtSlot()
    def toggle_visible(self):
        if not self.hidden and not self.ghost_recovery_available:
            return
        self.hidden = not self.hidden
        # Ventana realmente oculta (no solo pintada transparente): así no
        # hay forma de que intercepte clicks, sin depender de que el
        # window manager respete WA_TransparentForMouseEvents (en la
        # práctica varía mucho entre compositores/WMs de Linux).
        #
        # Costo conocido: con la ventana oculta no tiene foco, así que la
        # tecla T no la restaura (una ventana sin foco no recibe teclas).
        # El camino real de restauración es el mensaje de socket
        # {"type":"toggle"}, atado a un atajo del escritorio (ver
        # INTEGRATION.md); en el uso real, GuionAR rara vez tiene el foco
        # de todos modos, así que esto no cambia el flujo habitual.
        if self.hidden:
            self.hide()
        else:
            self.show()

    @pyqtSlot(bool)
    def set_ghost_recovery_available(self, available: bool):
        """Publica si existe un canal externo capaz de restaurar Ghost."""
        self.ghost_recovery_available = bool(available)
        if not self.ghost_recovery_available and self.hidden:
            self.hidden = False
            self.show()

    def _change_font(self, delta: int):
        self.cfg["font_size_current"] = max(
            14, min(self.FONT_MAX, self.cfg["font_size_current"] + delta))
        self.cfg["font_size_context"] = max(
            self.FONT_MIN, min(self.FONT_MAX, self.cfg["font_size_context"] + delta // 2))
        if self.guion is not None and self.guion.valido:
            self._reflow_y_anclar_guion()
        tamano = self.cfg["font_size_context" if self._hay_guion() else "font_size_current"]
        self._feedback_teclado(f"Texto {tamano} pt")
        self.barra.sincronizar()
        self.update()


# ---------------------------------------------------------------------------
# Demo mode: fake dictation + VAD so you can test without a pipeline
# ---------------------------------------------------------------------------
def _run_demo(overlay: TeleprompterOverlay):
    words = (
        "this is a live teleprompter demo driven by simulated dictation "
        "text arrives word by word exactly like a streaming transcriber "
        "the current line stays large and centered while previous lines "
        "fade out above it hover the panel to pause press space to pause "
        "plus and minus change the scroll speed and t hides the overlay"
    ).split()
    state = {"i": 0}

    def feed():
        if state["i"] >= len(words):
            overlay.set_speaking(False)
            return
        overlay.set_speaking(True)
        overlay.append_text(words[state["i"]])
        state["i"] += 1

    t = QTimer(overlay)
    t.setInterval(280)
    t.timeout.connect(feed)
    t.start()


def _parse_args():
    ap = argparse.ArgumentParser(prog="guionar", description="GuionAR teleprompter overlay")
    ap.add_argument("--demo", action="store_true", help="run with simulated dictation")
    ap.add_argument("--socket", action="store_true",
                    help="listen for pipeline messages (e.g. ParlAR) on the Unix socket")
    ap.add_argument("--socket-path", default=None, help="override Unix socket path")
    ap.add_argument("--guion", default=None,
                    help="documento a abrir en Modo Script (.txt, .md, .pdf, "
                         ".docx); también se puede abrir desde la ventana")
    ap.add_argument("--opacity", type=float, default=None,
                    help="panel background opacity, 0.0-1.0 (default 0.55)")
    ap.add_argument("--font-size", type=int, default=None,
                    help="current-line font size in pt (default 30)")
    ap.add_argument("--guardar-config", action="store_true",
                    help="persist current opacity/font-size to "
                         "~/.config/guionar/config.json")
    return ap.parse_args()


def _configuracion_efectiva(args):
    """Combina configuración persistida validada y overrides del CLI."""
    import guionar_config
    cfg = guionar_config.cargar()  # arranca con lo persistido, si hay
    if args.opacity is not None:
        cfg["bg_opacity"] = min(1.0, max(0.0, args.opacity))
    if args.font_size is not None:
        cfg["font_size_current"] = max(14, min(96, args.font_size))
        cfg["font_size_context"] = max(10, cfg["font_size_current"] * 3 // 5)
    if args.guardar_config:
        guionar_config.guardar(cfg)
    return cfg


def main():
    args = _parse_args()
    cfg = _configuracion_efectiva(args)

    app = QApplication(sys.argv)

    # El timer le devuelve periódicamente control al intérprete para ejecutar
    # el handler Python. SIGINT pide la misma salida Qt que Ctrl+Q; al volver
    # de app.exec(), el finally ejecuta el shutdown cooperativo del bridge.
    def _salir_por_sigint(_signum, _frame):
        app.quit()

    signal.signal(signal.SIGINT, _salir_por_sigint)
    _sigint_pump = QTimer()
    _sigint_pump.timeout.connect(lambda: None)
    _sigint_pump.start(200)

    overlay = TeleprompterOverlay(cfg)
    if args.guion:
        # Comportamiento histórico de --guion: mismo pipeline de documentos,
        # sin la pausa inicial del flujo interactivo (selector / drag & drop).
        overlay.cargar_guion(args.guion)

    bridge = None
    if args.socket:
        from bridge import SocketBridge
        bridge = SocketBridge(overlay, path=args.socket_path) \
            if args.socket_path else SocketBridge(overlay)
        overlay.set_ghost_recovery_available(bridge.start())

    overlay.show()
    if args.demo:
        _run_demo(overlay)

    try:
        code = app.exec()
    except KeyboardInterrupt:
        code = 0
    finally:
        if bridge is not None:
            bridge.stop()
        overlay._cargador.esperar()
    sys.exit(code)


if __name__ == "__main__":
    main()
