"""Проверка документов базы знаний: метаданные, пометка «демо», нумерация пунктов для ссылок."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

DOCS_ROOT = Path(__file__).resolve().parents[2] / "data"
DOCS = sorted((DOCS_ROOT / "documents").glob("*.md")) + sorted(
    p for p in (DOCS_ROOT / "documents_adversarial").glob("*.md") if p.name != "README.md"
)
REQUIRED = {"slug", "title", "doc_type", "effective_date", "language", "trust_level", "owner", "demo"}


def front_matter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, f"{path.name}: нет front matter"
    return yaml.safe_load(m.group(1)), m.group(2)


def test_documents_count():
    assert 5 <= len(list((DOCS_ROOT / "documents").glob("*.md"))) <= 8


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_metadata(path):
    meta, body = front_matter(path)
    assert REQUIRED <= meta.keys()
    assert meta["demo"] is True
    assert meta["language"] in ("ru", "kk")
    assert meta["trust_level"] in ("internal", "external")
    assert "ДЕМО" in body
    if meta["trust_level"] == "internal":
        assert re.search(r"^\d+\.\d+\.", body, re.M), "нужны пронумерованные пункты для ссылок"


def test_slugs_unique():
    slugs = [front_matter(p)[0]["slug"] for p in DOCS]
    assert len(slugs) == len(set(slugs))


def test_adversarial_docs_are_external():
    for p in DOCS:
        meta, _ = front_matter(p)
        if meta.get("test_purpose"):
            assert meta["trust_level"] == "external"
