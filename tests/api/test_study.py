"""Tela de Estudos (ADR-0043, KUBO-135): lista de Materiais, envio e conferência.

Unit com a store mockada — o COMPORTAMENTO de persistência vive no
`tests/store/test_study.py` (integração); aqui ficam o MOLDE da rota de escrita
(CSRF, validação na borda, limite de tamanho, erro de parse) e a leitura.

O arquivo enviado NUNCA é gravado com o nome que o usuário mandou: o teste do
caminho feliz prova que o path vem do id gerado, não do filename (path traversal).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import MaterialParseError, StoreError
from kubo.store.study import Material
from kubo.study.parsing import ParsedChapter, ParsedMaterial

_MATERIAL_ID = RecordID("material", "abc123")
_EPUB_BYTES = b"PK\x03\x04-epub-de-mentira"


def _material(**kw: object) -> Material:
    base: dict[str, object] = {
        "id": _MATERIAL_ID,
        "tenant_id": RecordID("tenant", "breakglass"),
        "user_id": RecordID("user", "breakglass-owner"),
        "title": "Manual de Kubo",
        "fmt": "epub",
        "original_filename": "manual.epub",
        "file_path": "/data/materials/t/u/abc123.epub",
        "size_bytes": 1024,
        "chapter_count": 2,
        "created_at": datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Material(**base)  # type: ignore[arg-type]


def _parsed() -> ParsedMaterial:
    return ParsedMaterial(
        title="Manual de Kubo",
        chapters=[ParsedChapter(seq=1, title="Capítulo Um", content="conteúdo", part=None)],
    )


@pytest.fixture(autouse=True)
def stub_study_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Leituras vazias por padrão e volume de materiais apontado para o tmp do teste."""
    monkeypatch.setenv("KUBO_MATERIALS_DIR", str(tmp_path))
    # Escrita usa connect_rw (ADR-0018); no unit não há KUBO_RW_SURREAL_PASS, então o
    # stub entra ANTES de rw_config() explodir — 503 de env ausente é caso do
    # test_sources.py, não deste arquivo.
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_material", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chapters", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.count_chapters", lambda db, **kw: 0)


@pytest.fixture
def parse_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Espiona `parse_material`: a lista fica VAZIA se a rota recusou antes de parsear.

    A ordem de validação (extensão → tamanho → parse) é comportamento, não detalhe: um
    arquivo de 50MB recusado só DEPOIS do parse já custou a memória que o limite existe
    para não gastar."""
    calls: list[str] = []

    def _spy(data: bytes, fmt: str) -> ParsedMaterial:
        calls.append(fmt)
        return _parsed()

    monkeypatch.setattr("kubo.api.routes.study.parse_material", _spy)
    return calls


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form de envio de Material."""
    html = authed_client.get("/study/materials").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


def test_materials_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/study/materials", follow_redirects=False).status_code == 303


def test_materials_page_shows_upload_form(authed_client: TestClient) -> None:
    """A tela traz o form de envio aceitando só epub/PDF."""
    resp = authed_client.get("/study/materials")

    assert resp.status_code == 200
    assert 'type="file"' in resp.text
    assert ".epub" in resp.text
    assert ".pdf" in resp.text


def test_materials_page_lists_materials(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cada material aparece com título e número de capítulos."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [_material()]
    )

    html = authed_client.get("/study/materials").text

    assert "Manual de Kubo" in html
    assert "2 capítulos" in html


def test_upload_rejects_missing_csrf(authed_client: TestClient) -> None:
    """POST sem CSRF é 403 — nada é lido nem gravado."""
    resp = authed_client.post(
        "/study/materials",
        files={"file": ("livro.epub", _EPUB_BYTES, "application/epub+zip")},
        follow_redirects=False,
    )

    assert resp.status_code == 403


