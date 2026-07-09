"""Tests de GuionAR: hardening del socket, semántica text/partial, y
comportamiento del overlay que no depende de un display real.

Corre headless (no necesita pantalla):
    QT_QPA_PLATFORM=offscreen python tests/test_guionar.py

Sin pytest ni otro framework, mismo estilo simple (check()) que los tests
de ParlAR, a propósito, para no sumar una dependencia de testing distinta
entre los dos repos.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time

# Asegura modo offscreen incluso si alguien corre el archivo sin la env var
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from guionar import TeleprompterOverlay
from bridge import SocketBridge, TeleprompterClient

FALLAS = []


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


# Una sola QApplication para toda la suite (Qt no permite más de una).
_app = QApplication(sys.argv)


def _overlay_con_bridge(cfg=None, socket_path=None):
    """Crea un overlay + SocketBridge sobre un socket temporal único, para
    que cada test tenga su propio servidor y no choquen entre sí."""
    ov = TeleprompterOverlay(cfg or {})
    path = socket_path or f"/tmp/test-guionar-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}.sock"
    br = SocketBridge(ov, path=path)
    br.start()
    time.sleep(0.15)  # darle tiempo al hilo del servidor a bindear
    return ov, br


def _drenar(segundos=0.2):
    """Procesa el loop de eventos de Qt un rato para que las señales
    encoladas (cross-thread) lleguen a los slots antes de inspeccionar
    el estado del overlay."""
    fin = time.time() + segundos
    while time.time() < fin:
        _app.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------- semántica

def test_texto_final_simple():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_text("hola mundo")
    _drenar()
    check("texto final se agrega", "hola mundo" in ov.current_line or
          any("hola mundo" in l for l in ov.lines))
    br.stop()


def test_partial_es_sufijo_no_reemplaza():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_text("confirmado")
    c.send_partial("hipotesis")
    _drenar()
    check("texto confirmado se conserva", ov.current_line == "confirmado")
    check("hipotesis pendiente queda aparte", ov.partial_text == "hipotesis")
    br.stop()


def test_final_limpia_partial_pendiente():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_partial("cola vieja")
    c.send_text("nuevo texto")
    _drenar()
    check("texto final descarta el partial anterior", ov.partial_text == "",
          f"quedó: {ov.partial_text!r}")
    br.stop()


def test_sin_duplicacion_streaming_incremental():
    """Simula el patrón real de ParlAR en modo streaming: partials que
    crecen y luego se confirman de a poco."""
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_partial("mañana vamos")
    c.send_partial("mañana vamos a grabar")
    c.send_text("mañana vamos")
    c.send_partial("a grabar el video")
    c.send_text("a grabar el video")
    _drenar()
    todo = " ".join(ov.lines) + " " + ov.current_line
    check("sin duplicar 'mañana vamos'", todo.count("mañana vamos") == 1, todo)
    check("incluye el segundo tramo", "grabar el video" in todo, todo)
    br.stop()


def test_vad_controla_scroll():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_vad(True)
    _drenar(0.1)
    check("vad true refleja speaking", ov.speaking is True)
    c.send_vad(False)
    _drenar(0.1)
    check("vad false refleja idle", ov.speaking is False)
    br.stop()


def test_clear_resetea_todo():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_text("algo para borrar")
    _drenar()
    c.send_clear()
    _drenar()
    check("clear vacía las líneas", len(ov.lines) == 0)
    check("clear vacía la línea actual", ov.current_line == "")
    br.stop()


def test_toggle_activa_modo_fantasma():
    ov, br = _overlay_con_bridge()
    check("arranca visible (no fantasma)", ov.hidden is False)
    c = TeleprompterClient(br.path)
    c.send_toggle()
    _drenar()
    check("toggle activa modo fantasma", ov.hidden is True)
    br.stop()


def test_modo_fantasma_oculta_la_ventana_de_verdad():
    """El modo fantasma debe ocultar la ventana a nivel sistema (hide()),
    no solo pintarla transparente: es la única garantía real, independiente
    del window manager, de que no intercepta clicks."""
    ov, br = _overlay_con_bridge()
    ov.show()
    check("arranca visible", ov.isVisible() is True)
    ov.toggle_visible()
    check("modo fantasma oculta la ventana de verdad (isVisible=False)",
          ov.isVisible() is False)
    ov.toggle_visible()
    check("al restaurar vuelve a mostrarse", ov.isVisible() is True)
    br.stop()


# ---------------------------------------------------------------- hardening

def test_json_malformado_no_crashea():
    ov, br = _overlay_con_bridge()
    s = socket.socket(socket.AF_UNIX)
    s.connect(br.path)
    s.sendall(b'{esto no es json\n')
    s.sendall(b'[1, 2, 3]\n')                       # json válido, no es dict
    s.sendall(b'{"type":"text","data":12345}\n')    # tipo de dato incorrecto
    s.sendall(b'{"type":"algo_desconocido"}\n')      # tipo desconocido
    s.close()
    _drenar()
    check("bridge sigue vivo tras json malformado", br._thread.is_alive())
    br.stop()


def test_mensaje_oversized_se_descarta():
    ov, br = _overlay_con_bridge()
    s = socket.socket(socket.AF_UNIX)
    s.connect(br.path)
    payload = json.dumps({"type": "text", "data": "A" * 100_000}).encode() + b"\n"
    s.sendall(payload)
    s.close()
    _drenar()
    todo = " ".join(ov.lines) + ov.current_line
    check("mensaje >64KB descartado", "AAAA" not in todo)
    check("bridge sigue vivo tras mensaje oversized", br._thread.is_alive())
    br.stop()


def test_palabra_patologica_se_trocea():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    c.send_text("B" * 500)  # una sola "palabra" sin espacios
    _drenar()
    check("líneas acotadas pese a palabra sin espacios",
          all(len(l) <= ov.cfg["line_char_limit"] + 20 for l in ov.lines))
    br.stop()


def test_rate_limit_no_crashea_con_spam():
    ov, br = _overlay_con_bridge()
    c = TeleprompterClient(br.path)
    t0 = time.time()
    for i in range(500):
        c.send_text(f"spam{i}")
    dt = time.time() - t0
    _drenar(0.3)
    check("500 mensajes rápidos no bloquean al emisor", dt < 2.0, f"tardó {dt:.2f}s")
    check("bridge sigue vivo tras el spam", br._thread.is_alive())
    br.stop()


def test_multiples_conexiones_simultaneas():
    """El fix de v0.1.1: un cliente de larga duración (como ParlAR) no debe
    bloquear a un segundo cliente corto (como un atajo de teclado)."""
    ov, br = _overlay_con_bridge()

    largo = socket.socket(socket.AF_UNIX)
    largo.connect(br.path)
    largo.sendall(b'{"type":"vad","data":true}\n')

    corto = socket.socket(socket.AF_UNIX)
    try:
        corto.settimeout(2.0)
        corto.connect(br.path)
        corto.sendall(b'{"type":"toggle"}\n')
        conectado = True
    except OSError:
        conectado = False
    finally:
        corto.close()

    check("segunda conexión no se bloquea con la primera abierta", conectado)
    _drenar()
    check("el toggle de la segunda conexión sí se aplicó", ov.hidden is True)

    largo.close()
    br.stop()


def test_cliente_roto_no_mata_el_bridge():
    ov, br = _overlay_con_bridge()
    s = socket.socket(socket.AF_UNIX)
    s.connect(br.path)
    s.sendall(b'{"type":"text","data":"parcial')  # sin cerrar el JSON ni el \n
    s.close()  # corta la conexión a mitad de mensaje
    _drenar()
    check("bridge sigue vivo tras cliente que corta a mitad de mensaje",
          br._thread.is_alive())
    # y sigue aceptando conexiones nuevas después
    c = TeleprompterClient(br.path)
    c.send_text("sigo funcionando")
    _drenar()
    todo = " ".join(ov.lines) + ov.current_line
    check("acepta conexiones nuevas después del cliente roto",
          "sigo funcionando" in todo)
    br.stop()


def test_sin_bridge_no_crashea():
    """El cliente debe ser fire-and-forget incluso si nunca hubo servidor."""
    c = TeleprompterClient("/tmp/este-socket-no-existe-nunca.sock")
    t0 = time.time()
    for _ in range(20):
        c.send_text("nadie escucha")
        c.send_vad(True)
    dt = time.time() - t0
    check("cliente sin servidor no bloquea ni lanza excepciones", dt < 1.0,
          f"tardó {dt:.2f}s")


# ---------------------------------------------------------------- performance

def test_timer_se_detiene_en_idle():
    ov, _ = _overlay_con_bridge()
    ov.set_speaking(True)
    ov.append_text("una linea corta para animar el scroll un toque")
    ov.set_speaking(False)  # pausa a mitad de animación
    _drenar(0.3)
    check("timer se detiene al pausar (VAD idle)", not ov._timer.isActive())

    ov.set_speaking(True)  # retoma
    _drenar(1.5)
    check("timer se detiene solo al terminar la animación",
          not ov._timer.isActive())
    check("el scroll efectivamente llegó a destino",
          abs(ov.scroll_target - ov.scroll_offset) < 0.5)


# ---------------------------------------------------------------- modo script

def _archivo_guion(texto: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                    encoding="utf-8")
    f.write(texto)
    f.close()
    return f.name


def test_cargar_guion_valido():
    ov, br = _overlay_con_bridge()
    ruta = _archivo_guion("uno dos tres cuatro cinco.")
    ov.cargar_guion(ruta)
    check("guion queda cargado", ov.guion is not None and ov.guion.valido)
    check("hay líneas envueltas para pintar", len(ov._lineas_guion) > 0)
    br.stop()
    os.unlink(ruta)


def test_cargar_guion_inexistente_no_crashea():
    ov, br = _overlay_con_bridge()
    ov.cargar_guion("/no/existe/en/serio.txt")
    check("guion inexistente no crashea y queda sin cargar", ov.guion is None)
    br.stop()


def test_cargar_guion_vacio_no_crashea():
    ov, br = _overlay_con_bridge()
    ruta = _archivo_guion("   \n\n  ")
    ov.cargar_guion(ruta)
    check("guion vacío no crashea y queda sin cargar", ov.guion is None)
    br.stop()
    os.unlink(ruta)


def test_modo_script_mueve_cursor_no_acumula_transcript():
    ov, br = _overlay_con_bridge()
    ruta = _archivo_guion("uno dos tres cuatro cinco")
    ov.cargar_guion(ruta)
    ov.append_text("uno dos")
    check("el cursor avanzó con texto confirmado", ov.guion.cursor == 2)
    check("el modo script no acumula en current_line", ov.current_line == "")
    check("el modo script no acumula en lines", len(ov.lines) == 0)
    br.stop()
    os.unlink(ruta)


def test_partial_no_mueve_cursor_en_modo_script():
    ov, br = _overlay_con_bridge()
    ruta = _archivo_guion("uno dos tres cuatro cinco")
    ov.cargar_guion(ruta)
    ov.set_partial("uno dos tres")
    check("el partial no mueve el cursor del guion", ov.guion.cursor == 0)
    check("el partial sigue guardado para mostrarse", ov.partial_text == "uno dos tres")
    br.stop()
    os.unlink(ruta)


def test_saltar_oracion_manual():
    ov, br = _overlay_con_bridge()
    ruta = _archivo_guion("Primera oración acá. Segunda oración acá.")
    ov.cargar_guion(ruta)
    ov.saltar_oracion(1)
    check("PageDown (saltar +1) mueve el cursor a mano", ov.guion.cursor > 0)
    cursor_tras_avanzar = ov.guion.cursor
    ov.saltar_oracion(-1)
    check("PageUp (saltar -1) vuelve para atrás",
          ov.guion.cursor < cursor_tras_avanzar)
    br.stop()
    os.unlink(ruta)


def test_sin_guion_append_text_sigue_igual():
    """El modo normal (sin --guion) no debe cambiar de comportamiento."""
    ov, br = _overlay_con_bridge()
    ov.append_text("sin guion cargado esto acumula como siempre")
    check("sin guion, el texto se acumula en current_line/lines",
          ov.current_line or any(ov.lines))
    check("guion queda None si nunca se cargó", ov.guion is None)
    br.stop()


# ---------------------------------------------------------------- main

def main():
    test_texto_final_simple()
    test_partial_es_sufijo_no_reemplaza()
    test_final_limpia_partial_pendiente()
    test_sin_duplicacion_streaming_incremental()
    test_vad_controla_scroll()
    test_clear_resetea_todo()
    test_toggle_activa_modo_fantasma()
    test_modo_fantasma_oculta_la_ventana_de_verdad()

    test_json_malformado_no_crashea()
    test_mensaje_oversized_se_descarta()
    test_palabra_patologica_se_trocea()
    test_rate_limit_no_crashea_con_spam()
    test_multiples_conexiones_simultaneas()
    test_cliente_roto_no_mata_el_bridge()
    test_sin_bridge_no_crashea()

    test_timer_se_detiene_en_idle()

    test_cargar_guion_valido()
    test_cargar_guion_inexistente_no_crashea()
    test_cargar_guion_vacio_no_crashea()
    test_modo_script_mueve_cursor_no_acumula_transcript()
    test_partial_no_mueve_cursor_en_modo_script()
    test_saltar_oracion_manual()
    test_sin_guion_append_text_sigue_igual()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests pasaron.")


if __name__ == "__main__":
    main()
