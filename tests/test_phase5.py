"""Regresiones, grupo 5: pintura visible y guardado atómico.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_phase5.py
"""

import json
import math
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication

import guionar_config
from guionar import SCRIPT_MARGIN_PX, TeleprompterOverlay


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _cerrar(overlay):
    overlay.close()
    _app.processEvents()


def _overlay_con_guion(cantidad=300, ancho=520, alto=260):
    palabras = [f"palabra{i:05d}" for i in range(cantidad)]
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(descriptor, " ".join(palabras).encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        overlay = TeleprompterOverlay({
            "width": ancho,
            "height": alto,
        })
        overlay.resize(ancho, alto)
        overlay.show()
        _app.processEvents()
        overlay.cargar_guion(ruta)
        return overlay
    finally:
        os.unlink(ruta)


def _rango_actual(overlay):
    helper = getattr(overlay, "_rango_lineas_guion_visibles", None)
    if helper is None:
        return None
    inicio, fin = helper()
    return inicio, fin, set(range(inicio, fin))


def _rango_legacy_visible(overlay):
    fm = QFontMetrics(overlay._font_context())
    avance = overlay._line_advance_guion_px()
    return {
        indice
        for indice in range(len(overlay._lineas_guion))
        if -avance <= (
            SCRIPT_MARGIN_PX + fm.ascent() + indice * avance
            - overlay.scroll_offset
        ) <= overlay.height() + avance
    }


def _rango_cubre_sin_crecer(overlay):
    actual = _rango_actual(overlay)
    if actual is None:
        return False
    inicio, fin, indices = actual
    visibles = _rango_legacy_visible(overlay)
    maximo_razonable = math.ceil(overlay.height()
                                  / overlay._line_advance_guion_px()) + 6
    return (
        0 <= inicio <= fin <= len(overlay._lineas_guion)
        and visibles <= indices
        and fin - inicio <= maximo_razonable
    )


class PainterRecorder:
    def __init__(self):
        self.font_weight = None
        self.color = None
        self.draws = []

    def setFont(self, font):
        self.font_weight = int(font.weight())

    def setPen(self, color):
        self.color = color.getRgb()

    def drawText(self, point, text):
        self.draws.append((
            round(point.x(), 4), round(point.y(), 4), text,
            self.font_weight, self.color,
        ))


class SyntheticLines:
    """Secuencia grande que contabiliza accesos sin reservar todo el guion."""

    def __init__(self, line_count, words_per_line=5):
        self.line_count = line_count
        self.line = [(i, "palabra") for i in range(words_per_line)]
        self.accesses = 0

    def __len__(self):
        return self.line_count

    def __getitem__(self, index):
        if index < 0 or index >= self.line_count:
            raise IndexError(index)
        self.accesses += 1
        return self.line


class FakeGuion:
    cursor = 0


def test_rango_visible_top_medio_fin_y_eof():
    overlay = _overlay_con_guion(800)
    try:
        check("renderer expone cálculo directo de rango visible",
              hasattr(overlay, "_rango_lineas_guion_visibles"))
        if not hasattr(overlay, "_rango_lineas_guion_visibles"):
            return

        overlay.scroll_offset = 0.0
        check("rango superior cubre las líneas pintables",
              _rango_cubre_sin_crecer(overlay))

        avance = overlay._line_advance_guion_px()
        overlay.scroll_offset = len(overlay._lineas_guion) * avance / 2
        check("rango medio cubre las líneas pintables",
              _rango_cubre_sin_crecer(overlay))

        overlay.scroll_offset = max(
            0.0, len(overlay._lineas_guion) * avance - overlay.height())
        check("rango final queda correctamente clampado",
              _rango_cubre_sin_crecer(overlay))

        overlay.guion.cursor = len(overlay.guion.palabras_norm)
        overlay._scroll_a_cursor(inmediato=True)
        actual = _rango_actual(overlay)
        ultima = max(i for i, linea in enumerate(overlay._lineas_guion)
                     if linea is not None)
        check("cursor terminal EOF conserva visible la última línea",
              _rango_cubre_sin_crecer(overlay)
              and actual is not None and ultima in actual[2])

        fm = QFontMetrics(overlay._font_context())
        linea_frontera = min(10, len(overlay._lineas_guion) - 1)
        overlay.scroll_offset = (
            SCRIPT_MARGIN_PX + fm.ascent()
            + (linea_frontera + 1) * avance
        )
        check("overscan conserva líneas exactamente en la frontera",
              _rango_cubre_sin_crecer(overlay)
              and linea_frontera in _rango_actual(overlay)[2])
    finally:
        _cerrar(overlay)


def test_rango_guion_corto_y_viewport_alto():
    overlay = _overlay_con_guion(3, ancho=720, alto=1000)
    try:
        actual = _rango_actual(overlay)
        check("guion muy corto queda completamente incluido",
              actual is not None
              and actual[:2] == (0, len(overlay._lineas_guion)))
        check("viewport más alto que el guion conserva todo el contenido",
              _rango_cubre_sin_crecer(overlay))
    finally:
        _cerrar(overlay)


def test_rango_sobrevive_resize_y_fuente():
    overlay = _overlay_con_guion(500, ancho=360)
    try:
        overlay.guion.cursor = len(overlay.guion.palabras_norm) // 2
        overlay._scroll_a_cursor(inmediato=True)
        check("rango inicial antes de reflow es válido",
              _rango_cubre_sin_crecer(overlay))

        lineas_angostas = len(overlay._lineas_guion)
        overlay.resize(900, overlay.height())
        _app.processEvents()
        check("resize recalcula rango visible sin omisiones",
              len(overlay._lineas_guion) < lineas_angostas
              and _rango_cubre_sin_crecer(overlay))

        lineas_fuente_normal = len(overlay._lineas_guion)
        overlay._change_font(+12)
        check("cambio de fuente recalcula rango visible sin omisiones",
              len(overlay._lineas_guion) > lineas_fuente_normal
              and _rango_cubre_sin_crecer(overlay))
    finally:
        _cerrar(overlay)


def test_salida_visible_equivale_al_recorrido_completo():
    overlay = _overlay_con_guion(600)
    try:
        if not hasattr(overlay, "_rango_lineas_guion_visibles"):
            check("salida visible equivale al renderer previo", False,
                  "helper de rango ausente")
            return
        posiciones = [
            (0.0, 0),
            (len(overlay._lineas_guion)
             * overlay._line_advance_guion_px() / 2,
             len(overlay.guion.palabras_norm) // 2),
        ]
        posiciones.append((
            max(0.0, len(overlay._lineas_guion)
                * overlay._line_advance_guion_px() - overlay.height()),
            len(overlay.guion.palabras_norm),
        ))
        equivalentes = True
        helper = overlay._rango_lineas_guion_visibles
        for offset, cursor in posiciones:
            overlay.scroll_offset = offset
            overlay.guion.cursor = cursor

            completo = PainterRecorder()
            overlay._rango_lineas_guion_visibles = (
                lambda: (0, len(overlay._lineas_guion)))
            overlay._paint_script(completo)

            optimizado = PainterRecorder()
            overlay._rango_lineas_guion_visibles = helper
            overlay._paint_script(optimizado)
            equivalentes = equivalentes and completo.draws == optimizado.draws
        overlay._rango_lineas_guion_visibles = helper
        check("salida visible equivale al recorrido completo",
              equivalentes)
    finally:
        _cerrar(overlay)


def test_trabajo_de_pintura_no_escala_con_total():
    overlay = TeleprompterOverlay({"width": 720, "height": 260})
    overlay.guion = FakeGuion()
    overlay.scroll_offset = 0.0
    accesos = {}
    try:
        for words in (1_000, 10_000, 100_000):
            lineas = SyntheticLines(math.ceil(words / 5))
            overlay._lineas_guion = lineas
            overlay._paint_script(PainterRecorder())
            accesos[words] = lineas.accesses
        check("pintura 1k/10k/100k queda acotada al viewport",
              max(accesos.values()) <= 20
              and max(accesos.values()) - min(accesos.values()) <= 2,
              repr(accesos))
    finally:
        _cerrar(overlay)


class ConfigTemporal:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = guionar_config.CONFIG_DIR
        self.old_file = guionar_config.CONFIG_FILE
        guionar_config.CONFIG_DIR = Path(self.tmp.name)
        guionar_config.CONFIG_FILE = Path(self.tmp.name) / "config.json"
        return guionar_config.CONFIG_FILE

    def __exit__(self, *_args):
        guionar_config.CONFIG_DIR = self.old_dir
        guionar_config.CONFIG_FILE = self.old_file
        self.tmp.cleanup()


def _cfg(opacidad, actual, contexto):
    return {
        "bg_opacity": opacidad,
        "font_size_current": actual,
        "font_size_context": contexto,
    }


def test_guardado_normal_reemplazo_y_carga():
    with ConfigTemporal() as target:
        primero = _cfg(0.4, 30, 18)
        segundo = _cfg(0.8, 42, 25)
        guionar_config.guardar(primero)
        valido_primero = json.loads(target.read_text(encoding="utf-8"))
        guionar_config.guardar(segundo)
        contenido = target.read_text(encoding="utf-8")
        valido_segundo = json.loads(contenido)
        check("guardado atómico normal produce JSON válido",
              valido_primero == primero and valido_segundo == segundo)
        check("guardado reemplaza configuración existente",
              valido_segundo == segundo and contenido.count("bg_opacity") == 1)
        check("load-after-save conserva configuración normalizada",
              guionar_config.cargar() == segundo)


class FailingWriter:
    def __init__(self, inner):
        self.inner = inner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.inner.close()

    def write(self, data):
        self.inner.write(data[:7])
        self.inner.flush()
        raise OSError("falla inyectada durante escritura temporal")

    def flush(self):
        self.inner.flush()

    def fileno(self):
        return self.inner.fileno()


def test_falla_escritura_temporal_preserva_anterior():
    with ConfigTemporal() as target:
        anterior = _cfg(0.4, 30, 18)
        guionar_config.guardar(anterior)
        bytes_anteriores = target.read_bytes()
        original_fdopen = guionar_config.os.fdopen

        def fdopen_fallido(fd, *args, **kwargs):
            return FailingWriter(original_fdopen(fd, *args, **kwargs))

        guionar_config.os.fdopen = fdopen_fallido
        error = None
        try:
            guionar_config.guardar(_cfg(0.9, 50, 30))
        except OSError as exc:
            error = str(exc)
        finally:
            guionar_config.os.fdopen = original_fdopen

        huerfanos = list(target.parent.glob(f".{target.name}.*.tmp"))
        check("falla de escritura preserva archivo anterior",
              error is not None and target.read_bytes() == bytes_anteriores)
        check("falla de escritura limpia temporal", not huerfanos,
              repr(huerfanos))


def test_falla_replace_preserva_anterior():
    with ConfigTemporal() as target:
        anterior = _cfg(0.4, 30, 18)
        guionar_config.guardar(anterior)
        bytes_anteriores = target.read_bytes()
        original_replace = guionar_config.os.replace

        def replace_fallido(_source, _target):
            raise OSError("falla inyectada en replace")

        guionar_config.os.replace = replace_fallido
        error = None
        try:
            guionar_config.guardar(_cfg(0.9, 50, 30))
        except OSError as exc:
            error = str(exc)
        finally:
            guionar_config.os.replace = original_replace

        huerfanos = list(target.parent.glob(f".{target.name}.*.tmp"))
        check("falla de replace preserva archivo anterior",
              error is not None and target.read_bytes() == bytes_anteriores)
        check("falla de replace limpia temporal", not huerfanos,
              repr(huerfanos))


def main():
    test_rango_visible_top_medio_fin_y_eof()
    test_rango_guion_corto_y_viewport_alto()
    test_rango_sobrevive_resize_y_fuente()
    test_salida_visible_equivale_al_recorrido_completo()
    test_trabajo_de_pintura_no_escala_con_total()
    test_guardado_normal_reemplazo_y_carga()
    test_falla_escritura_temporal_preserva_anterior()
    test_falla_replace_preserva_anterior()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests del grupo 5 pasaron.")


if __name__ == "__main__":
    main()
