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
    QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
    QPainterPath, QRegion, QGuiApplication, QKeySequence, QShortcut, QCursor,
)
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget

import document_loader
import guionar_config
import ui_controls as ui
from ui_controls import (
    ACENTO, COLOR_ACTIVO, COLOR_INACTIVO, TEXTO, AutoHide, ControlBar,
    TextButton, WindowControls, con_alpha,
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
    # Preferencias de escritorio (persistidas por guionar_config.py)
    "text_alignment": "center",  # left | center | right (Modo Script)
    "pause_on_hover": False,     # el puntero muestra controles; pausar es opt-in
    "position_locked": False,    # bloquea mover/redimensionar la ventana
    "always_on_top": True,       # fijada sobre las demás ventanas (pin)
    "remember_geometry": True,   # restaura posición y tamaño al abrir
    "auto_hide_controls": True,  # los controles se ocultan al volver a leer
    "window_geometry": None,     # {"x", "y", "width", "height"} o None
}

# Valores que "Restaurar valores" devuelve en Apariencia.
APARIENCIA_POR_DEFECTO = ("text_alignment", "bg_opacity",
                          "font_size_current", "font_size_context")

GUARDADO_DIFERIDO_MS = 500   # agrupa cambios rápidos (slider, resize)


def _salir_aplicacion():
    app = QApplication.instance()
    if app is not None:
        app.quit()


def geometria_visible(rect: QRect, pantallas: list, minimo: QSize) -> QRect:
    """Ajusta ``rect`` para que quede entero dentro de una pantalla.

    Elige la pantalla (geometría disponible) con mayor intersección; si no
    toca ninguna, la primera. Achica el tamaño si no entra y corre la
    posición hacia adentro. ``pantallas`` vacío deja el rect intacto.
    """
    if not pantallas:
        return QRect(rect)
    destino = max(pantallas, key=lambda p: (
        p.intersected(rect).width() * p.intersected(rect).height()))
    if destino.intersected(rect).isEmpty():
        destino = pantallas[0]
    ancho = max(min(rect.width(), destino.width()), min(minimo.width(), destino.width()))
    alto = max(min(rect.height(), destino.height()), min(minimo.height(), destino.height()))
    x = min(max(rect.x(), destino.left()), destino.left() + destino.width() - ancho)
    y = min(max(rect.y(), destino.top()), destino.top() + destino.height() - alto)
    return QRect(x, y, ancho, alto)

SCRIPT_MARGIN_PX = 20
# Primera línea del guion debajo de la fila de estado (chip + documento).
SCRIPT_TOP_PX = SCRIPT_MARGIN_PX + 14

# Avance automático (Modo Script sin voz): la misma velocidad px/s del
# scroll se expresa en líneas por segundo, así no depende de la fuente.
# 120 px/s (nivel 4) = media línea por segundo.
AUTO_PPS_POR_LINEA_SEG = 240.0

# Medida máxima de línea (en caracteres promedio): en ventanas anchas las
# líneas larguísimas obligan a mover la vista, lo que se nota en cámara.
MEDIDA_MAX_CARACTERES = 70

# Con fondo poco opaco, un contorno oscuro de 1 px mantiene legible el texto
# sobre ventanas claras. El texto en sí no cambia de opacidad.
CONTORNO_DESDE_OPACIDAD = 0.5

# Jerarquía del guion: leído < próximo < inmediato < actual.
_BLANCO_LEIDO = QColor(255, 255, 255, 88)
_BLANCO_PROXIMO = QColor(255, 255, 255, 178)
_BLANCO_INMEDIATO = QColor(255, 255, 255, 228)
_BLANCO_ACTUAL = QColor(255, 255, 255, 255)

