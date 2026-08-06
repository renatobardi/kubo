"""Contrato da store de perfil do usuário (ADR-0045, KUBO-148).

Integração (SurrealDB real): user_profile e theme no membership.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import MembershipRequiredError, StoreError
from kubo.store import client, migrations, tenancy

pytestmark = pytest.mark.integration


_PROFILE_DB = "test_profile"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_PROFILE_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_PROFILE_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_PROFILE_DB};")


def test_user_profile_is_created_on_first_update(db: Any) -> None:
    """update_user_profile cria o perfil se ele ainda não existe."""
    user = tenancy.create_user(db, firebase_uid="uid-1", email="a@example.com")

    profile = tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
    )

    assert profile.display_name == "Renato"
    assert profile.language == "pt-BR"
    assert profile.timezone == "America/Sao_Paulo"
    assert profile.user == user.id


def test_user_profile_is_updated_on_second_call(db: Any) -> None:
    """update_user_profile atualiza o perfil existente."""
    user = tenancy.create_user(db, firebase_uid="uid-2", email="b@example.com")
    tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
    )

    profile = tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Bardi",
        language="en-US",
        timezone="UTC",
        work_context="Arquiteto de plataforma.",
    )

    assert profile.display_name == "Bardi"
    assert profile.language == "en-US"
    assert profile.timezone == "UTC"
    assert profile.work_context == "Arquiteto de plataforma."
    assert tenancy.get_user_profile(db, user.id) == profile


def test_get_user_profile_missing_returns_none(db: Any) -> None:
    """Perfil inexistente retorna None."""
    user = tenancy.create_user(db, firebase_uid="uid-none", email="none@example.com")
    assert tenancy.get_user_profile(db, user.id) is None


def test_update_user_profile_persists_work_context(db: Any) -> None:
    """work_context é parte do user_profile e volta em get_user_profile."""
    user = tenancy.create_user(db, firebase_uid="uid-ctx", email="ctx@example.com")

    profile = tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="  Arquiteto de plataforma.  ",
    )

    assert profile.work_context == "Arquiteto de plataforma."
    loaded = tenancy.get_user_profile(db, user.id)
    assert loaded is not None
    assert loaded.work_context == "Arquiteto de plataforma."


def test_update_user_profile_clears_empty_work_context(db: Any) -> None:
    """Enviar work_context vazio apaga o campo."""
    user = tenancy.create_user(db, firebase_uid="uid-clear", email="clear@example.com")
    tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="Contexto antigo.",
    )

    profile = tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="",
    )

    assert profile.work_context is None


def test_update_user_profile_preserves_work_context_when_omitted(db: Any) -> None:
    """Omitir work_context (default _UNSET) preserva o valor existente — não apaga."""
    user = tenancy.create_user(db, firebase_uid="uid-keep", email="keep@example.com")
    tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="Arquiteto de plataforma.",
    )

    profile = tenancy.update_user_profile(
        db,
        user_id=user.id,
        display_name="Renato Bardi",
        language="en-US",
        timezone="UTC",
        # work_context omitido intencionalmente
    )

    assert profile.display_name == "Renato Bardi"
    assert profile.work_context == "Arquiteto de plataforma."


def test_update_user_profile_rejects_long_work_context(db: Any) -> None:
    """work_context acima de 4000 caracteres é recusado."""
    user = tenancy.create_user(db, firebase_uid="uid-long-ctx", email="long-ctx@example.com")

    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=user.id,
            display_name="Renato",
            language="pt-BR",
            timezone="America/Sao_Paulo",
            work_context="x" * (tenancy.MAX_WORK_CONTEXT_LENGTH + 1),
        )


def test_update_user_profile_rejects_empty_display_name(db: Any) -> None:
    """Nome vazio ou só espaços é recusado."""
    user = tenancy.create_user(db, firebase_uid="uid-empty", email="empty@example.com")

    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=user.id,
            display_name="   ",
            language="pt-BR",
            timezone="America/Sao_Paulo",
        )


def test_update_user_profile_rejects_long_display_name(db: Any) -> None:
    """Nome acima de 64 caracteres é recusado."""
    user = tenancy.create_user(db, firebase_uid="uid-long", email="long@example.com")

    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=user.id,
            display_name="x" * 65,
            language="pt-BR",
            timezone="America/Sao_Paulo",
        )


def test_update_user_profile_rejects_missing_user(db: Any) -> None:
    """Tentar atualizar perfil de um user que não existe recusa."""
    from surrealdb import RecordID

    missing_user_id = RecordID("user", "não-existe")
    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=missing_user_id,
            display_name="Renato",
            language="pt-BR",
            timezone="America/Sao_Paulo",
        )


def test_update_user_profile_rejects_invalid_language(db: Any) -> None:
    """Language outside BCP 47 format is rejected."""
    user = tenancy.create_user(db, firebase_uid="uid-lang", email="lang@example.com")

    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=user.id,
            display_name="Renato",
            language="not_a_tag!",
            timezone="America/Sao_Paulo",
        )


def test_update_user_profile_rejects_invalid_timezone(db: Any) -> None:
    """Timezone outside the IANA database is rejected."""
    user = tenancy.create_user(db, firebase_uid="uid-tz", email="tz@example.com")

    with pytest.raises(StoreError):
        tenancy.update_user_profile(
            db,
            user_id=user.id,
            display_name="Renato",
            language="pt-BR",
            timezone="Not/AZone",
        )


def test_membership_theme_defaults_to_system(db: Any) -> None:
    """Membership nova já carrega o tema 'system' por padrão."""
    user = tenancy.create_user(db, firebase_uid="uid-theme", email="theme@example.com")
    tenant = tenancy.create_tenant(db, name="Tema", owner_user_id=user.id)

    membership = tenancy.get_membership(db, user_id=user.id, tenant_id=tenant.id)
    assert membership is not None
    assert membership.theme == "system"


def test_update_membership_theme(db: Any) -> None:
    """O dono pode alterar o tema da própria membership."""
    user = tenancy.create_user(db, firebase_uid="uid-owner-theme", email="ot@example.com")
    tenant = tenancy.create_tenant(db, name="Tenant T", owner_user_id=user.id)

    updated = tenancy.update_membership_theme(
        db, user_id=user.id, tenant_id=tenant.id, theme="dark"
    )
    assert updated.theme == "dark"

    loaded = tenancy.get_membership(db, user_id=user.id, tenant_id=tenant.id)
    assert loaded is not None
    assert loaded.theme == "dark"


def test_update_membership_theme_rejects_invalid(db: Any) -> None:
    """Valores fora de light/dark/system são recusados."""
    user = tenancy.create_user(db, firebase_uid="uid-bad-theme", email="bad@example.com")
    tenant = tenancy.create_tenant(db, name="Tenant B", owner_user_id=user.id)

    with pytest.raises(StoreError):
        tenancy.update_membership_theme(db, user_id=user.id, tenant_id=tenant.id, theme="blue")


def test_update_membership_theme_rejects_non_member(db: Any) -> None:
    """Um user que não pertence ao tenant não pode alterar tema lá."""
    user = tenancy.create_user(db, firebase_uid="uid-out-theme", email="out@example.com")
    other = tenancy.create_user(db, firebase_uid="uid-owner-2", email="owner2@example.com")
    tenant = tenancy.create_tenant(db, name="Tenant C", owner_user_id=other.id)

    with pytest.raises(MembershipRequiredError):
        tenancy.update_membership_theme(db, user_id=user.id, tenant_id=tenant.id, theme="dark")


def test_get_tenant_work_context_reads_owner_profile(db: Any) -> None:
    """get_tenant_work_context lê o work_context do DONO do tenant (ADR-0051 §I.1/
    Nota de compatibilidade) — a nota é do tenant, ancorada em quem administra o
    workspace, não em cada membro."""
    owner = tenancy.create_user(db, firebase_uid="uid-wc-owner", email="wc@example.com")
    tenant = tenancy.create_tenant(db, name="WC Tenant", owner_user_id=owner.id)
    tenancy.update_user_profile(
        db,
        user_id=owner.id,
        display_name="Dono",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="Curte IA aplicada e infra.",
    )

    assert tenancy.get_tenant_work_context(db, tenant.id) == "Curte IA aplicada e infra."


def test_get_tenant_work_context_empty_when_owner_has_no_profile(db: Any) -> None:
    """Dono sem perfil ainda: contexto vazio, não erro — a nota roda com ou sem
    alavanca de curadoria (ADR-0051 §I.1)."""
    owner = tenancy.create_user(db, firebase_uid="uid-wc-noprofile", email="np@example.com")
    tenant = tenancy.create_tenant(db, name="No Profile Tenant", owner_user_id=owner.id)

    assert tenancy.get_tenant_work_context(db, tenant.id) == ""


def test_get_tenant_work_context_empty_when_work_context_unset(db: Any) -> None:
    """Dono com perfil mas sem work_context preenchido: contexto vazio."""
    owner = tenancy.create_user(db, firebase_uid="uid-wc-unset", email="unset@example.com")
    tenant = tenancy.create_tenant(db, name="Unset Tenant", owner_user_id=owner.id)
    tenancy.update_user_profile(
        db, user_id=owner.id, display_name="Dono", language="pt-BR", timezone="UTC"
    )

    assert tenancy.get_tenant_work_context(db, tenant.id) == ""
