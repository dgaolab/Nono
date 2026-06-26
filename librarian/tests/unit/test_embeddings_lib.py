import os
import sys

from nono_librarian.lib import embeddings


def test_constants():
    assert embeddings.MODEL_NAME == "BAAI/bge-small-en-v1.5"
    assert embeddings.DIM == 384


def test_cosine_identical_is_one():
    assert abs(embeddings.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    assert embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_empty_and_mismatch_and_zero():
    assert embeddings.cosine([], [1.0]) == 0.0
    assert embeddings.cosine([1.0, 2.0], [1.0]) == 0.0
    assert embeddings.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_node_embedding_text_composition():
    node = {"title": "Epilepsy", "summary": "A seizure disorder.", "keywords": ["scn1a", "dravet"]}
    assert embeddings.node_embedding_text(node) == "Epilepsy. A seizure disorder. scn1a dravet"


def test_node_embedding_text_tolerates_missing_fields():
    assert embeddings.node_embedding_text({}) == "."


def test_text_hash_stable_and_sensitive():
    assert embeddings.text_hash("abc") == embeddings.text_hash("abc")
    assert embeddings.text_hash("abc") != embeddings.text_hash("abd")


def test_embeddings_unavailable_is_runtimeerror():
    assert issubclass(embeddings.EmbeddingsUnavailable, RuntimeError)
