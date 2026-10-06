"""Loading and applying the learned relevance scorer (scikit-learn pipeline).

The serialized artifact contains the fitted Pipeline (scaler + logistic regression)
and the exact feature names it was trained on. Loading refuses an artifact whose
feature schema differs from the serving code.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import joblib
import numpy as np

from ..db import connection
from ..storage import sha256_file, storage
from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION


@dataclass
class LoadedRanker:
    id: str
    pipeline: object
    feature_names: list[str]
    no_answer_threshold: float | None

    def score(self, X: np.ndarray) -> np.ndarray:
        # decision_function is monotonic with predict_proba but avoids saturation ties.
        return self.pipeline.decision_function(X)

    def explain(self, X: np.ndarray, top: int = 3) -> list[list[dict]]:
        """Per-row top feature contributions (coef * standardized value) for a linear model."""
        scaler = self.pipeline.named_steps["scale"]
        clf = self.pipeline.named_steps["clf"]
        Z = scaler.transform(X)
        contrib = Z * clf.coef_[0]
        out = []
        for row in contrib:
            idx = np.argsort(-np.abs(row))[:top]
            out.append([{"feature": self.feature_names[i], "contribution": round(float(row[i]), 3)} for i in idx])
        return out


_lock = threading.Lock()
_cache: dict[str, LoadedRanker] = {}
_active: tuple[float, str | None] = (0.0, None)


def load_artifact(path: str, model_id: str) -> LoadedRanker:
    blob = joblib.load(path)
    if blob["feature_schema_version"] != FEATURE_SCHEMA_VERSION or blob["feature_names"] != FEATURE_NAMES:
        raise RuntimeError(f"model {model_id} was trained on a different feature schema")
    return LoadedRanker(model_id, blob["pipeline"], blob["feature_names"], blob.get("no_answer_threshold"))


def active_model(ttl_s: float = 15.0) -> LoadedRanker | None:
    """Return the active model, re-checking the registry at most every `ttl_s` seconds."""
    global _active
    with _lock:
        ts, mid = _active
        if time.time() - ts > ttl_s:
            with connection() as conn:
                row = conn.execute("SELECT id, path, model_sha256 FROM model_versions WHERE is_active").fetchone()
            mid = row["id"] if row else None
            if row and mid not in _cache:
                path = storage().path(row["path"])
                if sha256_file(path) != row["model_sha256"]:
                    raise RuntimeError(f"model artifact checksum mismatch for {mid}")
                _cache[mid] = load_artifact(str(path), mid)
            _active = (time.time(), mid)
        return _cache.get(mid) if mid else None


def invalidate() -> None:
    global _active
    with _lock:
        _active = (0.0, None)
