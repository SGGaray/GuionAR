"""Regresiones de remediación Fase 2: lifecycle, rate limit y SIGINT.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_phase2.py
"""

import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bridge as bridge_module
from bridge import SocketBridge


FALLAS = []


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _esperar(predicado, timeout=1.5):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if predicado():
            return True
        time.sleep(0.01)
    return predicado()


def _lectores_de(bridge):
    resultado = []
    for hilo in threading.enumerate():
        destino = getattr(hilo, "_target", None)
        if (getattr(destino, "__self__", None) is bridge
                and getattr(destino, "__name__", "") in {
                    "_handle_connection", "_connection_worker"
                }):
            resultado.append(hilo)
    return resultado


def _conectar(path):
    cliente = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cliente.settimeout(1.0)
    cliente.connect(str(path))
    return cliente


def _enviar(cliente, tipo, data=None):
    mensaje = {"type": tipo}
    if data is not None:
        mensaje["data"] = data
    cliente.sendall((json.dumps(mensaje) + "\n").encode("utf-8"))


def _raw(tipo, data=None):
    mensaje = {"type": tipo}
    if data is not None:
        mensaje["data"] = data
    return json.dumps(mensaje).encode("utf-8")


def _cerrar(cliente):
    try:
        cliente.close()
    except OSError:
        pass


class RecordingBridge(SocketBridge):
    """Observa dispatch real sin depender del event loop de Qt."""

    def __init__(self, path=None):
        kwargs = {"path": str(path)} if path is not None else {}
        super().__init__(**kwargs)
        self.records = []
        self.records_lock = threading.Lock()

    def _record(self, tipo, data=None):
        with self.records_lock:
            self.records.append((tipo, data))

    def push_text(self, text):
        self._record("text", text)

    def push_partial(self, text):
        self._record("partial", text)

    def push_vad(self, speaking):
        self._record("vad", speaking)

    def push_clear(self):
        self._record("clear")

    def push_toggle(self):
        self._record("toggle")

    def cantidad(self, tipo):
        with self.records_lock:
            return sum(1 for actual, _ in self.records if actual == tipo)

    def contiene(self, tipo, data=None):
        with self.records_lock:
            return (tipo, data) in self.records


def test_stop_sin_clientes_y_repetido():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sin-clientes.sock"
        bridge = RecordingBridge(path)
        check("bridge sin clientes inicia", bridge.start())
        primera = bridge.stop()
        segunda = bridge.stop()
        check("stop sin clientes termina listener", not bridge._thread.is_alive())
        check("stop sin clientes limpia endpoint", not os.path.lexists(path))
        try:
            rechazado = _conectar(path)
        except OSError:
            rechazado = None
        check("stop no acepta conexiones nuevas", rechazado is None)
        if rechazado is not None:
            _cerrar(rechazado)
        check("stop repetido es seguro", primera is not False and segunda is not False)


def test_stop_cierra_lectores_idle():
    for cantidad in (1, 4):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"idle-{cantidad}.sock"
            bridge = RecordingBridge(path)
            clientes = []
            try:
                check(f"bridge para {cantidad} idle inicia", bridge.start())
                clientes = [_conectar(path) for _ in range(cantidad)]
                _esperar(lambda: len(_lectores_de(bridge)) == cantidad)
                antes = len(_lectores_de(bridge))
                resultado = bridge.stop()
                despues = len(_lectores_de(bridge))
                check(f"se observaron {cantidad} lectores idle", antes == cantidad,
                      f"antes={antes}")
                check(f"stop converge {cantidad} lectores a cero",
                      despues == 0, f"después={despues}")
                check(f"registro queda vacío con {cantidad} lectores",
                      getattr(bridge, "active_connection_count", -1) == 0
                      and getattr(bridge, "worker_count", -1) == 0)
                check(f"stop reporta cierre completo con {cantidad} lectores",
                      resultado is not False)
            finally:
                for cliente in clientes:
                    _cerrar(cliente)
                bridge.stop()


def test_eof_normal_limpia_worker():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "eof.sock"
        bridge = RecordingBridge(path)
        cliente = None
        try:
            check("bridge para EOF inicia", bridge.start())
            cliente = _conectar(path)
            check("EOF crea un lector", _esperar(lambda: len(_lectores_de(bridge)) == 1))
            cliente.close()
            cliente = None
            check("EOF normal limpia lector",
                  _esperar(lambda: len(_lectores_de(bridge)) == 0))
        finally:
            if cliente is not None:
                _cerrar(cliente)
            bridge.stop()


