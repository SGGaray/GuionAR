#!/usr/bin/env bash
# Prueba de mantenimiento: instala la release real en un HOME descartable.
#
#   scripts/test_release_install.sh DIR_RELEASE [opciones]
#
# DIR_RELEASE contiene guionar-<versión>-linux.tar.gz y SHA256SUMS.
#
#   --python RUTA     Python de bootstrap (por defecto: el que elija install.sh)
#   --pip-cache DIR   caché de pip compartida entre fases
#   --keep            conserva el directorio temporal para inspección
#
# Nunca escribe en el HOME real: HOME, XDG_*, PATH y caché apuntan a mktemp;
# update-desktop-database y gtk-update-icon-cache son shims que registran la
# llamada, y los compiladores fallan. Requiere red (pip descarga PyQt6).
set -euo pipefail

REPO_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REAL_HOME="$HOME"
RELEASE_DIR=""
BOOTSTRAP=""
PIP_CACHE=""
KEEP=0

while (($#)); do
    case "$1" in
        --python) BOOTSTRAP="$2"; shift ;;
        --pip-cache) PIP_CACHE="$2"; shift ;;
        --keep) KEEP=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        -*) echo "opción desconocida: $1" >&2; exit 2 ;;
        *) RELEASE_DIR="$1" ;;
    esac
    shift
done
[[ -n "$RELEASE_DIR" ]] || { echo "falta DIR_RELEASE" >&2; exit 2; }
RELEASE_DIR="$(CDPATH= cd -- "$RELEASE_DIR" && pwd -P)"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/guionar-release-test.XXXXXX")"
if ((KEEP)); then
    trap 'echo "temporal conservado: $TMP"' EXIT
else
    trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf -- "$TMP"' EXIT
fi
PASOS=0

falla() { echo "FAIL: $*" >&2; exit 1; }
ok() { PASOS=$((PASOS + 1)); echo "  ok: $*"; }
igual() { [[ "$1" == "$2" ]] || falla "$3: '$1' != '$2'"; ok "$3"; }
ausente() { [[ ! -e "$1" && ! -L "$1" ]] || falla "no debería existir: $1"; }
huella() { (cd "$1" && find . -printf '%p %y %l\n' | sort && find . -type f -exec sha256sum {} + | sort); }

