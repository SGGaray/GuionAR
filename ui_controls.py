"""Controles y cromo visual del overlay.

Sólo presentación: los botones llaman a los mismos métodos que los atajos
de teclado, así que no hay una segunda copia del estado. La barra lee el
estado del overlay en ``sincronizar()``. Las funciones ``pintar_*`` dibujan
chip de estado, nombre del documento, estado vacío, avisos y drag & drop a
partir de valores ya decididos por el overlay.

Motion: sólo opacidad y una traslación corta, con duraciones breves
(hover ~110 ms, mostrar/ocultar ~130 ms). Nada se anima en reposo.
"""

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QRegion,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget,
)

# Paleta compartida con guionar.py: un único acento cálido y blancos con
# distintas opacidades. Sin gradientes ni glow.
ACENTO = QColor(255, 196, 92)
PANEL = QColor(16, 18, 23)
TEXTO = QColor(255, 255, 255)
COLOR_ERROR = QColor(255, 128, 112)
COLOR_ACTIVO = QColor(96, 214, 132)
COLOR_INACTIVO = QColor(160, 164, 172)

# Fila superior: chip de estado, nombre del documento y controles de
# ventana comparten centro vertical y margen lateral.
FILA_SUPERIOR_CENTRO = 20
MARGEN_LATERAL = 8
ALTO_PASTILLA = 20

HOVER_MS = 110
MOSTRAR_MS = 130
OCULTAR_MS = 150
DESPLAZAMIENTO_PX = 6


def aplicar_siempre_encima(widget: QWidget, activo: bool):
    """Cambia WindowStaysOnTopHint de una ventana sin ocultarla.

    QWidget.setWindowFlags() sobre una ventana visible la oculta (llama a
    setParent). Si la ventana nativa ya existe, se actualizan los flags de
    la QWindow (en X11 Qt avisa al gestor con _NET_WM_STATE_ABOVE sin volver
    a mapearla) y se registra el mismo valor en el widget con
    overrideWindowFlags para que no se desincronicen.
    """
    sobre = Qt.WindowType.WindowStaysOnTopHint
    flags = widget.windowFlags() | sobre if activo else widget.windowFlags() & ~sobre
    if flags == widget.windowFlags() and (
            widget.windowHandle() is None or widget.windowHandle().flags() == flags):
        return
    ventana = widget.windowHandle()
    if ventana is None:
        widget.setWindowFlags(flags)   # todavía sin ventana nativa: sin efectos
        return
    widget.overrideWindowFlags(flags)
    ventana.setFlags(flags)


def con_alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class _BotonBase(QAbstractButton):
    """Botón pintado a mano con estados hover/pressed/focus/disabled."""

    def __init__(self, tooltip: str, parent=None):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Un click no roba el foco (Espacio sigue siendo Play/Pausa global);
        # Tab sí lo da, con anillo visible.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._hover = 0.0
        self._foco_teclado = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(HOVER_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_hover)

    def _set_hover(self, valor):
        self._hover = float(valor)
        self.update()

    def _animar_hover(self, destino: float):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(destino)
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._animar_hover(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animar_hover(0.0)
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self._foco_teclado = e.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        foco = getattr(self.parent(), "foco_en_control", None)
        if callable(foco):
            foco()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._foco_teclado = False
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e):
        if not self.isEnabled():
            self._anim.stop()
            self._hover = 0.0
        super().changeEvent(e)

    # Acción destructiva (Cerrar): el hover se tiñe de rojo para que no
    # se confunda con las acciones vecinas.
    destructivo = False

    def _fondo(self, p: QPainter, rect: QRectF, radio: float, base=0.0):
        if not self.isEnabled():
            alpha = base
        elif self.isDown():
            alpha = base + 0.20      # pressed: inmediato, sin animación
        else:
            alpha = base + 0.11 * self._hover
        if alpha > 0:
            path = QPainterPath()
            path.addRoundedRect(rect, radio, radio)
            tinte = COLOR_ERROR if self.destructivo and self.isEnabled() else TEXTO
            p.fillPath(path, con_alpha(tinte, alpha * (1.6 if tinte is COLOR_ERROR else 1.0)))
        if self._foco_teclado and self.hasFocus():
            p.setPen(QPen(con_alpha(ACENTO, 0.9), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), radio, radio)

    def _color_tinta(self) -> QColor:
        if not self.isEnabled():
            return con_alpha(TEXTO, 0.28)
        return con_alpha(TEXTO, 0.78 + 0.22 * max(self._hover, 1.0 if self.isDown() else 0.0))


