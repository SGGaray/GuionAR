"""Contrato de release: versión única, artefacto, instalador y desinstalador.

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_release.py

El instalador corre en proceso: sólo venv y pip son falsos (el "venv" delega
en el Python de los tests, que ya tiene PyQt6), así que la validación de
PyQt6, QtPdf, ``guionar --version`` y el smoke headless son reales.
``scripts/test_release_install.sh`` cubre la instalación real con pip.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packaging" / "linux"))

import build_release  # noqa: E402
import guionar_installer as gi  # noqa: E402

VERSION = build_release.leer_version()


def ejecutar(*args, cwd=ROOT, env=None, timeout=60):
    return subprocess.run(args, cwd=cwd, env=env, text=True,
                          capture_output=True, check=False, timeout=timeout)


def digest(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def arbol(raiz: Path) -> dict[str, str]:
    resultado = {}
    for ruta in sorted(raiz.rglob("*")):
        clave = str(ruta.relative_to(raiz))
        if ruta.is_symlink():
            resultado[clave] = "->" + os.readlink(ruta)
        elif ruta.is_file():
            resultado[clave] = digest(ruta)
        else:
            resultado[clave] = "dir"
    return resultado


class VersionUnica(unittest.TestCase):
    def test_fuente_unica(self):
        literales = [
            ruta.name for ruta in [*ROOT.glob("*.py"), *ROOT.glob("scripts/*.py"),
                                   *ROOT.glob("packaging/linux/*"), ROOT / "bin/guionar",
                                   ROOT / "install.sh", ROOT / "uninstall.sh"]
            if ruta.is_file() and f'"{VERSION}"' in ruta.read_text(encoding="utf-8")]
        self.assertEqual(literales, ["guionar_version.py"])
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"\n## [{VERSION}] - ", changelog)
        self.assertLess(changelog.index("## [Unreleased]"),
                        changelog.index(f"## [{VERSION}]"))

    def test_version_por_lanzador_y_modulo(self):
        for comando in ([str(ROOT / "bin/guionar"), "--version"],
                        [sys.executable, str(ROOT / "guionar.py"), "--version"]):
            with self.subTest(comando=comando[0]):
                resultado = ejecutar(*comando)
                self.assertEqual(resultado.returncode, 0, resultado.stderr)
                self.assertEqual(resultado.stdout, f"GuionAR {VERSION}\n")

    def test_version_headless_no_importa_qt(self):
        codigo = (
            "import runpy, sys\n"
            "sys.argv = ['guionar.py', '--version']\n"
            "try:\n"
            f"    runpy.run_path({str(ROOT / 'guionar.py')!r}, run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    assert exc.code == 0, exc.code\n"
            "cargados = [m for m in sys.modules if m.startswith('PyQt6')]\n"
            "assert not cargados, cargados\n"
        )
        entorno = {k: v for k, v in os.environ.items()
                   if k not in {"DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM"}}
        resultado = ejecutar(sys.executable, "-B", "-c", codigo, env=entorno)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout, f"GuionAR {VERSION}\n")

    def test_version_en_help(self):
        resultado = ejecutar(sys.executable, str(ROOT / "guionar.py"), "--help")
        self.assertIn("--version", resultado.stdout)


class ArtefactoRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._epoch = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = "1790000000"
        cls.antes = cls._estado_git()
        cls.salida = Path(cls._tmp.name) / "out1"
        cls.tarball = build_release.construir(cls.salida)["tarball"]
        cls.prefijo = f"guionar-{VERSION}"

    @classmethod
    def tearDownClass(cls):
        if cls._epoch is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = cls._epoch
        cls._tmp.cleanup()

    @staticmethod
    def _estado_git():
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            return None
        return subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain=v1",
             "--untracked-files=all"],
            text=True, capture_output=True, check=False).stdout

    def archivos(self):
        with tarfile.open(self.tarball, "r:gz") as tar:
            return {m.name[len(self.prefijo) + 1:]: m
                    for m in tar.getmembers() if m.isfile()}

    def test_nombres(self):
        self.assertEqual(sorted(p.name for p in self.salida.iterdir()),
                         sorted([f"guionar-{VERSION}-linux.tar.gz", "SHA256SUMS"]))

    def test_allowlist_exacta(self):
        esperados = {
            "README.md", "LICENSE", "SECURITY.md", "INTEGRATION.md",
            "CHANGELOG.md", "VERSION", "install.sh", "uninstall.sh",
            "constraints.txt", "app/bin/guionar",
            "app/packaging/linux/guionar.desktop.in",
            "app/packaging/linux/guionar_installer.py",
            "app/packaging/linux/python-versions.txt",
            *(f"app/{m}" for m in (
                "bridge.py", "desktop_shell.py", "document_loader.py",
                "guion.py", "guionar.py", "guionar_client.py",
                "guionar_config.py", "guionar_version.py", "ui_controls.py")),
            *(f"app/{r.relative_to(ROOT).as_posix()}"
              for r in ROOT.glob("assets/**/*.svg")),
        }
        self.assertEqual(set(self.archivos()), esperados)

    def test_exclusiones(self):
        with tarfile.open(self.tarball, "r:gz") as tar:
            nombres = tar.getnames()
        for nombre in nombres:
            for prohibido in (".git", ".github", "/tests", "/docs", ".venv",
                              "__pycache__", ".claude", "snapshots",
                              "install-desktop-entry.sh", ".pyc"):
                self.assertNotIn(prohibido, nombre)

    def test_metadata_normalizada_y_version(self):
        with tarfile.open(self.tarball, "r:gz") as tar:
            for miembro in tar.getmembers():
                self.assertEqual((miembro.uid, miembro.gid, miembro.uname,
                                  miembro.gname), (0, 0, "", ""))
                self.assertEqual(miembro.mtime, 1790000000)
                ejecutable = miembro.isdir() or miembro.name.endswith(
                    (".sh", "/bin/guionar"))
                self.assertEqual(miembro.mode, 0o755 if ejecutable else 0o644,
                                 miembro.name)
            version = tar.extractfile(f"{self.prefijo}/VERSION").read()
            pythons = tar.extractfile(
                f"{self.prefijo}/app/packaging/linux/python-versions.txt").read()
            pins = tar.extractfile(f"{self.prefijo}/constraints.txt").read().decode()
        self.assertEqual(version, f"{VERSION}\n".encode())
        self.assertEqual(pythons.decode().split(), ["3.12", "3.13", "3.14"])
        self.assertEqual(
            [l for l in pins.splitlines() if not l.startswith("#")],
            ["pyqt6==6.11.0", "pyqt6-qt6==6.11.1", "pyqt6-sip==13.11.1"])
        self.assertEqual(self.tarball.read_bytes()[4:8], b"\0\0\0\0")

    def test_sha256sums(self):
        self.assertEqual((self.salida / "SHA256SUMS").read_text(),
                         f"{digest(self.tarball)}  {self.tarball.name}\n")
        if shutil.which("sha256sum"):
            resultado = ejecutar("sha256sum", "-c", "SHA256SUMS", cwd=self.salida)
            self.assertEqual(resultado.returncode, 0, resultado.stdout)

    def test_sin_rutas_del_repo(self):
        with tarfile.open(self.tarball, "r:gz") as tar:
            for miembro in tar.getmembers():
                if miembro.isfile():
                    self.assertNotIn(str(ROOT).encode(),
                                     tar.extractfile(miembro).read(), miembro.name)

    def test_determinista(self):
        segunda = Path(self._tmp.name) / "out2"
        build_release.construir(segunda)
        self.assertEqual(digest(self.tarball),
                         digest(segunda / self.tarball.name))

    def test_no_modifica_worktree(self):
        if self.antes is None:
            self.skipTest("sin git")
        self.assertEqual(self._estado_git(), self.antes)

    def test_release_extraido_no_depende_de_git(self):
        destino = Path(self._tmp.name) / "extraido"
        with tarfile.open(self.tarball, "r:gz") as tar:
            tar.extractall(destino, filter="data")
        raiz = destino / self.prefijo
        self.assertFalse(any(p.name == ".git" for p in raiz.rglob("*")))
        origen = gi.detectar_origen(raiz)
        self.assertEqual(origen.version, VERSION)
        self.assertEqual(gi.versiones_python(origen), ["3.12", "3.13", "3.14"])
        for archivo in ("install.sh", "uninstall.sh", "app/bin/guionar"):
            self.assertNotIn("git ", (raiz / archivo).read_text())
            self.assertTrue(os.access(raiz / archivo, os.X_OK))

    def test_todo_modulo_del_repo_esta_en_la_allowlist(self):
        self.assertEqual({r.name for r in ROOT.glob("*.py")},
                         set(build_release.MODULOS))


# ---------------------------------------------------------------------------
# Instalador en proceso
# ---------------------------------------------------------------------------


class HerramientasFalsas(gi.Herramientas):
    """venv que delega en el Python de los tests; pip simulado."""

    def __init__(self):
        self.fallar_pip = False
        self.llamadas = []

    def crear_venv(self, destino):
        self.llamadas.append(("venv", destino))
        (destino / "bin").mkdir(parents=True)
        python = destino / "bin" / "python"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python.chmod(0o755)

    def pip_install(self, python, argumentos):
        self.llamadas.append(("pip", argumentos))
        return not self.fallar_pip


class InstaladorBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "home con ñ"
        self.data = self.home / ".local" / "share"
        self.config = self.home / ".config"
        self.entorno = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.data),
                        "XDG_CONFIG_HOME": str(self.config)}
        self.destino = gi.Destino.desde_entorno(self.entorno)
        self.release = self.base / "release extraído"
        self._crear_release(self.release)
        self.origen = gi.detectar_origen(self.release)
        self.herramientas = HerramientasFalsas()
        tools = self.base / "tools"
        tools.mkdir()
        for comando in ("update-desktop-database", "gtk-update-icon-cache"):
            (tools / comando).symlink_to(shutil.which("true"))
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{tools}:{self._path}"

    def tearDown(self):
        os.environ["PATH"] = self._path
        self._tmp.cleanup()

    @staticmethod
    def _crear_release(raiz: Path):
        for relativo, datos in build_release.contenido_release(VERSION).items():
            ruta = raiz / relativo
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(datos)
            if relativo in build_release.EJECUTABLES:
                ruta.chmod(0o755)

    def instalar(self, herramientas=None):
        instalador = gi.Instalador(self.origen, self.destino,
                                   herramientas or self.herramientas)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return instalador.instalar()

    def uninstall(self, *args, script=None, entorno_extra=None):
        entorno = dict(os.environ)
        entorno.update(self.entorno)
        entorno.update(entorno_extra or {})
        return ejecutar(str(script or self.destino.uninstall), *args,
                        cwd=self.base, env=entorno)

    def sembrar_config(self):
        config = self.config / "guionar" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"bg_opacity": 0.4, "font_size_current": 40}\n')
        return {config: digest(config)}

    def assertPreservados(self, preservados):
        for ruta, valor in preservados.items():
            self.assertTrue(ruta.exists(), ruta)
            self.assertEqual(digest(ruta), valor, ruta)

    def crear_checkout_legacy(self):
        """Copia de repo + launcher creado por install-desktop-entry.sh."""
        checkout = self.home / "Documentos" / "Guion AR $repo"
        for relativo in ("bin/guionar", "guionar.py", "guionar_version.py",
                         "assets/guionar.svg",
                         "packaging/linux/guionar.desktop.in",
                         "packaging/linux/install-desktop-entry.sh"):
            (checkout / relativo).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relativo, checkout / relativo)
        venv = checkout / ".venv" / "bin" / "python"
        venv.parent.mkdir(parents=True)
        venv.write_text("#!/bin/sh\n")
        venv.chmod(0o755)
        entorno = dict(os.environ, XDG_DATA_HOME=str(self.data))
        resultado = ejecutar(
            "sh", str(checkout / "packaging/linux/install-desktop-entry.sh"),
            env=entorno)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return checkout


class InstalacionNueva(InstaladorBase):
    def test_layout_enlaces_y_desktop_apuntan_a_current(self):
        relativo, previous = self.instalar()
        d = self.destino
        self.assertRegex(relativo, rf"^versions/{re.escape(VERSION)}-[0-9a-f]{{8}}$")
        self.assertIsNone(previous)
        self.assertEqual(os.readlink(d.current), relativo)
        self.assertEqual(os.readlink(d.bin_dir / "guionar"),
                         str(d.install_home / "current/app/bin/guionar"))
        self.assertEqual(os.readlink(d.bin_dir / "guionar-uninstall"),
                         str(d.uninstall))
        desktop = d.desktop.read_text()
        self.assertTrue(desktop.startswith(gi.MARCA_DESKTOP + "\n"))
        self.assertIn(f'Exec="{d.install_home}/current/app/bin/guionar" --socket',
                      desktop)
        for prohibido in (str(ROOT), str(self.release), "/versions/"):
            self.assertNotIn(prohibido, desktop)
        self.assertEqual(d.icono.read_bytes(), (ROOT / "assets/guionar.svg").read_bytes())
        version_dir = d.install_home / relativo
        self.assertTrue((version_dir / ".venv/bin/python").exists())
        self.assertTrue((version_dir / "app/guionar.py").exists())
        self.assertFalse((version_dir / gi.MARCA_INCOMPLETA).exists())
        self.assertFalse((self.config / "guionar").exists())
        datos = json.loads(d.metadata.read_text())
        self.assertEqual(set(datos),
                         {"app", "version", "current", "previous", "installed_at"})
        self.assertEqual((datos["app"], datos["version"], datos["current"]),
                         ("GuionAR", VERSION, relativo))
        self.assertNotIn(str(self.home), d.metadata.read_text())

    def test_lanzador_instalado_usa_el_venv_de_su_version(self):
        relativo, _ = self.instalar()
        lanzador = self.destino.bin_dir / "guionar"
        resultado = ejecutar(str(lanzador), "--version", cwd=self.base)
        self.assertEqual(resultado.stdout, f"GuionAR {VERSION}\n")
        traza = ejecutar("sh", "-x", str(lanzador), "--version", cwd=self.base)
        self.assertIn(f"{relativo}/.venv/bin/python", traza.stderr)

    def test_pip_solo_wheels_con_constraints(self):
        self.instalar()
        pip = [a for tipo, a in self.herramientas.llamadas if tipo == "pip"]
        self.assertEqual(pip, [["-c", str(self.release / "constraints.txt"),
                                "--only-binary=:all:", "PyQt6"]])

    def test_idempotente_poda_y_preserva_config(self):
        preservados = self.sembrar_config()
        primero, _ = self.instalar()
        desktop = self.destino.desktop.read_bytes()
        segundo, previo2 = self.instalar()
        tercero, previo3 = self.instalar()
        self.assertEqual((previo2, previo3), (primero, segundo))
        self.assertEqual(sorted(p.name for p in self.destino.versions.iterdir()),
                         sorted(Path(v).name for v in (segundo, tercero)))
        self.assertEqual(self.destino.desktop.read_bytes(), desktop)
        self.assertPreservados(preservados)


class FalloAntesDelSwap(InstaladorBase):
    def test_fallo_de_pip_preserva_current(self):
        self.instalar()
        self.sembrar_config()
        antes = arbol(self.home)
        self.herramientas.fallar_pip = True
        with self.assertRaises(gi.ErrorInstalacion):
            self.instalar()
        self.assertEqual(arbol(self.home), antes)

    def test_smoke_roto_preserva_current(self):
        self.instalar()
        antes = arbol(self.home)
        (self.release / "app" / "guionar.py").write_text("raise SystemExit(3)\n")
        with self.assertRaisesRegex(gi.ErrorInstalacion, "smoke"):
            self.instalar()
        self.assertEqual(arbol(self.home), antes)

    def test_version_inconsistente_se_rechaza(self):
        (self.release / "VERSION").write_text("9.9.9\n")
        with self.assertRaisesRegex(gi.ErrorInstalacion, "VERSION"):
            gi.detectar_origen(self.release)

    def test_fallo_en_instalacion_nueva_no_deja_rastros(self):
        self.herramientas.fallar_pip = True
        with self.assertRaises(gi.ErrorInstalacion):
            self.instalar()
        self.assertFalse(os.path.lexists(self.destino.install_home))

    def test_desktop_ajeno_aborta_sin_efectos(self):
        self.destino.desktop.parent.mkdir(parents=True)
        self.destino.desktop.write_text("[Desktop Entry]\nName=Otro\nExec=otro\n")
        antes = arbol(self.home)
        with self.assertRaisesRegex(gi.ErrorInstalacion, "no fue generado"):
            self.instalar()
        self.assertEqual(arbol(self.home), antes)
        self.assertEqual(self.herramientas.llamadas, [])

    def test_enlace_ajeno_aborta_sin_efectos(self):
        self.destino.bin_dir.mkdir(parents=True)
        (self.destino.bin_dir / "guionar").symlink_to("/opt/otro/guionar")
        antes = arbol(self.home)
        with self.assertRaisesRegex(gi.ErrorInstalacion, "no pertenece"):
            self.instalar()
        self.assertEqual(arbol(self.home), antes)


class MigracionLegacy(InstaladorBase):
    def test_reemplaza_launcher_legacy_sin_tocar_checkout_ni_config(self):
        checkout = self.crear_checkout_legacy()
        preservados = self.sembrar_config()
        checkout_antes = arbol(checkout)
        legacy = self.destino.desktop.read_text()
        self.assertEqual(gi.estado_desktop(self.destino.desktop), "legacy")
        self.assertIn('Guion AR \\\\$repo/bin/guionar" --socket', legacy)

        self.instalar()

        desktop = self.destino.desktop.read_text()
        self.assertIn(
            f'Exec="{self.destino.install_home}/current/app/bin/guionar" --socket',
            desktop)
        self.assertNotIn(str(checkout), desktop)
        self.assertEqual(arbol(checkout), checkout_antes)  # incluye .venv
        self.assertPreservados(preservados)

    def test_plantilla_legacy_es_la_de_install_desktop_entry(self):
        self.assertEqual(
            gi.PLANTILLA_LEGACY,
            (ROOT / "packaging/linux/guionar.desktop.in").read_text())

    def test_renderer_y_script_legacy_escapan_igual(self):
        for nombre in ("normal", "con espacio", "dólar $x", 'comilla "q"',
                       "barra \\ b", "acento `a`"):
            with self.subTest(nombre=nombre):
                checkout = self.base / nombre
                (checkout / "packaging/linux").mkdir(parents=True)
                (checkout / "assets").mkdir()
                for relativo in ("packaging/linux/install-desktop-entry.sh",
                                 "packaging/linux/guionar.desktop.in",
                                 "assets/guionar.svg"):
                    shutil.copy2(ROOT / relativo, checkout / relativo)
                datos = self.base / f"datos {len(nombre)}"
                ejecutar("sh", str(checkout / "packaging/linux/install-desktop-entry.sh"),
                         env=dict(os.environ, XDG_DATA_HOME=str(datos)))
                generado = (datos / "applications/guionar.desktop").read_text()
                self.assertTrue(gi.es_desktop_legacy(generado), generado)
                propio = gi.renderizar_desktop(
                    gi.PLANTILLA_LEGACY, checkout / "bin" / "guionar")
                self.assertEqual(propio, f"{gi.MARCA_DESKTOP}\n{generado}")

    def test_reconocimiento_conservador(self):
        valido = gi.PLANTILLA_LEGACY.replace(
            "@EXEC@", '"/home/x/GuionAR/bin/guionar"')
        self.assertTrue(gi.es_desktop_legacy(valido))
        for nombre, variante in (
                ("linea extra", valido + "X-Extra=1\n"),
                ("otro ejecutable", valido.replace("bin/guionar", "bin/otro")),
                ("sin --socket", valido.replace(" --socket", "")),
                ("relativo", valido.replace('"/home/x', '"home/x')),
                ("nombre cambiado", valido.replace("Name=GuionAR", "Name=Otro")),
                ("sin comillas", valido.replace('"', ""))):
            with self.subTest(variante=nombre):
                self.assertFalse(gi.es_desktop_legacy(variante))

    def test_launcher_legacy_editado_no_se_reemplaza(self):
        self.crear_checkout_legacy()
        with self.destino.desktop.open("a") as archivo:
            archivo.write("NoDisplay=true\n")
        antes = arbol(self.home)
        with self.assertRaisesRegex(gi.ErrorInstalacion, "no fue generado"):
            self.instalar()
        self.assertEqual(arbol(self.home), antes)


class Desinstalacion(InstaladorBase):
    def test_default_conserva_config_y_checkout(self):
        checkout = self.crear_checkout_legacy()
        self.instalar()
        preservados = self.sembrar_config()
        checkout_antes = arbol(checkout)
        resultado = self.uninstall()
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        d = self.destino
        for ruta in (d.current, d.versions, d.metadata, d.uninstall, d.desktop,
                     d.icono, d.install_home, d.bin_dir / "guionar",
                     d.bin_dir / "guionar-uninstall"):
            self.assertFalse(os.path.lexists(ruta), ruta)
        self.assertPreservados(preservados)
        self.assertEqual(arbol(checkout), checkout_antes)
        self.assertIn("--purge-data", resultado.stdout)

    def test_guionar_uninstall_sin_release_y_con_otra_xdg(self):
        self.instalar()
        shutil.rmtree(self.release)
        resultado = self.uninstall(
            script=self.destino.bin_dir / "guionar-uninstall",
            entorno_extra={"XDG_DATA_HOME": str(self.base / "otra")})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertFalse(os.path.lexists(self.destino.install_home))

    def test_purge_data_borra_solo_config_de_guionar(self):
        self.instalar()
        self.sembrar_config()
        otra = self.config / "parlar" / "config.json"
        otra.parent.mkdir(parents=True)
        otra.write_text("{}")
        resultado = self.uninstall("--purge-data")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertFalse(os.path.lexists(self.config / "guionar"))
        self.assertEqual(otra.read_text(), "{}")

    def test_icono_modificado_no_se_borra(self):
        self.instalar()
        self.destino.icono.write_text("<svg>otro</svg>")
        resultado = self.uninstall()
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(self.destino.icono.read_text(), "<svg>otro</svg>")

    def test_opcion_invalida_no_borra(self):
        self.instalar()
        antes = arbol(self.home)
        resultado = self.uninstall("--purgar")
        self.assertEqual(resultado.returncode, 2)
        self.assertEqual(arbol(self.home), antes)


class WrapperInstallSh(unittest.TestCase):
    def preparar(self, base: Path, version_python: str):
        release = base / "release"
        InstaladorBase._crear_release(release)
        marca = base / "instalador-ejecutado"
        python = base / "python-falso"
        python.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-c" ]; then\n'
            f'  case "$2" in *version_info*) echo {version_python} ;; esac\n'
            "  exit 0\n"
            "fi\n"
            f'printf "%s\\n" "$@" > "{marca}"\n')
        python.chmod(0o755)
        entorno = dict(os.environ, GUIONAR_BOOTSTRAP_PYTHON=str(python),
                       HOME=str(base / "home"))
        return release, marca, entorno

    def test_python_no_soportado(self):
        with tempfile.TemporaryDirectory() as tmp:
            release, marca, entorno = self.preparar(Path(tmp), "3.11")
            resultado = ejecutar(str(release / "install.sh"), env=entorno)
            self.assertEqual(resultado.returncode, 1)
            self.assertIn("Esta versión de GuionAR soporta Python 3.12–3.14.",
                          resultado.stderr)
            self.assertIn("Instalá una versión compatible o descargá una "
                          "release más nueva.", resultado.stderr)
            self.assertFalse(marca.exists())

    def test_python_soportado_delega(self):
        with tempfile.TemporaryDirectory() as tmp:
            release, marca, entorno = self.preparar(Path(tmp), "3.12")
            resultado = ejecutar(str(release / "install.sh"), env=entorno)
            self.assertEqual(resultado.returncode, 0, resultado.stderr)
            self.assertEqual(marca.read_text().splitlines(), [
                str(release.resolve() / "app/packaging/linux/guionar_installer.py"),
                "--origen", str(release.resolve())])

    def test_checkout_no_es_release(self):
        resultado = ejecutar(str(ROOT / "install.sh"), env=dict(os.environ))
        self.assertEqual(resultado.returncode, 1)
        self.assertIn("no es una release de GuionAR", resultado.stderr)

    def test_help_y_opcion_invalida(self):
        ayuda = ejecutar(str(ROOT / "install.sh"), "--help")
        self.assertEqual(ayuda.returncode, 0)
        self.assertIn("guionar-uninstall", ayuda.stdout)
        self.assertEqual(ejecutar(str(ROOT / "install.sh"), "--x").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
