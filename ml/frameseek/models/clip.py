"""OpenCLIP paired image/text encoders (one 512-d space for frames and queries).

The image tower is only needed by the ingestion worker. The search service loads the
model with `text_only=True` and drops the visual tower to save memory.
"""
from __future__ import annotations

import functools
from typing import Callable

import numpy as np
from PIL import Image

ProgressFn = Callable[[float], None]


@functools.lru_cache(maxsize=2)
def _model(model_name: str, hf_repo: str, revision: str, text_only: bool):
    import open_clip
    import torch
    from huggingface_hub import hf_hub_download

    weights = hf_hub_download(hf_repo, "open_clip_model.safetensors", revision=revision)
    model, _, preprocess = open_clip.create_model_and_transforms(model_name, pretrained=weights)
    model.eval()
    if text_only:
        model.visual = None  # type: ignore[assignment]
    tokenizer = open_clip.get_tokenizer(model_name)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    return model, preprocess, tokenizer


def _load(cfg: dict, text_only: bool):
    return _model(cfg["model"], cfg["hf_repo"], cfg["hf_revision"], text_only)


def encode_images(paths: list, cfg: dict, batch_size: int = 16,
                  progress: ProgressFn | None = None) -> np.ndarray:
    """Encode image files in small batches; only one batch of decoded images is in memory."""
    import torch

    model, preprocess, _ = _load(cfg, text_only=False)
    out = []
    with torch.no_grad():
        for i in range(0, len(paths), batch_size):
            tensors = []
            for p in paths[i:i + batch_size]:
                with Image.open(p) as im:
                    tensors.append(preprocess(im.convert("RGB")))
            batch = torch.stack(tensors)
            feats = model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            out.append(feats.cpu().numpy().astype(np.float32))
            if progress:
                progress(min(1.0, (i + batch_size) / max(1, len(paths))))
    if not out:
        return np.zeros((0, cfg["dim"]), dtype=np.float32)
    return np.concatenate(out)


def encode_text(texts: list[str], cfg: dict) -> np.ndarray:
    import torch

    model, _, tokenizer = _load(cfg, text_only=True)
    with torch.no_grad():
        feats = model.encode_text(tokenizer(texts))
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy().astype(np.float32)


def release() -> None:
    import gc
    _model.cache_clear()
    gc.collect()