class IconButton(_BotonBase):
    """Botón cuadrado con un glifo vectorial simple."""

    LADO = 30

    def __init__(self, icono: str, tooltip: str, parent=None):
        super().__init__(tooltip, parent)
        self.icono = icono
        self.activo = False   # estado encendido (p. ej. posición bloqueada)
        self.setFixedSize(self.LADO, self.LADO)

    def set_activo(self, activo: bool):
        if activo != self.activo:
            self.activo = activo
            self.update()

    def set_icono(self, icono: str, tooltip: str | None = None):
        if tooltip is not None and tooltip != self.toolTip():
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        if icono != self.icono:
            self.icono = icono
            self.update()

    def sizeHint(self):
        return QSize(self.LADO, self.LADO)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        self._fondo(p, rect, 8)
        tinta = self._color_tinta()
        if self.activo and self.isEnabled():
            tinta = con_alpha(ACENTO, 0.95)   # encendido: no depende sólo del glifo
        dibujar_icono(p, self.icono, QRectF(self.rect()).center(), tinta)
        p.end()


class TextButton(_BotonBase):
    """Botón de texto para la acción principal del estado vacío."""

    def __init__(self, texto: str, tooltip: str, parent=None, icono=None):
        super().__init__(tooltip, parent)
        self.texto = texto
        self.icono = icono
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)
        ancho = self.fontMetrics().horizontalAdvance(texto) + (58 if icono else 36)
        self.setFixedSize(ancho, 36)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        self._fondo(p, rect, 10, base=0.12)
        tinta = self._color_tinta()
        x = 18.0
        if self.icono:
            dibujar_icono(p, self.icono, QPointF(x + 7, rect.center().y()), tinta)
            x += 22
        p.setPen(tinta)
        p.setFont(self.font())
        fm = self.fontMetrics()
        y = rect.center().y() + (fm.ascent() - fm.descent()) / 2
        p.drawText(QPointF(x, y), self.texto)
        p.end()


