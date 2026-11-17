"""Разбор markdown-документов базы знаний: метаданные из front matter и чанки по пунктам.

Один пронумерованный пункт («2.1. …» вместе с таблицей под ним) = один чанк. Так у каждого
фрагмента есть номер пункта, и агент может сослаться на него в ответе. Текст без нумерации
(входящие письма) режется по абзацам.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import REPO_ROOT

CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)+)\.\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
MAX_UNNUMBERED_CHARS = 900


@dataclass
class Chunk:
    index: int
    section: str | None
    heading: str | None
    content: str

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content) // 4)


@dataclass
class KBDocument:
    slug: str
    title: str
    doc_type: str
    version: str | None
    effective_date: str | None
    language: str
    trust_level: str
    source_path: str
    sha256: str
    chunks: list[Chunk] = field(default_factory=list)


def default_document_paths(root: Path = REPO_ROOT / "data") -> list[Path]:
    paths = sorted((root / "documents").glob("*.md"))
    paths += sorted(p for p in (root / "documents_adversarial").glob("*.md") if p.name != "README.md")
    return paths


def parse_document(path: Path) -> KBDocument:
    raw = path.read_text(encoding="utf-8")
    m = FRONT_MATTER_RE.match(raw)
    if not m:
        raise ValueError(f"{path.name}: нет front matter")
    meta = yaml.safe_load(m.group(1))
    try:
        source = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        source = path.name
    doc = KBDocument(
        slug=meta["slug"],
        title=meta["title"],
        doc_type=meta["doc_type"],
        version=None if meta.get("version") is None else str(meta["version"]),
        effective_date=None if meta.get("effective_date") is None else str(meta["effective_date"]),
        language=meta["language"],
        trust_level=meta["trust_level"],
        source_path=source,
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )
    doc.chunks = split_into_chunks(m.group(2))
    return doc


def split_into_chunks(body: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading: str | None = None
    section: str | None = None
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        if section is None and len(text) > MAX_UNNUMBERED_CHARS:
            for part in _split_paragraphs(text):
                chunks.append(Chunk(len(chunks), None, heading, part))
        else:
            chunks.append(Chunk(len(chunks), section, heading, text))

    for line in body.splitlines():
        if line.startswith("> ДЕМО") or line.startswith("> ДЕМО-ҚҰЖАТ"):
            continue  # пометка о демо есть в каждом документе — в поиске она только шум
        h = HEADING_RE.match(line)
        if h:
            flush()
            section = None
            if len(h.group(1)) >= 2:
                heading = h.group(2).strip()
            continue
        c = CLAUSE_RE.match(line)
        if c:
            flush()
            section = c.group(1)
        buf.append(line)
    flush()
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > MAX_UNNUMBERED_CHARS:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts
