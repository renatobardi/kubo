"""Parser de Material de estudo (ADR-0043): epub e PDF → capítulos em ordem de leitura.

Função PURA: recebe bytes, devolve estrutura. Sem banco, sem rede, sem disco.
Material enviado pelo dono é entrada externa hostil (CLAUDE.md §Segurança): todo
parse de XML passa por `defusedxml`, e falha de formato vira `MaterialParseError`
legível — nunca uma exceção crua da biblioteca vazando para a rota.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from xml.etree.ElementTree import Element  # noqa: S405 — só o TIPO; quem parseia é o defusedxml

from defusedxml.ElementTree import fromstring as xml_fromstring
from pypdf import PdfReader

from kubo.errors import MaterialParseError

MaterialFormat = Literal["epub", "pdf"]

# Cercas de VOLUME (achado ALTO da revisão de segurança): material é entrada hostil e
# um epub de 0,2 MB comprime 200 MB de XHTML e o repete em N navPoints — sem teto, o
# parser aloca gigabytes e derruba o host (o compose não tem mem_limit).
# Bytes DESCOMPRIMIDOS por entrada do zip — mata a zip bomb clássica.
_MAX_ENTRY_BYTES = 10 * 2**20
# Chars somados de TODOS os capítulos (epub e PDF) — mata a amplificação por repetição.
_MAX_TOTAL_TEXT = 5_000_000
# Capítulos por material — sumário absurdo é recusado antes de abrir qualquer arquivo.
_MAX_CHAPTERS = 500
# Aninhamento do navMap — o percurso é recursivo; sem teto, um NCX hostil de milhares
# de níveis estoura a pilha (RecursionError → 500) em vez de recusa legível.
_MAX_NAV_DEPTH = 64

_CONTAINER_PATH = "META-INF/container.xml"
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_UNTITLED_CHAPTER = "Sem título"
_PDF_SINGLE_CHAPTER = "Documento"

# Tags cujo texto é estrutura/metadado, não leitura.
_SKIPPED_TAGS = frozenset({"script", "style", "head", "title"})
# Tags que separam blocos de leitura — viram quebra de linha no texto puro.
_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"})

_EPUB_UNREADABLE = "O arquivo não parece um epub válido (não abriu como pacote epub)."
_EPUB_NO_TOC = "O epub não traz sumário (NCX) — sem ele não dá para separar os capítulos."
_EPUB_EMPTY = "O epub abriu, mas nenhum capítulo tem texto legível."
_PDF_UNREADABLE = "O arquivo não parece um PDF válido (não abriu para leitura)."
_PDF_EMPTY = "O PDF não tem texto extraível (provavelmente é uma digitalização em imagem)."
_PDF_ENCRYPTED = "O PDF está protegido por senha — envie uma cópia sem proteção."
_ENTRY_TOO_BIG = "Uma parte interna do arquivo é grande demais para processar com segurança."
_TOC_TOO_DEEP = "O sumário do epub tem níveis demais para processar com segurança."
_TOO_MUCH_TEXT = "O material tem texto demais para processar de uma vez — envie um arquivo menor."
_TOO_MANY_CHAPTERS = "O sumário tem capítulos demais para um único material."

# Tamanho do bloco de leitura em streaming das entradas do zip.
_READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ParsedChapter:
    """Um capítulo extraído do material, na ordem de leitura."""

    seq: int
    title: str
    content: str
    part: str | None


@dataclass(frozen=True)
class ParsedMaterial:
    """Resultado do parse: título do material (quando o formato oferece) + capítulos."""

    title: str | None
    chapters: list[ParsedChapter]


class _TextExtractor(HTMLParser):
    """Extrai texto puro de um XHTML de capítulo, preservando as quebras de bloco."""

    def __init__(self) -> None:
        """Inicia o acumulador de texto e o contador de profundidade em tag muda."""
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Entra em modo mudo nas tags de metadado; marca quebra nas tags de bloco."""
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Sai do modo mudo ou marca a quebra de linha ao fechar um bloco."""
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Acumula o texto visível (fora das tags de metadado)."""
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        """Texto acumulado, com quebras normalizadas e sem espaço nas pontas."""
        lines = [line.strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _plain_text(markup: str) -> str:
    """Texto puro de um XHTML de capítulo — as tags somem, os parágrafos ficam."""
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    return extractor.text()


def _parse_xml(raw: bytes, message: str) -> Element:
    """Parseia XML hostil com defusedxml; qualquer falha vira `MaterialParseError`."""
    try:
        parsed: Element = xml_fromstring(raw)
    except Exception as exc:  # noqa: BLE001 — nenhuma exceção crua de parse chega à rota
        raise MaterialParseError(message) from exc
    return parsed


def _guard_chapter_count(count: int) -> None:
    """Recusa um sumário com capítulos demais ANTES de abrir qualquer arquivo."""
    if count > _MAX_CHAPTERS:
        raise MaterialParseError(_TOO_MANY_CHAPTERS)


def _guard_total_text(total: int) -> None:
    """Recusa o material quando o texto acumulado passa do teto de memória."""
    if total > _MAX_TOTAL_TEXT:
        raise MaterialParseError(_TOO_MUCH_TEXT)


def _zip_read(archive: zipfile.ZipFile, name: str, message: str) -> bytes:
    """Lê uma entrada do zip em streaming, abortando se DESCOMPRIMIR acima do teto.

    `archive.read()` materializa tudo antes de qualquer checagem — é o que transforma
    um epub de 0,2 MB numa alocação de gigabytes. O `file_size` do header serve só de
    pré-check barato: ele é escrito por quem montou o zip, então o contador do
    streaming é a cerca de verdade.
    """
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise MaterialParseError(message) from exc
    if info.file_size > _MAX_ENTRY_BYTES:
        raise MaterialParseError(_ENTRY_TOO_BIG)

    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(name) as handle:
            while chunk := handle.read(_READ_CHUNK):
                total += len(chunk)
                if total > _MAX_ENTRY_BYTES:
                    raise MaterialParseError(_ENTRY_TOO_BIG)
                chunks.append(chunk)
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise MaterialParseError(message) from exc
    return b"".join(chunks)


def _opf_path(archive: zipfile.ZipFile) -> str:
    """Caminho do OPF declarado no `META-INF/container.xml`."""
    root = _parse_xml(_zip_read(archive, _CONTAINER_PATH, _EPUB_UNREADABLE), _EPUB_UNREADABLE)
    rootfile = root.find(".//{*}rootfile")
    full_path = rootfile.get("full-path") if rootfile is not None else None
    if not full_path:
        raise MaterialParseError(_EPUB_UNREADABLE)
    return full_path


def _ncx_path(opf: Element, opf_dir: str) -> str:
    """Caminho do sumário NCX declarado no manifest do OPF."""
    for item in opf.findall(".//{*}manifest/{*}item"):
        href = item.get("href")
        if item.get("media-type") == _NCX_MEDIA_TYPE and href:
            return posixpath.normpath(posixpath.join(opf_dir, href))
    raise MaterialParseError(_EPUB_NO_TOC)


def _nav_label(node: Element) -> str:
    """Rótulo de um navPoint (`navLabel/text`), ou o placeholder de capítulo sem título."""
    text_node = node.find("{*}navLabel/{*}text")
    label = (text_node.text or "").strip() if text_node is not None else ""
    return label or _UNTITLED_CHAPTER


def _nav_src(node: Element) -> str:
    """`content/@src` de um navPoint, sem o fragmento (`#âncora`); vazio se ausente."""
    content = node.find("{*}content")
    src = content.get("src", "") if content is not None else ""
    return src.split("#", 1)[0]


def _walk_nav(
    node: Element, part: str | None, out: list[tuple[str, str, str | None]], depth: int = 0
) -> None:
    """Percorre o navMap em profundidade: navPoint FOLHA é capítulo, navPoint PAI vira `part`.

    Um pai compartilha o `src` do primeiro filho (convenção do NCX) — emiti-lo também
    como capítulo duplicaria o texto do filho, então ele só empresta o rótulo.
    """
    if depth > _MAX_NAV_DEPTH:
        raise MaterialParseError(_TOC_TOO_DEEP)
    children = node.findall("{*}navPoint")
    if not children:
        src = _nav_src(node)
        if src:
            out.append((_nav_label(node), src, part))
        return
    label = _nav_label(node)
    for child in children:
        _walk_nav(child, label, out, depth + 1)


def _toc_entries(ncx: Element) -> list[tuple[str, str, str | None]]:
    """Entradas do sumário (título, src, parte) em ordem de leitura."""
    entries: list[tuple[str, str, str | None]] = []
    for nav_point in ncx.findall("{*}navMap/{*}navPoint"):
        _walk_nav(nav_point, None, entries)
    if not entries:
        raise MaterialParseError(_EPUB_NO_TOC)
    _guard_chapter_count(len(entries))
    return entries


def _opf_title(opf: Element) -> str | None:
    """`dc:title` do OPF, ou None quando o pacote não declara título."""
    node = opf.find(".//{*}metadata/{*}title")
    title = (node.text or "").strip() if node is not None else ""
    return title or None


def _epub_chapters(
    archive: zipfile.ZipFile, opf_dir: str, entries: list[tuple[str, str, str | None]]
) -> list[ParsedChapter]:
    """Lê o XHTML de cada entrada do sumário e devolve os capítulos com `seq` 1-based.

    O acumulador de texto entre capítulos é o que pega a amplificação: N navPoints
    apontando para o MESMO arquivo passam individualmente na cerca de entrada e só
    somados revelam o volume.
    """
    chapters: list[ParsedChapter] = []
    total = 0
    for seq, (title, src, part) in enumerate(entries, start=1):
        href = posixpath.normpath(posixpath.join(opf_dir, src))
        markup = _zip_read(archive, href, _EPUB_UNREADABLE).decode("utf-8", errors="replace")
        content = _plain_text(markup)
        total += len(content)
        _guard_total_text(total)
        chapters.append(ParsedChapter(seq=seq, title=title, content=content, part=part))
    return chapters


def _parse_epub(data: bytes) -> ParsedMaterial:
    """Extrai título e capítulos de um epub (container → OPF → NCX → XHTML)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise MaterialParseError(_EPUB_UNREADABLE) from exc

    with archive:
        opf_name = _opf_path(archive)
        opf_dir = posixpath.dirname(opf_name)
        opf = _parse_xml(_zip_read(archive, opf_name, _EPUB_UNREADABLE), _EPUB_UNREADABLE)
        ncx = _parse_xml(_zip_read(archive, _ncx_path(opf, opf_dir), _EPUB_NO_TOC), _EPUB_NO_TOC)
        chapters = _epub_chapters(archive, opf_dir, _toc_entries(ncx))
        material_title = _opf_title(opf)

    if not any(chapter.content for chapter in chapters):
        raise MaterialParseError(_EPUB_EMPTY)
    return ParsedMaterial(title=material_title, chapters=chapters)


def _page_texts(reader: PdfReader) -> list[str]:
    """Texto de cada página; página ilegível vira string vazia em vez de derrubar o parse.

    O acumulado é conferido A CADA página: um PDF com milhares de páginas densas para
    no teto em vez de extrair tudo primeiro e só então descobrir o tamanho.
    """
    texts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 — página hostil não invalida o material inteiro
            text = ""
        total += len(text)
        _guard_total_text(total)
        texts.append(text)
    return texts


def _outline_start(reader: PdfReader, item: Any) -> tuple[str, int] | None:
    """(título, página) de um marcador top-level; None se ele não resolve numa página real."""
    try:
        page = reader.get_destination_page_number(item)
        title = str(item.title or "").strip()
    except Exception:  # noqa: BLE001 — marcador quebrado é ignorado, não é fatal
        return None
    if not title or page is None or not 0 <= page < len(reader.pages):
        return None
    return title, page


def _outline_starts(reader: PdfReader) -> list[tuple[str, int]]:
    """Marcadores top-level do outline como (título, página inicial), na ordem do arquivo."""
    try:
        outline: Any = reader.outline
    except Exception:  # noqa: BLE001 — outline quebrado degrada para capítulo único
        return []
    # Sub-níveis (listas aninhadas) são ignorados: o corte de capítulo é só no topo.
    resolved = (_outline_start(reader, item) for item in outline if not isinstance(item, list))
    return [start for start in resolved if start is not None]


def _pdf_chapters(texts: list[str], starts: list[tuple[str, int]]) -> list[ParsedChapter]:
    """Um capítulo por marcador top-level; sem marcadores, o documento inteiro vira um só."""
    if not starts:
        content = "\n\n".join(text for text in texts if text).strip()
        return [ParsedChapter(seq=1, title=_PDF_SINGLE_CHAPTER, content=content, part=None)]
    _guard_chapter_count(len(starts))
    chapters: list[ParsedChapter] = []
    for seq, (title, start) in enumerate(starts, start=1):
        end = starts[seq][1] if seq < len(starts) else len(texts)
        content = "\n\n".join(text for text in texts[start:end] if text).strip()
        chapters.append(ParsedChapter(seq=seq, title=title, content=content, part=None))
    return chapters


def _parse_pdf(data: bytes) -> ParsedMaterial:
    """Extrai título e capítulos de um PDF (outline top-level quando existe)."""
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception as exc:  # noqa: BLE001 — entrada hostil: erro cru nunca chega à rota
        raise MaterialParseError(_PDF_UNREADABLE) from exc
    if encrypted:
        raise MaterialParseError(_PDF_ENCRYPTED)

    chapters = _pdf_chapters(_page_texts(reader), _outline_starts(reader))
    if not any(chapter.content for chapter in chapters):
        raise MaterialParseError(_PDF_EMPTY)

    metadata = reader.metadata
    title = (metadata.title or "").strip() if metadata is not None else ""
    return ParsedMaterial(title=title or None, chapters=chapters)


def parse_material(data: bytes, fmt: MaterialFormat) -> ParsedMaterial:
    """Extrai título e capítulos de um material epub/PDF.

    Levanta `MaterialParseError` quando os bytes não são do formato declarado,
    quando o epub não tem sumário, ou quando o resultado não tem nenhum capítulo
    com conteúdo.
    """
    if fmt == "epub":
        return _parse_epub(data)
    return _parse_pdf(data)