def dibujar_icono(p: QPainter, icono: str, centro: QPointF, color: QColor):
    """Glifos de 14 px aprox., trazados con QPainterPath (nítidos a
    cualquier escala, sin depender del tema de íconos del escritorio)."""
    cx, cy = centro.x(), centro.y()
    p.save()
    pen = QPen(color, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if icono == "play":
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 6.5)
        path.lineTo(cx + 6.5, cy)
        path.lineTo(cx - 4, cy + 6.5)
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif icono == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(cx - 5.5, cy - 6.5, 4, 13), 1.2, 1.2)
        p.drawRoundedRect(QRectF(cx + 1.5, cy - 6.5, 4, 13), 1.2, 1.2)
    elif icono == "replay":
        p.drawArc(QRectF(cx - 6, cy - 6, 12, 12), 100 * 16, 290 * 16)
        path = QPainterPath()
        path.moveTo(cx - 1.5, cy - 9)
        path.lineTo(cx + 2.2, cy - 6)
        path.lineTo(cx - 1.5, cy - 3)
        p.drawPath(path)
    elif icono in ("up", "down"):
        s = -1 if icono == "up" else 1
        path = QPainterPath()
        path.moveTo(cx - 5.5, cy - 2.5 * s)
        path.lineTo(cx, cy + 3 * s)
        path.lineTo(cx + 5.5, cy - 2.5 * s)
        p.drawPath(path)
    elif icono in ("minus", "plus"):
        p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
        if icono == "plus":
            p.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 5))
    elif icono == "open":
        path = QPainterPath()
        path.moveTo(cx - 7, cy + 5.5)
        path.lineTo(cx - 7, cy - 5.5)
        path.lineTo(cx - 2.5, cy - 5.5)
        path.lineTo(cx - 0.5, cy - 3.5)
        path.lineTo(cx + 7, cy - 3.5)
        path.lineTo(cx + 7, cy + 5.5)
        path.closeSubpath()
        p.drawPath(path)
    elif icono in ("pin", "pin-off"):
        # Chinche: derecha = fijada, inclinada = suelta (la forma también
        # comunica el estado, no sólo el color de acento).
        if icono == "pin-off":
            p.translate(cx, cy)
            p.rotate(40)
            p.translate(-cx, -cy)
        path = QPainterPath()
        path.moveTo(cx - 3.5, cy - 6.5)
        path.lineTo(cx + 3.5, cy - 6.5)
        path.moveTo(cx - 2.2, cy - 6.5)
        path.lineTo(cx - 2.2, cy - 2.2)
        path.lineTo(cx - 5.0, cy + 0.8)
        path.lineTo(cx + 5.0, cy + 0.8)
        path.lineTo(cx + 2.2, cy - 2.2)
        path.lineTo(cx + 2.2, cy - 6.5)
        path.moveTo(cx, cy + 0.8)
        path.lineTo(cx, cy + 7.0)
        p.drawPath(path)
    elif icono in ("lock", "unlock"):
        p.drawRoundedRect(QRectF(cx - 5.5, cy - 1, 11, 8), 1.6, 1.6)
        arco = QPainterPath()
        izquierda = cx - 3.5 if icono == "lock" else cx - 0.5
        arco.moveTo(izquierda, cy - 1)
        arco.lineTo(izquierda, cy - 3.5)
        arco.arcTo(QRectF(izquierda, cy - 7.5, 7, 7), 180, -180)
        if icono == "lock":
            arco.lineTo(izquierda + 7, cy - 1)
        p.drawPath(arco)
    elif icono == "hide":
        p.drawLine(QPointF(cx - 5.5, cy + 4), QPointF(cx + 5.5, cy + 4))
    elif icono == "close":
        p.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
        p.drawLine(QPointF(cx + 4.5, cy - 4.5), QPointF(cx - 4.5, cy + 4.5))
    elif icono == "settings":
        # Controles deslizantes: a 14 px se reconoce mejor que un engranaje
        # (que se confunde con "brillo").
        for dy, perilla in ((-4.5, 2.5), (0, -2.5), (4.5, 1.0)):
            p.drawLine(QPointF(cx - 6.5, cy + dy), QPointF(cx + 6.5, cy + dy))
            p.setBrush(QColor(PANEL))
            p.drawEllipse(QPointF(cx + perilla, cy + dy), 2.1, 2.1)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif icono in ("text-smaller", "text-larger"):
        f = QFont(p.font())
        f.setPixelSize(11 if icono == "text-smaller" else 16)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(color)
        rect = QRectF(cx - 10, cy - 10, 20, 20)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "A")
    p.restore()