def test_limite_de_conexiones():
    limite = getattr(bridge_module, "MAX_CLIENT_CONNECTIONS", 8)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "limite.sock"
        bridge = RecordingBridge(path)
        admitidos = []
        excedentes = []
        try:
            check("bridge para límite inicia", bridge.start())
            admitidos = [_conectar(path) for _ in range(limite)]
            check("límite admite capacidad configurada",
                  _esperar(lambda: len(_lectores_de(bridge)) == limite),
                  f"lectores={len(_lectores_de(bridge))}")

            for _ in range(6):
                excedentes.append(_conectar(path))
            _esperar(lambda: len(_lectores_de(bridge)) > limite, timeout=0.4)
            lectores = len(_lectores_de(bridge))
            check("excedentes no crean workers", lectores <= limite,
                  f"límite={limite}, lectores={lectores}")
            check("registros respetan límite",
                  getattr(bridge, "active_connection_count", limite + 1) <= limite
                  and getattr(bridge, "worker_count", limite + 1) <= limite)

            cerrados = 0
            for cliente in excedentes:
                try:
                    if cliente.recv(1) == b"":
                        cerrados += 1
                except (OSError, TimeoutError):
                    pass
            check("conexiones excedentes se cierran pronto", cerrados == len(excedentes),
                  f"cerradas={cerrados}/{len(excedentes)}")

            _enviar(admitidos[0], "text", "usable-en-el-limite")
            check("clientes existentes siguen usables al límite",
                  _esperar(lambda: bridge.contiene("text", "usable-en-el-limite")))

            bridge.stop()
            check("stop al límite converge workers a cero",
                  len(_lectores_de(bridge)) == 0)
            check("stop al límite vacía registros",
                  bridge.active_connection_count == 0 and bridge.worker_count == 0)
        finally:
            for cliente in admitidos + excedentes:
                _cerrar(cliente)
            bridge.stop()


def test_no_dispatch_despues_de_stop():
    bridge = RecordingBridge()
    bridge.stop()
    bridge._handle(_raw("text", "AFTER_STOP"))
    check("llamada directa después de stop no despacha",
          not bridge.contiene("text", "AFTER_STOP"))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "race.sock"
        bridge = RecordingBridge(path)
        cliente = None
        entro = threading.Event()
        liberar = threading.Event()
        original = bridge._handle

        def retenido(raw):
            entro.set()  # recv ya entregó una línea completa al worker
            liberar.wait(timeout=2.0)
            original(raw)

        bridge._handle = retenido
        try:
            check("bridge para carrera inicia", bridge.start())
            cliente = _conectar(path)
            _enviar(cliente, "text", "LEIDO_ANTES_DE_STOP")
            check("worker alcanzó frontera de dispatch", entro.wait(timeout=1.0))
            hilo_stop = threading.Thread(target=bridge.stop)
            hilo_stop.start()
            check("stop publicó frontera de shutdown",
                  _esperar(bridge._stop.is_set, timeout=1.0))
            liberar.set()
            hilo_stop.join(timeout=2.5)
            check("stop termina en carrera de dispatch", not hilo_stop.is_alive())
            check("bytes leídos antes de stop no despachan después de frontera",
                  not bridge.contiene("text", "LEIDO_ANTES_DE_STOP"))
        finally:
            liberar.set()
            if cliente is not None:
                _cerrar(cliente)
            bridge.stop()


def test_rate_limit_diferenciado():
    limite_parcial = getattr(
        bridge_module, "MAX_PARTIAL_MSGS_PER_SEC",
        getattr(bridge_module, "MAX_MSGS_PER_SEC", 200),
    )
    limite_control = getattr(bridge_module, "MAX_CONTROL_MSGS_PER_SEC", 32)

    normal = RecordingBridge()
    normal._handle(_raw("partial", "flujo normal"))
    check("flujo partial normal se entrega",
          normal.contiene("partial", "flujo normal"))

    inundado = RecordingBridge()
    for i in range(limite_parcial + 25):
        inundado._handle(_raw("partial", f"p{i}"))
    check("flood partial queda acotado",
          inundado.cantidad("partial") == limite_parcial,
          f"recibidos={inundado.cantidad('partial')}")

    controles = RecordingBridge()
    controles._handle(_raw("vad", True))
    for i in range(limite_parcial - 1):
        controles._handle(_raw("partial", f"p{i}"))
    controles._handle(_raw("vad", False))
    controles._handle(_raw("clear"))
    controles._handle(_raw("toggle"))
    controles._handle(_raw("text", "final-importante"))
    check("VAD false sobrevive saturación partial",
          controles.contiene("vad", False))
    check("clear sobrevive saturación partial", controles.contiene("clear"))
    check("toggle sobrevive saturación partial", controles.contiene("toggle"))
    check("texto final sobrevive saturación partial",
          controles.contiene("text", "final-importante"))

    desconocidos = RecordingBridge()
    for _ in range(limite_parcial + 20):
        desconocidos._handle(_raw("desconocido"))
    desconocidos._handle(_raw("toggle"))
    check("mensajes desconocidos no obtienen prioridad de control",
          desconocidos.cantidad("toggle") == 1)

    acotado = RecordingBridge()
    for _ in range(limite_control + 10):
        acotado._handle(_raw("toggle"))
    check("etiquetar controles no da bypass ilimitado",
          acotado.cantidad("toggle") == limite_control,
          f"recibidos={acotado.cantidad('toggle')}")

    recupera = RecordingBridge()
    for i in range(limite_parcial + 1):
        recupera._handle(_raw("partial", f"inicial-{i}"))
    cantidad_inicial = recupera.cantidad("partial")
    limite_tiempo = time.monotonic() + 1.5
    intento = 0
    while (recupera.cantidad("partial") == cantidad_inicial
           and time.monotonic() < limite_tiempo):
        recupera._handle(_raw("partial", f"recuperado-{intento}"))
        intento += 1
        time.sleep(0.02)
    check("ventana de rate limit se recupera",
          recupera.cantidad("partial") > cantidad_inicial)


