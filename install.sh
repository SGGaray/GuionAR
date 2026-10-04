#!/usr/bin/env bash
# Instalación de usuario de GuionAR desde la release extraída.
#
# Instala en ~/.local/share/guionar con su propio entorno (PyQt6 en wheels,
# sin compilar), agrega GuionAR al menú y crea guionar y guionar-uninstall en
# ~/.local/bin. No depende de .git. El staging y el swap atómico viven en
# app/packaging/linux/guionar_installer.py.
set -euo pipefail

ORIGEN="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PACKAGING="$ORIGEN/app/packaging/linux"

uso() {
    cat <<'TXT'
Uso: ./install.sh

  -h, --help   muestra esta ayuda sin modificar el sistema

Instala GuionAR para tu usuario. Actualizar: extraé la release nueva y
ejecutá su install.sh. Desinstalar: guionar-uninstall.
TXT
}

for argumento in "$@"; do
    case "$argumento" in
        -h|--help) uso; exit 0 ;;
        *) echo "!! opción desconocida: $argumento" >&2; uso >&2; exit 2 ;;
    esac
done

if [[ ! -f "$ORIGEN/VERSION" || ! -f "$PACKAGING/guionar_installer.py" ]]; then
    echo "!! $ORIGEN no es una release de GuionAR." >&2
    echo "   Descargá la release desde https://github.com/SGGaray/GuionAR/releases" >&2
    exit 1
fi
mapfile -t VERSIONES < "$PACKAGING/python-versions.txt"

version_de() {
    "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null
}

compatible() {
    local version soportada
    version="$(version_de "$1")" || return 1
    for soportada in "${VERSIONES[@]}"; do
        [[ "$version" == "$soportada" ]] && return 0
    done
    return 1
}

# Python pedido explícitamente, python3 del sistema y luego versiones
# soportadas de la más nueva a la más vieja.
CANDIDATOS=()
if [[ -n "${GUIONAR_BOOTSTRAP_PYTHON:-}" ]]; then
    CANDIDATOS+=("$GUIONAR_BOOTSTRAP_PYTHON")
else
    CANDIDATOS+=(python3)
    for ((i = ${#VERSIONES[@]} - 1; i >= 0; i--)); do
        CANDIDATOS+=("python${VERSIONES[i]}")
    done
fi

PYTHON=""
for candidato in "${CANDIDATOS[@]}"; do
    if resuelto="$(command -v -- "$candidato" 2>/dev/null)" \
            && compatible "$resuelto"; then
        PYTHON="$resuelto"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "!! Esta versión de GuionAR soporta Python ${VERSIONES[0]}–${VERSIONES[-1]}." >&2
    echo "   Instalá una versión compatible o descargá una release más nueva." >&2
    encontrado="$(version_de "${CANDIDATOS[0]}" || true)"
    if [[ -n "$encontrado" ]]; then
        echo "   Encontrado: Python $encontrado (${CANDIDATOS[0]})" >&2
    fi
    exit 1
fi

if ! "$PYTHON" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    version="$(version_de "$PYTHON")"
    echo "!! $PYTHON no incluye el módulo venv." >&2
    echo "   En Debian/Ubuntu: sudo apt install python${version}-venv" >&2
    exit 1
fi

exec "$PYTHON" "$PACKAGING/guionar_installer.py" --origen "$ORIGEN"