class _PanelFlotante(QWidget):
    """Panel de botones que aparece y desaparece con opacidad y una
    traslación corta. Las subclases deciden contenido y posición."""

    ALTO = 40
    DIRECCION = 1    # 1: entra subiendo (abajo); -1: entra bajando (arriba)

    def __init__(self, overlay):
        super().__init__(overlay)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(self.ALTO)
        self._efecto = QGraphicsOpacityEffect(self)
        self._efecto.setOpacity(0.0)
        self.setGraphicsEffect(self._efecto)
        self._progreso = 0.0
        self.visible_objetivo = False
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._aplicar_progreso)
        self._anim.finished.connect(self._animacion_terminada)
        self.hide()

    @property
    def overlay(self):
        # Sin referencia propia al overlay: evita un ciclo panel<->overlay.
        return self.parentWidget()

    def sincronizar(self):
        pass

    def reubicar(self):
        self.resize(self.sizeHint().width(), self.ALTO)
        self._mover(self._progreso)

    def _posicion_base(self):
        raise NotImplementedError

    def _mover(self, progreso):
        x, y = self._posicion_base()
        self.move(x, y + self.DIRECCION * round((1.0 - progreso) * DESPLAZAMIENTO_PX))

    def foco_en_control(self):
        """Navegar con Tab muestra el panel aunque el puntero no esté."""
        self.mostrar()

    # ---------------------------------------------------------- motion
    def mostrar(self):
        if self.visible_objetivo:
            return
        self.visible_objetivo = True
        self.sincronizar()
        self.reubicar()
        self.show()
        self._animar(1.0, MOSTRAR_MS)

    def ocultar(self):
        if not self.visible_objetivo:
            return
        if self.tiene_foco():
            return  # foco de teclado adentro: no esconder lo que se usa
        self.visible_objetivo = False
        self._animar(0.0, OCULTAR_MS)

    def _animar(self, destino, duracion):
        self._anim.stop()
        # El efecto de opacidad pinta el panel fuera de pantalla en cada
        # repintado: sólo se usa mientras dura la transición.
        self._efecto.setEnabled(True)
        self._anim.setDuration(duracion)
        self._anim.setStartValue(self._progreso)
        self._anim.setEndValue(destino)
        self._anim.start()

    def _aplicar_progreso(self, valor):
        self._progreso = float(valor)
        self._efecto.setOpacity(self._progreso)
        self._mover(self._progreso)

    def _animacion_terminada(self):
        if self.visible_objetivo:
            self._efecto.setEnabled(False)   # opaco y quieto: sin costo extra
        else:
            self.hide()

    def quieto_y_opaco(self) -> bool:
        """Visible del todo y sin transición en curso (fondo opaco)."""
        return self.visible_objetivo and self._progreso >= 1.0 and not self._efecto.isEnabled()

    def region_opaca(self) -> QRegion:
        """Forma exacta de la pastilla (1 px adentro del borde
        antialiaseado), en coordenadas del overlay."""
        rect = QRectF(self.geometry()).adjusted(1.5, 1.5, -1.5, -1.5)
        radio = min(12.0, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radio, radio)
        return QRegion(path.toFillPolygon().toPolygon())

    def tiene_foco(self) -> bool:
        return any(w.hasFocus() for w in self.findChildren(QAbstractButton))

    def _separadores(self):
        return ()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radio = min(12, rect.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radio, radio)
        # Opaco: el overlay puede omitir repintar lo que queda debajo.
        p.fillPath(path, PANEL)
        p.setPen(QPen(con_alpha(TEXTO, 0.08), 1))
        p.drawPath(path)
        # Separadores finos entre grupos.
        p.setPen(QPen(con_alpha(TEXTO, 0.12), 1))
        margen = self.height() * 0.28
        for a, b in self._separadores():
            if not (a.isVisible() and b.isVisible()):
                continue
            x = (a.geometry().right() + b.geometry().left()) / 2 + 0.5
            p.drawLine(QPointF(x, margen), QPointF(x, self.height() - margen))
        p.end()


