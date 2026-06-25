#!/usr/bin/env python3
"""Embedding seam for semantic node search — the single place that touches the model.

`embed_texts` lazily loads a local fastembed ONNX model; every other helper is
pure. fastembed is imported ONLY inside the model loader, so importing this
module never pulls in fastembed (callers degrade to lexical when unavailable).
"""

import hashlib
import math

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384


class EmbeddingsUnavailable(RuntimeError):
    """Raised when the embedding model cannot be loaded or run."""


_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        try:
            from fastembed import TextEmbedding
        except Exception as e:  # ImportError or any transitive load failure
            raise EmbeddingsUnavailable(f"fastembed unavailable: {e}") from e
        try:
            _MODEL = TextEmbedding(model_name=MODEL_NAME)
        except Exception as e:
            raise EmbeddingsUnavailable(f"could not load model {MODEL_NAME}: {e}") from e
    return _MODEL


def embed_texts(texts):
    """Return one DIM-length vector (list[float]) per input text."""
    if not texts:
        return []
    model = _get_model()
    try:
        return [list(map(float, v)) for v in model.embed(list(texts))]
    except Exception as e:
        raise EmbeddingsUnavailable(f"embedding failed: {e}") from e


def node_embedding_text(node):
    """The text embedded for a node: title + summary + keywords."""
    title = (node.get("title") or "").strip()
    summary = (node.get("summary") or "").strip()
    keywords = " ".join(node.get("keywords") or [])
    return f"{title}. {summary} {keywords}".strip()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(y * y for y in b))
    if ma == 0 or mb == 0:
        return 0.0
    return dot / (ma * mb)
