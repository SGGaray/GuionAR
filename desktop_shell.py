"""Integración de escritorio: ícono, ventana de Configuración y bandeja.

Sólo presentación y delegación: todo cambio pasa por los setters del
overlay (los mismos que usan los controles), que aplican en vivo, guardan
y emiten ``preferencias_cambiadas`` para que cada superficie se
sincronice. Ninguna clase guarda una referencia propia al overlay: lo
obtienen como padre Qt, así no se forman ciclos de referencias.
"""

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSlider, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ui_controls import (
    ACENTO, COLOR_ACTIVO, SUPERFICIE, TEXTO_PRINCIPAL, TEXTO_SECUNDARIO,
    aplicar_siempre_encima,
)

ASSETS = Path(__file__).resolve().parent / "assets"
ICONO_SVG = ASSETS / "guionar.svg"
TAMANOS_ICONO = (16, 22, 24, 32, 48, 64, 128, 256)

ALINEACIONES = (("left", "Izquierda"), ("center", "Centro"), ("right", "Derecha"))


# ---------------------------------------------------------------- ícono

def _pixmap_icono(lado: int) -> QPixmap:
    """El mismo dibujo que assets/guionar.svg, para cuando Qt no tiene el
    plugin SVG (algunas instalaciones de distribución lo separan)."""
    pixmap = QPixmap(lado, lado)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(lado / 64, lado / 64)
    panel = QPainterPath()
    panel.addRoundedRect(QRectF(4, 8, 56, 48), 11, 11)
    p.fillPath(panel, QColor(22, 24, 29))
    p.setPen(Qt.PenStyle.NoPen)
    for x, y, ancho, color in ((18, 18, 28, QColor(255, 255, 255, 97)),
                               (18, 29, 34, QColor(255, 255, 255)),
                               (18, 40, 22, QColor(255, 255, 255, 158))):
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x, y, ancho, 6), 3, 3)
    p.setBrush(QColor(255, 196, 92))
    p.drawRoundedRect(QRectF(10, 28, 4, 8), 2, 2)
    p.end()
    return pixmap


def icono_app() -> QIcon:
    """Ícono propio de GuionAR, sin depender del tema del escritorio."""
    icono = QIcon(str(ICONO_SVG))
    if not icono.isNull() and not icono.pixmap(16, 16).isNull():
        return icono
    icono = QIcon()
    for lado in TAMANOS_ICONO:
        icono.addPixmap(_pixmap_icono(lado))
    return icono


# ---------------------------------------------------------------- Configuración

_ESTILO = """
QWidget#configuracion { background: %SUPERFICIE%; }
QWidget#configuracion, QWidget#configuracion QLabel,
QWidget#configuracion QCheckBox { color: %TEXTO%; font-size: 10pt; }
QLabel#seccion { color: %SECUNDARIO%; font-size: 8.5pt; font-weight: 600;
                 letter-spacing: 1px; padding-top: 6px; }
QLabel#valor { color: %TEXTO%; min-width: 40px; }
QLabel#estado { color: %SECUNDARIO%; font-size: 9pt; }
QLabel#estado[conectado="true"] { color: %ACTIVO%; }
QPushButton { background: rgba(255,255,255,0.08); color: %TEXTO%;
              border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
              padding: 5px 12px; }
QPushButton:hover { background: rgba(255,255,255,0.14); }
QPushButton:pressed { background: rgba(255,255,255,0.22); }
QPushButton:focus { border: 1px solid %ACENTO%; }
QPushButton:disabled { color: rgba(255,255,255,0.35); }
QPushButton#segmento:checked { background: %ACENTO%; color: %SUPERFICIE%;
                               border-color: %ACENTO%; font-weight: 600; }
QPushButton#segmento:checked:focus { border: 2px solid %TEXTO%; }
QPushButton#discreto { background: transparent; border-color: transparent;
                       color: %SECUNDARIO%; padding: 4px 6px; }
QPushButton#discreto:hover { color: %TEXTO%; background: rgba(255,255,255,0.08); }
QPushButton#discreto:focus { border: 1px solid %ACENTO%; }
QSpinBox { background: rgba(255,255,255,0.08); color: %TEXTO%;
           border: 1px solid rgba(255,255,255,0.10); border-radius: 6px;
           padding: 3px 6px; }
QSpinBox:hover { border-color: rgba(255,255,255,0.22); }
QSpinBox:focus { border: 1px solid %ACENTO%; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; border: none;
                                             background: transparent; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: rgba(255,255,255,0.12); }
QSpinBox::up-arrow { image: url(%ARRIBA%); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url(%ABAJO%); width: 10px; height: 10px; }
QSlider { min-height: 20px; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px;
                             background: rgba(255,255,255,0.16); }
QSlider::sub-page:horizontal { background: %ACENTO%; border-radius: 2px; }
QSlider::handle:horizontal { background: %TEXTO%; width: 14px; height: 14px;
                             margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background: #ffffff; }
QSlider::handle:horizontal:focus { background: %ACENTO%; }
QCheckBox { spacing: 9px; padding: 3px 0; }
QCheckBox:focus { color: %ACENTO%; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 4px;
                       border: 1px solid rgba(255,255,255,0.38);
                       background: transparent; }
QCheckBox::indicator:hover { border-color: rgba(255,255,255,0.7); }
QCheckBox::indicator:focus { border: 2px solid %ACENTO%; }
QCheckBox::indicator:checked { background: %ACENTO%; border-color: %ACENTO%;
                               image: url(%CHECK%); }
QCheckBox::indicator:checked:focus { border: 2px solid %TEXTO%; }
QCheckBox:disabled { color: rgba(255,255,255,0.35); }
"""


