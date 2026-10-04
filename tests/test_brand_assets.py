"""Identidad de GuionAR: assets vectoriales, tray y escritorio.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_brand_assets.py
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QFont, QFontInfo, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

import desktop_shell
from guionar import DEFAULTS

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SVG_NS = "{http://www.w3.org/2000/svg}"
REQUERIDOS = ("guionar.svg", "brand/guionar-mono.svg", "brand/guionar-16.svg",
              "tray/guionar-tray.svg", "check.svg", "arrow-up.svg",
              "arrow-down.svg")


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def svgs():
    return sorted(ASSETS.rglob("*.svg"))


def test_archivos():
    for relativo in REQUERIDOS:
        check(f"existe {relativo}", (ASSETS / relativo).is_file())
    for ruta in svgs():
        texto = ruta.read_text(encoding="utf-8")
        raiz = ET.fromstring(texto)
        caja = [float(v) for v in raiz.get("viewBox", "").split()]
        etiquetas = {e.tag.replace(SVG_NS, "") for e in raiz.iter()}
        prohibidas = etiquetas & {"image", "text", "font", "filter", "script",
                                  "linearGradient", "radialGradient",
                                  "foreignObject"}
        check(f"{ruta.relative_to(ASSETS)}: SVG válido, viewBox cuadrado, "
              "sin raster/fuentes/filtros/refs externas",
              raiz.tag == SVG_NS + "svg" and len(caja) == 4
              and caja[:2] == [0.0, 0.0] and caja[2] == caja[3] > 0
              and not prohibidas
              and not re.search(r"@font-face|data:|https?://(?!www\.w3\.org)",
                                texto),
              f"{caja} {prohibidas}")


def test_variantes():
    mono = (ASSETS / "brand/guionar-mono.svg").read_text(encoding="utf-8")
    check("mono usa currentColor, sin colores fijos",
          "currentColor" in mono and not re.search(r"#[0-9A-Fa-f]{6}", mono))
    raiz = ET.parse(ASSETS / "brand/guionar-16.svg").getroot()
    enteros = all(float(r.get(a, "0")).is_integer()
                  for r in raiz.iter(SVG_NS + "rect")
                  for a in ("x", "y", "width", "height"))
    check("máster de 16 px en píxeles enteros", enteros)
    tray = ET.parse(ASSETS / "tray/guionar-tray.svg").getroot()
    primero = [e for e in tray if e.tag != SVG_NS + "title"][0]
    check("tray sin baldosa de fondo",
          not (primero.get("width") == "16" and primero.get("height") == "16"))


def _visibles(ruta, lado):
    imagen = QImage(QSize(lado, lado), QImage.Format.Format_ARGB32)
    imagen.fill(0)
    pintor = QPainter(imagen)
    QSvgRenderer(str(ruta)).render(pintor)
    pintor.end()
    return sum(1 for x in range(lado) for y in range(lado)
               if imagen.pixelColor(x, y).alpha() > 0)


def test_render_chico():
    for ruta in svgs():
        renderer = QSvgRenderer(str(ruta))
        check(f"{ruta.relative_to(ASSETS)}: Qt lo carga", renderer.isValid())
        for lado in (16, 20, 24, 32):
            if ruta.parent == ASSETS and ruta.name != "guionar.svg":
                continue   # flechas/check de la QSS: tamaño propio
            check(f"{ruta.relative_to(ASSETS)} @{lado}px visible",
                  _visibles(ruta, lado) > lado)


def test_iconos_de_escritorio():
    app = desktop_shell.icono_app()
    bandeja = desktop_shell.icono_bandeja()
    check("ícono de app carga y cubre 16 px",
          not app.isNull() and not app.pixmap(16, 16).isNull())
    check("ícono de bandeja propio y cargable",
          not bandeja.isNull() and not bandeja.pixmap(22, 22).isNull())
    for lado in (16, 32, 64):
        pix = desktop_shell._pixmap_icono(lado)
        check(f"respaldo pintado @{lado}px", not pix.isNull()
              and pix.width() == lado)
    plantilla = (ROOT / "packaging/linux/guionar.desktop.in").read_text(
        encoding="utf-8")
    instalador = (ROOT / "packaging/linux/install-desktop-entry.sh").read_text(
        encoding="utf-8")
    check("desktop entry apunta al ícono instalado",
          re.search(r"^Icon=guionar$", plantilla, re.M) is not None
          and "assets/guionar.svg" in instalador)


def _rects(ruta):
    raiz = ET.parse(ruta).getroot()
    return [tuple(float(r.get(a, "0")) for a in ("x", "y", "width", "height"))
            for r in raiz.iter(SVG_NS + "rect")]


def test_tray_deriva_del_mark():
    master = [r for r in _rects(ASSETS / "brand/guionar-16.svg") if r[2] != 16]
    tray = _rects(ASSETS / "tray/guionar-tray.svg")
    check("tray = máster de 16 sin baldosa (mismas líneas y marca)",
          sorted(tray) == sorted(master), f"{tray} vs {master}")


def test_configuracion_con_marca_e_iconos():
    from guionar import TeleprompterOverlay
    ov = TeleprompterOverlay({"width": 720, "height": 260})
    try:
        ventana = desktop_shell.VentanaConfiguracion(ov)
        ventana.show()
        _app.processEvents()
        pix = ventana.lbl_marca.pixmap()
        logico = pix.deviceIndependentSize()
        check("Configuración muestra el app mark en el encabezado",
              not pix.isNull() and round(logico.width()) == desktop_shell.LADO_MARCA)
        check("la marca se rasteriza a la densidad de la pantalla",
              abs(pix.devicePixelRatio() - (ventana.devicePixelRatioF() or 1)) < 1e-6)
        check("Configuración y overlay tienen ícono de ventana",
              not ventana.windowIcon().isNull())
        check("el ícono de la app se carga una sola vez",
              desktop_shell.icono_app() is desktop_shell.icono_app())
        ventana.close()
    finally:
        ov.close()


def test_fuente_generica():
    familia = DEFAULTS["font_family"]
    resuelta = QFontInfo(QFont(familia, 18)).family()
    check("la fuente default es una familia genérica que resuelve",
          familia == "Sans Serif" and bool(resuelta), resuelta)


def main():
    test_archivos()
    test_variantes()
    test_render_chico()
    test_iconos_de_escritorio()
    test_tray_deriva_del_mark()
    test_configuracion_con_marca_e_iconos()
    test_fuente_generica()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de identidad pasaron.")


if __name__ == "__main__":
    main()
