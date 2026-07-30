"""Parser de Material de estudo (ADR-0043): epub e PDF → capítulos em ordem de leitura.

Função PURA: recebe bytes, devolve estrutura. Sem banco, sem rede, sem disco.
Material enviado pelo dono é entrada externa hostil (CLAUDE.md §Segurança): todo
parse de XML passa por `defusedxml`, e falha de formato vira `MaterialParseError`
legível — nunca uma exceção crua da biblioteca vazando para a rota.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MaterialFormat = Literal["epub", "pdf"]


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


def parse_material(data: bytes, fmt: MaterialFormat) -> ParsedMaterial:
    """Extrai título e capítulos de um material epub/PDF.

    Levanta `MaterialParseError` quando os bytes não são do formato declarado,
    quando o epub não tem sumário, ou quando o resultado não tem nenhum capítulo
    com conteúdo.
    """
    raise NotImplementedError
