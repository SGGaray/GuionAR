"""Pulido visual final (Phase 9B): sólo invariantes de lo que cambió.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_visual_polish.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QFontDatabase, QImage, QPainter, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

from desktop_shell import VentanaConfiguracion, estilo_configuracion
from guionar import TeleprompterOverlay
from ui_controls import ACENTO, LADO_BOTON, IconButton, SUPERFICIE


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)

TEXTO = ("Todo ese andamiaje tiene un objetivo claro que no es otro que "
         "permitir que la persona que habla frente a la cámara pueda seguir "
         "su guion sin perder el hilo, incluso cuando la frase se extiende. ") * 12


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _overlay(ancho=720, alto=260, familia=None):
    cfg = {"width": ancho, "height": alto}
    if familia:
        cfg["font_family"] = familia
    ov = TeleprompterOverlay(cfg)
    ov.resize(ancho, alto)
    ov.show()
    _app.processEvents()
    return ov


def _cargar(ov, texto=TEXTO):
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    os.write(descriptor, texto.encode("utf-8"))
    os.close(descriptor)
    try:
        ov.cargar_guion(ruta)
        _app.processEvents()
    finally:
        os.unlink(ruta)


def test_medida_de_linea_no_depende_del_metadato_de_la_fuente():
    familias = [f for f in ("Noto Sans", "DejaVu Sans", "Liberation Sans",
                            "Cantarell", "Noto Serif")
                if f in QFontDatabase.families()] or [None]
    for familia in familias:
        ov = _overlay(1600, 400, familia)
        try:
            _cargar(ov)
            largos = [len(" ".join(w for _, w in ln))
                      for ln in ov._lineas_guion if ln]
            maximo = max(largos)
            check(f"F15 {familia or 'default'}: líneas de ~70 caracteres "
                  "en ventana ancha", 60 <= maximo <= 75, str(maximo))
        finally:
            ov.close()


def test_botones_de_ventana_y_barra_con_el_mismo_tamano():
    ov = _overlay()
    try:
        botones = (ov.barra.findChildren(IconButton)
                   + ov.ventana_controles.findChildren(IconButton))
        tamanos = {(b.width(), b.height()) for b in botones}
        check("un solo tamaño de botón en barra y controles de ventana",
              tamanos == {(LADO_BOTON, LADO_BOTON)}, repr(tamanos))
        check("el panel superior contiene sus botones sin recortarlos",
              ov.ventana_controles.height() >= LADO_BOTON + 4)
    finally:
        ov.close()


def _luminancia_media(boton):
    imagen = QImage(boton.size(), QImage.Format.Format_ARGB32)
    imagen.fill(SUPERFICIE)
    pintor = QPainter(imagen)
    boton.render(pintor, QPoint(), QRegion(),
                 QWidget.RenderFlag.DrawChildren)   # sin fondo de ventana
    pintor.end()
    total = n = 0
    for x in range(2, boton.width() - 2, 3):
        for y in (3, boton.height() - 4):          # bordes, lejos del glifo
            c = QColor(imagen.pixel(x, y))
            total += c.red() + c.green()
            n += 1
    return total / n


def test_pin_y_candado_activos_tienen_fondo_propio():
    ov = _overlay()
    try:
        boton = ov.ventana_controles.btn_bloquear
        boton.set_activo(False)
        apagado = _luminancia_media(boton)
        boton.set_activo(True)
        encendido = _luminancia_media(boton)
        check("activo se distingue por fondo, no sólo por color del glifo",
              encendido > apagado + 15, f"{apagado:.0f} -> {encendido:.0f}")
    finally:
        ov.close()


def test_configuracion_usa_la_paleta_y_foco_visible():
    estilo = estilo_configuracion()
    check("Configuración sin marcadores sin resolver", "%" not in estilo)
    check("Configuración toma el acento de la paleta",
          ACENTO.name() in estilo and SUPERFICIE.name() in estilo)
    for regla in ("QCheckBox::indicator:focus",
                  "QCheckBox::indicator:checked:focus",
                  "QPushButton#segmento:checked:focus"):
        check(f"foco visible: {regla}", regla in estilo)


def test_estado_de_parlar_en_configuracion():
    ov = _overlay()
    try:
        ventana = VentanaConfiguracion(ov)
        ventana.show()
        _app.processEvents()
        check("sin listener no se muestra estado de ParlAR",
              not ventana.lbl_parlar.isVisible())
        ov.set_ghost_recovery_available(True)
        _app.processEvents()
        check("listener sin ParlAR: no detectado, sin jerga",
              ventana.lbl_parlar.isVisible()
              and ventana.lbl_parlar.text().startswith("No detectado")
              and "sock" not in ventana.lbl_parlar.text())
        ov.set_voice_clients(1)
        _app.processEvents()
        check("ParlAR presente: conectado",
              ventana.lbl_parlar.text() == "Conectado"
              and ventana.lbl_parlar.property("conectado") is True)
        ov.set_speaking(True)
        ov.set_voice_clients(0)
        _app.processEvents()
        check("el detalle de voz vive en el chip; Configuración no parpadea",
              ventana.lbl_parlar.text() in ("Conectado",)
              or ventana.lbl_parlar.text().startswith("No detectado"))
        ventana.close()
    finally:
        ov.close()


def test_hidpi_dibujo_en_coordenadas_logicas():
    ov = _overlay()
    try:
        boton = ov.barra.btn_play
        imagen = QImage(boton.width() * 2, boton.height() * 2,
                        QImage.Format.Format_ARGB32)
        imagen.setDevicePixelRatio(2.0)
        imagen.fill(SUPERFICIE)
        pintor = QPainter(imagen)
        boton.render(pintor)
        pintor.end()
        check("botón renderiza a 200 % sin cambiar su tamaño lógico",
              boton.width() == LADO_BOTON
              and imagen.deviceIndependentSize().width() == LADO_BOTON)
    finally:
        ov.close()


def main():
    test_medida_de_linea_no_depende_del_metadato_de_la_fuente()
    test_botones_de_ventana_y_barra_con_el_mismo_tamano()
    test_pin_y_candado_activos_tienen_fondo_propio()
    test_configuracion_usa_la_paleta_y_foco_visible()
    test_estado_de_parlar_en_configuracion()
    test_hidpi_dibujo_en_coordenadas_logicas()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de pulido visual pasaron.")


if __name__ == "__main__":
    main()
