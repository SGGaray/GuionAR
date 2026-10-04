#!/usr/bin/env python3
"""Construye el release Linux de GuionAR de forma reproducible.

Produce en ``--out`` (por defecto ``dist/release``, ignorado por git):

    guionar-<versión>-linux.tar.gz
    SHA256SUMS

La versión sale de ``guionar_version.py``. El tarball se arma desde una
allowlist explícita (nunca el checkout entero) con orden estable, uid/gid 0,
mtime ``SOURCE_DATE_EPOCH`` y gzip sin timestamp: el mismo commit produce los
mismos bytes. El working tree no se modifica.

    scripts/build_release.py
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
# CPython con wheels de PyQt6 probados para este release.
PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
RAIZ = ("README.md", "LICENSE", "SECURITY.md", "INTEGRATION.md",
        "CHANGELOG.md", "install.sh", "uninstall.sh")
MODULOS = ("bridge.py", "desktop_shell.py", "document_loader.py", "guion.py",
           "guionar.py", "guionar_client.py", "guionar_config.py",
           "guionar_version.py", "ui_controls.py")
APP_PATRONES = ("bin/guionar", "assets/*.svg", "assets/brand/*.svg",
                "assets/tray/*.svg")
PACKAGING = ("packaging/linux/guionar.desktop.in",
             "packaging/linux/guionar_installer.py")
EJECUTABLES = {"install.sh", "uninstall.sh", "app/bin/guionar"}
PROHIBIDOS = re.compile(
    r"(^|/)(\.git|\.github|tests|docs|\.venv|venv|__pycache__|\.claude|"
    r"snapshots|build|dist|[^/]*\.egg-info)(/|$)|\.(pyc|log|sock)$")


class ErrorBuild(RuntimeError):
    pass


def exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise ErrorBuild(mensaje)


def leer_version(raiz: Path = ROOT) -> str:
    texto = (raiz / "guionar_version.py").read_text(encoding="utf-8")
    coincidencia = re.search(r'^__version__ = "([^"]+)"$', texto, re.M)
    exigir(coincidencia is not None, "guionar_version.py sin __version__")
    return coincidencia[1]


def source_date_epoch() -> int:
    valor = os.environ.get("SOURCE_DATE_EPOCH")
    if valor:
        return int(valor)
    try:
        return int(subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%ct"],
            check=True, text=True, capture_output=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return int((ROOT / "guionar_version.py").stat().st_mtime)


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def generar_constraints() -> str:
    pins = {}
    for linea in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue
        nombre, separador, version = linea.partition("==")
        exigir(bool(separador) and bool(version)
               and not re.search(r"[<>=!~*,; ]", version),
               f"constraint sin versión exacta: {linea!r}")
        pins[re.sub(r"[-_.]+", "-", nombre).lower()] = version
    exigir("pyqt6" in pins, "constraints.txt debe fijar PyQt6")
    return ("# Stack PyQt6 probado de GuionAR; pip lo aplica con -c.\n"
            + "".join(f"{n}=={pins[n]}\n" for n in sorted(pins)))


def archivos_app() -> dict[str, Path]:
    """app/<relativo> -> origen en el repo, sólo desde la allowlist."""
    modulos_repo = {r.name for r in ROOT.glob("*.py")}
    exigir(modulos_repo == set(MODULOS),
           "los módulos del repo no coinciden con la allowlist del release: "
           f"{sorted(modulos_repo ^ set(MODULOS))}")
    archivos = {f"app/{m}": ROOT / m for m in MODULOS}
    for patron in APP_PATRONES:
        coincidencias = sorted(ROOT.glob(patron))
        exigir(bool(coincidencias), f"sin archivos para {patron}")
        for ruta in coincidencias:
            archivos[f"app/{ruta.relative_to(ROOT).as_posix()}"] = ruta
    for relativo in PACKAGING:
        archivos[f"app/{relativo}"] = ROOT / relativo
    return archivos


def _info(nombre: str, tipo: bytes, tamano: int, modo: int,
          epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(nombre)
    info.type, info.size, info.mode, info.mtime = tipo, tamano, modo, epoch
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def contenido_release(version: str) -> dict[str, bytes]:
    contenido = {nombre: (ROOT / nombre).read_bytes() for nombre in RAIZ}
    contenido["VERSION"] = f"{version}\n".encode()
    contenido["constraints.txt"] = generar_constraints().encode()
    for relativo, origen in archivos_app().items():
        contenido[relativo] = origen.read_bytes()
    contenido["app/packaging/linux/python-versions.txt"] = (
        "\n".join(PYTHON_VERSIONS) + "\n").encode()
    return contenido


def escribir_tarball(contenido: dict[str, bytes], prefijo: str,
                     destino: Path, epoch: int) -> None:
    directorios = {prefijo}
    for relativo in contenido:
        partes = relativo.split("/")[:-1]
        for i in range(1, len(partes) + 1):
            directorios.add(f"{prefijo}/{'/'.join(partes[:i])}")
    entradas = sorted(
        [(d, None) for d in directorios]
        + [(f"{prefijo}/{r}", datos) for r, datos in contenido.items()])
    crudo = io.BytesIO()
    with tarfile.open(fileobj=crudo, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for nombre, datos in entradas:
            if datos is None:
                tar.addfile(_info(nombre, tarfile.DIRTYPE, 0, 0o755, epoch))
                continue
            relativo = nombre.split("/", 1)[1]
            modo = 0o755 if relativo in EJECUTABLES else 0o644
            tar.addfile(_info(nombre, tarfile.REGTYPE, len(datos), modo, epoch),
                        io.BytesIO(datos))
    with destino.open("wb") as salida:
        with gzip.GzipFile(filename="", mode="wb", fileobj=salida,
                           compresslevel=9, mtime=0) as comprimido:
            comprimido.write(crudo.getvalue())


def verificar(salida: Path, version: str, esperado: dict[str, bytes]) -> None:
    prefijo = f"guionar-{version}"
    tarball = salida / f"{prefijo}-linux.tar.gz"
    sumas = (salida / "SHA256SUMS").read_text(encoding="utf-8")
    exigir(sumas == f"{sha256(tarball)}  {tarball.name}\n",
           "SHA256SUMS no coincide con el tarball")
    with tarfile.open(tarball, "r:gz") as tar:
        archivos = {m.name: m for m in tar.getmembers() if m.isfile()}
        exigir(set(archivos) == {f"{prefijo}/{r}" for r in esperado},
               "contenido fuera de la allowlist")
        repo = str(ROOT).encode()
        for miembro in tar.getmembers():
            exigir(miembro.isfile() or miembro.isdir(),
                   f"tipo no permitido: {miembro.name}")
            exigir(not PROHIBIDOS.search(miembro.name),
                   f"ruta prohibida: {miembro.name}")
            exigir((miembro.uid, miembro.gid, miembro.uname, miembro.gname)
                   == (0, 0, "", ""), f"dueño no normalizado: {miembro.name}")
            if miembro.isfile():
                datos = tar.extractfile(miembro).read()
                exigir(datos == esperado[miembro.name.split("/", 1)[1]],
                       f"contenido alterado: {miembro.name}")
                exigir(repo not in datos, f"{miembro.name} contiene la ruta del repo")
        exigir(tar.extractfile(f"{prefijo}/VERSION").read() == f"{version}\n".encode(),
               "VERSION inesperado")
        app_version = tar.extractfile(f"{prefijo}/app/guionar_version.py").read()
        exigir(f'__version__ = "{version}"'.encode() in app_version,
               "la app empaquetada declara otra versión")


def construir(salida: Path) -> dict[str, Path]:
    version = leer_version()
    epoch = source_date_epoch()
    contenido = contenido_release(version)
    salida.mkdir(parents=True, exist_ok=True)
    prefijo = f"guionar-{version}"
    tarball = salida / f"{prefijo}-linux.tar.gz"
    escribir_tarball(contenido, prefijo, tarball, epoch)
    (salida / "SHA256SUMS").write_text(
        f"{sha256(tarball)}  {tarball.name}\n", encoding="utf-8")
    verificar(salida, version, contenido)
    return {"tarball": tarball, "sha256sums": salida / "SHA256SUMS"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    args = parser.parse_args(argv)
    try:
        artefactos = construir(args.out.resolve())
    except (ErrorBuild, OSError) as exc:
        print(f"!! build de release falló: {exc}", file=sys.stderr)
        return 1
    for ruta in artefactos.values():
        print(ruta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