def estilo_configuracion() -> str:
    """QSS de Configuración con la paleta de ui_controls (sin hex sueltos)."""
    estilo = _ESTILO
    for marca, color in (("%SUPERFICIE%", SUPERFICIE), ("%TEXTO%", TEXTO_PRINCIPAL),
                         ("%SECUNDARIO%", TEXTO_SECUNDARIO), ("%ACENTO%", ACENTO),
                         ("%ACTIVO%", COLOR_ACTIVO)):
        estilo = estilo.replace(marca, color.name())
    for marca, archivo in (("%CHECK%", "check.svg"), ("%ARRIBA%", "arrow-up.svg"),
                           ("%ABAJO%", "arrow-down.svg")):
        estilo = estilo.replace(marca, (ASSETS / archivo).as_posix())
    return estilo


class VentanaConfiguracion(QWidget):
    """Ventana chica, no modal. Todo cambio se aplica en vivo."""

    def __init__(self, overlay):
        # Hija de Qt del overlay (transitoria, queda encima de él) pero ventana
        # propia: sigue visible aunque el overlay se oculte.
        super().__init__(overlay, Qt.WindowType.Window)
        self.setObjectName("configuracion")
        self.setWindowTitle("Configuración de GuionAR")
        self.setWindowIcon(icono_app())
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(estilo_configuracion())

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 16, 20, 18)
        raiz.setSpacing(6)

        # ------------------------------------------------ Apariencia
        raiz.addWidget(self._seccion("APARIENCIA"))
        apariencia = QFormLayout()
        apariencia.setHorizontalSpacing(16)
        apariencia.setVerticalSpacing(10)

        self.grupo_alineacion = QButtonGroup(self)
        self.grupo_alineacion.setExclusive(True)
        fila_alineacion = QHBoxLayout()
        fila_alineacion.setSpacing(4)
        self.botones_alineacion = {}
        for valor, texto in ALINEACIONES:
            boton = QPushButton(texto)
            boton.setObjectName("segmento")
            boton.setCheckable(True)
            boton.setProperty("alineacion", valor)
            self.grupo_alineacion.addButton(boton)
            self.botones_alineacion[valor] = boton
            fila_alineacion.addWidget(boton)
        self.grupo_alineacion.buttonClicked.connect(self._alineacion_elegida)
        apariencia.addRow("Alineación", fila_alineacion)

        self.slider_opacidad = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacidad.setRange(0, 100)
        self.slider_opacidad.setPageStep(10)
        self.slider_opacidad.setFixedHeight(20)
        self.slider_opacidad.setAccessibleName("Opacidad del fondo")
        self.lbl_opacidad = QLabel()
        self.lbl_opacidad.setObjectName("valor")
        self.lbl_opacidad.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        fila_opacidad = QHBoxLayout()
        fila_opacidad.addWidget(self.slider_opacidad, 1)
        fila_opacidad.addWidget(self.lbl_opacidad)
        self.slider_opacidad.valueChanged.connect(self._opacidad_elegida)
        apariencia.addRow("Opacidad del fondo", fila_opacidad)

        self.spin_texto = QSpinBox()
        self.spin_texto.setRange(overlay.FONT_MIN, overlay.FONT_MAX)
        self.spin_texto.setSuffix(" pt")
        self.spin_texto.setAccessibleName("Tamaño de texto")
        self.spin_texto.valueChanged.connect(self._tamano_elegido)
        apariencia.addRow("Tamaño de texto", self.spin_texto)
        raiz.addLayout(apariencia)

        self.btn_restaurar = QPushButton("Restaurar valores")
        self.btn_restaurar.setObjectName("discreto")
        self.btn_restaurar.setToolTip("Vuelve Apariencia a sus valores por defecto")
        self.btn_restaurar.clicked.connect(overlay.restaurar_apariencia)
        fila_restaurar = QHBoxLayout()
        fila_restaurar.addStretch(1)
        fila_restaurar.addWidget(self.btn_restaurar)
        raiz.addLayout(fila_restaurar)

        # ------------------------------------------------ Comportamiento
        raiz.addWidget(self._seccion("COMPORTAMIENTO"))
        self.chk_siempre_encima = QCheckBox("Mantener GuionAR sobre otras ventanas")
        self.chk_pausa_hover = QCheckBox("Pausar al pasar el mouse")
        self.chk_bloqueo = QCheckBox("Bloquear posición y tamaño")
        self.chk_geometria = QCheckBox("Recordar posición y tamaño")
        self.chk_autoocultar = QCheckBox("Ocultar controles automáticamente")
        for chk, setter in ((self.chk_siempre_encima, overlay.set_siempre_encima),
                            (self.chk_pausa_hover, overlay.set_pausa_hover),
                            (self.chk_bloqueo, overlay.set_bloqueo),
                            (self.chk_geometria, overlay.set_recordar_geometria),
                            (self.chk_autoocultar, overlay.set_autoocultar_controles)):
            chk.toggled.connect(setter)
            raiz.addWidget(chk)

        # ------------------------------------------------ ParlAR (sólo lectura)
        # El mismo estado que el chip y la bandeja; discreto, sin acciones:
        # la integración es automática.
        self.lbl_parlar_titulo = self._seccion("PARLAR")
        raiz.addWidget(self.lbl_parlar_titulo)
        self.lbl_parlar = QLabel()
        self.lbl_parlar.setObjectName("estado")
        self.lbl_parlar.setAccessibleName("Estado de ParlAR")
        raiz.addWidget(self.lbl_parlar)

        overlay.preferencias_cambiadas.connect(self.sincronizar)
        self.sincronizar()
        self.setMinimumWidth(380)
        self.adjustSize()

    @staticmethod
    def _seccion(texto):
        etiqueta = QLabel(texto)
        etiqueta.setObjectName("seccion")
        return etiqueta

    @property
    def overlay(self):
        return self.parentWidget()

    def _seguir_fijado(self):
        """Con GuionAR fijado, Configuración también se mantiene encima: si
        no, quedaría atrapada detrás del overlay. Con el pin apagado es una
        ventana normal; nunca se fija por su cuenta."""
        aplicar_siempre_encima(self, self.overlay.cfg["always_on_top"])

    def sincronizar(self):
        """Refleja el estado del overlay sin reemitir cambios."""
        self._seguir_fijado()
        cfg = self.overlay.cfg
        widgets = (self.slider_opacidad, self.spin_texto, self.chk_siempre_encima,
                   self.chk_pausa_hover,
                   self.chk_bloqueo, self.chk_geometria, self.chk_autoocultar)
        for w in widgets:
            w.blockSignals(True)
        try:
            boton = self.botones_alineacion.get(cfg["text_alignment"])
            if boton is not None:
                boton.setChecked(True)
            porcentaje = round(cfg["bg_opacity"] * 100)
            self.slider_opacidad.setValue(porcentaje)
            self.lbl_opacidad.setText(f"{porcentaje} %")
            self.spin_texto.setValue(cfg["font_size_context"])
            self.chk_siempre_encima.setChecked(cfg["always_on_top"])
            self.chk_pausa_hover.setChecked(cfg["pause_on_hover"])
            self.chk_bloqueo.setChecked(cfg["position_locked"])
            self.chk_geometria.setChecked(cfg["remember_geometry"])
            self.chk_autoocultar.setChecked(cfg["auto_hide_controls"])
            self._sincronizar_parlar()
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _sincronizar_parlar(self):
        estado = self.overlay.estado_parlar()
        visible = estado is not None
        self.lbl_parlar_titulo.setVisible(visible)
        self.lbl_parlar.setVisible(visible)
        if not visible:
            return
        # Sólo presencia: el detalle (voz/espera) cambia con cada VAD y vive
        # en el chip del overlay, que es donde se lee.
        conectado = estado != "No detectado"
        texto = ("Conectado" if conectado
                 else "No detectado · se conecta solo al abrir ParlAR")
        if self.lbl_parlar.text() != texto:
            self.lbl_parlar.setText(texto)
        if self.lbl_parlar.property("conectado") != conectado:
            self.lbl_parlar.setProperty("conectado", conectado)
            self.lbl_parlar.style().unpolish(self.lbl_parlar)
            self.lbl_parlar.style().polish(self.lbl_parlar)

    def _alineacion_elegida(self, boton):
        self.overlay.set_alineacion(boton.property("alineacion"))

    def _opacidad_elegida(self, valor):
        self.lbl_opacidad.setText(f"{valor} %")
        self.overlay.set_opacidad(valor / 100)

    def _tamano_elegido(self, valor):
        self.overlay.set_tamano_texto(valor)

    def showEvent(self, e):
        super().showEvent(e)
        self._sincronizar_parlar()
        self.setFocus()  # sin foco inicial en un control: nada parece elegido

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- bandeja

