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
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "guionar"
CONFIG_FILE = CONFIG_DIR / "config.json"

CLAVES_PERSISTIDAS = ("bg_opacity", "font_size_current", "font_size_context")


def cargar() -> dict:
    """Devuelve las claves persistidas encontradas en disco, o {} si no
    hay archivo o está corrupto (nunca lanza excepción)."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] no se pudo leer {CONFIG_FILE}: {e}; usando valores por defecto")
        return {}
    return {k: v for k, v in data.items() if k in CLAVES_PERSISTIDAS}


def guardar(cfg: dict) -> None:
    """Persiste las claves visuales de cfg. Crea el directorio si no existe."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: cfg[k] for k in CLAVES_PERSISTIDAS if k in cfg}
    CONFIG_FILE.write_text(json.dumps(data, indent=2))
    print(f"[config] guardada en {CONFIG_FILE}")
