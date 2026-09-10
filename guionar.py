#!/usr/bin/env python3
"""
GuionAR: professional teleprompter overlay for live dictation
--------------------------------------------------------------
Floating, always-on-top teleprompter window driven by a speech pipeline
(e.g. ParlAR) over a Unix socket or in-process Qt signals.

Usage:
    python guionar.py --demo                 # standalone demo, no pipeline
    python guionar.py --socket               # listen for ParlAR messages
    python guionar.py --socket --opacity 0.4 --font-size 36

Requires: PyQt6  (pip install PyQt6)
Works on X11 and Wayland (see INTEGRATION.md for Wayland notes).
"""

import argparse
import signal
import sys
import time
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSlot
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath,
    QGuiApplication, QKeySequence, QShortcut, QCursor,
)
from PyQt6.QtWidgets import QApplication, QWidget


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


class TeleprompterOverlay(QWidget):
    """Frameless, translucent, always-on-top teleprompter overlay."""

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
        self._lineas_guion = []      # líneas ya envueltas para pintar/scrollear
        self._linea_por_indice = {}  # índice de palabra global -> línea
        self._guion_layout_width = None

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

        # Single repaint timer, only ticks while animation is needed
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / self.cfg["fps"]))
        self._timer.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

        # --- Drag / resize state ----------------------------------------
        self._dragging = False

        # --- Phase 5: keyboard shortcuts --------------------------------
        self._make_shortcuts()

        self.setWindowTitle("GuionAR")
        self.setMouseTracking(True)

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

        # Todo evento final aceptado termina la hipótesis, incluso si su
        # payload no contiene palabras utilizables.
        self.partial_text = ""
        if not text.strip():
            self.update()
            return
        text = text[: self.cfg["max_input_chars"]]

        if self.guion is not None and self.guion.valido:
            self.guion.avanzar(text)
            self._scroll_a_cursor()
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
        self.update()

    @pyqtSlot(str)
    def set_partial(self, text: str):
        """Show an in-flight hypothesis as a dim suffix after the committed
        text. Replaced wholesale by each partial; cleared by final text."""
        if not isinstance(text, str):
            return
        self.partial_text = text[-self.cfg["max_input_chars"]:].strip()
        self.update()

    @pyqtSlot(bool)
    def set_speaking(self, speaking: bool):
        """VAD hook: True while user is speaking (Phase 4)."""
        speaking = bool(speaking)
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
        self.update()

    # ------------------------------------------------------------------
    # Modo Script: carga y layout (ver guion.py)
    # ------------------------------------------------------------------
    def cargar_guion(self, ruta: str):
        """Carga un guion desde archivo. Si falla o queda vacío, nunca
        crashea: se avisa por stderr y se sigue en modo dictado normal."""
        from guion import Guion
        try:
            with open(ruta, encoding="utf-8") as archivo:
                texto = archivo.read()
        except (OSError, UnicodeError) as e:
            print(f"[guion] no se pudo leer {ruta}: {e}; sigo en modo dictado normal",
                  file=__import__("sys").stderr)
            return
        g = Guion(texto)
        if not g.valido:
            print(f"[guion] {ruta} está vacío o no tiene palabras; "
                  f"sigo en modo dictado normal", file=__import__("sys").stderr)
            return
        self.guion = g
        self._reflow_y_anclar_guion()
        self.update()
        print(f"[guion] cargado: {ruta} ({len(g.palabras_norm)} palabras)")

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

        def palabra_visual(palabra):
            if ancho(palabra + " ") <= disponible:
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
            ancho_palabra = ancho(visual + " ")
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
        return QFontMetrics(self._font_context()).height() * 1.3

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
        self.update()

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

        if self.guion is not None and self.guion.valido:
            self._paint_script(p)
            self._draw_status(p)
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
        for li, linea in enumerate(self._lineas_guion):
            y = SCRIPT_MARGIN_PX + fm.ascent() + li * adv - self.scroll_offset
            if y < -adv or y > self.height() + adv:
                continue
            if linea is None:
                continue
            x = SCRIPT_MARGIN_PX
            for idx, palabra in linea:
                if idx < cursor:
                    color, negrita = QColor(255, 255, 255, 90), False
                elif idx == cursor:
                    color, negrita = QColor(255, 255, 255, 255), True
                elif idx <= cursor + 2:
                    color, negrita = QColor(255, 255, 255, 220), False
                else:
                    color, negrita = QColor(255, 255, 255, 150), False
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

    def _draw_status(self, p: QPainter):
        if self.paused or self.hover_paused:
            label, color = "PAUSED", QColor(255, 180, 60)
        elif self.speaking:
            label, color = "LIVE", QColor(80, 220, 120)
        else:
            label, color = "IDLE", QColor(150, 150, 150)
        f = QFont(self.cfg["font_family"], 9)
        p.setFont(f)
        p.setPen(color)
        p.drawText(QPointF(14, 20), f"● {label}   {int(self.speed_pps)} px/s")

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
        super().mouseMoveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (getattr(self, "guion", None) is not None and self.guion.valido
                and self.width() != self._guion_layout_width):
            self._reflow_y_anclar_guion()

    # ------------------------------------------------------------------
    # Phase 5: hover pause
    # ------------------------------------------------------------------
    def enterEvent(self, e):
        self.hover_paused = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover_paused = False
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
            "Up": lambda: self._change_font(+2),
            "Down": lambda: self._change_font(-2),
            "C": self.clear,
            "PgUp": lambda: self.saltar_oracion(-1),
            "PgDown": lambda: self.saltar_oracion(1),
        }
        for key, fn in binds.items():
            QShortcut(QKeySequence(key), self, activated=fn)

    def speed_up(self):
        self.speed_pps = min(600.0, self.speed_pps + self.cfg["scroll_pps_step"])
        self.update()

    def speed_down(self):
        self.speed_pps = max(30.0, self.speed_pps - self.cfg["scroll_pps_step"])
        self.update()

    def toggle_pause(self):
        self.paused = not self.paused
        if not self.paused:
            self._request_animation()
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
        self.cfg["font_size_current"] = max(14, self.cfg["font_size_current"] + delta)
        self.cfg["font_size_context"] = max(10, self.cfg["font_size_context"] + delta // 2)
        if self.guion is not None and self.guion.valido:
            self._reflow_y_anclar_guion()
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
                    help="ruta a un archivo de guion (modo script: sigue la "
                         "voz sobre texto preparado en vez de transcript libre)")
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
    sys.exit(code)


if __name__ == "__main__":
    main()
