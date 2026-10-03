"""Tests de carga de documentos: TXT, Markdown, PDF y DOCX por un único
pipeline (document_loader.load_document / load_script).

Corre headless:
    QT_QPA_PLATFORM=offscreen python tests/test_documents.py

Los PDF se generan con QPdfWriter (capa de texto real o sólo gráficos) y
los DOCX se arman con zipfile, así la suite no necesita fixtures binarios.
"""

import os
import sys
import tempfile
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtGui import QFont, QPageSize, QPainter, QPdfWriter
from PyQt6.QtWidgets import QApplication

import document_loader
from document_loader import load_document, load_script

FALLAS = []
_app = QApplication.instance() or QApplication(sys.argv)
_TMP = tempfile.TemporaryDirectory()


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"[PASA] {nombre}")
    else:
        print(f"[FALLA] {nombre} {detalle}")
        FALLAS.append(nombre)


def _ruta(nombre):
    return os.path.join(_TMP.name, nombre)


def _escribir(nombre, contenido):
    ruta = _ruta(nombre)
    modo = "wb" if isinstance(contenido, bytes) else "w"
    kwargs = {} if isinstance(contenido, bytes) else {"encoding": "utf-8"}
    with open(ruta, modo, **kwargs) as archivo:
        archivo.write(contenido)
    return ruta


def _pdf(nombre, paginas, con_texto=True):
    ruta = _ruta(nombre)
    writer = QPdfWriter(ruta)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    p = QPainter(writer)
    p.setFont(QFont("DejaVu Sans", 11))
    for i, lineas in enumerate(paginas):
        if i:
            writer.newPage()
        if con_texto:
            for j, linea in enumerate(lineas):
                p.drawText(300, 400 + j * 260, linea)
        else:
            p.fillRect(300, 400, 4000, 3000, 0)  # "escaneo": sólo gráficos
    p.end()
    return ruta


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _docx(nombre, cuerpo_xml, documento=True, prologo=""):
    ruta = _ruta(nombre)
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>{prologo}'
           f'<w:document xmlns:w="{_W_NS}" xmlns:mc="{_MC_NS}">'
           f'<w:body>{cuerpo_xml}</w:body></w:document>')
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/content-types"/>')
        if documento:
            z.writestr("word/document.xml", xml)
    return ruta


def _p(*runs):
    return "<w:p>" + "".join(f"<w:r>{r}</w:r>" for r in runs) + "</w:p>"


def _t(texto):
    return f'<w:t xml:space="preserve">{texto}</w:t>'


# ---------------------------------------------------------------- contrato

def test_api_y_formatos():
    check("formatos soportados", all(document_loader.es_soportado(f"x{e}")
          for e in (".txt", ".md", ".markdown", ".pdf", ".docx", ".PDF")))
    check("extensión desconocida no soportada",
          not document_loader.es_soportado("x.rtf")
          and not document_loader.es_soportado("sin_extension"))
    filtro = document_loader.filtro_dialogo()
    check("filtro del selector incluye los cuatro formatos",
          all(f"*{e}" in filtro for e in (".txt", ".md", ".pdf", ".docx")))


def test_txt():
    ruta = _escribir("charla.txt", "Hola a todos.\r\nSegunda línea.\r\n\r\n\r\n\r\nOtro párrafo.")
    r = load_document(ruta)
    check("TXT carga", r.ok and r.source_type == "txt", repr(r.error))
    check("TXT informa nombre visible", r.display_name == "charla.txt")
    check("TXT normaliza saltos y párrafos",
          r.text == "Hola a todos.\nSegunda línea.\n\nOtro párrafo.", repr(r.text))

    bom = _escribir("bom.txt", b"\xef\xbb\xbfcon BOM \xc3\xb1and\xc3\xba")
    r = load_document(bom)
    check("TXT con BOM UTF-8", r.ok and r.text == "con BOM ñandú", repr(r.text))

    invisibles = _escribir("invisibles.txt", "pala­bra​ fin\x07al\tok")
    r = load_document(invisibles)
    check("TXT quita invisibles y controles", r.ok and r.text == "palabra final ok",
          repr(r.text))

    latin1 = _escribir("latin1.txt", "canción".encode("latin-1"))
    r = load_document(latin1)
    check("TXT no UTF-8 falla con mensaje claro",
          not r.ok and "UTF-8" in r.error and r.text == "", repr(r.error))

    binario = _escribir("binario.txt", b"abc\x00\x01\x02def")
    r = load_document(binario)
    check("TXT binario se rechaza", not r.ok and "texto" in r.error, repr(r.error))

    grande = _escribir("grande.txt", ("palabra " * 12 + "fin.\n") * 15_000)
    t0 = time.perf_counter()
    r = load_script(grande)
    dt = time.perf_counter() - t0
    check("TXT grande (~195k palabras) carga", r.ok and r.guion.valido, repr(r.error))
    check("TXT grande carga en tiempo razonable", dt < 5.0, f"{dt:.2f}s")


