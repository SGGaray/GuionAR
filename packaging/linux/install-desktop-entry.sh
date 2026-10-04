#!/bin/sh
# Instala GuionAR en el menú de aplicaciones del usuario actual.
#
#   packaging/linux/install-desktop-entry.sh             instalar
#   packaging/linux/install-desktop-entry.sh --uninstall desinstalar
#
# Copia el ícono y genera guionar.desktop apuntando a bin/guionar de esta
# copia del repositorio (la ruta se calcula al instalar; no hay rutas fijas).
# Si movés el repositorio, volvé a ejecutarlo.
set -eu

self=$(readlink -f -- "$0")
root=$(cd -- "$(dirname -- "$self")/../.." && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}
apps="$data/applications"
icons="$data/icons/hicolor/scalable/apps"
desktop="$apps/guionar.desktop"

refrescar() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$apps" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -q -t "$data/icons/hicolor" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$desktop" "$icons/guionar.svg"
    refrescar
    echo "GuionAR quitado del menú de aplicaciones."
    exit 0
fi

mkdir -p "$apps" "$icons"
install -m 644 "$root/assets/guionar.svg" "$icons/guionar.svg"

# Exec entre comillas según la especificación .desktop: dentro de comillas
# se escapan " ` $ \, y después la regla general de strings duplica cada
# barra invertida. Por último, se escapa para el reemplazo de sed (\ & |).
exec_path=$(printf '%s' "$root/bin/guionar" | sed -e 's/[\\"`$]/\\&/g' -e 's/\\/\\\\/g')
exec_sed=$(printf '"%s"' "$exec_path" | sed 's/[\\&|]/\\&/g')
sed "s|@EXEC@|$exec_sed|" "$root/packaging/linux/guionar.desktop.in" > "$desktop"
chmod 644 "$desktop"
refrescar
echo "GuionAR instalado en $desktop"
