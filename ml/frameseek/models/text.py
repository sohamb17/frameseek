"""Sentence encoder for transcript and OCR text (one shared 384-d space)."""
from __future__ import annotations

import functools

import numpy as np


@functools.lru_cache(maxsize=1)
def _model(name: str, revision: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name, revision=revision, device="cpu")


def encode(texts: list[str], cfg: dict, batch_size: int = 32) -> np.ndarray:
    model = _model(cfg["model"], cfg["revision"])
    if not texts:
        return np.zeros((0, cfg["dim"]), dtype=np.float32)
    emb = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True,
                       show_progress_bar=False)
    assert emb.shape[1] == cfg["dim"], f"text encoder dim {emb.shape[1]} != configured {cfg['dim']}"
    return emb.astype(np.float32)


def release() -> None:
    import gc
    _model.cache_clear()
    gc.collect()