class ControlBar(_PanelFlotante):
    """Barra compacta del teleprompter, centrada abajo, visible sólo
    durante la interacción.

    El overlay es dueño del estado; la barra sólo lo refleja y delega.
    """

    ALTO = 40
    MARGEN_INFERIOR = 12

    def __init__(self, overlay):
        super().__init__(overlay)
        self.btn_abrir = IconButton("open", "Abrir guion (Ctrl+O)", self)
        self.btn_anterior = IconButton("up", "Oración anterior (Re Pág)", self)
        self.btn_play = IconButton("pause", "Pausar (Espacio)", self)
        self.btn_siguiente = IconButton("down", "Oración siguiente (Av Pág)", self)
        self.btn_lento = IconButton("minus", "Más lento (-)", self)
        self.lbl_velocidad = QLabel(self)
        self.btn_rapido = IconButton("plus", "Más rápido (+)", self)
        self.btn_texto_menor = IconButton("text-smaller", "Texto más chico (↓)", self)
        self.btn_texto_mayor = IconButton("text-larger", "Texto más grande (↑)", self)

        f = QFont(self.font())
        f.setPointSizeF(9.5)
        self.lbl_velocidad.setFont(f)
        self.lbl_velocidad.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_velocidad.setMinimumWidth(42)
        self.lbl_velocidad.setToolTip("Velocidad")
        self.lbl_velocidad.setStyleSheet("color: rgba(255,255,255,190);")
        # La etiqueta no debe tragarse el arrastre de la ventana.
        self.lbl_velocidad.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.btn_abrir.clicked.connect(overlay.elegir_documento)
        self.btn_anterior.clicked.connect(overlay.oracion_anterior)
        self.btn_play.clicked.connect(overlay.toggle_pause)
        self.btn_siguiente.clicked.connect(overlay.oracion_siguiente)
        self.btn_lento.clicked.connect(overlay.speed_down)
        self.btn_rapido.clicked.connect(overlay.speed_up)
        self.btn_texto_menor.clicked.connect(overlay.achicar_texto)
        self.btn_texto_mayor.clicked.connect(overlay.agrandar_texto)

        fila = QHBoxLayout(self)
        fila.setContentsMargins(6, 5, 6, 5)
        fila.setSpacing(2)
        grupos = (
            (self.btn_abrir,),
            (self.btn_anterior, self.btn_play, self.btn_siguiente),
            (self.btn_lento, self.lbl_velocidad, self.btn_rapido),
            (self.btn_texto_menor, self.btn_texto_mayor),
        )
        for i, grupo in enumerate(grupos):
            if i:
                fila.addSpacing(10)
            for w in grupo:
                fila.addWidget(w)
        self._grupo_texto = grupos[3]

    # ---------------------------------------------------------- estado
    def sincronizar(self):
        ov = self.overlay
        hay_guion = ov.guion is not None and ov.guion.valido
        if ov.script_terminado():
            self.btn_play.set_icono("replay", "Empezar de nuevo (Espacio)")
        elif ov.paused:
            self.btn_play.set_icono("play", "Reproducir (Espacio)")
        else:
            self.btn_play.set_icono("pause", "Pausar (Espacio)")
        for b in (self.btn_anterior, self.btn_siguiente):
            b.setEnabled(hay_guion)
        self.btn_lento.setEnabled(ov.speed_pps > ov.SPEED_MIN)
        self.btn_rapido.setEnabled(ov.speed_pps < ov.SPEED_MAX)
        self.lbl_velocidad.setText(f"Vel {ov.nivel_velocidad()}")
        self.btn_texto_menor.setEnabled(ov.cfg["font_size_context"] > ov.FONT_MIN)
        self.btn_texto_mayor.setEnabled(ov.cfg["font_size_context"] < ov.FONT_MAX)

    def reubicar(self):
        """Centra la barra abajo; en ventanas angostas omite tamaño de texto."""
        disponible = self.overlay.width() - 24
        for w in self._grupo_texto:
            w.setVisible(True)
        ancho = self.sizeHint().width()
        if ancho > disponible:
            for w in self._grupo_texto:
                w.setVisible(False)
            ancho = self.sizeHint().width()
        ancho = min(ancho, max(0, disponible))
        self.resize(ancho, self.ALTO)
        self._mover(self._progreso)

    def _base_y(self):
        return self.overlay.height() - self.ALTO - self.MARGEN_INFERIOR

    def _posicion_base(self):
        return (self.overlay.width() - self.width()) // 2, self._base_y()

    def _separadores(self):
        return ((self.btn_abrir, self.btn_anterior),
                (self.btn_siguiente, self.btn_lento),
                (self.btn_rapido, self.btn_texto_menor))


