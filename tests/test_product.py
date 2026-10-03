"""Tests de producto: carga desde la UI, controles visuales, estados del
Modo Script, avance automático vs. seguimiento de voz y compatibilidad.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_product.py

Se prueban contratos observables (qué documento queda activo, dónde está
el cursor, qué comunica el estado, qué hace cada control), no detalles de
pintura.
"""

import gc
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QShortcut
from PyQt6.QtWidgets import QApplication

import document_loader
from bridge import SocketBridge
from guionar import TeleprompterOverlay, _CargadorDocumentos
from guionar_client import TeleprompterClient
from ui_controls import AutoHide

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
_TMP = tempfile.TemporaryDirectory()

ORACIONES = " ".join(
    f"Oración número {i} con contenido suficiente para ocupar varias líneas "
    f"en la ventana del teleprompter." for i in range(40))


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _drenar(segundos=0.2):
    fin = time.time() + segundos
    while time.time() < fin:
        _app.processEvents()
        time.sleep(0.01)


def _esperar(predicado, timeout=3.0):
    fin = time.time() + timeout
    while time.time() < fin:
        _app.processEvents()
        if predicado():
            return True
        time.sleep(0.01)
    return predicado()


def _archivo(nombre, contenido):
    ruta = os.path.join(_TMP.name, nombre)
    modo = "wb" if isinstance(contenido, bytes) else "w"
    kwargs = {} if isinstance(contenido, bytes) else {"encoding": "utf-8"}
    with open(ruta, modo, **kwargs) as f:
        f.write(contenido)
    return ruta


def _overlay(ancho=720, alto=260):
    ov = TeleprompterOverlay({"width": ancho, "height": alto})
    ov.resize(ancho, alto)
    ov.show()
    _app.processEvents()
    return ov


def _cerrar(ov):
    ov.close()
    _app.processEvents()
    gc.collect()  # liberar en un punto seguro, no dentro de una señal


def _ticks(ov, cantidad, dt=0.05):
    for _ in range(cantidad):
        ov._last_tick = time.monotonic() - dt
        ov._tick()


