"""Integración GuionAR <-> productor de voz (ParlAR) sin terminal.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_integration.py

Usa socket Unix y SocketBridge reales, y guionar_client en modo
auto_connect como productor (audio y STT simulados: sólo se envían los
mensajes que ParlAR enviaría). Incluye una variante con GuionAR corriendo
como proceso aparte.
"""

import gc
import json
import os
import resource
import signal
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

from PyQt6.QtWidgets import QApplication

from bridge import SocketBridge
from desktop_shell import BandejaGuionAR, VentanaConfiguracion, crear_bandeja
from guionar import TeleprompterOverlay
from guionar_client import TeleprompterClient

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
_TMP = tempfile.TemporaryDirectory()

GUION = ("Buenas tardes a todos. Hoy les voy a contar cómo llegamos hasta acá. "
         "Empezamos con una idea simple. Después vinieron las pruebas.")


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _esperar(predicado, timeout=4.0):
    fin = time.time() + timeout
    while time.time() < fin:
        _app.processEvents()
        if predicado():
            return True
        time.sleep(0.01)
    _app.processEvents()
    return predicado()


def _ruta_socket():
    return os.path.join(_TMP.name, f"g{time.time_ns() % 10**9}.sock")


def _archivo(nombre, contenido):
    ruta = os.path.join(_TMP.name, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def _overlay():
    ov = TeleprompterOverlay({"width": 600, "height": 240})
    ov.show()
    _app.processEvents()
    return ov


def _bridge(ov, ruta):
    br = SocketBridge(ov, path=ruta)
    ov.set_ghost_recovery_available(br.start())
    return br


def _cerrar(ov):
    ov.close()
    _app.processEvents()
    gc.collect()


def _crudo(ruta, *mensajes):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(ruta)
    for m in mensajes:
        s.sendall((m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n")
    return s


def _hilos(nombre):
    return sum(1 for h in threading.enumerate() if h.name == nombre and h.is_alive())


HOLA = {"type": "hello", "client": "parlar", "role": "voice-producer", "protocol": 1}


# ---------------------------------------------------------------- GuionAR

def test_hello_es_presencia_no_voz():
    ov = _overlay()
    ruta = _ruta_socket()
    br = _bridge(ov, ruta)
    try:
        ov.cargar_guion(_archivo("g1.txt", GUION))
        check("sin ParlAR: listener activo informa 'No detectado'",
              ov.estado_parlar() == "No detectado")
        parlar = _crudo(ruta, HOLA)
        check("hello marca a ParlAR como conectado",
              _esperar(lambda: ov.parlar_presente) and br.voice_client_count == 1
              and ov.estado_parlar() == "Conectado")
        check("hello solo no es voz: sigue el avance sin voz",
              not ov.voz_conectada and br.voice_producer_count == 0 and ov._modo_auto())
        parlar.sendall(json.dumps({"type": "vad", "data": False}).encode() + b"\n")
        check("con voz real pasa a seguimiento de voz",
              _esperar(lambda: ov.voz_conectada) and ov.estado_parlar() == "Esperando voz")
        parlar.sendall(json.dumps({"type": "vad", "data": True}).encode() + b"\n")
        parlar.sendall(json.dumps({"type": "text", "data": "Buenas tardes a todos"}).encode() + b"\n")
        check("hablando: 'Siguiendo la voz' y el cursor avanza",
              _esperar(lambda: ov.guion.cursor == 4)
              and ov.estado_parlar() == "Siguiendo la voz")
        cursor = ov.guion.cursor
        parlar.close()
        check("ParlAR se cierra: presencia y voz se limpian",
              _esperar(lambda: not ov.parlar_presente and not ov.voz_conectada)
              and br.voice_client_count == 0 and br.voice_producer_count == 0)
        check("vuelve al avance sin voz sin perder la posición",
              ov._modo_auto() and ov.guion.cursor == cursor
              and ov.estado_parlar() == "No detectado")
    finally:
        br.stop()
        _cerrar(ov)


def test_control_hello_invalido_y_cliente_legado():
    ov = _overlay()
    ruta = _ruta_socket()
    br = _bridge(ov, ruta)
    conexiones = []
    try:
        ov.cargar_guion(_archivo("g2.txt", GUION))
        conexiones.append(_crudo(ruta, {"type": "clear"}, {"type": "toggle"}))
        _esperar(lambda: ov.hidden)
        invalidos = [
            {"type": "hello", "client": "parlar", "role": "control"},
            {"type": "hello", "client": 7, "role": "voice-producer"},
            {"type": "hello", "client": "", "role": "voice-producer"},
            {"type": "hello", "client": "x" * 65, "role": "voice-producer"},
            {"type": "hello", "role": "voice-producer"},
            {"type": "hello", "client": ["parlar"], "role": "voice-producer"},
        ]
        conexiones.append(_crudo(ruta, *invalidos, b"{no es json", b"[1,2]"))
        _esperar(lambda: br.active_connection_count == 2)
        time.sleep(0.2)
        _app.processEvents()
        check("controles y hellos inválidos no se presentan como ParlAR",
              br.voice_client_count == 0 and not ov.parlar_presente
              and br._thread.is_alive())
        legado = _crudo(ruta, {"type": "vad", "data": True},
                        {"type": "text", "data": "Buenas tardes"})
        conexiones.append(legado)
        check("cliente legado (sin hello) sigue funcionando como siempre",
              _esperar(lambda: ov.guion.cursor == 2) and ov.voz_conectada
              and br.voice_client_count == 0)
        legado.close()
        check("cliente legado al cerrarse vuelve al avance sin voz",
              _esperar(lambda: not ov.voz_conectada))
        dos = [_crudo(ruta, HOLA), _crudo(ruta, HOLA, HOLA)]
        conexiones.extend(dos)
        check("hello repetido no duplica: un cliente por conexión",
              _esperar(lambda: br.voice_client_count == 2))
        dos[0].close()
        check("con dos productores presentados, cerrar uno mantiene la presencia",
              _esperar(lambda: br.voice_client_count == 1) and ov.parlar_presente)
    finally:
        for c in conexiones:
            c.close()
        br.stop()
        _cerrar(ov)


def test_listener_ciclo_de_vida_y_ui():
    ov = _overlay()
    try:
        check("sin listener no se informa estado de ParlAR", ov.estado_parlar() is None)
        bandeja = crear_bandeja(ov, disponible=True)
        bandeja.sincronizar()
        check("bandeja oculta la línea de ParlAR sin listener",
              not bandeja.accion_parlar.isVisible())
        ruta = _ruta_socket()
        br = _bridge(ov, ruta)
        bandeja.sincronizar()
        check("bandeja con listener: 'ParlAR: no detectado'",
              bandeja.accion_parlar.isVisible()
              and bandeja.accion_parlar.text() == "ParlAR: no detectado")
        parlar = _crudo(ruta, HOLA)
        check("la bandeja se actualiza sola al conectarse ParlAR",
              _esperar(lambda: bandeja.accion_parlar.text() == "ParlAR: conectado"))
        check("estado vacío menciona a ParlAR conectado",
              "ParlAR conectado" in ov._textos_estado_vacio()[1])
        parlar.close()
        check("y vuelve a 'no detectado' al cerrarse",
              _esperar(lambda: bandeja.accion_parlar.text() == "ParlAR: no detectado"))
        for _ in range(4):
            ov.abrir_configuracion()
        check("Settings y bandeja no se duplican",
              len(ov.findChildren(VentanaConfiguracion)) == 1
              and len(ov.findChildren(BandejaGuionAR)) == 1)
        t0 = time.perf_counter()
        completo = br.stop()
        check("cierre del listener limpio y acotado",
              completo and time.perf_counter() - t0 < 2.5 and not os.path.exists(ruta))
    finally:
        _cerrar(ov)


# ---------------------------------------------------------------- cliente

class Registro:
    def __init__(self):
        self.lineas = []
        self.estados = []
        self.lock = threading.Lock()

    def log(self, texto):
        with self.lock:
            self.lineas.append(texto)

    def estado(self, conectado):
        with self.lock:
            self.estados.append(conectado)


def test_cliente_ausente_sin_spam_ni_bloqueo():
    reg = Registro()
    hilos_antes = _hilos("guionar-conexion")
    cliente = TeleprompterClient(_ruta_socket(), auto_connect=True, client_name="parlar",
                                 on_state=reg.estado, log=reg.log)
    try:
        for _ in range(3):
            cliente.start()   # idempotente
        check("como mucho un hilo de conexión", _hilos("guionar-conexion") - hilos_antes == 1)
        cpu0 = resource.getrusage(resource.RUSAGE_SELF)
        t0 = time.perf_counter()
        time.sleep(3.0)
        cpu1 = resource.getrusage(resource.RUSAGE_SELF)
        uso = ((cpu1.ru_utime + cpu1.ru_stime) - (cpu0.ru_utime + cpu0.ru_stime)) / 3.0 * 100
        check("GuionAR ausente: sin logs repetidos ni cambios de estado",
              reg.lineas == [] and reg.estados == [] and not cliente.connected, repr(reg.lineas))
        check("GuionAR ausente: consumo despreciable", uso < 2.0, f"{uso:.2f}%")
        t0 = time.perf_counter()
        for i in range(500):
            cliente.send_partial(f"hipótesis {i}")
            cliente.send_text("texto")
            cliente.send_vad(i % 2 == 0)
        dt = time.perf_counter() - t0
        check("enviar sin GuionAR nunca bloquea al STT", dt < 0.2, f"{dt:.3f}s")
    finally:
        t0 = time.perf_counter()
        terminado = cliente.close()
        check("cierre durante la espera de reintento es inmediato",
              terminado and time.perf_counter() - t0 < 0.5)


def test_cliente_aparece_muere_y_reconecta():
    ruta = _ruta_socket()
    reg = Registro()
    cliente = TeleprompterClient(ruta, auto_connect=True, client_name="parlar",
                                 on_state=reg.estado, log=reg.log)
    ov = _overlay()
    try:
        time.sleep(0.3)
        br = _bridge(ov, ruta)
        t0 = time.perf_counter()
        check("GuionAR aparece después: conexión automática en ~2 s",
              _esperar(lambda: cliente.connected and br.voice_client_count == 1, 3.0),
              f"{time.perf_counter() - t0:.2f}s")
        check("se presenta con hello al conectar", _esperar(lambda: ov.parlar_presente))
        t0 = time.perf_counter()
        br.stop()
        check("GuionAR se cierra: el cliente lo detecta enseguida",
              _esperar(lambda: not cliente.connected, 1.0), f"{time.perf_counter() - t0:.2f}s")
        cliente.send_text("se descarta sin error")
        br2 = _bridge(ov, ruta)
        check("GuionAR reaparece: reconexión automática",
              _esperar(lambda: cliente.connected and br2.voice_client_count == 1, 3.0))
        check("logs: sólo transiciones, sin repetidos",
              reg.lineas == ["[guionar] conectado", "[guionar] desconectado",
                             "[guionar] conectado"], repr(reg.lineas))
        check("on_state avisa cada transición una vez", reg.estados == [True, False, True])
        check("tras reconectar sigue habiendo un solo hilo de conexión",
              _hilos("guionar-conexion") == 1, str(_hilos("guionar-conexion")))
        errores = []

        def rafaga():
            try:
                for i in range(200):
                    cliente.send_partial(f"p{i}")
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos_envio = [threading.Thread(target=rafaga) for _ in range(4)]
        for h in hilos_envio:
            h.start()
        br2.stop()
        for h in hilos_envio:
            h.join(5)
        check("envíos concurrentes durante una caída no lanzan errores", not errores)
    finally:
        cliente.close()
        _cerrar(ov)


# ---------------------------------------------------------------- E2E

def test_e2e_productor_primero_guionar_despues():
    """Productor arranca primero, GuionAR aparece, conversación, GuionAR se
    va y vuelve: todo sin intervención."""
    ruta = _ruta_socket()
    productor = TeleprompterClient(ruta, auto_connect=True, client_name="parlar",
                                   log=lambda _t: None)
    ov = _overlay()
    try:
        ov.cargar_guion(_archivo("e2e.txt", GUION))
        time.sleep(0.5)
        check("E2E: sin GuionAR el productor sigue vivo y desconectado",
              not productor.connected and productor._hilo.is_alive())
        br = _bridge(ov, ruta)
        check("E2E: GuionAR aparece y el productor conecta",
              _esperar(lambda: productor.connected and ov.parlar_presente, 3.0))
        # STT simulado: lo mismo que ParlAR mandaría al dictar.
        productor.send_vad(True)
        productor.send_partial("Buenas tar")
        check("E2E: VAD y parcial llegan",
              _esperar(lambda: ov.speaking and ov.partial_text == "Buenas tar"))
        productor.send_text("Buenas tardes a todos")
        check("E2E: texto confirmado mueve el cursor",
              _esperar(lambda: ov.guion.cursor == 4 and ov.partial_text == ""))
        productor.send_vad(False)
        check("E2E: silencio coherente", _esperar(lambda: not ov.speaking)
              and ov.estado_parlar() == "Esperando voz")
        br.stop()
        _cerrar(ov)   # cerrar GuionAR termina su proceso: nada sobrevive
        check("E2E: GuionAR desaparece y el productor lo detecta",
              _esperar(lambda: not productor.connected, 1.5))
        ov = _overlay()   # GuionAR reabierto: instancia nueva
        ov.cargar_guion(_archivo("e2e.txt", GUION))
        br2 = _bridge(ov, ruta)
        check("E2E: GuionAR reaparece y reconecta",
              _esperar(lambda: productor.connected and ov.parlar_presente, 3.0))
        productor.send_vad(True)
        productor.send_text("Buenas tardes a todos. Hoy les")
        check("E2E: después de reconectar vuelve a seguir la voz",
              _esperar(lambda: ov.guion.cursor == 6) and ov.voz_conectada,
              str(ov.guion.cursor))
        br2.stop()
    finally:
        productor.close()
        _cerrar(ov)


def test_e2e_guionar_como_proceso():
    """GuionAR real (main) en otro proceso, cerrado y reabierto."""
    ruta = _ruta_socket()
    cfg = tempfile.mkdtemp()
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "XDG_CONFIG_HOME": cfg}

    def lanzar():
        return subprocess.Popen([sys.executable, str(ROOT / "guionar.py"), "--socket",
                                 "--socket-path", ruta], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    reg = Registro()
    productor = TeleprompterClient(ruta, auto_connect=True, client_name="parlar",
                                   on_state=reg.estado, log=reg.log)
    procesos = []
    try:
        p = lanzar()
        procesos.append(p)
        check("proceso: ParlAR conecta solo a un GuionAR recién abierto",
              _esperar(lambda: productor.connected, 8.0))
        productor.send_vad(True)
        productor.send_text("hola")
        p.send_signal(signal.SIGINT)
        p.wait(10)
        check("proceso: GuionAR se cierra limpio", p.returncode == 0,
              p.stderr.read()[-300:] if p.stderr else "")
        check("proceso: el productor detecta el cierre", _esperar(lambda: not productor.connected, 2.0))
        p2 = lanzar()
        procesos.append(p2)
        check("proceso: GuionAR reabierto, reconexión automática",
              _esperar(lambda: productor.connected, 8.0))
        check("proceso: transiciones registradas una vez cada una",
              reg.estados == [True, False, True], repr(reg.estados))
    finally:
        productor.close()
        for p in procesos:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()


def main():
    test_hello_es_presencia_no_voz()
    test_control_hello_invalido_y_cliente_legado()
    test_listener_ciclo_de_vida_y_ui()
    test_cliente_ausente_sin_spam_ni_bloqueo()
    test_cliente_aparece_muere_y_reconecta()
    test_e2e_productor_primero_guionar_despues()
    test_e2e_guionar_como_proceso()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de integración pasaron.")


if __name__ == "__main__":
    main()