def test_upload_rejects_unsupported_extension(
    authed_client: TestClient, parse_spy: list[str]
) -> None:
    """Extensão fora de epub/pdf volta 400 SEM chegar ao parse."""
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/study/materials",
        files={"file": ("anotacoes.txt", b"texto solto", "text/plain")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert parse_spy == []


def test_upload_rejects_file_above_size_limit(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, parse_spy: list[str]
) -> None:
    """Arquivo acima do teto configurado volta 400 antes do parse."""
    monkeypatch.setenv("KUBO_MATERIAL_MAX_MB", "1")
    csrf = _csrf(authed_client)
    oversized = b"x" * (2 * 1024 * 1024)

    resp = authed_client.post(
        "/study/materials",
        files={"file": ("grande.epub", oversized, "application/epub+zip")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert parse_spy == []


def test_upload_reports_parse_error_without_persisting(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Material ilegível volta 400 com a mensagem do erro — nada gravado no volume."""

    def _boom(data: bytes, fmt: str) -> ParsedMaterial:
        raise MaterialParseError("O arquivo não parece um epub válido.")

    monkeypatch.setattr("kubo.api.routes.study.parse_material", _boom)

    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/materials",
        files={"file": ("quebrado.epub", _EPUB_BYTES, "application/epub+zip")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "epub válido" in resp.text
    assert list(tmp_path.rglob("*.epub")) == []


def test_upload_persists_and_redirects_to_review(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Caminho feliz: grava o arquivo com nome derivado do id e redireciona (PRG)."""
    monkeypatch.setattr("kubo.api.routes.study.parse_material", lambda data, fmt: _parsed())
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_material", lambda db, **kw: _material()
    )

    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/materials",
        files={"file": ("../../etc/passwd.epub", _EPUB_BYTES, "application/epub+zip")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    saved = list(tmp_path.rglob("*.epub"))
    assert len(saved) == 1
    assert "passwd" not in str(saved[0])


def test_upload_removes_saved_file_when_store_fails(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Store falhando DEPOIS de gravar o arquivo não deixa órfão no volume.

    O arquivo é gravado antes da escrita no banco; sem a remoção no caminho de erro, cada
    falha de store deixaria um material sem registro — lixo invisível que só cresce."""

    def _explode(db: object, **kw: object) -> Material:
        raise StoreError("transação revertida")

    monkeypatch.setattr("kubo.api.routes.study.parse_material", lambda data, fmt: _parsed())
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_material", _explode)

    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/materials",
        files={"file": ("livro.epub", _EPUB_BYTES, "application/epub+zip")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 500
    assert list(tmp_path.rglob("*.epub")) == []


def test_detail_returns_404_for_unknown_material(authed_client: TestClient) -> None:
    """Material inexistente (ou de outro usuário) é 404, não 500."""
    resp = authed_client.get("/study/materials/nao-existe")

    assert resp.status_code == 404


def test_upload_denies_without_session_before_parsing(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, parse_spy: list[str]
) -> None:
    """Sem sessão resolvível, o 403 vem ANTES de ler/parsear o corpo.

    Autorização depois do parse significa gastar memória a mando de quem não passou
    no gate — a ordem é a defesa, não um detalhe de escrita."""
    csrf = _csrf(authed_client)
    monkeypatch.setattr("kubo.api.routes.study.resolve_session", lambda request, db: None)

    resp = authed_client.post(
        "/study/materials",
        files={"file": ("livro.epub", _EPUB_BYTES, "application/epub+zip")},
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert parse_spy == []


def test_upload_rejects_declared_content_length_above_limit(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, parse_spy: list[str]
) -> None:
    """Content-Length declarando corpo acima do teto é 400 sem sequer ler o arquivo."""
    monkeypatch.setenv("KUBO_MATERIAL_MAX_MB", "1")
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/study/materials",
        files={"file": ("grande.epub", _EPUB_BYTES, "application/epub+zip")},
        data={"csrf": csrf},
        headers={"Content-Length": str(50 * 1024 * 1024)},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert parse_spy == []


def test_upload_removes_saved_file_on_unexpected_error(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Falha INESPERADA (não ConfigError/StoreError) também não deixa órfão no volume.

    A limpeza não pode depender do catálogo de exceções conhecidas: qualquer erro
    depois da gravação deixaria um arquivo sem registro, lixo que só cresce."""

    def _boom(db: object, **kw: object) -> Material:
        raise RuntimeError("falha inesperada na store")

    monkeypatch.setattr("kubo.api.routes.study.parse_material", lambda data, fmt: _parsed())
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_material", _boom)
    csrf = _csrf(authed_client)

    with pytest.raises(RuntimeError):
        authed_client.post(
            "/study/materials",
            files={"file": ("livro.epub", _EPUB_BYTES, "application/epub+zip")},
            data={"csrf": csrf},
            follow_redirects=False,
        )

    assert list(tmp_path.rglob("*.epub")) == []
