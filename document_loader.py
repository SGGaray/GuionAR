"""Carga de documentos para Modo Script.

Un único pipeline para todos los formatos:

    archivo -> extracción de texto -> normalización -> Guion

Cada formato sólo aporta su extractor (TXT/MD/PDF/DOCX). Validación de
ruta, límites, normalización y construcción del Guion son comunes.

Todo es local. No se renderiza HTML, no se ejecutan macros ni se resuelve
contenido externo. ``load_document`` y ``load_script`` nunca lanzan
excepción: los errores vuelven en ``DocumentResult.error`` con un mensaje
apto para mostrar al usuario.

Módulo sin Qt salvo el extractor PDF, que importa QtPdf (incluido en la
wheel de PyQt6) sólo cuando hace falta.
"""

from dataclasses import dataclass
import html
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from guion import Guion

FORMATOS = {
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
    ".docx": "docx",
}

# Un guion razonable mide decenas de KB. Los límites sólo evitan que un
# archivo accidental (un video renombrado, un dump) congele la carga.
MAX_BYTES = 64 * 1024 * 1024
MAX_BYTES_TEXTO = 8 * 1024 * 1024
MAX_CHARS = 2_000_000
MAX_DOCX_XML_BYTES = 64 * 1024 * 1024

MSG_VACIO = "El archivo está vacío"
MSG_SIN_PALABRAS = "El documento no tiene palabras para leer"
MSG_PDF_SIN_TEXTO = ("No se encontró texto legible en este PDF. "
                     "Si es un escaneo, necesita OCR")


@dataclass(frozen=True)
class DocumentResult:
    path: str
    display_name: str
    source_type: str | None
    text: str = ""
    error: str | None = None
    guion: Guion | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class DocumentError(Exception):
    """Error esperable de carga; el mensaje se muestra tal cual."""


def tipo_de(ruta: str) -> str | None:
    return FORMATOS.get(os.path.splitext(str(ruta))[1].lower())


def es_soportado(ruta: str) -> bool:
    return tipo_de(ruta) is not None


def filtro_dialogo() -> str:
    patrones = " ".join(f"*{ext}" for ext in FORMATOS)
    return f"Guiones ({patrones});;Todos los archivos (*)"


def formatos_legibles() -> str:
    return "TXT, MD, PDF o DOCX"


# ---------------------------------------------------------------- pipeline

def load_document(ruta) -> DocumentResult:
    """Extrae y normaliza el texto de ``ruta``. Nunca lanza excepción."""
    ruta = os.fspath(ruta)
    nombre = os.path.basename(ruta) or ruta
    tipo = tipo_de(ruta)
    try:
        if tipo is None:
            raise DocumentError(
                f"Formato no soportado. Usá {formatos_legibles()}")
        _validar_archivo(ruta, tipo)
        texto = normalizar_texto(_EXTRACTORES[tipo](ruta))
        if not texto:
            raise DocumentError(MSG_PDF_SIN_TEXTO if tipo == "pdf" else MSG_VACIO)
        if len(texto) > MAX_CHARS:
            raise DocumentError("El documento es demasiado grande para un guion")
    except DocumentError as e:
        return DocumentResult(ruta, nombre, tipo, error=str(e))
    except Exception:  # extractor roto: nunca debe tumbar la UI
        return DocumentResult(ruta, nombre, tipo,
                              error="No se pudo leer el documento")
    return DocumentResult(ruta, nombre, tipo, text=texto)


def load_script(ruta) -> DocumentResult:
    """``load_document`` + construcción del Guion listo para Modo Script."""
    resultado = load_document(ruta)
    if not resultado.ok:
        return resultado
    guion = Guion(resultado.text)
    if not guion.valido:
        return DocumentResult(resultado.path, resultado.display_name,
                              resultado.source_type, error=MSG_SIN_PALABRAS)
    return DocumentResult(resultado.path, resultado.display_name,
                          resultado.source_type, text=resultado.text,
                          guion=guion)


def _validar_archivo(ruta: str, tipo: str):
    try:
        st = os.stat(ruta)
    except FileNotFoundError:
        raise DocumentError("El archivo no existe") from None
    except OSError:
        raise DocumentError("No se pudo acceder al archivo") from None
    if not os.path.isfile(ruta):
        raise DocumentError("La ruta no es un archivo")
    if st.st_size == 0:
        raise DocumentError(MSG_VACIO)
    limite = MAX_BYTES_TEXTO if tipo in ("txt", "markdown") else MAX_BYTES
    if st.st_size > limite:
        raise DocumentError("El documento es demasiado grande para un guion")


