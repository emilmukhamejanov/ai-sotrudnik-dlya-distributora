"""Индексация документов в kb: python -m dala_agent.rag.indexer [--force] [файлы…]

Документ пропускается, если не изменился (sha256) и проиндексирован той же моделью эмбеддингов.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ..config import get_settings
from ..embeddings import Embedder, build_embedder, to_pgvector
from .documents import KBDocument, default_document_paths, parse_document

log = logging.getLogger(__name__)


@dataclass
class IndexReport:
    indexed: list[str]
    skipped: list[str]
    chunks: int


def index_documents(
    dsn: str, embedder: Embedder, paths: list[Path] | None = None, *, force: bool = False
) -> IndexReport:
    docs = [parse_document(p) for p in (paths or default_document_paths())]
    report = IndexReport(indexed=[], skipped=[], chunks=0)
    with psycopg.connect(dsn, application_name="dala-agent-indexer") as conn:
        for doc in docs:
            row = conn.execute(
                "SELECT sha256, embedding_model FROM kb.documents WHERE slug = %s", (doc.slug,)
            ).fetchone()
            if row and not force and row[0] == doc.sha256 and row[1] == embedder.name:
                report.skipped.append(doc.slug)
                continue
            _upsert(conn, doc, embedder)
            conn.commit()
            report.indexed.append(doc.slug)
            report.chunks += len(doc.chunks)
            log.info("Проиндексирован %s: %d чанков", doc.slug, len(doc.chunks))
    return report


def _embedding_text(doc: KBDocument, heading: str | None, content: str) -> str:
    return " ".join(x for x in (doc.title, heading, content) if x)


def _upsert(conn: psycopg.Connection, doc: KBDocument, embedder: Embedder) -> None:
    vectors = embedder.embed_documents([_embedding_text(doc, c.heading, c.content) for c in doc.chunks])
    doc_id = conn.execute(
        """
        INSERT INTO kb.documents (slug, title, doc_type, version, effective_date, language, trust_level,
                                  source_path, sha256, embedding_model, indexed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (slug) DO UPDATE SET
            title = EXCLUDED.title, doc_type = EXCLUDED.doc_type, version = EXCLUDED.version,
            effective_date = EXCLUDED.effective_date, language = EXCLUDED.language,
            trust_level = EXCLUDED.trust_level, source_path = EXCLUDED.source_path,
            sha256 = EXCLUDED.sha256, embedding_model = EXCLUDED.embedding_model, indexed_at = now()
        RETURNING id
        """,
        (
            doc.slug, doc.title, doc.doc_type, doc.version, doc.effective_date, doc.language,
            doc.trust_level, doc.source_path, doc.sha256, embedder.name,
        ),
    ).fetchone()[0]  # fmt: skip
    conn.execute("DELETE FROM kb.chunks WHERE document_id = %s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO kb.chunks (document_id, chunk_index, section, heading, content, token_count, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            """,
            [
                (doc_id, c.index, c.section, c.heading, c.content, c.token_estimate, to_pgvector(v))
                for c, v in zip(doc.chunks, vectors, strict=True)
            ],
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Индексация документов в базу знаний kb")
    parser.add_argument("paths", nargs="*", type=Path, help="markdown-файлы (по умолчанию data/documents*)")
    parser.add_argument("--force", action="store_true", help="переиндексировать даже без изменений")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    report = index_documents(settings.service_dsn(), build_embedder(settings), args.paths or None, force=args.force)
    print(
        f"Проиндексировано документов: {len(report.indexed)} ({report.chunks} чанков), "
        f"без изменений: {len(report.skipped)}"
    )


if __name__ == "__main__":
    main()