def _drop(ov, ruta, solo_entrar=False):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(ruta)])
    acciones = Qt.DropAction.CopyAction
    entrar = QDragEnterEvent(QPointF(10, 10).toPoint(), acciones, mime,
                             Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier)
    ov.dragEnterEvent(entrar)
    if solo_entrar:
        return entrar
    soltar = QDropEvent(QPointF(10, 10), acciones, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    ov.dropEvent(soltar)
    return soltar


# ---------------------------------------------------------------- estados

def test_estado_vacio_ofrece_carga():
    ov = _overlay()
    try:
        check("sin guion ni dictado hay estado vacío", ov._estado_vacio())
        check("estado vacío muestra la acción de carga", ov.btn_vacio.isVisible())
        check("la barra arranca oculta", not ov.barra.isVisible())
        ov.grab()  # pintura del estado vacío no falla
        ov.append_text("dictado en vivo")
        check("el dictado reemplaza el estado vacío",
              not ov._estado_vacio() and not ov.btn_vacio.isVisible())
        ov.clear()
        check("clear de dictado vuelve al estado vacío",
              ov._estado_vacio() and ov.btn_vacio.isVisible())
    finally:
        _cerrar(ov)


def test_documento_nuevo_y_reemplazo():
    a = _archivo("a.txt", ORACIONES)
    b = _archivo("b.md", "# Otro guion\n\nTexto **distinto** para reemplazar.")
    ov = _overlay(400)
    try:
        check("abrir documento devuelve éxito", ov.abrir_documento(a) is True)
        check("documento activo visible por nombre", ov.documento_nombre == "a.txt")
        check("documento nuevo queda en pausa listo para empezar",
              ov.paused and ov._estado()[0] == "Listo para empezar")
        check("documento nuevo arranca en cursor 0 y viewport arriba",
              ov.guion.cursor == 0 and ov.scroll_offset == 0.0)
        check("con documento no hay estado vacío ni botón central",
              not ov._estado_vacio() and not ov.btn_vacio.isVisible())

        ov.append_text(" ".join(w for _, w in ov.guion.originales[:60]))
        ov.set_partial("hipótesis pendiente")
        ov._scroll_a_cursor(inmediato=True)
        guion_a = ov.guion
        check("estado intermedio avanzado", ov.guion.cursor == 60 and ov.scroll_offset > 0)

        check("reemplazar documento sin reiniciar", ov.abrir_documento(b) is True)
        check("reemplazo cambia guion y nombre",
              ov.guion is not guion_a and ov.documento_nombre == "b.md"
              and ov.guion.originales[0][1] == "Otro")
        check("reemplazo reinicia cursor, scroll y partial",
              ov.guion.cursor == 0 and ov.scroll_offset == ov.scroll_target == 0.0
              and ov.partial_text == "")

        guion_b = ov.guion
        ov.saltar_oracion(1)
        cursor_b = ov.guion.cursor
        for ruta in (_archivo("vacio.txt", b""), _archivo("x.rtf", "hola"),
                     _archivo("roto.pdf", b"%PDF-1.4 roto"), "/no/existe.docx"):
            check(f"falla al reemplazar con {os.path.basename(ruta)} devuelve False",
                  ov.abrir_documento(ruta) is False)
            check(f"falla con {os.path.basename(ruta)} conserva documento y posición",
                  ov.guion is guion_b and ov.guion.cursor == cursor_b
                  and ov.documento_nombre == "b.md")
            check(f"falla con {os.path.basename(ruta)} se informa",
                  ov.documento_error and ov.toast_texto == ov.documento_error)
        ov.grab()
    finally:
        _cerrar(ov)


def test_errores_desde_estado_vacio():
    ov = _overlay()
    try:
        casos = {
            "vacío": (_archivo("vacio2.txt", b""), document_loader.MSG_VACIO),
            "formato": (_archivo("x.odt", "hola"), "Formato no soportado"),
            "corrupto": (_archivo("roto.docx", os.urandom(300)), "DOCX"),
        }
        for nombre, (ruta, esperado) in casos.items():
            ov.abrir_documento(ruta)
            check(f"error {nombre} deja estado vacío con mensaje",
                  ov._estado_vacio() and ov.guion is None
                  and esperado in (ov.documento_error or ""), repr(ov.documento_error))
            ov.grab()
        ov.abrir_documento(_archivo("ok.txt", "Ahora sí."))
        check("carga válida limpia el error previo",
              ov.documento_error is None and ov.guion is not None)
    finally:
        _cerrar(ov)


def test_carga_en_segundo_plano():
    ov = _overlay()
    try:
        ruta = _archivo("fondo.txt", ORACIONES)
        hilo_ui = threading.get_ident()
        resultado = ov.abrir_documento(ruta, en_segundo_plano=True)
        check("carga en segundo plano no bloquea ni devuelve resultado",
              resultado is None and ov.cargando)
        ov.grab()  # estado "cargando" pintable
        check("carga en segundo plano termina y aplica",
              _esperar(lambda: not ov.cargando and ov.guion is not None))
        check("resultado aplicado en el hilo de UI", threading.get_ident() == hilo_ui)
        check("carga en segundo plano deja el documento listo",
              ov.documento_nombre == "fondo.txt" and ov.paused)

        lenta = _archivo("primera.txt", "primera " * 20000)
        rapida = _archivo("segunda.txt", "segunda versión")
        ov.abrir_documento(lenta, en_segundo_plano=True)
        ov.abrir_documento(rapida, en_segundo_plano=True)
        _esperar(lambda: not ov.cargando)
        _drenar(0.5)  # da tiempo a que llegue la carga reemplazada
        check("una carga reemplazada no pisa la última elegida",
              ov.documento_nombre == "segunda.txt", repr(ov.documento_nombre))

        for i in range(10):
            ov.abrir_documento(_archivo(f"rep{i}.txt", f"documento {i} final."),
                               en_segundo_plano=True)
        _esperar(lambda: not ov.cargando)
        _drenar(0.3)
        check("cambios de documento repetidos terminan en el último",
              ov.documento_nombre == "rep9.txt")
    finally:
        _cerrar(ov)


def _loader_lento(prefijo_lento="lenta"):
    """load_script instrumentado: las rutas con prefijo quedan retenidas
    hasta ``liberar``; registra llamadas y concurrencia máxima."""
    original = document_loader.load_script
    estado = {"activas": 0, "max": 0, "llamadas": [],
              "entro": threading.Event(), "liberar": threading.Event()}
    lock = threading.Lock()

    def lenta(ruta):
        nombre = os.path.basename(ruta)
        with lock:
            estado["llamadas"].append(nombre)
            estado["activas"] += 1
            estado["max"] = max(estado["max"], estado["activas"])
        try:
            if nombre.startswith(prefijo_lento):
                estado["entro"].set()
                estado["liberar"].wait(timeout=10)
            return original(ruta)
        finally:
            with lock:
                estado["activas"] -= 1

    return original, lenta, estado


def _hilos_loader():
    return sum(1 for h in threading.enumerate()
               if h.name == "guionar-loader" and h.is_alive())


def test_cargador_acotado_gana_la_ultima():
    original, lenta, estado = _loader_lento()
    document_loader.load_script = lenta
    ov = _overlay()
    emitidos = []
    ov._cargador.terminado.connect(lambda token, _r: emitidos.append(token))
    try:
        hilos_antes = _hilos_loader()
        t0 = time.perf_counter()
        ov.abrir_documento(_archivo("lenta.txt", ORACIONES), en_segundo_plano=True)
        dt = time.perf_counter() - t0
        check("carga lenta vuelve enseguida al hilo de UI", dt < 0.2, f"{dt:.3f}s")
        check("la extracción lenta empezó", estado["entro"].wait(timeout=2.0))
        disparos = []
        QTimer.singleShot(0, lambda: disparos.append(1))
        check("la UI sigue procesando eventos durante la extracción",
              _esperar(lambda: disparos == [1], timeout=1.0) and ov.cargando)

        ov.abrir_documento(_archivo("segunda.txt", "Segunda elección."),
                           en_segundo_plano=True)
        for i in range(20):
            ov.abrir_documento(_archivo(f"rafaga{i}.txt", f"Ráfaga {i} final."),
                               en_segundo_plano=True)
        check("una ráfaga no crea un hilo por click",
              _hilos_loader() - hilos_antes <= 1,
              f"{hilos_antes}->{_hilos_loader()}")
        check("mientras extrae no se lanzan otras extracciones",
              estado["llamadas"] == ["lenta.txt"], repr(estado["llamadas"]))

        estado["liberar"].set()
        check("la ráfaga termina en la última elección",
              _esperar(lambda: not ov.cargando
                       and ov.documento_nombre == "rafaga19.txt"))
        _drenar(0.2)
        check("sólo se extrae la vigente y la última pendiente",
              estado["llamadas"] == ["lenta.txt", "rafaga19.txt"],
              repr(estado["llamadas"]))
        check("nunca hay dos extracciones simultáneas", estado["max"] == 1,
              repr(estado["max"]))
        check("resultados reemplazados nunca se publican",
              emitidos == [ov._carga_token], repr(emitidos))
        check("el documento final es la última elección",
              ov.documento_nombre == "rafaga19.txt")
    finally:
        estado["liberar"].set()
        document_loader.load_script = original
        ov._cargador.esperar(timeout=2.0)
        _cerrar(ov)


def test_cargador_cierre_acotado():
    inactivo = _CargadorDocumentos()
    t0 = time.perf_counter()
    check("cerrar un cargador sin uso es inmediato",
          inactivo.esperar(timeout=1.0) is True
          and time.perf_counter() - t0 < 0.1)

    original, lenta, estado = _loader_lento()
    document_loader.load_script = lenta
    cargador = _CargadorDocumentos()
    emitidos = []
    cargador.terminado.connect(lambda token, _r: emitidos.append(token))
    try:
        cargador.iniciar(1, _archivo("lenta_cierre.txt", "texto"))
        check("extracción retenida en curso", estado["entro"].wait(timeout=2.0))
        cargador.iniciar(2, _archivo("pendiente.txt", "texto"))
        t0 = time.perf_counter()
        terminado = cargador.esperar(timeout=0.3)
        dt = time.perf_counter() - t0
        check("cierre no espera indefinidamente a una extracción lenta",
              terminado is False and dt < 1.0, f"{dt:.2f}s")
        check("después de cerrar no acepta cargas nuevas",
              cargador.iniciar(3, "x.txt") is False)
        estado["liberar"].set()
        check("el worker termina al liberar la extracción",
              cargador.esperar(timeout=2.0) is True)
        _drenar(0.1)
        check("tras el cierre no se publica ni se procesa lo pendiente",
              emitidos == [] and estado["llamadas"] == ["lenta_cierre.txt"],
              repr((emitidos, estado["llamadas"])))
    finally:
        estado["liberar"].set()
        document_loader.load_script = original


def test_selector_y_drag_drop_usan_el_mismo_loader():
    llamadas = []
    original = document_loader.load_script

    def espia(ruta):
        llamadas.append(os.path.basename(ruta))
        return original(ruta)

    document_loader.load_script = espia
    ov = _overlay()
    try:
        ruta = _archivo("elegido.txt", "Texto elegido desde el selector.")
        pedidos = []

        def selector(padre, titulo, directorio, filtro):
            pedidos.append((padre, filtro))
            return ruta, filtro

        ov._selector_archivo = selector
        ov.elegir_documento()
        check("selector recibe el overlay y el filtro de formatos",
              pedidos and pedidos[0][0] is ov and "*.pdf" in pedidos[0][1])
        check("selector carga el archivo elegido",
              _esperar(lambda: ov.documento_nombre == "elegido.txt"))

        ov._selector_archivo = lambda *a: ("", "")
        ov.elegir_documento()
        _drenar(0.1)
        check("cancelar el selector no cambia nada", ov.documento_nombre == "elegido.txt")

        soltado = _archivo("soltado.md", "# Soltado\n\nDesde drag and drop.")
        entrar = _drop(ov, soltado, solo_entrar=True)
        check("drag de formato soportado se acepta e indica destino",
              entrar.isAccepted() and ov.arrastre == "ok")
        _drop(ov, soltado)
        check("drop limpia el indicador", ov.arrastre is None)
        check("drop carga el documento", _esperar(lambda: ov.documento_nombre == "soltado.md"))

        no_soportado = _archivo("imagen.png", b"\x89PNG")
        _drop(ov, no_soportado, solo_entrar=True)
        check("drag de formato no soportado avisa antes de soltar", ov.arrastre == "no")
        ov.grab()
        _drop(ov, no_soportado)
        _esperar(lambda: not ov.cargando)
        check("drop no soportado conserva documento e informa",
              ov.documento_nombre == "soltado.md"
              and "Formato no soportado" in (ov.documento_error or ""))
        check("selector y drag & drop pasan por el mismo loader",
              llamadas == ["elegido.txt", "soltado.md", "imagen.png"], repr(llamadas))
    finally:
        document_loader.load_script = original
        _cerrar(ov)


# ---------------------------------------------------------------- controles

def test_play_pausa_unico():
    ov = _overlay(400)
    try:
        ov.abrir_documento(_archivo("pp.txt", ORACIONES))
        barra = ov.barra
        check("botón muestra Play cuando está en pausa", barra.btn_play.icono == "play")
        barra.btn_play.click()
        check("botón Play reanuda usando el mismo estado paused", ov.paused is False)
        check("botón refleja Pausa al reproducir", barra.btn_play.icono == "pause")
        check("reproducir inicia la animación", ov._timer.isActive())
        ov.toggle_pause()  # mismo camino que Espacio
        check("Espacio/toggle pausa", ov.paused is True and barra.btn_play.icono == "play")
        _ticks(ov, 1)
        check("en pausa el timer se detiene (CPU idle)", not ov._timer.isActive())

        for _ in range(51):
            ov.toggle_pause()
        check("pausar/reanudar repetidamente queda consistente",
              ov.paused is False and barra.btn_play.icono == "pause")

        ov.hover_paused = True
        _ticks(ov, 1)
        check("hover sigue pausando sin tocar paused",
              not ov._timer.isActive() and ov.paused is False
              and ov._estado()[0] == "En pausa (puntero)")
        ov.hover_paused = False
    finally:
        _cerrar(ov)


def test_avance_automatico_y_velocidad():
    def recorrido(nivel):
        ov = _overlay(400)
        try:
            ov.abrir_documento(_archivo(f"auto{nivel}.txt", ORACIONES))
            ov.speed_pps = nivel * 30.0
            ov.toggle_pause()
            cursores = []
            for _ in range(40):
                _ticks(ov, 1)
                cursores.append(ov.guion.cursor)
            return ov.scroll_offset, cursores, ov._estado()[0]
        finally:
            _cerrar(ov)

    lento, cursores_lento, estado = recorrido(2)
    rapido, cursores_rapido, _ = recorrido(8)
    check("sin voz, Play hace avanzar el guion", lento > 0)
    check("estado comunica lectura automática", estado == "Leyendo", estado)
    check("velocidad modifica el avance proporcionalmente",
          abs(rapido / lento - 4.0) < 0.05, f"{lento:.1f} vs {rapido:.1f}")
    check("el cursor sigue la lectura sin retroceder",
          cursores_rapido == sorted(cursores_rapido) and cursores_rapido[-1] > 0)

    ov = _overlay(400)
    try:
        ov.abrir_documento(_archivo("vel.txt", ORACIONES))
        barra = ov.barra
        inicial = ov.speed_pps
        barra.btn_rapido.click()
        check("botón + usa la misma velocidad", ov.speed_pps == inicial + 30)
        check("etiqueta refleja el valor actual",
              barra.lbl_velocidad.text() == f"Vel {ov.nivel_velocidad()}")
        ov.speed_down()  # mismo camino que la tecla -
        check("tecla - y botón comparten estado", ov.speed_pps == inicial)
        for _ in range(40):
            ov.speed_up()
        check("velocidad respeta máximo seguro",
              ov.speed_pps == ov.SPEED_MAX and not barra.btn_rapido.isEnabled())
        for _ in range(40):
            barra.btn_lento.click() if barra.btn_lento.isEnabled() else ov.speed_down()
        check("velocidad respeta mínimo seguro",
              ov.speed_pps == ov.SPEED_MIN and not barra.btn_lento.isEnabled())
        check("cambio de velocidad por teclado con barra oculta da feedback",
              ov.toast_texto.startswith("Velocidad"))
    finally:
        _cerrar(ov)


def test_fin_del_guion_y_reinicio():
    ov = _overlay(400)
    try:
        ov.abrir_documento(_archivo("fin.txt", "Uno dos tres. Cuatro cinco seis."))
        ov.speed_pps = ov.SPEED_MAX
        ov.toggle_pause()
        _ticks(ov, 400)
        check("el avance automático llega al fin del guion", ov.script_terminado())
        check("al terminar queda detenido y lo comunica",
              ov.paused and not ov._timer.isActive()
              and ov._estado()[0] == "Fin del guion")
        check("botón ofrece empezar de nuevo", ov.barra.btn_play.icono == "replay")
        ov.toggle_pause()
        check("Play al final vuelve al comienzo y reproduce",
              ov.guion.cursor == 0 and ov.scroll_offset == 0.0 and not ov.paused)
    finally:
        _cerrar(ov)


def test_navegacion_por_botones():
    ov = _overlay(400)
    try:
        check("sin guion la navegación está deshabilitada",
              not ov.barra.btn_siguiente.isEnabled())
        ov.abrir_documento(_archivo("nav.txt", ORACIONES))
        esperado = ov.guion._inicios_oracion[1]
        ov.barra.btn_siguiente.click()
        check("botón siguiente usa saltar_oracion (PageDown)", ov.guion.cursor == esperado)
        ov.barra.btn_anterior.click()
        check("botón anterior usa saltar_oracion (PageUp)", ov.guion.cursor == 0)

        ov.toggle_pause()  # reproduciendo en modo automático
        _ticks(ov, 30)
        ov.barra.btn_siguiente.click()
        tras_salto = ov.guion.cursor
        check("navegar durante la lectura sigue reproduciendo",
              not ov.paused and ov._timer.isActive())
        _ticks(ov, 2)
        check("el avance continúa desde la posición corregida",
              ov.guion.cursor >= tras_salto)
    finally:
        _cerrar(ov)


def test_tamano_texto_conserva_posicion():
    ov = _overlay(400)
    try:
        ov.abrir_documento(_archivo("fuente.txt", ORACIONES))
        for _ in range(6):
            ov.saltar_oracion(1)
        cursor = ov.guion.cursor
        tamano = ov.cfg["font_size_context"]
        ov.barra.btn_texto_mayor.click()
        check("botón A+ agranda el texto", ov.cfg["font_size_context"] > tamano)
        check("A+ conserva la posición semántica", ov.guion.cursor == cursor)
        ov.barra.btn_texto_menor.click()
        ov.resize(700, ov.height())
        _app.processEvents()
        check("A- y resize conservan la posición semántica",
              ov.guion.cursor == cursor and ov.cfg["font_size_context"] == tamano)
        linea = ov._linea_visual_del_cursor()
        check("tras resize la línea del cursor queda anclada",
              abs(ov.scroll_target - max(0.0, (linea - 1) * ov._line_advance_guion_px()))
              < 0.01)
        for _ in range(200):
            ov._change_font(+2)
        check("tamaño de texto respeta máximo",
              ov.cfg["font_size_context"] <= ov.FONT_MAX
              and ov.cfg["font_size_current"] <= ov.FONT_MAX
              and not ov.barra.btn_texto_mayor.isEnabled())
    finally:
        _cerrar(ov)


def test_atajos_existentes_siguen_registrados():
    ov = _overlay()
    try:
        teclas = {s.key().toString() for s in ov.findChildren(QShortcut)}
        esperadas = {"+", "=", "-", "Space", "T", "Ctrl+Q", "Up", "Down", "C",
                     "PgUp", "PgDown", "Ctrl+O"}
        check("atajos históricos y Ctrl+O registrados", esperadas <= teclas,
              repr(esperadas - teclas))
    finally:
        _cerrar(ov)


def test_teclado_en_la_barra():
    from PyQt6.QtTest import QTest
    ov = _overlay()
    try:
        ov.activateWindow()
        ov.abrir_documento(_archivo("teclado.txt", ORACIONES))
        abiertos = []
        ov._selector_archivo = lambda *a: (abiertos.append(1), ("", ""))[1]
        ov.barra.btn_abrir.setFocus(Qt.FocusReason.TabFocusReason)
        _app.processEvents()
        check("Tab muestra la barra", ov.barra.visible_objetivo)
        QTest.keyClick(ov.barra.btn_abrir, Qt.Key.Key_Space)
        _app.processEvents()
        check("Espacio sigue siendo Play/Pausa aunque un botón tenga foco",
              ov.paused is False and not abiertos)
        QTest.keyClick(ov.barra.btn_abrir, Qt.Key.Key_Return)
        _app.processEvents()
        check("Enter activa el botón con foco", abiertos == [1])
        ov.barra.ocultar()
        check("la barra no se oculta con foco de teclado adentro",
              ov.barra.visible_objetivo)
    finally:
        _cerrar(ov)


def test_barra_aparece_con_interaccion_y_se_oculta():
    ov = _overlay()
    try:
        AutoHide.SALIDA_MS = 30
        ov._autohide.actividad()
        check("interacción muestra la barra", ov.barra.visible_objetivo and ov.barra.isVisible())
        check("barra entra animada (no salta)", ov.barra._progreso < 1.0)
        _drenar(0.35)
        check("animación de entrada termina opaca", ov.barra._progreso == 1.0)
        ov._autohide.salida()
        _drenar(0.45)
        check("al volver a leer la barra se oculta",
              not ov.barra.visible_objetivo and not ov.barra.isVisible())
        ov.resize(320, 140)
        _app.processEvents()
        ov.barra.mostrar()
        check("barra cabe en la ventana mínima",
              ov.barra.width() <= ov.width() - 24 and ov.barra.x() >= 0)
    finally:
        AutoHide.SALIDA_MS = 350
        _cerrar(ov)


# ---------------------------------------------------------------- voz / ParlAR

def test_modo_voz_y_desconexion_parlar():
    ov = _overlay(400)
    try:
        ov.abrir_documento(_archivo("voz.txt", ORACIONES))
        ov.toggle_pause()
        ov.set_speaking(False)
        check("evento de ParlAR activa seguimiento por voz", ov.voz_conectada)
        check("con voz en silencio no hay avance automático",
              ov._estado()[0] == "Esperando voz")
        _ticks(ov, 20)
        check("sin hablar el guion no se mueve", ov.scroll_offset == 0.0)
        ov.set_speaking(True)
        palabras = [w for _, w in ov.guion.originales[:30]]
        ov.append_text(" ".join(palabras))
        check("la voz mueve el cursor", ov.guion.cursor == 30)
        check("estado comunica seguimiento de voz", ov._estado()[0] == "Siguiendo la voz")
        ov.set_voice_producers(0)
        check("sin conexiones vuelve a avance automático",
              not ov.voz_conectada and ov._estado()[0] == "Leyendo")
        cursor = ov.guion.cursor
        _ticks(ov, 60)
        check("tras desconectar ParlAR sigue desde la misma posición",
              ov.guion.cursor >= cursor)
    finally:
        _cerrar(ov)


def test_bridge_informa_conexiones():
    ov = _overlay()
    path = f"/tmp/test-guionar-product-{os.getpid()}-{time.time_ns()}.sock"
    bridge = SocketBridge(ov, path=path)
    try:
        ov.set_ghost_recovery_available(bridge.start())
        ov.cargar_guion(_archivo("socket.txt", ORACIONES))
        cliente = TeleprompterClient(path)
        cliente.send_vad(True)
        check("ParlAR por socket activa modo voz", _esperar(lambda: ov.voz_conectada))
        cliente.send_text("Oración número 0")
        check("ParlAR por socket sigue moviendo el cursor",
              _esperar(lambda: ov.guion.cursor == 3))
        cliente._sock.close()
        cliente._sock = None
        check("desconexión de ParlAR vuelve a modo automático",
              _esperar(lambda: not ov.voz_conectada))
        toggle = TeleprompterClient(path)
        toggle.send_toggle()
        check("un atajo toggle no se confunde con ParlAR",
              _esperar(lambda: ov.hidden) and not ov.voz_conectada)
    finally:
        bridge.stop()
        _cerrar(ov)


class _BridgeObservable(SocketBridge):
    """SocketBridge real que avisa cuando despachó un clear (control)."""

    def __init__(self, overlay, path):
        super().__init__(overlay, path=path)
        self.clear_visto = threading.Event()

    def push_clear(self):
        super().push_clear()
        self.clear_visto.set()


def _bridge_de_prueba(ov, clase=SocketBridge):
    path = f"/tmp/test-guionar-voz-{os.getpid()}-{time.time_ns()}.sock"
    bridge = clase(ov, path)
    ov.set_ghost_recovery_available(bridge.start())
    ov.cargar_guion(_archivo("voz_bridge.txt", ORACIONES))
    return bridge


def _conectar(path, *mensajes):
    cliente = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cliente.connect(path)
    for mensaje in mensajes:
        cliente.sendall((json.dumps(mensaje) + "\n").encode("utf-8"))
    return cliente


def _cerrar_socket(cliente):
    try:
        cliente.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    cliente.close()


def test_control_persistente_no_mantiene_modo_voz():
    ov = _overlay()
    bridge = _bridge_de_prueba(ov, _BridgeObservable)
    voz = control = None
    try:
        voz = _conectar(bridge.path, {"type": "vad", "data": True})
        control = _conectar(bridge.path, {"type": "desconocido"},
                            {"type": "clear"})
        check("cliente de control despachó su clear",
              bridge.clear_visto.wait(timeout=2.0))
        check("voz + control conectados, sólo la voz es productora",
              _esperar(lambda: bridge.active_connection_count == 2
                       and bridge.voice_producer_count == 1 and ov.voz_conectada))
        _cerrar_socket(voz)
        voz = None
        check("cerrar la voz deja cero productores",
              _esperar(lambda: bridge.voice_producer_count == 0))
        check("vuelve a modo automático con el control todavía conectado",
              _esperar(lambda: not ov.voz_conectada)
              and bridge.active_connection_count == 1 and ov._modo_auto())
    finally:
        for cliente in (voz, control):
            if cliente is not None:
                _cerrar_socket(cliente)
        bridge.stop()
        _cerrar(ov)


def test_dos_productores_de_voz():
    ov = _overlay()
    bridge = _bridge_de_prueba(ov)
    a = b = None
    try:
        a = _conectar(bridge.path, {"type": "partial", "data": "hola"})
        b = _conectar(bridge.path, {"type": "text", "data": "Oración"})
        check("dos productores de voz registrados",
              _esperar(lambda: bridge.voice_producer_count == 2 and ov.voz_conectada))
        _cerrar_socket(a)
        a = None
        check("cerrar uno deja un productor",
              _esperar(lambda: bridge.voice_producer_count == 1))
        _drenar(0.1)
        check("con un productor restante sigue en modo voz", ov.voz_conectada)
        _cerrar_socket(b)
        b = None
        check("cerrar el segundo deja cero productores",
              _esperar(lambda: bridge.voice_producer_count == 0))
        check("sin productores vuelve a modo automático",
              _esperar(lambda: not ov.voz_conectada))
    finally:
        for cliente in (a, b):
            if cliente is not None:
                _cerrar_socket(cliente)
        bridge.stop()
        _cerrar(ov)


def test_cierres_concurrentes_de_productores():
    ov = _overlay()
    bridge = _bridge_de_prueba(ov)
    emitidos = []
    lock = threading.Lock()

    def registrar(cantidad):
        with lock:
            emitidos.append(cantidad)

    bridge.voice_producers_changed.connect(
        registrar, Qt.ConnectionType.DirectConnection)
    cantidad = 6
    try:
        for ronda in range(5):
            clientes = [_conectar(bridge.path,
                                  {"type": "partial", "data": f"r{ronda}c{i}"})
                        for i in range(cantidad)]
            listos = _esperar(lambda: bridge.voice_producer_count == cantidad
                              and ov.voz_conectada)
            barrera = threading.Barrier(cantidad)

            def cerrar(cliente):
                barrera.wait(timeout=2.0)
                _cerrar_socket(cliente)

            hilos = [threading.Thread(target=cerrar, args=(c,)) for c in clientes]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(timeout=3.0)
            vacio = _esperar(lambda: bridge.voice_producer_count == 0
                             and bridge.active_connection_count == 0)
            _esperar(lambda: not ov.voz_conectada)
            with lock:
                ultimo = emitidos[-1] if emitidos else None
            check(f"ronda {ronda}: cierres concurrentes terminan sin voz",
                  listos and vacio and ultimo == 0 and not ov.voz_conectada,
                  f"listos={listos} vacio={vacio} ultimo={ultimo} "
                  f"voz={ov.voz_conectada}")
    finally:
        bridge.stop()
        _cerrar(ov)


# ---------------------------------------------------------------- regresiones

def test_dictado_sin_script_no_cambia():
    ov = _overlay()
    try:
        ov.set_speaking(True)
        ov.append_text("hola mundo esto es dictado libre")
        ov.set_partial("hipótesis")
        check("dictado acumula como siempre",
              "hola mundo" in ov.current_line and ov.partial_text == "hipótesis")
        check("dictado no activa avance automático", not ov._modo_auto())
        check("estado de dictado comunica en vivo", ov._estado()[0] == "En vivo")
        ov.toggle_pause()
        check("pausa en dictado usa el mismo estado", ov._estado()[0] == "En pausa")
        ov.grab()
    finally:
        _cerrar(ov)


def test_cargar_guion_api_historica():
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("historico.txt", "uno dos tres"))
        check("cargar_guion conserva su contrato (no pausa)", ov.paused is False)
        ov.append_text("uno dos")
        check("cargar_guion sigue la voz como antes", ov.guion.cursor == 2)
        ov.cargar_guion("/no/existe.txt")
        check("cargar_guion fallido conserva el guion", ov.guion.cursor == 2)
    finally:
        _cerrar(ov)