# ---------------------------------------------------------------- normalización

_ESPECIALES = str.maketrans({
    "­": None,      # guion blando
    "​": None, "‌": None, "‍": None, "⁠": None,
    "﻿": None,      # BOM / zero-width no-break space
    " ": " ", " ": " ", " ": " ",
    "\t": " ",
    "\f": "\n\n",        # salto de página
    " ": "\n",
    " ": "\n\n",
})


def normalizar_texto(texto: str) -> str:
    """Texto limpio para teleprompter: saltos Unix, sin caracteres de
    control ni invisibles, espacios simples, párrafos separados por
    exactamente una línea vacía. No cambia palabras: el matching de voz
    normaliza aparte."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = unicodedata.normalize("NFC", texto.translate(_ESPECIALES))
    texto = "".join(c for c in texto
                    if c == "\n" or unicodedata.category(c) != "Cc")
    lineas = [re.sub(r" {2,}", " ", linea).strip() for linea in texto.split("\n")]
    texto = "\n".join(lineas)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# ---------------------------------------------------------------- extractores

def _leer_utf8(ruta: str) -> str:
    try:
        with open(ruta, "rb") as archivo:
            datos = archivo.read()
    except OSError:
        raise DocumentError("No se pudo leer el archivo") from None
    if b"\x00" in datos:
        raise DocumentError("El archivo no parece texto plano")
    try:
        return datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentError(
            "El archivo no está en UTF-8. Guardalo como UTF-8 y volvé a abrirlo"
        ) from None


def _extraer_txt(ruta: str) -> str:
    return _leer_utf8(ruta)


_MD_BLOQUES_ACTIVOS = re.compile(
    r"<(script|style|iframe|object|embed|template)\b.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL)
_MD_COMENTARIO = re.compile(r"<!--.*?-->", re.DOTALL)
_MD_ETIQUETA = re.compile(r"</?[A-Za-z][^<>]*>")
_MD_IMAGEN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_LINK_REF = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_MD_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_MD_DEF_REF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+\S+.*$")
_MD_TITULO = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_MD_REGLA = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_MD_TABLA_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_MD_LISTA = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(\[[ xX]\]\s+)?")
_MD_SUBRAYADO = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_MD_ENFASIS = re.compile(r"(\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
# Con guion bajo sólo hay énfasis fuera de palabras: snake_case queda intacto.
_MD_ENFASIS_BAJO = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_MD_CODIGO = re.compile(r"`+([^`]*)`+")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


def markdown_a_texto(fuente: str) -> str:
    """Markdown -> texto de lectura. No es un renderer: quita marcas de
    formato y descarta HTML (nunca se interpreta ni se ejecuta). Las líneas
    ``>`` se conservan porque en GuionAR son notas del guion."""
    fuente = fuente.replace("\r\n", "\n").replace("\r", "\n")
    fuente = _MD_COMENTARIO.sub("", _MD_BLOQUES_ACTIVOS.sub("", fuente))

    lineas = fuente.split("\n")
    # Front matter YAML al comienzo del archivo.
    if lineas and lineas[0].strip() == "---":
        for i in range(1, len(lineas)):
            if lineas[i].strip() in ("---", "..."):
                lineas = lineas[i + 1:]
                break

    salida = []
    en_codigo = False
    for linea in lineas:
        if re.match(r"^\s{0,3}(```|~~~)", linea):
            en_codigo = not en_codigo
            continue
        if en_codigo:
            salida.append(linea.strip())
            continue
        if _MD_DEF_REF.match(linea):
            continue
        if _MD_REGLA.match(linea) or _MD_TABLA_SEP.match(linea):
            salida.append("")
            continue
        if _MD_SUBRAYADO.match(linea) and salida and salida[-1].strip():
            salida.append("")  # subrayado setext: el título ya es su línea
            continue
        titulo = _MD_TITULO.match(linea)
        if titulo:
            # Un título es su propio párrafo en el teleprompter.
            salida.extend(["", titulo.group(1), ""])
            continue
        linea = _MD_LISTA.sub(r"\1", linea)
        if "|" in linea and linea.strip().startswith("|"):
            linea = " ".join(c.strip() for c in linea.strip().strip("|").split("|"))
        salida.append(linea)

    texto = "\n".join(salida)
    # Los escapes se protegen antes de quitar marcas y se restauran al final.
    texto = _MD_ESCAPE.sub(lambda m: chr(0xE000 + ord(m.group(1))), texto)
    texto = _MD_IMAGEN.sub(r"\1", texto)
    texto = _MD_LINK.sub(r"\1", texto)
    texto = _MD_LINK_REF.sub(r"\1", texto)
    texto = _MD_AUTOLINK.sub(r"\1", texto)
    texto = _MD_ETIQUETA.sub("", texto)
    texto = _MD_CODIGO.sub(r"\1", texto)
    for _ in range(3):  # énfasis anidado: ***x***, **_x_**
        texto = _MD_ENFASIS.sub(r"\2", texto)
        texto = _MD_ENFASIS_BAJO.sub(r"\2", texto)
    texto = re.sub("[\ue000-\ue07f]", lambda m: chr(ord(m.group()) - 0xE000), texto)
    return html.unescape(texto)


def _extraer_markdown(ruta: str) -> str:
    return markdown_a_texto(_leer_utf8(ruta))


def _extraer_pdf(ruta: str) -> str:
    try:
        from PyQt6.QtPdf import QPdfDocument
    except ImportError:
        raise DocumentError(
            "Esta instalación de PyQt6 no incluye soporte PDF (QtPdf)") from None

    documento = QPdfDocument(None)
    try:
        error = documento.load(ruta)
        errores = QPdfDocument.Error
        if error in (errores.IncorrectPassword, errores.UnsupportedSecurityScheme):
            raise DocumentError("El PDF está protegido con contraseña")
        if error == errores.FileNotFound:
            raise DocumentError("El archivo no existe")
        if error != errores.None_ or documento.pageCount() <= 0:
            raise DocumentError("El PDF está dañado o no es un PDF válido")
        paginas = []
        total = 0
        for pagina in range(documento.pageCount()):
            texto = documento.getAllText(pagina).text()
            total += len(texto)
            if total > MAX_CHARS:
                raise DocumentError("El documento es demasiado grande para un guion")
            paginas.append(texto)
        return "\n\n".join(paginas)
    finally:
        documento.close()
        documento.deleteLater()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = ("{http://schemas.openxmlformats.org/markup-compatibility/2006}"
                "Fallback")


def _extraer_docx(ruta: str) -> str:
    """Párrafos de word/document.xml con stdlib. No se ejecutan macros ni
    se siguen relaciones externas: sólo se lee el XML del cuerpo."""
    try:
        with zipfile.ZipFile(ruta) as paquete:
            try:
                info = paquete.getinfo("word/document.xml")
            except KeyError:
                raise DocumentError("El DOCX no tiene contenido de documento") from None
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise DocumentError("El documento es demasiado grande para un guion")
            with paquete.open(info) as xml:
                datos = xml.read(MAX_DOCX_XML_BYTES + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
            NotImplementedError, RuntimeError):
        raise DocumentError("El DOCX está dañado o no es un DOCX válido") from None
    except OSError:
        raise DocumentError("No se pudo leer el archivo") from None

    if len(datos) > MAX_DOCX_XML_BYTES:
        raise DocumentError("El documento es demasiado grande para un guion")
    # OOXML nunca declara DTD. Rechazarla corta cualquier expansión de
    # entidades antes de llegar al parser.
    if b"<!DOCTYPE" in datos or b"<!ENTITY" in datos:
        raise DocumentError("El DOCX está dañado o no es un DOCX válido")
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError:
        raise DocumentError("El DOCX está dañado o no es un DOCX válido") from None

    parrafos = []

    def recorrer(elemento, destino):
        for hijo in elemento:
            tag = hijo.tag
            if tag == _MC_FALLBACK:
                continue  # copia alternativa de un cuadro de texto ya leído
            if tag == _W + "p":
                propio = []
                recorrer(hijo, propio)
                texto = "".join(propio).strip()
                if texto:
                    parrafos.append(texto)
            elif tag == _W + "t":
                destino.append(hijo.text or "")
            elif tag in (_W + "tab", _W + "noBreakHyphen"):
                destino.append(" " if tag == _W + "tab" else "-")
            elif tag in (_W + "br", _W + "cr"):
                destino.append("\n")
            elif tag in (_W + "delText", _W + "instrText"):
                continue
            else:
                recorrer(hijo, destino)

    recorrer(raiz, [])
    return "\n\n".join(parrafos)


_EXTRACTORES = {
    "txt": _extraer_txt,
    "markdown": _extraer_markdown,
    "pdf": _extraer_pdf,
    "docx": _extraer_docx,
}