def test_markdown():
    fuente = """---
title: Charla
---
# Título **principal**

Hola *mundo*, un [enlace](https://example.com) y ![logo](logo.png).
snake_case_name se conserva y \\*esto\\* también.

<script>alert("nunca")</script>
<b>negrita</b> &amp; <!-- comentario oculto --> fin.

- primer punto
1. segundo punto

> nota para quien lee

| col a | col b |
|-------|-------|
| uno   | dos   |

```
codigo literal
```
"""
    r = load_document(_escribir("charla.md", fuente))
    texto = r.text
    check("MD carga", r.ok and r.source_type == "markdown", repr(r.error))
    check("MD quita marcas de formato",
          "Título principal" in texto and "Hola mundo" in texto
          and "*" not in texto.replace("*esto*", ""), repr(texto))
    check("MD conserva texto de enlaces, no URLs",
          "enlace" in texto and "example.com" not in texto and "logo.png" not in texto)
    check("MD no conserva HTML activo ni comentarios",
          "alert" not in texto and "<" not in texto and "comentario" not in texto,
          repr(texto))
    check("MD decodifica entidades y quita etiquetas", "negrita & fin." in texto,
          repr(texto))
    check("MD respeta snake_case y escapes",
          "snake_case_name" in texto and "*esto*" in texto)
    check("MD quita front matter y viñetas",
          "title:" not in texto and "primer punto" in texto
          and "- primer" not in texto and "1. segundo" not in texto)
    check("MD conserva notas '>' del guion", "> nota para quien lee" in texto)
    check("MD aplana tablas", "col a col b" in texto and "---" not in texto)

    s = load_script(_escribir("charla2.md", fuente))
    check("MD termina en Guion válido sin las notas",
          s.ok and "nota" not in [w for _, w in s.guion.originales])

    grande = _escribir("grande.md", ("## Sección\n\nTexto con **énfasis** y "
                                     "[link](x). " * 30 + "\n\n") * 600)
    t0 = time.perf_counter()
    r = load_script(grande)
    dt = time.perf_counter() - t0
    check("MD grande carga en tiempo razonable", r.ok and dt < 5.0, f"{dt:.2f}s")


def test_pdf():
    ruta = _pdf("charla.pdf", [
        ["Primera página con acentos: canción, ñandú.", "Segunda línea."],
        ["Otra página del guion."],
    ])
    r = load_document(ruta)
    check("PDF textual carga", r.ok and r.source_type == "pdf", repr(r.error))
    check("PDF extrae texto con acentos",
          "canción" in r.text and "ñandú" in r.text and "Otra página" in r.text,
          repr(r.text))
    check("PDF separa páginas como párrafos",
          r.text.index("Otra") > r.text.index("\n\n"), repr(r.text))

    escaneo = _pdf("escaneo.pdf", [[]], con_texto=False)
    r = load_document(escaneo)
    check("PDF sin capa de texto falla de forma comprensible",
          not r.ok and r.error.startswith("No se encontró texto legible en este PDF"),
          repr(r.error))

    corrupto = _escribir("roto.pdf", b"%PDF-1.4\n" + os.urandom(2048))
    r = load_document(corrupto)
    check("PDF corrupto no crashea y explica", not r.ok and "PDF" in r.error,
          repr(r.error))

    disfrazado = _escribir("disfrazado.pdf", "esto no es un pdf")
    check("texto con extensión .pdf se rechaza", not load_document(disfrazado).ok)

    paginas = [[f"Página {i} línea {j} del guion largo." for j in range(30)]
               for i in range(80)]
    grande = _pdf("grande.pdf", paginas)
    t0 = time.perf_counter()
    r = load_script(grande)
    dt = time.perf_counter() - t0
    check("PDF grande (80 páginas) carga", r.ok and r.guion.valido, repr(r.error))
    check("PDF grande carga en tiempo razonable", dt < 5.0, f"{dt:.2f}s")