class WindowControls(_PanelFlotante):
    """Controles de la ventana, arriba a la derecha y separados del
    teleprompter: fijar · bloquear | ocultar · configuración | cerrar."""

    ALTO = 34
    DIRECCION = -1

    def __init__(self, overlay):
        super().__init__(overlay)
        self.btn_fijar = IconButton("pin", "Dejar de mantener sobre otras ventanas", self)
        self.btn_bloquear = IconButton("unlock", "Bloquear posición", self)
        self.btn_ocultar = IconButton("hide", "Ocultar (seguí desde la bandeja)", self)
        self.btn_configuracion = IconButton("settings", "Configuración", self)
        self.btn_cerrar = IconButton("close", "Cerrar GuionAR", self)
        for b in (self.btn_fijar, self.btn_bloquear, self.btn_ocultar,
                  self.btn_configuracion, self.btn_cerrar):
            b.setFixedSize(28, 28)

        self.btn_fijar.clicked.connect(overlay.alternar_siempre_encima)
        self.btn_bloquear.clicked.connect(overlay.alternar_bloqueo)
        self.btn_ocultar.clicked.connect(overlay.ocultar_ventana)
        self.btn_configuracion.clicked.connect(overlay.abrir_configuracion)
        self.btn_cerrar.clicked.connect(overlay.salir)

        fila = QHBoxLayout(self)
        fila.setContentsMargins(3, 3, 3, 3)
        fila.setSpacing(1)
        self.btn_cerrar.destructivo = True
        fila.addWidget(self.btn_fijar)      # fijar y bloquear: estado de la ventana
        fila.addWidget(self.btn_bloquear)
        fila.addSpacing(8)
        fila.addWidget(self.btn_ocultar)
        fila.addWidget(self.btn_configuracion)
        fila.addSpacing(8)   # Cerrar separado: un click errado no termina la app
        fila.addWidget(self.btn_cerrar)

    def sincronizar(self):
        ov = self.overlay
        fijada = ov.cfg["always_on_top"]
        if fijada:
            self.btn_fijar.set_icono("pin", "Dejar de mantener sobre otras ventanas")
        else:
            self.btn_fijar.set_icono("pin-off", "Mantener sobre otras ventanas")
        self.btn_fijar.set_activo(fijada)
        bloqueada = ov.cfg["position_locked"]
        if bloqueada:
            self.btn_bloquear.set_icono("lock", "Desbloquear posición")
        else:
            self.btn_bloquear.set_icono("unlock", "Bloquear posición")
        self.btn_bloquear.set_activo(bloqueada)
        self.btn_ocultar.setEnabled(ov.tray_disponible)
        self.btn_ocultar.setToolTip(
            "Ocultar (seguí desde la bandeja)" if ov.tray_disponible else
            "Ocultar requiere la bandeja del sistema")

    def _posicion_base(self):
        return (self.overlay.width() - self.width() - MARGEN_LATERAL,
                FILA_SUPERIOR_CENTRO - self.ALTO // 2)

    def _separadores(self):
        return ((self.btn_bloquear, self.btn_ocultar),
                (self.btn_configuracion, self.btn_cerrar))


class AutoHide:
    """Temporizadores de los paneles: aparecen con el puntero y
    desaparecen al volver a leer (puntero afuera o quieto un rato).
    Con ``habilitado`` en False quedan siempre visibles."""

    QUIETO_MS = 2600
    SALIDA_MS = 350

    def __init__(self, *paneles):
        self.paneles = paneles
        self.barra = paneles[0]
        self.habilitado = True
        self._quieto = QTimer(self.barra)
        self._quieto.setSingleShot(True)
        self._quieto.timeout.connect(self._quieto_vencido)
        self._salida = QTimer(self.barra)
        self._salida.setSingleShot(True)
        self._salida.timeout.connect(self._ocultar_todos)

    def set_habilitado(self, habilitado: bool):
        self.habilitado = bool(habilitado)
        if not self.habilitado:
            self._quieto.stop()
            self._salida.stop()
            for panel in self.paneles:
                panel.mostrar()

    def actividad(self):
        self._salida.stop()
        for panel in self.paneles:
            panel.mostrar()
        if self.habilitado:
            self._quieto.start(self.QUIETO_MS)

    def salida(self):
        if not self.habilitado:
            return
        self._quieto.stop()
        self._salida.start(self.SALIDA_MS)

    def _ocultar_todos(self):
        if not self.habilitado:
            return
        if any(panel.tiene_foco() for panel in self.paneles):
            return  # navegación con teclado en curso: todo sigue a mano
        for panel in self.paneles:
            panel.ocultar()

    def _quieto_vencido(self):
        if any(panel.underMouse() for panel in self.paneles):
            self._quieto.start(self.QUIETO_MS)
            return
        self._ocultar_todos()


# ---------------------------------------------------------------- cromo pintado

def fuente_ui(familia: str, puntos: float, peso=QFont.Weight.Normal) -> QFont:
    f = QFont(familia)
    f.setPointSizeF(puntos)
    f.setWeight(peso)
    return f


def _pastilla(p: QPainter, rect: QRectF, alpha: float):
    path = QPainterPath()
    path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    p.fillPath(path, con_alpha(PANEL, alpha))


def _baseline_centrada(fm, rect: QRectF) -> float:
    return rect.center().y() + (fm.ascent() - fm.descent()) / 2


def _centrado(p: QPainter, fm, texto: str, ancho: float, baseline: float):
    texto = fm.elidedText(texto, Qt.TextElideMode.ElideRight, int(ancho - 40))
    p.drawText(QPointF((ancho - fm.horizontalAdvance(texto)) / 2, baseline), texto)


def pintar_chip_estado(p: QPainter, familia: str, etiqueta: str,
                       color: QColor) -> float:
    """Chip arriba a la izquierda. Devuelve su borde derecho."""
    f = fuente_ui(familia, 8.5, QFont.Weight.DemiBold)
    fm = QFontMetrics(f)
    rect = QRectF(MARGEN_LATERAL, FILA_SUPERIOR_CENTRO - ALTO_PASTILLA / 2,
                  fm.horizontalAdvance(etiqueta) + 28, ALTO_PASTILLA)
    _pastilla(p, rect, 0.72)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(rect.left() + 11, rect.center().y()), 3.2, 3.2)
    p.setFont(f)
    p.setPen(con_alpha(TEXTO, 0.86))
    p.drawText(QPointF(rect.left() + 19, _baseline_centrada(fm, rect)), etiqueta)
    return rect.right()