# Seguimiento por voz: la línea que se lee se mantiene en una zona estable
# del área útil (debajo de la fila de estado, encima de aviso/parcial y de
# la barra si está visible). Fracciones desde arriba de esa área: un poco
# por encima del centro deja contexto leído arriba y el ojo cerca de la
# cámara. Dentro de la zona muerta el viewport no se mueve.
LECTURA_ANCLA = 0.40
LECTURA_ZONA = (0.30, 0.55)
# Reserva inferior: margen + aviso/parcial (28 px) + separación. Es fija en
# modo voz para que aparezca o no la pastilla nunca mueva la zona.
RESERVA_INFERIOR_PX = 14 + 28 + 6
RESERVA_BARRA_PX = ControlBar.ALTO + 8
# Aproximación al objetivo: exponencial corta (~0.2 s), sin overshoot, con
# un piso de velocidad para que el último tramo no se arrastre.
VOZ_TAU_S = 0.07
VOZ_MIN_PPS = 240.0

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

    # Cambió una preferencia (desde cualquier lugar): Configuración, la
    # bandeja y los controles se sincronizan con esta señal.
    preferencias_cambiadas = pyqtSignal()
    # La ventana se cerró (Alt+F4, gestor de ventanas): main() termina.
    cerrada = pyqtSignal()

    def __init__(self, cfg: dict | None = None, persistir: bool = False):
        super().__init__()
        self.cfg = {**DEFAULTS, **(cfg or {})}
        # Sólo la aplicación real guarda preferencias en disco; un overlay
        # creado por tests o integraciones in-process no toca la config.
        self._persistir = persistir
        self._cambios_pendientes = {}
        self.tray_disponible = False
        self._configuracion = None
        self._oculta_por_usuario = False
        self._salir_app = _salir_aplicacion

        # --- Phase 1: window flags -------------------------------------
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool  # no taskbar entry
        if self.cfg["always_on_top"]:
            # Desde el arranque, sin depender de un toggle posterior.
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 140)
        self.resize(self.cfg["width"], self.cfg["height"])
        self._position_top_center()
        if self.cfg["remember_geometry"] and self.cfg["window_geometry"]:
            self._restaurar_geometria(self.cfg["window_geometry"])

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
        self._x_lineas_guion = []    # x inicial de cada línea (alineación)
        self._guion_layout_width = None
        self._anchos_clave = None    # caché de anchos de palabra por fuente
        self._anchos = {}
        self._metricas_clave = None  # caché de fuente/métricas del guion
        self._metricas = None
        self._lectura_auto = None    # avance automático, en líneas leídas
        self._buffer_contorno = None

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
        self.parlar_presente = False # ParlAR se presentó (hello) y sigue
                                     # conectado, aunque todavía no hable
        self._parciales_bloqueados = False  # navegación manual en curso
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
        self.ventana_controles = WindowControls(self)
        self._autohide = AutoHide(self.barra, self.ventana_controles)
        self._buffer_script = None

        self._timer_guardado = QTimer(self)
        self._timer_guardado.setSingleShot(True)
        self._timer_guardado.timeout.connect(self.guardar_preferencias)

        self.setWindowTitle("GuionAR")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._refrescar_ui()
        self._autohide.set_habilitado(self.cfg["auto_hide_controls"])

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
            # El final es la autoridad: avanza el cursor confirmado y
            # descarta el provisional (avanzar lo hace). Si el parcial ya
            # estaba en el lugar correcto, no hay salto.
            self.guion.avanzar(text)
            self._parciales_bloqueados = False
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
        # Seguimiento en vivo: el parcial mueve sólo el cursor provisional.
        # Uno vacío no retrocede nada; el provisional espera al final.
        if (self.partial_text and self._hay_guion()
                and not self._parciales_bloqueados
                and self.guion.proponer_parcial(self.partial_text)):
            self._scroll_a_cursor()
        self._refrescar_ui()
        self.update()

    @pyqtSlot(bool)
    def set_speaking(self, speaking: bool):
        """VAD hook: True while user is speaking (Phase 4)."""
        speaking = bool(speaking)
        self._marcar_voz()
        changed = self.speaking != speaking
        self.speaking = speaking
        if speaking and changed:
            # Nueva unidad: parte del confirmado, sin arrastrar la hipótesis
            # previa (si el final llegó, ya la había descartado). vad:false
            # no toca el provisional: el final llega ~0.3 s después.
            self._parciales_bloqueados = False
            self._descartar_provisional()
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
            # ni el guion ni su posición semántica/visual confirmada.
            self.guion.descartar_provisional()
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
            self._parciales_bloqueados = False
            if self._hay_guion():
                self.guion.descartar_provisional()
            adv = self._line_advance_guion_px() if self._hay_guion() else 0
            if adv > 0:
                # El avance automático retoma desde lo que se ve, sin saltar
                # desde la zona de lectura de voz a su propia marca.
                self.scroll_target = self.scroll_offset
                self._lectura_auto = self.scroll_offset / adv
            self._refrescar_ui()
            self.update()

    @pyqtSlot(int)
    def set_voice_clients(self, cantidad: int):
        """El SocketBridge informa clientes presentados como productores de
        voz (ParlAR conectado). Sólo cambia lo que se informa: el paso a
        seguimiento de voz sigue ocurriendo con voz real."""
        presente = cantidad > 0
        if presente != self.parlar_presente:
            self.parlar_presente = presente
            self._refrescar_ui()
            self.update()
            self.preferencias_cambiadas.emit()   # bandeja y Settings releen

    def estado_parlar(self):
        """Estado de ParlAR para la UI, o None si GuionAR no está esperando
        conexiones (sin listener no hay nada que informar)."""
        if self.voz_conectada:
            return "Siguiendo la voz" if self.speaking else "Esperando voz"
        if self.parlar_presente:
            return "Conectado"
        if self.ghost_recovery_available:   # listener local activo
            return "No detectado"
        return None

    def _cursor_visible(self) -> int:
        """Posición que se pinta y sigue el viewport (provisional si hay)."""
        return getattr(self.guion, "cursor_visible", self.guion.cursor)

    def _descartar_provisional(self):
        if self._hay_guion() and self.guion.provisional is not None:
            self.guion.descartar_provisional()
            self._scroll_a_cursor()

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
        self._parciales_bloqueados = False
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
        una separación de párrafo. ``_x_lineas_guion`` guarda el x inicial
        de cada línea según la alineación configurada.
        """
        self._lineas_guion = []
        self._linea_por_indice = {}
        self._x_lineas_guion = []
        if self.guion is None or not self.guion.valido:
            return

        ancho_util = max(1, self.width() - 2 * SCRIPT_MARGIN_PX)
        fm_normal = QFontMetrics(self._font_context())
        disponible = max(1, min(ancho_util, round(
            fm_normal.averageCharWidth() * MEDIDA_MAX_CARACTERES)))
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

        def medidas(texto):
            """(ancho máximo normal/bold, ancho normal), ambos con espacio."""
            valor = anchos.get(texto)
            if valor is None:
                con_espacio = texto + " "
                valor = anchos[texto] = (ancho(con_espacio),
                                         fm_normal.horizontalAdvance(con_espacio))
            return valor

        def ancho_con_espacio(texto):
            return medidas(texto)[0]

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

        # Alineación: el inicio de cada línea se fija con su ancho normal
        # (el resaltado no cambia el peso, así que nada se corre). El corte
        # sigue midiendo con bold como margen de seguridad. Las líneas se
        # alinean respecto del ancho útil de la ventana; la medida máxima
        # sólo limita cuánto texto entra en cada una.
        factor = {"left": 0.0, "center": 0.5, "right": 1.0}.get(
            self.cfg.get("text_alignment"), 0.5)
        espacio_normal = fm_normal.horizontalAdvance(" ")

        def cerrar_linea(linea, normal, ensanche):
            reservado = max(0, normal - espacio_normal)
            self._lineas_guion.append(linea)
            self._x_lineas_guion.append(
                SCRIPT_MARGIN_PX + max(0.0, ancho_util - reservado) * factor)

        linea = []
        ancho_linea = 0
        normal_linea = 0
        ensanche_linea = 0
        parrafo_anterior = None
        for idx, (parrafo_idx, palabra) in enumerate(self.guion.originales):
            if parrafo_anterior is not None and parrafo_idx != parrafo_anterior:
                if linea:
                    cerrar_linea(linea, normal_linea, ensanche_linea)
                    linea, ancho_linea, normal_linea, ensanche_linea = [], 0, 0, 0
                self._lineas_guion.append(None)
                self._x_lineas_guion.append(None)
            parrafo_anterior = parrafo_idx

            visual = palabra_visual(palabra)
            ancho_palabra, normal_palabra = medidas(visual)
            if linea and ancho_linea + ancho_palabra > disponible:
                cerrar_linea(linea, normal_linea, ensanche_linea)
                linea, ancho_linea, normal_linea, ensanche_linea = [], 0, 0, 0
            linea.append((idx, visual))
            ancho_linea += ancho_palabra
            normal_linea += normal_palabra
            ensanche_linea = max(ensanche_linea, ancho_palabra - normal_palabra)
        if linea:
            cerrar_linea(linea, normal_linea, ensanche_linea)
        for li, ln in enumerate(self._lineas_guion):
            if ln is None:
                continue
            for idx, _ in ln:
                self._linea_por_indice[idx] = li
        self._guion_layout_width = self.width()

    def _metricas_guion(self):
        """Fuente y métricas del guion, creadas una vez por configuración
        de fuente (no por palabra ni por frame)."""
        clave = (self.cfg["font_family"], self.cfg["font_size_context"])
        if self._metricas_clave != clave:
            fuente = self._font_context()
            fm = QFontMetrics(fuente)
            self._metricas_clave = clave
            self._metricas = (fuente, fm, fm.height() * 1.35)
        return self._metricas

    def _line_advance_guion_px(self) -> float:
        return self._metricas_guion()[2]

    def _baseline_guion(self, li: int, fm=None) -> float:
        """Baseline de la línea ``li`` en el viewport, en píxeles de
        dispositivo: texto, marca y subrayado se mueven exactamente juntos."""
        fuente, fm_cache, adv = self._metricas_guion()
        fm = fm or fm_cache
        y = SCRIPT_TOP_PX + fm.ascent() + li * adv - self.scroll_offset
        dpr = self.devicePixelRatioF() or 1.0
        return round(y * dpr) / dpr

    def _linea_visual_del_cursor(self):
        if not self._lineas_guion or self.guion is None:
            return None
        cursor = self._cursor_visible()
        if cursor >= len(self.guion.palabras_norm):
            return next((i for i in range(len(self._lineas_guion) - 1, -1, -1)
                         if self._lineas_guion[i] is not None), None)
        return self._linea_por_indice.get(cursor)

    def _scroll_a_cursor(self, inmediato=False):
        if self._seguimiento_voz():
            self._seguir_cursor_voz(inmediato)
            return
        li = self._linea_visual_del_cursor()
        if li is None:
            return
        adv = self._line_advance_guion_px()
        # deja una línea de contexto arriba del cursor, no lo pega al borde
        self.scroll_target = max(0.0, (li - 1) * adv)
        self._lectura_auto = None   # el avance automático retoma desde el cursor
        if inmediato:
            self.scroll_offset = self.scroll_target
            self._timer.stop()
        else:
            self._request_animation()

    # ------------------------------------------------------------------
    # Seguimiento por voz: viewport con zona de lectura y zona muerta
    # ------------------------------------------------------------------
    def _seguimiento_voz(self) -> bool:
        """Guion cargado y voz conectada: el matching es la autoridad de la
        posición; el viewport sólo mantiene esa línea en zona de lectura."""
        return self._hay_guion() and self.voz_conectada

    def _area_lectura(self):
        """(arriba, abajo) en px del viewport donde una línea se lee sin
        quedar tapada por aviso/parcial ni por la barra visible."""
        alto_linea = self._metricas_guion()[1].height()
        arriba = float(SCRIPT_TOP_PX)
        limite = self.height() - (
            RESERVA_BARRA_PX if self.barra.visible_objetivo else 0)
        abajo = limite - RESERVA_INFERIOR_PX
        if abajo - arriba < alto_linea:
            # Ventana muy baja: se cede la reserva del parcial, nunca la
            # de la barra.
            abajo = min(float(limite), arriba + alto_linea)
        return arriba, max(arriba, float(abajo))

    def _zona_lectura(self):
        """(zona_min, ancla, zona_max) para el centro de la línea activa."""
        arriba, abajo = self._area_lectura()
        alto = abajo - arriba
        medio = self._metricas_guion()[1].height() / 2
        minimo, maximo = arriba + medio, max(arriba + medio, abajo - medio)

        def acotar(y):
            return min(max(y, minimo), maximo)

        zona_min = acotar(arriba + LECTURA_ZONA[0] * alto)
        zona_max = acotar(arriba + LECTURA_ZONA[1] * alto)
        return zona_min, acotar(arriba + LECTURA_ANCLA * alto), zona_max

    def _scroll_maximo_voz(self) -> float:
        """Al final, la última línea se apoya en el borde inferior del área:
        nunca medio viewport vacío para llevarla al ancla."""
        ultima = self._ultima_linea_guion()
        if ultima is None:
            return 0.0
        _, fm, adv = self._metricas_guion()
        fondo = SCRIPT_TOP_PX + ultima * adv + fm.height()
        return max(0.0, fondo - self._area_lectura()[1])

    def _objetivo_voz(self):
        li = self._linea_visual_del_cursor()
        if li is None:
            return None
        _, fm, adv = self._metricas_guion()
        centro = SCRIPT_TOP_PX + li * adv + fm.height() / 2
        zona_min, ancla, zona_max = self._zona_lectura()
        y = centro - self.scroll_target
        if zona_min - 0.5 <= y <= zona_max + 0.5:
            return self.scroll_target     # dentro de la zona: no mover
        return min(max(0.0, centro - ancla), self._scroll_maximo_voz())

    def _seguir_cursor_voz(self, inmediato=False):
        objetivo = self._objetivo_voz()
        if objetivo is None:
            return
        self.scroll_target = objetivo
        self._lectura_auto = None
        if inmediato or abs(self.scroll_target - self.scroll_offset) <= 0.5:
            if self.scroll_offset != self.scroll_target:
                self.scroll_offset = self.scroll_target
                self.update()
            self._timer.stop()
        else:
            self._request_animation()

    def _area_lectura_cambio(self):
        """La barra apareció: la línea activa debe quedar por encima. Al
        ocultarse el área sólo crece y no se mueve nada."""
        if self._seguimiento_voz():
            self._seguir_cursor_voz()

    def _tick_voz(self, dt: float):
        restante = self.scroll_target - self.scroll_offset
        if abs(restante) <= 0.5:
            self.scroll_offset = self.scroll_target
            self._timer.stop()
            self.update()
            return
        paso = restante * (1.0 - math.exp(-dt / VOZ_TAU_S))
        minimo = VOZ_MIN_PPS * dt
        if abs(paso) < minimo:
            paso = math.copysign(min(minimo, abs(restante)), restante)
        self.scroll_offset += paso
        self._repintar_lectura()

    def _reflow_y_anclar_guion(self):
        """Reconstruye geometría y alinea el viewport al cursor semántico."""
        self._reflow_guion()
        self._scroll_a_cursor(inmediato=True)

    def saltar_oracion(self, delta: int):
        """Corrección manual: PageUp/PageDown en el overlay."""
        if self.guion is None:
            return
        self.guion.saltar_oracion(delta)   # descarta el provisional
        # Un parcial de la frase en curso todavía describe la posición vieja:
        # no puede deshacer la navegación. Se reanuda con la próxima unidad.
        self._parciales_bloqueados = self.voz_conectada
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
        # La lectura avanza en líneas. El scroll se detiene en el ancla final
        # (la misma que usa el cursor terminal, así un resize no salta); la
        # lectura sigue por las líneas que quedan a la vista y termina una
        # línea después de que la última fue la actual.
        if self._lectura_auto is None:
            self._lectura_auto = self._lectura_desde_cursor()
        self._lectura_auto += self.speed_pps / AUTO_PPS_POR_LINEA_SEG * dt
        maximo = max(0.0, (ultima - 1) * adv)
        self.scroll_offset = min(maximo, self._lectura_auto * adv)
        self.scroll_target = self.scroll_offset
        if self._lectura_auto >= ultima:
            self.guion.cursor = len(self.guion.palabras_norm)
            self.paused = True
            self._timer.stop()
            self._notificar("Fin del guion")
            self.barra.sincronizar()
        else:
            self._cursor_desde_lectura()
        self._repintar_lectura()

    def _lectura_desde_cursor(self) -> float:
        linea = self._linea_visual_del_cursor()
        return float(max(0, (linea or 0) - 1))

    def _cursor_desde_lectura(self):
        """La línea que llega a la marca de lectura pasa a ser la actual.
        El avance automático nunca mueve el cursor hacia atrás."""
        lineas_avanzadas = self._lectura_auto or 0.0
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
        self.guion.descartar_provisional()
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

        # Seguimiento por voz: el viewport acompaña al cursor aunque VAD ya
        # cerró la frase o el puntero está encima (el texto final llega
        # después del vad:false). Sólo corre hasta alcanzar el objetivo.
        if self._seguimiento_voz():
            self._tick_voz(dt)
            return

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
            self._repintar_lectura()
            return

        # Animation settled: snap and stop (idle CPU ~0)
        self.scroll_offset = self.scroll_target
        self._timer.stop()
        self.update()

    def _repintar_lectura(self):
        """Repintado por frame de scroll. Excluye el interior de los paneles
        visibles y quietos: son opacos, así que lo que hay debajo no se ve y
        sus botones no necesitan repintarse 60 veces por segundo. Sólo se
        excluye la pastilla en sí: las esquinas redondeadas dejan ver el
        fondo y se siguen repintando."""
        region = QRegion(self.rect())
        for panel in (self.barra, self.ventana_controles):
            if panel.isVisible() and panel.quieto_y_opaco():
                region -= panel.region_opaca()
        self.update(region)

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
        fuente, fm, adv = self._metricas_guion()
        anchos = self._anchos
        cursor = self._cursor_visible()
        # Sin voz el cursor avanza por línea: se destaca la línea completa.
        # Con voz el cursor es una palabra reconocida: se destaca la palabra.
        linea_actual = (self._linea_visual_del_cursor()
                        if self._modo_auto() and not self.script_terminado()
                        else None)
        inicio, fin = self._rango_lineas_guion_visibles()
        xs = self._x_lineas_guion
        alineado = len(xs) == len(self._lineas_guion)
        # Un solo peso para todo el guion: el resaltado usa brillo (y un
        # subrayado aparte), nunca negrita, así las palabras no se corren.
        p.setFont(fuente)
        for li in range(inicio, fin):
            linea = self._lineas_guion[li]
            y = self._baseline_guion(li, fm)
            if y < -adv or y > self.height() + adv:
                continue
            if linea is None:
                continue
            x = xs[li] if alineado else SCRIPT_MARGIN_PX
            for idx, palabra in linea:
                if linea_actual is not None:
                    if li == linea_actual:
                        color = _BLANCO_ACTUAL
                    elif li < linea_actual:
                        color = _BLANCO_LEIDO
                    else:
                        color = _BLANCO_PROXIMO
                elif idx < cursor:
                    color = _BLANCO_LEIDO
                elif idx == cursor:
                    color = _BLANCO_ACTUAL
                elif idx <= cursor + 2:
                    color = _BLANCO_INMEDIATO
                else:
                    color = _BLANCO_PROXIMO
                p.setPen(color)
                texto = palabra + " "
                p.drawText(QPointF(x, y), texto)
                medida = anchos.get(palabra)
                x += medida[1] if medida else fm.horizontalAdvance(texto)

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
        self._paint_subrayado_cursor(bp)
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
        alpha = self._alpha_contorno()
        if alpha > 0:
            self._paint_contorno(p, alpha)
        p.drawImage(QPointF(0, 0), self._buffer_script)

    def _alpha_contorno(self) -> float:
        """Intensidad del contorno: 0 con fondo opaco, hasta 0.85 con fondo
        casi transparente."""
        opacidad = self.cfg["bg_opacity"]
        if opacidad >= CONTORNO_DESDE_OPACIDAD:
            return 0.0
        return 0.85 * min(1.0, (CONTORNO_DESDE_OPACIDAD - opacidad) / 0.35)

    def _paint_contorno(self, p: QPainter, alpha: float):
        """Silueta oscura del texto ya pintado, desplazada 1 px en cuatro
        direcciones. Reutiliza un buffer: sin asignaciones por frame."""
        origen = self._buffer_script
        if self._buffer_contorno is None or self._buffer_contorno.size() != origen.size():
            self._buffer_contorno = QImage(
                origen.size(), QImage.Format.Format_ARGB32_Premultiplied)
            self._buffer_contorno.setDevicePixelRatio(origen.devicePixelRatio())
        sombra = self._buffer_contorno
        sombra.fill(Qt.GlobalColor.transparent)
        sp = QPainter(sombra)
        sp.drawImage(QPointF(0, 0), origen)
        sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        sp.fillRect(QRectF(0, 0, self.width(), self.height()), QColor(0, 0, 0, round(255 * alpha)))
        sp.end()
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawImage(QPointF(dx, dy), sombra)

    def _rect_subrayado_cursor(self):
        """Subrayado de la palabra reconocida (sólo siguiendo la voz)."""
        if (self._modo_auto() or self.script_terminado()
                or len(self._x_lineas_guion) != len(self._lineas_guion)):
            return None
        cursor = self._cursor_visible()
        li = self._linea_por_indice.get(cursor)
        if li is None or self._lineas_guion[li] is None:
            return None
        fuente, fm, adv = self._metricas_guion()
        x = self._x_lineas_guion[li]
        for idx, palabra in self._lineas_guion[li]:
            if idx == cursor:
                y = self._baseline_guion(li, fm) + max(2.0, fm.descent() * 0.5)
                return QRectF(x, y, fm.horizontalAdvance(palabra), 2.0)
            medida = self._anchos.get(palabra)
            x += medida[1] if medida else fm.horizontalAdvance(palabra + " ")
        return None

    def _paint_subrayado_cursor(self, p: QPainter):
        rect = self._rect_subrayado_cursor()
        if rect is None or rect.bottom() < 0 or rect.top() > self.height():
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(con_alpha(ACENTO, 0.9))
        p.drawRoundedRect(rect, 1.0, 1.0)

    def _rango_lineas_guion_visibles(self):
        """Índices candidatos a pintura para el viewport, con overscan."""
        cantidad = len(self._lineas_guion)
        avance = self._line_advance_guion_px()
        if cantidad == 0 or avance <= 0:
            return 0, 0

        fm = self._metricas_guion()[1]
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
            # Con los controles de ventana visibles, el nombre se corre a
            # su izquierda en vez de quedar tapado.
            hasta = self.width()
            if self.ventana_controles.visible_objetivo:
                hasta = self.ventana_controles._posicion_base()[0] + 2
            ui.pintar_nombre_documento(p, self.cfg["font_family"],
                                       self.documento_nombre, derecha + 8, hasta)

    def _paint_marca_lectura(self, p: QPainter):
        """Marca fina a la izquierda de la línea actual: la referencia de
        lectura que se mantiene quieta mientras el texto se mueve."""
        li = self._linea_visual_del_cursor()
        if li is None or self.script_terminado():
            return
        fm = self._metricas_guion()[1]
        y = self._baseline_guion(li, fm)
        if y < 0 or y > self.height():
            return
        # Con texto centrado o a la derecha la marca acompaña el inicio de
        # la línea en vez de quedar sola contra el borde.
        xs = self._x_lineas_guion
        inicio = (xs[li] if len(xs) == len(self._lineas_guion) and xs[li] is not None
                  else SCRIPT_MARGIN_PX)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(con_alpha(ACENTO, 0.9))
        alto = fm.ascent() * 0.8
        p.drawRoundedRect(QRectF(max(7.0, inicio - 13), y - alto + 1, 3, alto),
                          1.5, 1.5)

    def _textos_estado_vacio(self):
        """Título, detalle y color del estado vacío. En ventanas chicas
        se elige una variante más corta en vez de cortar la frase."""
        ancho = self.width() - 40
        fm_t = QFontMetrics(ui.fuente_ui(self.cfg["font_family"], 14, QFont.Weight.DemiBold))
        fm_d = QFontMetrics(ui.fuente_ui(self.cfg["font_family"], 9.5))

        def que_entre(fm, opciones):
            return next((o for o in opciones if fm.horizontalAdvance(o) <= ancho),
                        opciones[-1])

        if self.cargando:
            return ("Cargando…", getattr(self, "_nombre_en_carga", "") or "",
                    con_alpha(TEXTO, 0.55))
        titulo = que_entre(fm_t, ("Cargá un guion para comenzar", "Cargá un guion"))
        if self.documento_error:
            return titulo, self.documento_error, con_alpha(ui.COLOR_ERROR, 0.95)
        if self.voz_conectada or self.parlar_presente:
            detalle = que_entre(fm_d, ("ParlAR conectado · el dictado aparece acá",
                                       "ParlAR conectado"))
            return titulo, detalle, con_alpha(TEXTO, 0.55)
        formatos = document_loader.formatos_legibles()
        detalle = que_entre(fm_d, (f"Arrastrá un archivo o usá Abrir · {formatos}",
                                   "Arrastrá un archivo o usá Abrir", formatos))
        return titulo, detalle, con_alpha(TEXTO, 0.5)

    def _paint_estado_vacio(self, p: QPainter):
        """Sin guion ni dictado: qué hacer, con la acción a mano."""
        titulo, detalle, color = self._textos_estado_vacio()
        ui.pintar_estado_vacio(
            p, self.cfg["font_family"], self.width(), self.height(), titulo,
            detalle, color, 0 if self.cargando else self.btn_vacio.height())

    def _paint_capas_superiores(self, p: QPainter):
        familia = self.cfg["font_family"]
        base = self.height() - 14
        if self.barra.visible_objetivo:
            base -= ControlBar.ALTO + 8   # avisos y parcial no tapan la barra
        aviso_visible = self.toast_opacidad > 0.01 and bool(self.toast_texto)
        if aviso_visible:
            ui.pintar_aviso(p, familia, self.width(), base, self.toast_texto,
                            self.toast_tipo == "error", self.toast_opacidad)
        elif self.partial_text and self._hay_guion() and self.arrastre is None:
            ui.pintar_parcial(p, familia, self.width(), base, self.partial_text)
        if self.arrastre is not None:
            aceptable = self.arrastre == "ok"
            texto = ("Soltá para abrir el guion" if aceptable else
                     f"Formato no soportado · {document_loader.formatos_legibles()}")
            ui.pintar_arrastre(p, familia, QRectF(self.rect()).adjusted(6, 6, -6, -6),
                               max(4, self.cfg["corner_radius"] - 4), aceptable, texto)
        if not self.cfg["position_locked"]:
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
        self.ventana_controles.sincronizar()
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
        if self.cfg["position_locked"]:
            # Sin esto, arrastrar una ventana bloqueada parece no responder.
            self._notificar("Posición bloqueada · usá el candado para moverla")
        else:
            self._iniciar_movimiento(in_grip)
        e.accept()

    def _iniciar_movimiento(self, redimensionar: bool):
        """Delegado al compositor (X11 y Wayland). Bloqueado nunca llega acá."""
        wh = self.windowHandle()
        if wh is None:
            return
        if redimensionar:
            wh.startSystemResize(Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
        else:
            wh.startSystemMove()

    def mouseMoveEvent(self, e):
        g = self.cfg["resize_grip"]
        in_grip = (
            e.position().x() >= self.width() - g
            and e.position().y() >= self.height() - g
        )
        if self.cfg["position_locked"]:
            forma = Qt.CursorShape.ArrowCursor
        elif in_grip:
            forma = Qt.CursorShape.SizeFDiagCursor
        else:
            forma = Qt.CursorShape.OpenHandCursor
        self.setCursor(QCursor(forma))
        self._autohide.actividad()
        super().mouseMoveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if (getattr(self, "guion", None) is not None and self._hay_guion()
                and self.width() != self._guion_layout_width):
            self._reflow_y_anclar_guion()
        elif getattr(self, "guion", None) is not None and self._seguimiento_voz():
            self._seguir_cursor_voz(inmediato=True)   # cambió sólo el alto
        if getattr(self, "barra", None) is not None:
            self.barra.reubicar()
            self.ventana_controles.reubicar()
            self._ubicar_boton_vacio()
            self._recordar_geometria()

    def event(self, e):
        # Con los paneles ocultos sus botones no reciben foco: Tab (y
        # Shift+Tab) primero los muestran para que el teclado llegue a todos
        # los controles. Sólo teclas reales: activar la ventana no los abre.
        if (e.type() == QEvent.Type.KeyPress
                and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                and not (self.barra.visible_objetivo
                         and self.ventana_controles.visible_objetivo)):
            self._autohide.actividad()
        return super().event(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        if getattr(self, "barra", None) is not None:
            self._recordar_geometria()

    def closeEvent(self, e):
        self.guardar_preferencias()
        super().closeEvent(e)
        self.cerrada.emit()

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
        # El puntero muestra los controles; pausar es una preferencia.
        self.hover_paused = bool(self.cfg["pause_on_hover"])
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
    # Preferencias (Configuración, bandeja, controles de ventana)
    # ------------------------------------------------------------------
    def _cambiar_preferencia(self, clave: str, valor) -> bool:
        if self.cfg.get(clave) == valor:
            return False
        self.cfg[clave] = valor
        self._programar_guardado({clave: valor})
        return True

    def _preferencias_aplicadas(self):
        self._refrescar_ui()
        self.update()
        self.preferencias_cambiadas.emit()

    def set_alineacion(self, alineacion: str):
        if alineacion not in guionar_config.ALINEACIONES:
            return
        if self._cambiar_preferencia("text_alignment", alineacion):
            if self._hay_guion():
                self._reflow_y_anclar_guion()
            self._preferencias_aplicadas()

    def set_opacidad(self, opacidad: float):
        """Opacidad del fondo del panel; el texto no cambia."""
        opacidad = round(max(0.0, min(1.0, float(opacidad))), 2)
        if self._cambiar_preferencia("bg_opacity", opacidad):
            self._preferencias_aplicadas()

    def set_tamano_texto(self, puntos: int):
        """Tamaño del guion; el de dictado se deriva como en --font-size."""
        puntos = max(self.FONT_MIN, min(self.FONT_MAX, int(puntos)))
        if puntos == self.cfg["font_size_context"]:
            return
        self._aplicar_fuentes(max(14, min(self.FONT_MAX, round(puntos * 5 / 3))), puntos)
        self._preferencias_aplicadas()

    def _aplicar_fuentes(self, actual: int, contexto: int):
        self._cambiar_preferencia("font_size_current", actual)
        self._cambiar_preferencia("font_size_context", contexto)
        if self._hay_guion():
            self._reflow_y_anclar_guion()

    def restaurar_apariencia(self):
        """Vuelve sólo Apariencia a sus valores por defecto."""
        self._cambiar_preferencia("bg_opacity", DEFAULTS["bg_opacity"])
        alineacion_cambio = self._cambiar_preferencia(
            "text_alignment", DEFAULTS["text_alignment"])
        self._aplicar_fuentes(DEFAULTS["font_size_current"],
                              DEFAULTS["font_size_context"])
        if alineacion_cambio and self._hay_guion():
            self._reflow_y_anclar_guion()
        self._preferencias_aplicadas()

    def set_pausa_hover(self, activa: bool):
        if self._cambiar_preferencia("pause_on_hover", bool(activa)):
            if not activa and self.hover_paused:
                self.hover_paused = False
                self._request_animation()
            self._preferencias_aplicadas()

    def set_bloqueo(self, bloqueada: bool):
        if self._cambiar_preferencia("position_locked", bool(bloqueada)):
            self._preferencias_aplicadas()

    def set_siempre_encima(self, activo: bool):
        """Pin: mantener GuionAR sobre las demás ventanas. Distinto del
        candado (que sólo impide mover/redimensionar); conviven."""
        activo = bool(activo)
        if self._cambiar_preferencia("always_on_top", activo):
            self._aplicar_siempre_encima(activo)
            self._preferencias_aplicadas()

    def alternar_siempre_encima(self):
        self.set_siempre_encima(not self.cfg["always_on_top"])
        self._feedback_teclado("Fijada sobre otras ventanas" if self.cfg["always_on_top"]
                               else "Ya no está fijada sobre otras ventanas")

    def _aplicar_siempre_encima(self, activo: bool):
        """Sin recrear ni ocultar la ventana: posición, tamaño, foco y
        estado quedan intactos (ver ui_controls.aplicar_siempre_encima)."""
        ui.aplicar_siempre_encima(self, activo)
        if activo and self.isVisible():
            self.raise_()   # fijarla la trae al frente en el acto

    def alternar_bloqueo(self):
        self.set_bloqueo(not self.cfg["position_locked"])
        self._feedback_teclado("Posición bloqueada" if self.cfg["position_locked"]
                               else "Posición desbloqueada")

    def set_recordar_geometria(self, recordar: bool):
        if self._cambiar_preferencia("remember_geometry", bool(recordar)):
            self._recordar_geometria()
            self._preferencias_aplicadas()

    def set_autoocultar_controles(self, activo: bool):
        if self._cambiar_preferencia("auto_hide_controls", bool(activo)):
            self._autohide.set_habilitado(activo)
            if activo and not self.underMouse():
                self._autohide.salida()
            self._preferencias_aplicadas()

    # ---------------------------------------------------------- persistencia
    def _programar_guardado(self, cambios: dict):
        if not self._persistir:
            return
        self._cambios_pendientes.update(cambios)
        self._timer_guardado.start(GUARDADO_DIFERIDO_MS)

    def guardar_preferencias(self):
        """Escribe ya lo pendiente (también al cerrar o salir)."""
        self._timer_guardado.stop()
        if not self._persistir or not self._cambios_pendientes:
            return
        cambios, self._cambios_pendientes = self._cambios_pendientes, {}
        try:
            guionar_config.actualizar(cambios)
        except OSError as e:
            print(f"[config] no se pudieron guardar las preferencias: {e}",
                  file=sys.stderr)

    def _geometria_actual(self) -> dict:
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height()}

    def _recordar_geometria(self):
        if not self.cfg["remember_geometry"] or not self.isVisible():
            return
        geometria = self._geometria_actual()
        if geometria != self.cfg.get("window_geometry"):
            self.cfg["window_geometry"] = geometria
            self._programar_guardado({"window_geometry": geometria})

    def _restaurar_geometria(self, geometria: dict):
        pantallas = [s.availableGeometry() for s in QGuiApplication.screens()]
        rect = geometria_visible(
            QRect(geometria["x"], geometria["y"],
                  geometria["width"], geometria["height"]),
            pantallas, self.minimumSize())
        self.resize(rect.size())
        self.move(rect.topLeft())

    # ---------------------------------------------------------- ventana
    def set_tray_disponible(self, disponible: bool):
        self.tray_disponible = bool(disponible)
        self._refrescar_ui()

    def ocultar_ventana(self):
        """Ocultar desde la UI: el proceso sigue y la bandeja lo recupera.
        Sin bandeja no hay forma de volver, así que no se oculta."""
        if not self.tray_disponible:
            return
        self._oculta_por_usuario = True
        self.guardar_preferencias()
        self.hide()

    def mostrar_ventana(self):
        """Mostrar desde la bandeja: también deshace Ghost."""
        self._oculta_por_usuario = False
        self.hidden = False
        self.show()
        self.raise_()
        self.activateWindow()

    def abrir_configuracion(self):
        """Una sola ventana de Configuración: si ya existe, va al frente."""
        if self._configuracion is None:
            from desktop_shell import VentanaConfiguracion
            self._configuracion = VentanaConfiguracion(self)
        self._configuracion.sincronizar()
        self._configuracion.show()
        self._configuracion.raise_()
        self._configuracion.activateWindow()
        return self._configuracion

    def salir(self):
        """Cerrar GuionAR de verdad (botón cerrar, bandeja → Salir)."""
        self.guardar_preferencias()
        self._salir_app()

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
        if self._oculta_por_usuario and not self.hidden:
            self.mostrar_ventana()  # oculta desde la bandeja: toggle la trae
            return
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
        # Mismo camino que Configuración: queda guardado y sincronizado.
        self._cambiar_preferencia("font_size_current", max(
            14, min(self.FONT_MAX, self.cfg["font_size_current"] + delta)))
        self._cambiar_preferencia("font_size_context", max(
            self.FONT_MIN, min(self.FONT_MAX, self.cfg["font_size_context"] + delta // 2)))
        if self.guion is not None and self.guion.valido:
            self._reflow_y_anclar_guion()
        tamano = self.cfg["font_size_context" if self._hay_guion() else "font_size_current"]
        self._feedback_teclado(f"Texto {tamano} pt")
        self.barra.sincronizar()
        self.update()
        self.preferencias_cambiadas.emit()


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
    app.setApplicationName("GuionAR")
    app.setDesktopFileName("guionar")   # vincula la ventana con guionar.desktop
    # Con la bandeja, cerrar Configuración con el overlay oculto no debe
    # terminar el proceso. La salida es explícita (Cerrar, Salir, Ctrl+Q).
    app.setQuitOnLastWindowClosed(False)
    from desktop_shell import crear_bandeja, icono_app
    app.setWindowIcon(icono_app())

    # El timer le devuelve periódicamente control al intérprete para ejecutar
    # el handler Python. SIGINT pide la misma salida Qt que Ctrl+Q; al volver
    # de app.exec(), el finally ejecuta el shutdown cooperativo del bridge.
    def _salir_por_sigint(_signum, _frame):
        app.quit()

    signal.signal(signal.SIGINT, _salir_por_sigint)
    _sigint_pump = QTimer()
    _sigint_pump.timeout.connect(lambda: None)
    _sigint_pump.start(200)

    overlay = TeleprompterOverlay(cfg, persistir=True)
    overlay.setWindowIcon(icono_app())
    overlay.cerrada.connect(app.quit)
    app.aboutToQuit.connect(overlay.guardar_preferencias)
    bandeja = crear_bandeja(overlay)   # None si el escritorio no tiene bandeja
    overlay.set_tray_disponible(bandeja is not None)
    if bandeja is not None:
        bandeja.show()
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
