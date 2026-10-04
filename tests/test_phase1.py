"""Regresiones, grupo 1.

Corre sin display real:
    QT_QPA_PLATFORM=offscreen python tests/test_phase1.py
"""

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import guionar_config
from bridge import SocketBridge
from guionar import TeleprompterOverlay


FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _esperar(predicado, timeout=1.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if predicado():
            return True
        time.sleep(0.01)
    return predicado()


def _cargar_bytes(contenido):
    with tempfile.TemporaryDirectory() as tmp:
        anterior_dir = guionar_config.CONFIG_DIR
        anterior_file = guionar_config.CONFIG_FILE
        try:
            guionar_config.CONFIG_DIR = Path(tmp)
            guionar_config.CONFIG_FILE = Path(tmp) / "config.json"
            if contenido is not None:
                guionar_config.CONFIG_FILE.write_bytes(contenido)
            return guionar_config.cargar()
        finally:
            guionar_config.CONFIG_DIR = anterior_dir
            guionar_config.CONFIG_FILE = anterior_file


def test_configuracion_normalizada():
    check("config ausente usa fallback", _cargar_bytes(None) == {})

    valida = {
        "bg_opacity": 0.7,
        "font_size_current": 42,
        "font_size_context": 24,
        "ignorada": "valor",
    }
    check("config válida conserva campos públicos",
          _cargar_bytes(json.dumps(valida).encode("utf-8")) == {
              "bg_opacity": 0.7,
              "font_size_current": 42,
              "font_size_context": 24,
          })
    for clave, valores in {
        "bg_opacity": (0.0, 1.0),
        "font_size_current": (14, 96),
        "font_size_context": (10, 96),
    }.items():
        for valor in valores:
            resultado = _cargar_bytes(
                json.dumps({clave: valor}).encode("utf-8"))
            check(f"config acepta límite {clave}={valor}",
                  resultado.get(clave) == valor, repr(resultado))

    raices_invalidas = {
        "JSON malformado": b"{",
        "UTF-8 inválido": b'{"bg_opacity":"\xff"}',
        "array raíz": b"[]",
        "null raíz": b"null",
        "número raíz": b"7",
        "string raíz": b'"texto"',
        "bool raíz": b"true",
    }
    for caso, contenido in raices_invalidas.items():
        try:
            resultado = _cargar_bytes(contenido)
            seguro = resultado == {}
        except BaseException as exc:
            seguro = False
            resultado = repr(exc)
        check(f"config {caso} recupera", seguro, str(resultado))

    invalidos = [
        {"font_size_current": "30"},
        {"font_size_current": True},
        {"font_size_current": -1},
        {"font_size_current": 0},
        {"font_size_current": 10**100},
        {"font_size_context": 12.5},
        {"font_size_context": False},
        {"font_size_context": -1},
        {"font_size_context": 0},
        {"font_size_context": 10**100},
        {"bg_opacity": "0.5"},
        {"bg_opacity": True},
        {"bg_opacity": -0.1},
        {"bg_opacity": 1.1},
        {"bg_opacity": 10**100},
    ]
    for data in invalidos:
        clave = next(iter(data))
        resultado = _cargar_bytes(json.dumps(data).encode("utf-8"))
        check(f"config rechaza {clave}={data[clave]!r}", clave not in resultado,
              repr(resultado))

    for literal in (b"NaN", b"Infinity", b"-Infinity"):
        resultado = _cargar_bytes(b'{"bg_opacity":' + literal + b"}")
        check(f"config rechaza opacidad no finita {literal.decode()}",
              "bg_opacity" not in resultado, repr(resultado))

    mezcla = _cargar_bytes(
        b'{"bg_opacity":0.4,"font_size_current":"enorme",'
        b'"font_size_context":22}'
    )
    check("config mixta conserva campos válidos",
          mezcla == {"bg_opacity": 0.4, "font_size_context": 22}, repr(mezcla))


def test_override_cli_prevalece():
    if not hasattr(__import__("guionar"), "_configuracion_efectiva"):
        check("existe límite único config/CLI", False, "falta _configuracion_efectiva")
        return
    from guionar import _configuracion_efectiva

    with tempfile.TemporaryDirectory() as tmp:
        anterior_dir = guionar_config.CONFIG_DIR
        anterior_file = guionar_config.CONFIG_FILE
        try:
            guionar_config.CONFIG_DIR = Path(tmp)
            guionar_config.CONFIG_FILE = Path(tmp) / "config.json"
            guionar_config.CONFIG_FILE.write_text(
                '{"bg_opacity":0.2,"font_size_current":20,'
                '"font_size_context":12}', encoding="utf-8")
            args = SimpleNamespace(opacity=0.8, font_size=50,
                                   guardar_config=False)
            cfg = _configuracion_efectiva(args)
        finally:
            guionar_config.CONFIG_DIR = anterior_dir
            guionar_config.CONFIG_FILE = anterior_file
    check("CLI pisa opacidad persistida", cfg["bg_opacity"] == 0.8, repr(cfg))
    check("CLI pisa tamaño persistido", cfg["font_size_current"] == 50, repr(cfg))
    check("CLI deriva contexto seguro", cfg["font_size_context"] == 30, repr(cfg))


def test_startup_subproceso_con_config_rota():
    codigo = (
        "from PyQt6.QtWidgets import QApplication; "
        "import guionar_config; from guionar import TeleprompterOverlay; "
        "app=QApplication([]); ov=TeleprompterOverlay(guionar_config.cargar()); "
        "ov.show(); app.processEvents(); print('usable')"
    )
    casos = {
        "raíz inválida": b"[]",
        "UTF-8 inválido": b"\xff",
        "campos extremos": (
            b'{"bg_opacity":1e300,"font_size_current":999999999999999999999,'
            b'"font_size_context":-5}'
        ),
    }
    for nombre, contenido in casos.items():
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "guionar"
            config_dir.mkdir()
            (config_dir / "config.json").write_bytes(contenido)
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = tmp
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(
                [sys.executable, "-c", codigo], cwd=Path(__file__).parent.parent,
                env=env, capture_output=True, text=True, timeout=5,
            )
        check(f"startup recupera config {nombre}",
              proc.returncode == 0 and "usable" in proc.stdout,
              f"rc={proc.returncode} stderr={proc.stderr[-300:]!r}")


def _iniciar_bridge(path):
    bridge = SocketBridge(path=str(path))
    resultado = bridge.start()
    if isinstance(resultado, bool):
        return bridge, resultado
    _esperar(lambda: not bridge._thread.is_alive() or path.exists())
    return bridge, bridge._thread.is_alive()


def _detener_bridge(bridge):
    bridge.stop()
    bridge._thread.join(timeout=1.0)


def test_socket_rutas_y_stale():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nuevo.sock"
        bridge, iniciado = _iniciar_bridge(path)
        check("socket fresco inicia", iniciado)
        check("socket se crea con permiso 0600",
              stat.S_IMODE(path.lstat().st_mode) == 0o600 if path.exists() else False)
        _detener_bridge(bridge)
        check("cleanup elimina endpoint propio", not os.path.lexists(path))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ocupado"
        contenido = b"dato del usuario\x00intacto"
        path.write_bytes(contenido)
        bridge, iniciado = _iniciar_bridge(path)
        check("archivo regular impide socket", not iniciado)
        check("archivo regular informa conflicto claro",
              "not a Unix socket" in str(bridge._startup_error))
        check("archivo regular se conserva", path.is_file())
        try:
            contenido_actual = path.read_bytes()
        except OSError:
            contenido_actual = None
        check("contenido regular queda byte a byte", contenido_actual == contenido)
        _detener_bridge(bridge)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "directorio"
        path.mkdir()
        bridge, iniciado = _iniciar_bridge(path)
        check("directorio impide socket", not iniciado)
        check("directorio se conserva", path.is_dir())
        _detener_bridge(bridge)

    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "destino"
        destino.write_bytes(b"no tocar")
        path = Path(tmp) / "enlace"
        path.symlink_to(destino)
        bridge, iniciado = _iniciar_bridge(path)
        check("symlink impide socket", not iniciado)
        check("symlink se conserva", path.is_symlink())
        check("destino de symlink se conserva",
              destino.exists() and destino.read_bytes() == b"no tocar")
        _detener_bridge(bridge)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "stale.sock"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
        bridge, iniciado = _iniciar_bridge(path)
        check("socket stale se recupera", iniciado)
        _detener_bridge(bridge)
        check("cleanup de stale recuperado elimina endpoint", not path.exists())


def test_socket_instancia_activa_y_propiedad():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "unico.sock"
        primero, inicio_primero = _iniciar_bridge(path)
        segundo, inicio_segundo = _iniciar_bridge(path)
        check("primera instancia inicia", inicio_primero)
        check("segunda instancia activa es rechazada", not inicio_segundo)
        check("segunda instancia informa endpoint activo",
              "active Unix socket" in str(segundo._startup_error))
        check("segunda instancia no queda escuchando", not segundo._thread.is_alive())

        conexiones = []
        for _ in range(2):
            cliente = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                cliente.settimeout(1.0)
                cliente.connect(str(path))
                conexiones.append(True)
            except OSError:
                conexiones.append(False)
            finally:
                cliente.close()
        check("primera instancia sigue alcanzable", all(conexiones), repr(conexiones))
        check("no hay conexiones partidas hacia segunda instancia",
              not getattr(segundo, "_listening", False))
        _detener_bridge(segundo)
        _detener_bridge(primero)
        check("propietario limpia socket al detener", not os.path.lexists(path))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reemplazado.sock"
        bridge, iniciado = _iniciar_bridge(path)
        check("bridge para prueba de reemplazo inicia", iniciado)
        path.unlink()
        path.write_bytes(b"reemplazo ajeno")
        _detener_bridge(bridge)
        check("cleanup no borra pathname reemplazado", path.is_file())
        check("cleanup conserva contenido reemplazado",
              path.read_bytes() == b"reemplazo ajeno")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "repetido.sock"
        primero, inicio_primero = _iniciar_bridge(path)
        _detener_bridge(primero)
        segundo, inicio_segundo = _iniciar_bridge(path)
        _detener_bridge(segundo)
        check("start/stop repetido con nuevo bridge", inicio_primero and inicio_segundo)


def _archivo_bytes(contenido):
    descriptor, ruta = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(descriptor, contenido)
    finally:
        os.close(descriptor)
    return ruta


def test_carga_guion_utf8_y_recuperacion():
    casos_validos = (
        ("texto plano", "uno dos tres"),
        ("español acentuado", "Más allá está la canción"),
        ("emoji", "Inicio 🎬 acción"),
    )
    for nombre, texto in casos_validos:
        ruta = _archivo_bytes(texto.encode("utf-8"))
        try:
            ov = TeleprompterOverlay()
            ov.cargar_guion(ruta)
            check(f"guion {nombre} carga como UTF-8",
                  ov.guion is not None and ov.guion.valido)
        finally:
            os.unlink(ruta)

    recuperables = (
        ("UTF-8 inválido", _archivo_bytes(b"texto\xffroto")),
        ("vacío", _archivo_bytes(b"")),
        ("inexistente", "/no/existe/guionar-phase1.txt"),
    )
    directorio = tempfile.TemporaryDirectory()
    try:
        recuperables += (("error de lectura", directorio.name),)
        for nombre, ruta in recuperables:
            ov = TeleprompterOverlay()
            try:
                ov.cargar_guion(ruta)
                ov.append_text("dictado todavía usable")
                seguro = ov.guion is None and "dictado" in ov.current_line
            except BaseException:
                seguro = False
            check(f"guion {nombre} vuelve a dictado usable", seguro)
    finally:
        for _, ruta in recuperables[:2]:
            os.unlink(ruta)
        directorio.cleanup()


def main():
    test_configuracion_normalizada()
    test_override_cli_prevalece()
    test_startup_subproceso_con_config_rota()
    test_socket_rutas_y_stale()
    test_socket_instancia_activa_y_propiedad()
    test_carga_guion_utf8_y_recuperacion()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests del grupo 1 pasaron.")


if __name__ == "__main__":
    main()