def test_pintura_sin_io_ni_parsing():
    """paintEvent nunca vuelve a extraer ni a construir el guion."""
    ov = _overlay()
    original_doc = document_loader.load_document
    original_script = document_loader.load_script

    def prohibido(*_a, **_k):
        raise AssertionError("I/O en pintura")

    try:
        ov.abrir_documento(_archivo("pinta.txt", ORACIONES))
        document_loader.load_document = document_loader.load_script = prohibido
        ok = True
        try:
            for estado in range(4):
                if estado == 1:
                    ov.toggle_pause()
                    _ticks(ov, 5)
                elif estado == 2:
                    ov.arrastre = "ok"
                elif estado == 3:
                    ov.arrastre = None
                    ov.barra.mostrar()
                ov.grab()
        except AssertionError:
            ok = False
        check("pintura no hace I/O ni parsing", ok)
    finally:
        document_loader.load_document = original_doc
        document_loader.load_script = original_script
        _cerrar(ov)


def test_cli_guion_compatible():
    for nombre, contenido in (("cli.txt", "Hola desde la línea de comandos."),
                              ("cli.md", "# CLI\n\nTambién **Markdown**.")):
        ruta = _archivo(nombre, contenido)
        codigo = (
            "import sys, guionar\n"
            "from PyQt6.QtWidgets import QApplication\n"
            "def fake_exec(self):\n"
            "    ov = [w for w in QApplication.topLevelWidgets()\n"
            "          if isinstance(w, guionar.TeleprompterOverlay)][0]\n"
            "    print('ESTADO', ov.documento_nombre,\n"
            "          ov.guion is not None and ov.guion.valido, ov.paused)\n"
            "    return 0\n"
            "QApplication.exec = fake_exec\n"
            f"sys.argv = ['guionar.py', '--guion', {ruta!r}]\n"
            "guionar.main()\n"
        )
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["XDG_CONFIG_HOME"] = _TMP.name
        proc = subprocess.run([sys.executable, "-c", codigo], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=20)
        # Compatibilidad: documento cargado, Guion válido y sin la pausa
        # inicial que agrega el flujo interactivo (selector / drag & drop).
        check(f"--guion {nombre} carga sin forzar pausa (comportamiento histórico)",
              proc.returncode == 0 and f"ESTADO {nombre} True False" in proc.stdout,
              f"rc={proc.returncode} out={proc.stdout[-200:]!r} err={proc.stderr[-300:]!r}")


def main():
    test_estado_vacio_ofrece_carga()
    test_documento_nuevo_y_reemplazo()
    test_errores_desde_estado_vacio()
    test_carga_en_segundo_plano()
    test_cargador_acotado_gana_la_ultima()
    test_cargador_cierre_acotado()
    test_selector_y_drag_drop_usan_el_mismo_loader()
    test_play_pausa_unico()
    test_avance_automatico_y_velocidad()
    test_fin_del_guion_y_reinicio()
    test_navegacion_por_botones()
    test_tamano_texto_conserva_posicion()
    test_atajos_existentes_siguen_registrados()
    test_teclado_en_la_barra()
    test_barra_aparece_con_interaccion_y_se_oculta()
    test_modo_voz_y_desconexion_parlar()
    test_bridge_informa_conexiones()
    test_control_persistente_no_mantiene_modo_voz()
    test_dos_productores_de_voz()
    test_cierres_concurrentes_de_productores()
    test_dictado_sin_script_no_cambia()
    test_cargar_guion_api_historica()
    test_pintura_sin_io_ni_parsing()
    test_cli_guion_compatible()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de producto pasaron.")


if __name__ == "__main__":
    main()
