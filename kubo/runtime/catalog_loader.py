"""Generic loaders for declarative catalog items (personas, integrations, templates).

Used by `kubo/runtime/personas.py`, `integrations.py` and `flow_templates.py` to
avoid repeating the same YAML/DB loading pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol, Self, TypeVar

import yaml
from pydantic import ValidationError

from kubo.errors import ConfigError, format_validation_error


class _NamedModel(Protocol):
    """Pydantic catalog model with a name and model_validate."""

    name: str

    @classmethod
    def model_validate(cls, obj: Any, *, strict: bool | None = None) -> Self: ...


T = TypeVar("T", bound=_NamedModel)


def load_yaml_item(path: Path, model_cls: type[T], kind_label: str) -> T:
    """Load and validate a single YAML catalog item."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{kind_label} {path.name}: YAML is not a mapping")
    try:
        return model_cls.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"{kind_label} {path.name} invalid: {format_validation_error(exc)}"
        ) from exc


def load_items_from_dir(catalog_dir: Path, model_cls: type[T], kind_label: str) -> dict[str, T]:
    """Load all YAML catalog items from a directory, keyed by item name."""
    catalog: dict[str, T] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        item = load_yaml_item(path, model_cls, kind_label)
        if item.name in catalog:
            raise ConfigError(f"{kind_label} '{item.name}' declared in more than one file")
        catalog[item.name] = item
    return catalog


def load_items_from_db(
    db: Any,
    tenant_id: Any,
    user_id: Any,
    list_fn: Callable[..., list[dict[str, Any]]],
    model_cls: type[T],
    kind_label: str,
) -> dict[str, T]:
    """Load catalog items for a tenant from the DB, keyed by item name."""
    rows = list_fn(db, tenant_id=tenant_id, user_id=user_id)
    catalog: dict[str, T] = {}
    for row in rows:
        try:
            item = model_cls.model_validate(row)
        except ValidationError as exc:
            raise ConfigError(
                f"{kind_label} {row.get('name')!r} invalid: {format_validation_error(exc)}"
            ) from exc
        catalog[item.name] = item
    return catalog