def test_rate_limit_multicliente_y_listener_usable():
    limite = getattr(
        bridge_module, "MAX_PARTIAL_MSGS_PER_SEC",
        getattr(bridge_module, "MAX_MSGS_PER_SEC", 200),
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rate-multicliente.sock"
        bridge = RecordingBridge(path)
        cliente_a = cliente_b = None
        try:
            check("bridge para rate multicliente inicia", bridge.start())
            cliente_a = _conectar(path)
            cliente_b = _conectar(path)
            carga = b"".join(
                (json.dumps({"type": "partial", "data": f"a{i}"}) + "\n")
                .encode("utf-8") for i in range(limite)
            )
            cliente_a.sendall(carga)
            check("cliente A satura cuota partial",
                  _esperar(lambda: bridge.cantidad("partial") == limite))
            _enviar(cliente_b, "toggle")
            _enviar(cliente_b, "text", "final-de-b")
            check("control de cliente B sobrevive flood de A",
                  _esperar(lambda: bridge.contiene("toggle")))
            check("final de cliente B sobrevive flood de A",
                  _esperar(lambda: bridge.contiene("text", "final-de-b")))
            check("listener sigue vivo tras saturación", bridge._thread.is_alive())
        finally:
            if cliente_a is not None:
                _cerrar(cliente_a)
            if cliente_b is not None:
                _cerrar(cliente_b)
            bridge.stop()


def _esperar_path_socket(path, proc, timeout=4.0):
    def listo():
        if proc.poll() is not None:
            return True
        try:
            return stat.S_ISSOCK(path.lstat().st_mode)
        except OSError:
            return False

    _esperar(listo, timeout=timeout)
    try:
        return proc.poll() is None and stat.S_ISSOCK(path.lstat().st_mode)
    except OSError:
        return False


def _terminar_si_sigue(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def _lanzar_guionar(path):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, "guionar.py", "--socket", "--socket-path", str(path)],
        cwd=Path(__file__).parent.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_sigint_cooperativo_y_restart():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sigint.sock"
        primero = _lanzar_guionar(path)
        cliente = None
        forzado = False
        try:
            listo = _esperar_path_socket(path, primero)
            check("proceso con socket llega a ready", listo)
            if listo:
                # El listener publica el pathname justo antes de app.exec().
                # Da tiempo a que Qt empiece a procesar el handler de SIGINT.
                time.sleep(0.2)
                cliente = _conectar(path)
                carga = b"".join(
                    (json.dumps({"type": "partial", "data": f"p{i}"}) + "\n")
                    .encode("utf-8") for i in range(250)
                )
                cliente.sendall(carga)
                os.kill(primero.pid, signal.SIGINT)
                try:
                    primero.wait(timeout=4.0)
                except subprocess.TimeoutExpired:
                    forzado = True
                    primero.kill()
                    primero.wait(timeout=2.0)
            check("SIGINT termina dentro del límite", primero.poll() is not None)
            check("SIGINT no requiere SIGKILL", not forzado)
            check("SIGINT sale por ruta cooperativa",
                  primero.returncode is not None and primero.returncode >= 0,
                  f"returncode={primero.returncode}")
            check("SIGINT limpia endpoint propio", not os.path.lexists(path))
        finally:
            if cliente is not None:
                _cerrar(cliente)
            _terminar_si_sigue(primero)
            if os.path.lexists(path) and primero.poll() is not None:
                path.unlink()

        segundo = _lanzar_guionar(path)
        try:
            listo = _esperar_path_socket(path, segundo)
            check("nueva instancia reutiliza path tras SIGINT", listo)
            if listo:
                # El socket puede aparecer apenas antes de que comience el
                # event loop Qt. Evita que SIGINT llegue en esa frontera.
                time.sleep(0.2)
                os.kill(segundo.pid, signal.SIGINT)
                segundo.wait(timeout=4.0)
            check("segunda instancia también limpia", not os.path.lexists(path))
        finally:
            _terminar_si_sigue(segundo)
            if os.path.lexists(path) and segundo.poll() is not None:
                path.unlink()


def main():
    test_stop_sin_clientes_y_repetido()
    test_stop_cierra_lectores_idle()
    test_eof_normal_limpia_worker()
    test_limite_de_conexiones()
    test_no_dispatch_despues_de_stop()
    test_rate_limit_diferenciado()
    test_rate_limit_multicliente_y_listener_usable()
    test_sigint_cooperativo_y_restart()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de Fase 2 pasaron.")


if __name__ == "__main__":
    main()