def pintar_nombre_documento(p: QPainter, familia: str, nombre: str,
                            desde_x: float, ancho_ventana: float):
    """Nombre del documento activo, discreto, arriba a la derecha
    (``ancho_ventana`` es el borde derecho disponible)."""
    f = fuente_ui(familia, 8.5)
    fm = QFontMetrics(f)
    maximo = ancho_ventana - desde_x - MARGEN_LATERAL - 20
    if maximo < 40:
        return
    texto = fm.elidedText(nombre, Qt.TextElideMode.ElideMiddle, int(maximo))
    ancho = fm.horizontalAdvance(texto) + 20
    rect = QRectF(ancho_ventana - MARGEN_LATERAL - ancho,
                  FILA_SUPERIOR_CENTRO - ALTO_PASTILLA / 2, ancho, ALTO_PASTILLA)
    _pastilla(p, rect, 0.6)
    p.setFont(f)
    p.setPen(con_alpha(TEXTO, 0.6))
    p.drawText(QPointF(rect.left() + 10, _baseline_centrada(fm, rect)), texto)


def layout_estado_vacio(familia: str, alto_ventana: float, alto_boton: int):
    """Posiciones verticales (título, detalle, botón) del estado vacío.
    Única fuente para la pintura y para ubicar el botón real."""
    fm_t = QFontMetrics(fuente_ui(familia, 14, QFont.Weight.DemiBold))
    fm_d = QFontMetrics(fuente_ui(familia, 9.5))
    textos = fm_t.height() + 6 + fm_d.height()
    bloque = textos + (14 + alto_boton if alto_boton else 0)
    y_titulo = max(30.0, (alto_ventana - bloque) / 2)
    y_detalle = y_titulo + fm_t.height() + 6
    return y_titulo, y_detalle, y_titulo + textos + 14


