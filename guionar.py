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

        # --- Scrolling state (Phase 4/6) --------------------------------
        self.scroll_offset = 0.0     # px, animates toward target
        self.scroll_target = 0.0
        self.speed_pps = self.cfg["scroll_pps"]
        self.speaking = False        # VAD signal
        self.paused = False          # user pause (Space)
        self.hover_paused = False    # pause on hover (Phase 5)
        self.hidden = False          # ghost mode (T): panel invisible,
                                     # window kept alive so T can restore it

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
        """Append final transcribed text. Wraps into lines automatically."""
        if not isinstance(text, str) or not text.strip():
            return
        text = text[: self.cfg["max_input_chars"]]

        # Final text supersedes the pending hypothesis preview.
        self.partial_text = ""

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
        self.speaking = speaking
        if speaking:
            self._request_animation()

    @pyqtSlot()
    def clear(self):
        self.lines.clear()
        self.current_line = ""
        self.partial_text = ""
        self.scroll_offset = self.scroll_target = 0.0
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

        if self.hidden:
            # Ghost mode: draw only a faint pill so the user can find
            # and restore the overlay (click it or press T).
            p.setFont(QFont(self.cfg["font_family"], 9))
            p.setPen(QColor(255, 255, 255, 70))
            p.drawText(QPointF(14, 20), "GuionAR oculto · T para mostrar")
            p.end()
            return

        # Panel background
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()), self.cfg["corner_radius"], self.cfg["corner_radius"]
        )
        bg = QColor(10, 12, 16)
        bg.setAlphaF(self.cfg["bg_opacity"])
        p.fillPath(path, bg)

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
        self.hidden = not self.hidden
        self.update()

    def _change_font(self, delta: int):
        self.cfg["font_size_current"] = max(14, self.cfg["font_size_current"] + delta)
        self.cfg["font_size_context"] = max(10, self.cfg["font_size_context"] + delta // 2)
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
    ap.add_argument("--opacity", type=float, default=None,
                    help="panel background opacity, 0.0-1.0 (default 0.55)")
    ap.add_argument("--font-size", type=int, default=None,
                    help="current-line font size in pt (default 30)")
    ap.add_argument("--guardar-config", action="store_true",
                    help="persist current opacity/font-size to "
                         "~/.config/guionar/config.json")
    return ap.parse_args()


def main():
    args = _parse_args()
    import guionar_config
    cfg = guionar_config.cargar()  # arranca con lo persistido, si hay
    if args.opacity is not None:
        cfg["bg_opacity"] = min(1.0, max(0.0, args.opacity))
    if args.font_size is not None:
        cfg["font_size_current"] = max(14, min(96, args.font_size))
        cfg["font_size_context"] = max(10, cfg["font_size_current"] * 3 // 5)
    if args.guardar_config:
        guionar_config.guardar(cfg)

    app = QApplication(sys.argv)

    # Qt's event loop runs in C and never yields to the Python interpreter,
    # so a bare SIGINT (Ctrl+C) lands inside whatever Qt callback happens to
    # be running and aborts instead of exiting cleanly. Restoring the
    # default handler plus a periodic no-op timer gives Python a chance to
    # notice the signal and raise KeyboardInterrupt in a safe spot.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    _sigint_pump = QTimer()
    _sigint_pump.timeout.connect(lambda: None)
    _sigint_pump.start(200)

    overlay = TeleprompterOverlay(cfg)

    bridge = None
    if args.socket:
        from bridge import SocketBridge
        bridge = SocketBridge(overlay, path=args.socket_path) \
            if args.socket_path else SocketBridge(overlay)
        bridge.start()

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