def test_docx():
    cuerpo = (
        _p(_t("Primer párrafo"), "<w:tab/>", _t("con tab.")) +
        _p(_t("Segundo"), "<w:br/>", _t("con salto.")) +
        "<w:p/>" +
        "<w:tbl><w:tr><w:tc>" + _p(_t("Celda de tabla.")) + "</w:tc></w:tr></w:tbl>" +
        _p(_t("Texto vigente"), "<w:delText>texto borrado</w:delText>",
           "<w:instrText>PAGE</w:instrText>") +
        "<w:p><w:r><mc:AlternateContent><mc:Choice>" +
        "<w:txbxContent>" + _p(_t("Cuadro de texto.")) + "</w:txbxContent>" +
        "</mc:Choice><mc:Fallback>" + _p(_t("Cuadro de texto.")) +
        "</mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    r = load_document(_docx("charla.docx", cuerpo))
    check("DOCX carga", r.ok and r.source_type == "docx", repr(r.error))
    check("DOCX separa párrafos",
          r.text.split("\n\n")[0] == "Primer párrafo con tab.", repr(r.text))
    check("DOCX respeta saltos de línea", "Segundo\ncon salto." in r.text, repr(r.text))
    check("DOCX incluye tablas", "Celda de tabla." in r.text)
    check("DOCX ignora texto borrado y códigos de campo",
          "borrado" not in r.text and "PAGE" not in r.text, repr(r.text))
    check("DOCX no duplica cuadros de texto alternativos",
          r.text.count("Cuadro de texto.") == 1, repr(r.text))

    vacio = load_document(_docx("vacio.docx", "<w:p/><w:p/>"))
    check("DOCX sin texto informa vacío",
          not vacio.ok and vacio.error == document_loader.MSG_VACIO, repr(vacio.error))

    corrupto = load_document(_escribir("roto.docx", os.urandom(1024)))
    check("DOCX corrupto no crashea y explica",
          not corrupto.ok and "DOCX" in corrupto.error, repr(corrupto.error))

    sin_doc = load_document(_docx("sin_doc.docx", "", documento=False))
    check("ZIP sin word/document.xml se rechaza",
          not sin_doc.ok and "DOCX" in sin_doc.error, repr(sin_doc.error))

    bomba = ('<!DOCTYPE w:document [<!ENTITY a "aaaaaaaaaa">'
             '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>')
    r = load_document(_docx("entidades.docx", _p(_t("&b;")), prologo=bomba))
    check("DOCX con DTD/entidades se rechaza sin expandir",
          not r.ok and "aaaa" not in r.text, repr(r.error))

    xml_roto = _ruta("xml_roto.docx")
    with zipfile.ZipFile(xml_roto, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p>")
    r = load_document(xml_roto)
    check("DOCX con XML inválido se rechaza", not r.ok and "DOCX" in r.error)

    grande = _docx("grande.docx", _p(_t("Un párrafo del guion largo con varias palabras.")) * 20_000)
    t0 = time.perf_counter()
    r = load_script(grande)
    dt = time.perf_counter() - t0
    check("DOCX grande (20k párrafos) carga", r.ok and r.guion.valido, repr(r.error))
    check("DOCX grande carga en tiempo razonable", dt < 5.0, f"{dt:.2f}s")


def test_errores_generales():
    r = load_document(_escribir("vacio.txt", b""))
    check("archivo vacío informa vacío",
          not r.ok and r.error == document_loader.MSG_VACIO, repr(r.error))
    r = load_document(_escribir("espacios.md", "  \n\n\t \n"))
    check("archivo sólo con espacios informa vacío",
          not r.ok and r.error == document_loader.MSG_VACIO)
    r = load_document(_escribir("guion.rtf", "{\\rtf1 hola}"))
    check("extensión inválida informa formatos válidos",
          not r.ok and "Formato no soportado" in r.error and "PDF" in r.error,
          repr(r.error))
    r = load_document("/no/existe/guion.txt")
    check("archivo inexistente explica", not r.ok and "no existe" in r.error)
    directorio = _ruta("carpeta.txt")
    os.mkdir(directorio)
    check("directorio se rechaza", not load_document(directorio).ok)

    anterior = document_loader.MAX_BYTES_TEXTO
    document_loader.MAX_BYTES_TEXTO = 16
    try:
        r = load_document(_escribir("enorme.txt", "x" * 64))
        check("archivo demasiado grande se rechaza antes de leer",
              not r.ok and "grande" in r.error, repr(r.error))
    finally:
        document_loader.MAX_BYTES_TEXTO = anterior

    solo_notas = load_script(_escribir("notas.txt", "> sólo una nota\n> y otra"))
    check("documento sin palabras leíbles no produce Guion",
          not solo_notas.ok and solo_notas.guion is None
          and solo_notas.error == document_loader.MSG_SIN_PALABRAS)


def test_pipeline_unico():
    """El mismo contenido produce el mismo Guion desde los cuatro formatos."""
    frase = "Buenas tardes. Hoy hablamos del proyecto."
    rutas = {
        "txt": _escribir("igual.txt", frase),
        "markdown": _escribir("igual.md", "**Buenas** tardes. Hoy hablamos del proyecto."),
        "pdf": _pdf("igual.pdf", [[frase]]),
        "docx": _docx("igual.docx", _p(_t(frase))),
    }
    palabras = {}
    for tipo, ruta in rutas.items():
        r = load_script(ruta)
        palabras[tipo] = r.guion.palabras_norm if r.ok else r.error
    referencia = palabras["txt"]
    check("los cuatro formatos convergen al mismo Guion",
          all(v == referencia for v in palabras.values()), repr(palabras))


def main():
    test_api_y_formatos()
    test_txt()
    test_markdown()
    test_pdf()
    test_docx()
    test_errores_generales()
    test_pipeline_unico()
    _TMP.cleanup()

    print()
    if FALLAS:
        print(f"{len(FALLAS)} FALLARON: {FALLAS}")
        sys.exit(1)
    print("Todos los tests de documentos pasaron.")


if __name__ == "__main__":
    main()
