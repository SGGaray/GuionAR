"""Configuración persistente de GuionAR.

Se carga desde ~/.config/guionar/config.json si existe; si no, se usan
los DEFAULTS de guionar.py. Los flags CLI (--opacity, --font-size)
sobreescriben lo guardado. Para persistir los valores actuales, correr
con --guardar-config.

Solo se guardan las claves visuales (opacidad, tamaño de fuente): todo
lo operativo (--socket, --socket-path, --demo) es por sesión y no tiene
sentido persistirlo.
"""

import json
import math
import os
from pathlib import Path
import tempfile

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "guionar"
CONFIG_FILE = CONFIG_DIR / "config.json"

CLAVES_PERSISTIDAS = ("bg_opacity", "font_size_current", "font_size_context")

# Contrato de los únicos valores que cruzan desde disco hacia Qt. Los tamaños
# coinciden con los límites públicos del CLI y los mínimos de los controles.
LIMITES = {
    "bg_opacity": (0.0, 1.0),
    "font_size_current": (14, 96),
    "font_size_context": (10, 96),
}


def _valor_valido(clave: str, valor) -> bool:
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


def guardar(cfg: dict) -> None:
    """Persiste las claves visuales de cfg. Crea el directorio si no existe."""
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
    print(f"[config] guardada en {CONFIG_FILE}")
