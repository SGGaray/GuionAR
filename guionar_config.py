"""Configuración persistente de GuionAR.

Se carga desde ~/.config/guionar/config.json si existe; si no, se usan
los DEFAULTS de guionar.py. Los flags CLI (--opacity, --font-size)
sobreescriben lo guardado. Para persistir los valores actuales, correr
con --guardar-config.

Se guardan preferencias de apariencia y comportamiento (opacidad,
tamaños, alineación, pausa con el puntero, bloqueo, siempre encima,
geometría de la ventana, ocultado de controles). La ventana de Configuración las persiste
con ``actualizar()``, que sólo escribe las claves cambiadas y conserva el
resto de lo guardado. Todo lo operativo (--socket, --socket-path, --demo)
es por sesión y no tiene sentido persistirlo.

Una configuración vieja sin las claves nuevas sigue siendo válida: lo que
falta lo aportan los DEFAULTS de guionar.py.
"""

import json
import math
import os
from pathlib import Path
import tempfile

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "guionar"
CONFIG_FILE = CONFIG_DIR / "config.json"

CLAVES_PERSISTIDAS = (
    "bg_opacity", "font_size_current", "font_size_context",
    "text_alignment", "pause_on_hover", "position_locked",
    "remember_geometry", "auto_hide_controls", "window_geometry",
    "always_on_top",
)

# Contrato de los valores que cruzan desde disco hacia Qt. Los tamaños
# coinciden con los límites públicos del CLI y los mínimos de los controles.
LIMITES = {
    "bg_opacity": (0.0, 1.0),
    "font_size_current": (14, 96),
    "font_size_context": (10, 96),
}

ALINEACIONES = ("left", "center", "right")
CLAVES_BOOLEANAS = ("pause_on_hover", "position_locked", "always_on_top",
                    "remember_geometry", "auto_hide_controls")

# Geometría: un rectángulo plausible. Que caiga dentro de una pantalla
# real se corrige al restaurarla, no acá (los monitores cambian).
GEOMETRIA_TAMANO = (100, 20000)
GEOMETRIA_POSICION = (-100000, 100000)


def _entero(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _geometria_valida(valor) -> bool:
    if not isinstance(valor, dict) or set(valor) != {"x", "y", "width", "height"}:
        return False
    if not all(_entero(valor[k]) for k in valor):
        return False
    pmin, pmax = GEOMETRIA_POSICION
    tmin, tmax = GEOMETRIA_TAMANO
    return (pmin <= valor["x"] <= pmax and pmin <= valor["y"] <= pmax
            and tmin <= valor["width"] <= tmax and tmin <= valor["height"] <= tmax)


def _valor_valido(clave: str, valor) -> bool:
    if clave == "text_alignment":
        return isinstance(valor, str) and valor in ALINEACIONES
    if clave in CLAVES_BOOLEANAS:
        return isinstance(valor, bool)
    if clave == "window_geometry":
        return _geometria_valida(valor)
    minimo, maximo = LIMITES[clave]
    if isinstance(valor, bool):
        return False
    if clave == "bg_opacity":
        return (isinstance(valor, (int, float)) and math.isfinite(valor)
                and minimo <= valor <= maximo)
    return isinstance(valor, int) and minimo <= valor <= maximo


def normalizar(data) -> dict:
    """Filtra un documento persistido contra el contrato visual explícito.

    Un campo inválido se omite para que DEFAULTS aporte el fallback, sin
    descartar otros campos válidos del mismo documento.
    """
    if not isinstance(data, dict):
        print("[config] la raíz debe ser un objeto; usando valores por defecto")
        return {}

    resultado = {}
    for clave in CLAVES_PERSISTIDAS:
        if clave not in data:
            continue
        if _valor_valido(clave, data[clave]):
            resultado[clave] = data[clave]
        else:
            print(f"[config] valor inválido para {clave}; usando valor por defecto")
    return resultado


def cargar() -> dict:
    """Devuelve las claves persistidas encontradas en disco, o {} si no
    hay archivo o está corrupto (nunca lanza excepción)."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError, OSError) as e:
        print(f"[config] no se pudo leer {CONFIG_FILE}: {e}; usando valores por defecto")
        return {}
    return normalizar(data)


def guardar(cfg: dict, silencioso: bool = False) -> None:
    """Persiste las claves conocidas de cfg (escritura atómica). Crea el
    directorio si no existe."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = normalizar({k: cfg[k] for k in CLAVES_PERSISTIDAS if k in cfg})
    contenido = json.dumps(data, indent=2)
    descriptor, temporal = tempfile.mkstemp(
        dir=CONFIG_FILE.parent,
        prefix=f".{CONFIG_FILE.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, CONFIG_FILE)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise
    if not silencioso:
        print(f"[config] guardada en {CONFIG_FILE}")


def actualizar(cambios: dict) -> None:
    """Persiste sólo ``cambios`` sobre lo que ya hay en disco.

    Así un override de una sesión (``--opacity``) no se guarda por haber
    cambiado otra preferencia desde la ventana de Configuración."""
    datos = cargar()
    datos.update(normalizar(cambios))
    guardar(datos, silencioso=True)
