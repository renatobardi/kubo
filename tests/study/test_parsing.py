"""Parser de Material de estudo (ADR-0043, KUBO-135): epub/PDF → capítulos ordenados.

Unit puro, sem banco e sem rede. As fixtures são GERADAS EM CÓDIGO (zipfile/pypdf em
memória, determinísticas) — nenhum binário no repo: um epub commitado seria opaco à
revisão e o que ele testa ficaria invisível no diff.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from kubo.errors import MaterialParseError
from kubo.study.parsing import parse_material

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
   media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Manual de Kubo</dc:title>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="cap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="cap2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c3" href="cap3.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/><itemref idref="c2"/><itemref idref="c3"/>
  </spine>
</package>"""

# Sumário com aninhamento: "Parte I" agrupa os dois primeiros capítulos, o terceiro
# é solto no topo — é o que fixa a propagação de `part`.
_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="p1" playOrder="1">
      <navLabel><text>Parte I</text></navLabel>
      <content src="cap1.xhtml"/>
      <navPoint id="n1" playOrder="2">
        <navLabel><text>Capítulo Um</text></navLabel>
        <content src="cap1.xhtml"/>
      </navPoint>
      <navPoint id="n2" playOrder="3">
        <navLabel><text>Capítulo Dois</text></navLabel>
        <content src="cap2.xhtml"/>
      </navPoint>
    </navPoint>
    <navPoint id="n3" playOrder="4">
      <navLabel><text>Capítulo Três</text></navLabel>
      <content src="cap3.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"""

_EMPTY_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap></navMap>
</ncx>"""

_CHAPTER_TEXT = {
    "cap1.xhtml": "O texto do primeiro capítulo.",
    "cap2.xhtml": "O texto do segundo capítulo.",
    "cap3.xhtml": "O texto do terceiro capítulo.",
}


def _xhtml(body: str) -> str:
    """XHTML mínimo de um capítulo — o parágrafo é o que deve sobrar depois do strip."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ignorado</title></head>'
        f"<body><p>{body}</p></body></html>"
    )


def _epub(*, ncx: str | None = _NCX, container: str | None = _CONTAINER) -> bytes:
    """Epub válido mínimo; `ncx=None` remove o sumário e `container=None` o container.xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        if container is not None:
            zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", _OPF)
        if ncx is not None:
            zf.writestr("OEBPS/toc.ncx", ncx)
        for href, text in _CHAPTER_TEXT.items():
            zf.writestr(f"OEBPS/{href}", _xhtml(text))
    return buf.getvalue()


def _pdf(*, titles: list[str] | None = None, texts: list[str] | None = None) -> bytes:
    """PDF de N páginas com texto real extraível e, opcionalmente, outline top-level."""
    writer = PdfWriter()
    for text in texts or []:
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        font = DictionaryObject()
        font[NameObject("/Type")] = NameObject("/Font")
        font[NameObject("/Subtype")] = NameObject("/Type1")
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = writer._add_object(font)
        resources = DictionaryObject()
        resources[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = resources
        page[NameObject("/MediaBox")] = ArrayObject(
            [NumberObject(0), NumberObject(0), NumberObject(200), NumberObject(200)]
        )
    for index, title in enumerate(titles or []):
        writer.add_outline_item(title, index)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_epub_chapters_follow_reading_order() -> None:
    """Epub válido: capítulos na ordem do spine, `seq` 1-based e sem buracos."""
    parsed = parse_material(_epub(), "epub")

    assert [c.seq for c in parsed.chapters] == [1, 2, 3]
    assert [c.title for c in parsed.chapters] == [
        "Capítulo Um",
        "Capítulo Dois",
        "Capítulo Três",
    ]


def test_epub_reads_title_from_opf() -> None:
    """O título do material vem do `dc:title` do OPF."""
    assert parse_material(_epub(), "epub").title == "Manual de Kubo"


def test_epub_propagates_part_from_nested_toc() -> None:
    """navPoint pai vira `part` dos filhos; capítulo solto no topo fica sem parte."""
    parsed = parse_material(_epub(), "epub")

    assert [c.part for c in parsed.chapters] == ["Parte I", "Parte I", None]


def test_epub_extracts_plain_text_without_markup() -> None:
    """O conteúdo é texto puro: o parágrafo sobrevive, as tags não."""
    first = parse_material(_epub(), "epub").chapters[0]

    assert "O texto do primeiro capítulo." in first.content
    assert "<p>" not in first.content
    assert "</html>" not in first.content


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"nem-de-longe-um-zip", id="bytes-corrompidos"),
        pytest.param(_epub(container=None), id="zip-sem-container"),
        pytest.param(_epub(ncx=None), id="epub-sem-sumario"),
        pytest.param(_epub(ncx=_EMPTY_NCX), id="sumario-vazio"),
    ],
)
def test_epub_rejects_unreadable_input(data: bytes) -> None:
    """Arquivo ilegível vira MaterialParseError com mensagem legível, não exceção crua."""
    with pytest.raises(MaterialParseError) as exc:
        parse_material(data, "epub")

    assert str(exc.value).strip(), "MaterialParseError sem mensagem para o dono"


def test_pdf_with_outline_becomes_one_chapter_per_entry() -> None:
    """Com bookmarks top-level, cada entrada do outline é um capítulo, em ordem."""
    parsed = parse_material(
        _pdf(titles=["Abertura", "Fechamento"], texts=["Texto de abertura", "Texto final"]),
        "pdf",
    )

    assert [c.seq for c in parsed.chapters] == [1, 2]
    assert [c.title for c in parsed.chapters] == ["Abertura", "Fechamento"]
    assert "Texto de abertura" in parsed.chapters[0].content


def test_pdf_without_outline_becomes_single_chapter() -> None:
    """Sem outline, o PDF inteiro vira um capítulo único `seq=1`."""
    parsed = parse_material(_pdf(texts=["Corpo do documento"]), "pdf")

    assert len(parsed.chapters) == 1
    assert parsed.chapters[0].seq == 1
    assert "Corpo do documento" in parsed.chapters[0].content


def test_pdf_rejects_corrupted_bytes() -> None:
    """Bytes que não são PDF viram MaterialParseError."""
    with pytest.raises(MaterialParseError):
        parse_material(b"%PDF-1.4 mentira", "pdf")


def test_pdf_without_extractable_text_is_rejected() -> None:
    """PDF sem nenhum texto extraível não vira material vazio — falha alto."""
    with pytest.raises(MaterialParseError):
        parse_material(_pdf(texts=[]), "pdf")


def test_format_mismatch_is_rejected() -> None:
    """Epub declarado como PDF (e vice-versa) é recusado — a extensão não é confiável."""
    with pytest.raises(MaterialParseError):
        parse_material(_epub(), "pdf")
    with pytest.raises(MaterialParseError):
        parse_material(_pdf(texts=["x"]), "epub")
