"""Text embeddings with a zero-dependency default.

`HashingEmbedder` (default) hashes word unigrams/bigrams and character n-grams into
a fixed-size L2-normalised vector: fast, deterministic, no model download.
`SentenceTransformerEmbedder` is used automatically when the optional
`sentence-transformers` package is installed and AGENTQA_EMBEDDINGS=st.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 2048):
        self.dim = dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        text = text.lower()
        words = _TOKEN.findall(text)
        feats = [(f"w:{w}", 1.0) for w in words]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(words, words[1:], strict=False)]
        for w in words:
            padded = f"#{w}#"
            feats += [(f"c:{padded[i : i + 4]}", 0.3) for i in range(max(1, len(padded) - 3))]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, weight in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                out[row, h % self.dim] += weight * (1 if (h >> 63) & 1 else -1)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class SentenceTransformerEmbedder:
    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = SentenceTransformer(model)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


def default_embedder() -> Embedder:
    if os.getenv("AGENTQA_EMBEDDINGS", "hashing") == "st":
        return SentenceTransformerEmbedder()
    return HashingEmbedder()


def nearest_label(vectors: np.ndarray, prototypes: dict[str, np.ndarray]) -> list[tuple[str, float]]:
    """For each row, the label whose prototype centroid is most cosine-similar."""
    labels = list(prototypes)
    centroids = np.stack([prototypes[label] for label in labels])
    sims = vectors @ centroids.T
    idx = sims.argmax(axis=1)
    return [(labels[i], float(sims[r, i])) for r, i in enumerate(idx)]


def build_prototypes(embedder: Embedder, examples: dict[str, list[str]]) -> dict[str, np.ndarray]:
    protos = {}
    for label, texts in examples.items():
        v = embedder.embed(texts).mean(axis=0)
        protos[label] = v / (np.linalg.norm(v) or 1)
    return protos
