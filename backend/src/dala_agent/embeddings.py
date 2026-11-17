"""Эмбеддинги для RAG. Размерность 1024 зафиксирована схемой kb (vector(1024))."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from .config import Settings

DIM = 1024


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Офлайн-заглушка без ключей и сети: хэширование символьных n-грамм и «основ» слов.

    Это лексический поиск в векторной форме — словоформы («скидка» / «скидку») сближаются через
    общие n-граммы, синонимы — нет. Для демо без ключей достаточно; для качества — bge-m3 или voyage.
    """

    name = "hashing-ngram-1024"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        for token in re.findall(r"\w+", text.lower()):
            features = [("w", token[:6], 2.0)]
            padded = f"#{token}#"
            features += [("g", padded[i : i + 3], 1.0) for i in range(max(1, len(padded) - 2))]
            for kind, feat, weight in features:
                h = hashlib.blake2b(f"{kind}:{feat}".encode(), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % DIM
                vec[idx] += weight if h[4] & 1 else -weight
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class OpenAICompatibleEmbedder:
    """OpenAI-совместимый /embeddings: bge-m3 (vLLM, TEI, Ollama) или text-embedding-3-* c dimensions=1024."""

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None, batch_size: int = 64):
        import openai

        self.name = model
        self._client = openai.OpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self._batch = batch_size
        self._extra = {"dimensions": DIM} if model.startswith("text-embedding-3") else {}

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            resp = self._client.embeddings.create(model=self.name, input=texts[i : i + self._batch], **self._extra)
            out += [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        _check_dim(out, self.name)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, *, api_key: str, model: str = "voyage-3.5", batch_size: int = 64, timeout_s: float = 30.0):
        self.name = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._batch = batch_size
        self._timeout = timeout_s

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            payload = {
                "input": texts[i : i + self._batch],
                "model": self.name,
                "input_type": input_type,
                "output_dimension": DIM,
            }
            resp = httpx.post(self.URL, json=payload, headers=self._headers, timeout=self._timeout)
            resp.raise_for_status()
            out += [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]
        _check_dim(out, self.name)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def _check_dim(vectors: list[list[float]], model: str) -> None:
    if vectors and len(vectors[0]) != DIM:
        raise ValueError(f"{model} вернул размерность {len(vectors[0])}, а схема kb ожидает {DIM}")


def build_embedder(settings: Settings) -> Embedder:
    if settings.embeddings_provider == "hashing":
        return HashingEmbedder()
    if settings.embeddings_provider == "voyage":
        key = settings.voyage_api_key or settings.embeddings_api_key
        if not key:
            raise ValueError("Для EMBEDDINGS_PROVIDER=voyage задайте VOYAGE_API_KEY")
        return VoyageEmbedder(api_key=key.get_secret_value(), model=settings.embeddings_model or "voyage-3.5")
    if not settings.embeddings_model:
        raise ValueError("Для EMBEDDINGS_PROVIDER=openai задайте EMBEDDINGS_MODEL (например, BAAI/bge-m3)")
    key = settings.embeddings_api_key or settings.openai_api_key
    return OpenAICompatibleEmbedder(
        model=settings.embeddings_model,
        api_key=key.get_secret_value() if key else None,
        base_url=settings.embeddings_base_url or settings.openai_base_url,
    )


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
