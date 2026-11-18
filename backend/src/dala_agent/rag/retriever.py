"""Гибридный поиск по kb: вектор (pgvector, косинус) + полнотекстовый (tsvector) с объединением RRF.

Запрос фиксирован в коде и параметризован; выполняется сервисным подключением (у роли агента
для text-to-SQL доступа к kb нет).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg

from ..embeddings import Embedder, to_pgvector

RRF_K = 60

# Служебные слова не помогают полнотекстовому поиску (ru + kk).
STOPWORDS = frozenset(
    """
    как какая какой какие каков какова что это для при без или над под про его она они оно мне нам вам
    есть ли так уже где когда если чтобы можно нужно надо будет было был была были все всё весь этот эта
    эти того тот там тут даже только тоже ещё еще очень наш ваш свой мой сколько который которая
    және мен бен пен үшін деген қандай қалай қанша бұл осы сол бар жоқ ма ме ба бе па пе
    """.split()
)


@dataclass
class RetrievedChunk:
    chunk_id: int
    slug: str
    title: str
    doc_type: str
    language: str
    trust_level: str
    effective_date: str | None
    section: str | None
    heading: str | None
    content: str
    score: float
    vector_similarity: float | None
    lexical_hit: bool

    @property
    def citation(self) -> str:
        return f"{self.title}, п. {self.section}" if self.section else self.title

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "section": self.section,
            "heading": self.heading,
            "trust_level": self.trust_level,
            "score": round(self.score, 4),
            "vector_similarity": None if self.vector_similarity is None else round(self.vector_similarity, 4),
            "lexical_hit": self.lexical_hit,
            "preview": self.content[:200],
        }


def lexical_terms(text: str) -> list[str]:
    """Значимые слова → термы tsquery для конфигурации simple (по префиксу).

    Префикс — грубая замена стемминга, которой хватает для русского и казахского: длинные слова — первые
    5 букв («скидка», «скидку», «скидки» → скидк:*), слова из 4–5 букв — без последней («цены» → цен:*).
    """
    terms: list[str] = []
    for token in re.findall(r"\w+", text.lower()):
        if token in STOPWORDS or (len(token) < 3 and not token.isdigit()):
            continue
        if token.isdigit() or len(token) <= 3:
            term = token
        elif len(token) <= 5:
            term = f"{token[:-1]}:*"
        else:
            term = f"{token[:5]}:*"
        if term not in terms:
            terms.append(term)
    return terms


def lexical_query(text: str) -> str | None:
    """Вопрос → tsquery: значимые слова через OR."""
    terms = lexical_terms(text)
    return " | ".join(terms) if terms else None


class Retriever:
    def __init__(self, dsn: str, embedder: Embedder, *, candidates: int = 20):
        self._dsn = dsn
        self.embedder = embedder
        self._candidates = candidates

    def search(self, query: str, *, extra: str = "", top_k: int = 6) -> list[RetrievedChunk]:
        """query — вопрос пользователя; extra — ключевые слова от роутера (синонимы языком регламентов).

        Полнотекстовая часть ранжирует по охвату: сколько разных слов запроса нашлось в пункте, причём
        слова самого вопроса весят вдвое больше синонимов — иначе широкая переформулировка уводит поиск.
        """
        q_terms = lexical_terms(query)
        x_terms = [t for t in lexical_terms(extra) if t not in q_terms]
        tsq = " | ".join(q_terms + x_terms) or None
        params = {
            "vec": to_pgvector(self.embedder.embed_query(f"{query} {extra}".strip())),
            "tsq": tsq or "",
            "qterms": q_terms,
            "xterms": x_terms,
            "k": self._candidates,
            "top": top_k,
            "rrf": RRF_K,
        }
        lex_cte = (
            """
            lex_raw AS (
                -- к тексту пункта добавляем название документа: «регламент», «письмо поставщика» и т.п.
                SELECT c.id,
                       2 * (SELECT count(*) FROM unnest(%(qterms)s::text[]) w
                            WHERE (c.tsv || dt.tsv) @@ to_tsquery('simple', w))
                       + (SELECT count(*) FROM unnest(%(xterms)s::text[]) w
                          WHERE (c.tsv || dt.tsv) @@ to_tsquery('simple', w)) AS cover,
                       ts_rank_cd(c.tsv || dt.tsv, q.t) AS rank
                FROM kb.chunks c
                JOIN (SELECT id, to_tsvector('simple', title) AS tsv FROM kb.documents) dt
                  ON dt.id = c.document_id, q
                WHERE (c.tsv || dt.tsv) @@ q.t
            ),
            lex AS (
                SELECT id, row_number() OVER (ORDER BY cover DESC, rank DESC) AS rnk
                FROM lex_raw ORDER BY cover DESC, rank DESC LIMIT %(k)s
            )"""
            if tsq
            else "lex AS (SELECT NULL::bigint AS id, NULL::bigint AS rnk WHERE false)"
        )
        sql = f"""
            WITH q AS (
                SELECT %(vec)s::vector AS v, to_tsquery('simple', %(tsq)s) AS t
            ),
            vec AS (
                SELECT c.id,
                       row_number() OVER (ORDER BY c.embedding <=> q.v) AS rnk,
                       1 - (c.embedding <=> q.v) AS sim
                FROM kb.chunks c, q
                WHERE c.embedding IS NOT NULL
                ORDER BY c.embedding <=> q.v
                LIMIT %(k)s
            ),
            {lex_cte}
            SELECT c.id, d.slug, d.title, d.doc_type, d.language, d.trust_level, d.effective_date::text,
                   c.section, c.heading, c.content,
                   COALESCE(1.0 / (%(rrf)s + vec.rnk), 0) + COALESCE(1.0 / (%(rrf)s + lex.rnk), 0) AS score,
                   vec.sim, lex.rnk IS NOT NULL
            FROM (SELECT id FROM vec UNION SELECT id FROM lex) ids
            JOIN kb.chunks c ON c.id = ids.id
            JOIN kb.documents d ON d.id = c.document_id
            LEFT JOIN vec ON vec.id = c.id
            LEFT JOIN lex ON lex.id = c.id
            ORDER BY score DESC, c.id
            LIMIT %(top)s
        """
        with psycopg.connect(self._dsn, application_name="dala-agent-rag") as conn:
            conn.read_only = True
            rows = conn.execute(sql, params).fetchall()
        return [
            RetrievedChunk(
                chunk_id=r[0], slug=r[1], title=r[2], doc_type=r[3], language=r[4], trust_level=r[5],
                effective_date=r[6], section=r[7], heading=r[8], content=r[9], score=float(r[10]),
                vector_similarity=None if r[11] is None else float(r[11]), lexical_hit=bool(r[12]),
            )
            for r in rows
        ]  # fmt: skip