preparar_home() {
    local raiz="$1"
    H="$raiz/home"
    DATA="$H/.local/share"
    CONF="$H/.config"
    CACHE="$raiz/cache"
    RUNTIME="$raiz/run"
    SHIMS="$raiz/shims"
    mkdir -p "$H" "$CACHE" "$RUNTIME" "$SHIMS"
    chmod 700 "$RUNTIME"
    for shim in update-desktop-database gtk-update-icon-cache; do
        printf '#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >> "%s/shims.log"\nexit 0\n' \
            "$shim" "$raiz" > "$SHIMS/$shim"
        chmod +x "$SHIMS/$shim"
    done
    # Sin compiladores; y un python3 roto: el lanzador instalado debe usar su
    # propio .venv y nunca el Python del sistema.
    for roto in cc gcc c++ g++ clang python3; do
        printf '#!/bin/sh\necho "invocado: %s" >&2\nexit 97\n' "$roto" > "$SHIMS/$roto"
        chmod +x "$SHIMS/$roto"
    done
    local ruta
    for ruta in "$H" "$DATA" "$CONF" "$CACHE" "$RUNTIME"; do
        case "$ruta" in
            "$REAL_HOME"|"$REAL_HOME"/*) falla "ruta de prueba dentro del HOME real: $ruta" ;;
        esac
        [[ "$ruta" == "$TMP"/* ]] || falla "ruta de prueba fuera del temporal: $ruta"
    done
}

en_home() {
    local entorno=(
        HOME="$H" XDG_DATA_HOME="$DATA" XDG_CONFIG_HOME="$CONF"
        XDG_CACHE_HOME="$CACHE" XDG_RUNTIME_DIR="$RUNTIME"
        PATH="$SHIMS:/usr/bin:/bin" LANG=C.UTF-8 QT_QPA_PLATFORM=offscreen
        PIP_CACHE_DIR="${PIP_CACHE:-$TMP/pip-cache}"
        PIP_DISABLE_PIP_VERSION_CHECK=1
    )
    # Con un python3 roto en PATH, install.sh necesita un intérprete explícito.
    entorno+=(GUIONAR_BOOTSTRAP_PYTHON="${BOOTSTRAP:-$(command -v python3)}")
    env -i "${entorno[@]}" "$@"
}

instalar() {
    local release="$1" log="$2"
    en_home "$release/install.sh" >"$log" 2>&1 || { tail -30 "$log"; return 1; }
}

echo "==> Verificando SHA256SUMS"
(cd "$RELEASE_DIR" && sha256sum -c SHA256SUMS) || falla "sha256sum -c"
TARBALL="$(find "$RELEASE_DIR" -maxdepth 1 -name 'guionar-*-linux.tar.gz' | head -n1)"
[[ -n "$TARBALL" ]] || falla "no hay tarball en $RELEASE_DIR"
mkdir -p "$TMP/extraido"
tar -xzf "$TARBALL" -C "$TMP/extraido"
REL="$(find "$TMP/extraido" -mindepth 1 -maxdepth 1 -type d)"
VERSION="$(cat "$REL/VERSION")"
[[ ! -e "$REL/.git" ]] || falla "la release contiene .git"
ok "release $VERSION extraída sin .git"

# ---------------------------------------------------------------------------
echo "==> Instalación limpia"
preparar_home "$TMP/limpio"
instalar "$REL" "$TMP/limpio/install1.log" || falla "install.sh"
INST="$DATA/guionar"
DESKTOP="$DATA/applications/guionar.desktop"
CURRENT1="$(readlink "$INST/current")"
[[ "$CURRENT1" == versions/"$VERSION"-* ]] || falla "current inesperado: $CURRENT1"
ok "current -> $CURRENT1"
igual "$(readlink "$H/.local/bin/guionar")" "$INST/current/app/bin/guionar" "enlace guionar"
igual "$(readlink "$H/.local/bin/guionar-uninstall")" "$INST/uninstall.sh" "enlace guionar-uninstall"
igual "$(en_home "$H/.local/bin/guionar" --version)" "GuionAR $VERSION" "guionar --version"
grep -qxF "Exec=\"$INST/current/app/bin/guionar\" --socket" "$DESKTOP" \
    || falla "Exec del launcher no apunta a current"
ok "desktop apunta a current"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP" || falla "desktop-file-validate"
    ok "desktop-file-validate"
fi
for archivo in "$DESKTOP" "$INST/installed.json"; do
    if grep -qF -e "$REL" -e "$REPO_DIR" "$archivo"; then falla "ruta al release o repo en $archivo"; fi
done
for enlace in "$H"/.local/bin/*; do
    case "$(readlink "$enlace")" in "$REL"*|"$REPO_DIR"*) falla "enlace al release: $enlace" ;; esac
done
ok "sin rutas al release ni al repo"
[[ -f "$DATA/icons/hicolor/scalable/apps/guionar.svg" ]] || falla "falta el ícono"
ok "ícono hicolor/scalable/apps/guionar.svg"
VENV_PY="$INST/current/.venv/bin/python"
en_home "$VENV_PY" -m pip check >/dev/null || falla "pip check"
ok "pip check"
en_home "$VENV_PY" -m compileall -q "$INST/current/app" >/dev/null || falla "compileall"
ok "compileall de la app instalada"
en_home "$VENV_PY" -I -c 'from PyQt6.QtPdf import QPdfDocument' || falla "QtPdf"
ok "QtPdf disponible"
set +e
en_home timeout 4 "$H/.local/bin/guionar" --demo --socket >"$TMP/limpio/demo.log" 2>&1
estado=$?
set -e
[[ $estado -eq 124 ]] || { cat "$TMP/limpio/demo.log"; falla "guionar --demo terminó con $estado"; }
ok "la app instalada arranca headless (offscreen) desde el lanzador"
if grep -q "invocado:" "$TMP/limpio/install1.log" "$TMP/limpio/demo.log"; then
    falla "se invocó un compilador o el python3 del sistema"
fi
ok "sin compiladores ni python3 del sistema"

echo "==> Reinstalación idempotente"
mkdir -p "$CONF/guionar"
printf '{"bg_opacity": 0.4, "font_size_current": 40}\n' > "$CONF/guionar/config.json"
CONFIG_ANTES="$(sha256sum < "$CONF/guionar/config.json")"
DESKTOP_ANTES="$(sha256sum < "$DESKTOP")"
instalar "$REL" "$TMP/limpio/install2.log" || falla "reinstalación"
CURRENT2="$(readlink "$INST/current")"
[[ "$CURRENT2" != "$CURRENT1" ]] || falla "la reinstalación no creó versión nueva"
igual "$(sha256sum < "$DESKTOP")" "$DESKTOP_ANTES" "desktop idéntico"
igual "$(sha256sum < "$CONF/guionar/config.json")" "$CONFIG_ANTES" "config intacta"

echo "==> Fallo antes del swap"
cp -a "$REL" "$TMP/roto"
printf 'raise SystemExit(3)\n' > "$TMP/roto/app/guionar.py"
HUELLA_ANTES="$(huella "$H")"
if instalar "$TMP/roto" "$TMP/limpio/install-roto.log"; then falla "la release rota se instaló"; fi
igual "$(huella "$H")" "$HUELLA_ANTES" "fallo preserva current, launcher y config"

echo "==> Tercera instalación poda versiones viejas"
instalar "$REL" "$TMP/limpio/install3.log" || falla "tercera instalación"
igual "$(find "$INST/versions" -mindepth 1 -maxdepth 1 | wc -l)" "2" "se conservan current + previous"
ausente "$INST/$CURRENT1"

echo "==> Desinstalación por defecto"
rm -rf -- "$TMP/extraido" "$TMP/roto"  # guionar-uninstall no necesita la release
en_home "$H/.local/bin/guionar-uninstall" >"$TMP/limpio/uninstall.log" 2>&1 || falla "guionar-uninstall"
for ruta in "$INST" "$DESKTOP" "$DATA/icons/hicolor/scalable/apps/guionar.svg" \
        "$H/.local/bin/guionar" "$H/.local/bin/guionar-uninstall"; do
    ausente "$ruta"
done
ok "versiones, enlaces, launcher e ícono eliminados"
igual "$(sha256sum < "$CONF/guionar/config.json")" "$CONFIG_ANTES" "uninstall preserva config"

echo "==> --purge-data"
tar -xzf "$TARBALL" -C "$TMP"
REL="$TMP/guionar-$VERSION"
instalar "$REL" "$TMP/limpio/install4.log" || falla "reinstalación"
en_home "$H/.local/bin/guionar-uninstall" --purge-data >/dev/null 2>&1 || falla "purge"
ausente "$CONF/guionar"
ausente "$INST"
ok "--purge-data borra la config de GuionAR"

# ---------------------------------------------------------------------------
echo "==> Migración desde el launcher de una copia del repositorio"
preparar_home "$TMP/legacy"
INST="$DATA/guionar"
DESKTOP="$DATA/applications/guionar.desktop"
CHECKOUT="$H/Documentos/GuionAR copia"
mkdir -p "$CHECKOUT"
git -C "$REPO_DIR" archive HEAD | tar -x -C "$CHECKOUT"
mkdir -p "$CHECKOUT/.venv/bin"
printf '#!/bin/sh\n' > "$CHECKOUT/.venv/bin/python"
chmod +x "$CHECKOUT/.venv/bin/python"
printf 'entorno del usuario\n' > "$CHECKOUT/.venv/KEEP"
env -i HOME="$H" XDG_DATA_HOME="$DATA" PATH="$SHIMS:/usr/bin:/bin" \
    sh "$CHECKOUT/packaging/linux/install-desktop-entry.sh" >/dev/null \
    || falla "install-desktop-entry.sh"
grep -qF "Exec=\"$CHECKOUT/bin/guionar\" --socket" "$DESKTOP" || falla "launcher legacy no creado"
ok "launcher legacy apunta a la copia del repositorio"
mkdir -p "$CONF/guionar"
printf '{"bg_opacity": 0.3}\n' > "$CONF/guionar/config.json"
CONFIG_ANTES="$(sha256sum < "$CONF/guionar/config.json")"
CHECKOUT_ANTES="$(huella "$CHECKOUT")"

instalar "$REL" "$TMP/legacy/install.log" || falla "instalación sobre legacy"
grep -q "Launcher anterior reemplazado" "$TMP/legacy/install.log" || falla "legacy no reconocido"
grep -qxF "Exec=\"$INST/current/app/bin/guionar\" --socket" "$DESKTOP" \
    || falla "desktop no reemplazado"
ok "desktop reemplazado; Exec en current"
if grep -qF "$CHECKOUT" "$DESKTOP"; then falla "el desktop sigue apuntando a la copia"; fi
igual "$(huella "$CHECKOUT")" "$CHECKOUT_ANTES" "copia del repositorio y su .venv intactos"
igual "$(sha256sum < "$CONF/guionar/config.json")" "$CONFIG_ANTES" "config intacta"
en_home "$H/.local/bin/guionar-uninstall" >/dev/null 2>&1 || falla "uninstall tras migración"
igual "$(huella "$CHECKOUT")" "$CHECKOUT_ANTES" "uninstall no toca la copia del repositorio"

echo "==> Prueba de instalación aislada: OK ($PASOS comprobaciones)"