def crear_bandeja(overlay, disponible: bool | None = None):
    """Bandeja del sistema, o None si el escritorio no ofrece una.
    GuionAR funciona igual sin ella (sólo no se puede ocultar)."""
    if disponible is None:
        disponible = QSystemTrayIcon.isSystemTrayAvailable()
    if not disponible:
        return None
    return BandejaGuionAR(overlay)


class BandejaGuionAR(QSystemTrayIcon):
    """Ícono y menú mínimo: mostrar/ocultar, siempre encima, bloqueo,
    pausa con el puntero, Configuración y Salir."""

    def __init__(self, overlay):
        super().__init__(icono_app(), overlay)
        self.setToolTip("GuionAR")
        self._menu = QMenu()
        titulo = self._menu.addAction("GuionAR")
        titulo.setEnabled(False)
        self._menu.addSeparator()
        self.accion_mostrar = self._menu.addAction("Ocultar")
        self.accion_siempre_encima = self._menu.addAction("Mantener sobre otras ventanas")
        self.accion_siempre_encima.setCheckable(True)
        self.accion_bloquear = self._menu.addAction("Bloquear posición")
        self.accion_bloquear.setCheckable(True)
        self.accion_pausa = self._menu.addAction("Pausar al pasar el mouse")
        self.accion_pausa.setCheckable(True)
        self._menu.addSeparator()
        # Estado informativo, leído del overlay (no es una integración nueva).
        self.accion_parlar = self._menu.addAction("ParlAR: no detectado")
        self.accion_parlar.setEnabled(False)
        self.accion_configuracion = self._menu.addAction("Configuración…")
        self._menu.addSeparator()
        self.accion_salir = self._menu.addAction("Salir")
        self.setContextMenu(self._menu)

        # triggered (no toggled): sólo acciones del usuario llegan al overlay.
        self.accion_mostrar.triggered.connect(self._alternar_visible)
        self.accion_siempre_encima.triggered.connect(overlay.set_siempre_encima)
        self.accion_bloquear.triggered.connect(overlay.set_bloqueo)
        self.accion_pausa.triggered.connect(overlay.set_pausa_hover)
        self.accion_configuracion.triggered.connect(overlay.abrir_configuracion)
        self.accion_salir.triggered.connect(overlay.salir)
        self.activated.connect(self._activado)
        self._menu.aboutToShow.connect(self.sincronizar)
        overlay.preferencias_cambiadas.connect(self.sincronizar)
        self.sincronizar()

    @property
    def overlay(self):
        return self.parent()

    def sincronizar(self):
        ov = self.overlay
        self.accion_mostrar.setText("Ocultar" if ov.isVisible() else "Mostrar")
        self.accion_siempre_encima.setChecked(ov.cfg["always_on_top"])
        self.accion_bloquear.setChecked(ov.cfg["position_locked"])
        self.accion_pausa.setChecked(ov.cfg["pause_on_hover"])
        estado = ov.estado_parlar()
        self.accion_parlar.setVisible(estado is not None)
        if estado is not None:
            self.accion_parlar.setText(f"ParlAR: {estado.lower()}")

    def _alternar_visible(self):
        if self.overlay.isVisible():
            self.overlay.ocultar_ventana()
        else:
            self.overlay.mostrar_ventana()

    def _activado(self, razon):
        if razon in (QSystemTrayIcon.ActivationReason.Trigger,
                     QSystemTrayIcon.ActivationReason.DoubleClick):
            self.overlay.mostrar_ventana()