def pintar_estado_vacio(p: QPainter, familia: str, ancho: float, alto: float,
                        titulo: str, detalle: str, color_detalle: QColor,
                        alto_boton: int):
    f_t = fuente_ui(familia, 14, QFont.Weight.DemiBold)
    f_d = fuente_ui(familia, 9.5)
    y_titulo, y_detalle, _ = layout_estado_vacio(familia, alto, alto_boton)
    p.setFont(f_t)
    p.setPen(con_alpha(TEXTO, 0.92))
    fm_t = QFontMetrics(f_t)
    _centrado(p, fm_t, titulo, ancho, y_titulo + fm_t.ascent())
    p.setFont(f_d)
    p.setPen(color_detalle)
    fm_d = QFontMetrics(f_d)
    _centrado(p, fm_d, detalle, ancho, y_detalle + fm_d.ascent())


def pintar_aviso(p: QPainter, familia: str, ancho_ventana: float, base: float,
                 texto: str, error: bool, opacidad: float):
    """Aviso breve centrado. Entra subiendo 4 px junto con la opacidad:
    se nota que es nuevo sin llamar la atención."""
    f = fuente_ui(familia, 9.5, QFont.Weight.Medium)
    fm = QFontMetrics(f)
    texto = fm.elidedText(texto, Qt.TextElideMode.ElideRight, int(ancho_ventana - 64))
    ancho = fm.horizontalAdvance(texto) + (40 if error else 28)
    alto = 28
    rect = QRectF((ancho_ventana - ancho) / 2,
                  base - alto + (1.0 - opacidad) * 4, ancho, alto)
    p.save()
    p.setOpacity(opacidad)
    _pastilla(p, rect, 0.94)
    x = rect.left() + 14
    if error:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_ERROR)
        p.drawEllipse(QPointF(x + 3, rect.center().y()), 3.2, 3.2)
        x += 12
    p.setFont(f)
    p.setPen(con_alpha(TEXTO, 0.94))
    p.drawText(QPointF(x, _baseline_centrada(fm, rect)), texto)
    p.restore()


def pintar_parcial(p: QPainter, familia: str, ancho_ventana: float, base: float,
                   texto: str):
    """Hipótesis de voz en curso (Modo Script): tenue y fuera del guion,
    para confirmar que el micrófono escucha sin tapar lo que se lee."""
    f = fuente_ui(familia, 9.5)
    fm = QFontMetrics(f)
    # Se ve el final de la hipótesis, que es lo que acaba de decirse.
    texto = fm.elidedText(texto, Qt.TextElideMode.ElideLeft,
                          int(min(ancho_ventana * 0.7, ancho_ventana - 64)))
    alto = 26
    ancho = fm.horizontalAdvance(texto) + 24
    rect = QRectF((ancho_ventana - ancho) / 2, base - alto, ancho, alto)
    _pastilla(p, rect, 0.82)
    p.setFont(f)
    p.setPen(con_alpha(TEXTO, 0.6))
    p.drawText(QPointF(rect.left() + 12, _baseline_centrada(fm, rect)), texto)


def pintar_arrastre(p: QPainter, familia: str, rect: QRectF, radio: float,
                    aceptable: bool, texto: str):
    """Destino de drag & drop: borde punteado y una sola instrucción."""
    color = ACENTO if aceptable else COLOR_ERROR
    path = QPainterPath()
    path.addRoundedRect(rect, radio, radio)
    p.fillPath(path, con_alpha(PANEL, 0.86))
    p.fillPath(path, con_alpha(color, 0.06))
    p.setPen(QPen(con_alpha(color, 0.85), 1.5, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    f = fuente_ui(familia, 13, QFont.Weight.DemiBold)
    fm = QFontMetrics(f)
    p.setFont(f)
    p.setPen(con_alpha(TEXTO if aceptable else COLOR_ERROR, 0.95))
    ancho_total = rect.width() + 2 * rect.left()
    _centrado(p, fm, texto, ancho_total, rect.center().y() + fm.ascent() / 2 - 2)


def pintar_grip(p: QPainter, ancho: float, alto: float, progreso: float):
    """Indicio de redimensionado, visible sólo mientras hay interacción."""
    if progreso <= 0.01:
        return
    pen = QPen(con_alpha(TEXTO, 0.32 * progreso), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for d in (6, 10):
        p.drawLine(QPointF(ancho - d - 1, alto - 5), QPointF(ancho - 5, alto - d - 1))
