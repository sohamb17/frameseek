"""Train the learned relevance scorer (arm F) on human-labeled queries.

Model: StandardScaler + LogisticRegression on query/segment pairs (pointwise classifier used
for ranking). Candidates come from exactly the same multimodal pool as fixed fusion (arm E),
so E vs F isolates the value of learning the combination.

    python -m frameseek.eval.train                 # fit on train split, select C on dev
    python -m frameseek.eval.train --activate      # ... and serve it (search arm F)

Safeguards: refuses to train on fewer than --min-queries labeled training queries; fits the
scaler only on training rows; weights each query's candidates to sum to 1 so queries with
many candidates do not dominate; never looks at the test split.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config import index_config
from ..db import connection
from ..retrieval import search as S
from ..retrieval.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, build_matrix
from ..retrieval.ranker import LoadedRanker, invalidate
from ..storage import sha256_file, storage
from .labels import LabeledQuery, load_queries, load_splits
from .metrics import Interval, first_hit_rank, is_positive

C_GRID = [0.01, 0.1, 1.0, 10.0]


@dataclass
class Prepped:
    q: LabeledQuery
    prep: S.Prepared


def prepare_all(queries: list[LabeledQuery], owner: str) -> list[Prepped]:
    out = []
    for i, q in enumerate(queries):
        prep = S.prepare(S.SearchRequest(query=q.query, owner_id=owner, record=False))
        out.append(Prepped(q, prep))
        print(f"\r  candidates {i + 1}/{len(queries)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return out


def training_rows(items: list[Prepped]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    Xs, ys, ws = [], [], []
    for it in items:
        if not it.q.answers:
            continue  # no-answer queries have no positives; they only inform the abstention threshold
        cands = it.prep.cands
        if not cands:
            continue
        X = build_matrix(cands, S.channel_ranks(it.prep), it.prep.query)
        y = np.array([is_positive(Interval(c["video_id"], c["start_ms"], c["end_ms"]), it.q.answers)
                      for c in cands], dtype=int)
        Xs.append(X)
        ys.append(y)
        ws.append(np.full(len(cands), 1.0 / len(cands)))  # each query contributes total weight 1
    if not Xs:
        return np.zeros((0, len(FEATURE_NAMES))), np.zeros(0), np.zeros(0)
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(ws)


def fit(items: list[Prepped], C: float) -> Pipeline:
    X, y, w = training_rows(items)
    if len(set(y.tolist())) < 2:
        raise ValueError("training data needs both relevant and non-relevant candidates")
    # Balance classes on top of the per-query weights.
    pos = w[y == 1].sum()
    neg = w[y == 0].sum()
    w = np.where(y == 1, w * (0.5 / pos), w * (0.5 / neg)) * len(y)
    pipe = Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(C=C, max_iter=2000))])
    pipe.fit(X, y, clf__sample_weight=w)
    return pipe


def as_ranker(pipe: Pipeline, threshold: float | None = None, mid: str = "candidate") -> LoadedRanker:
    return LoadedRanker(mid, pipe, list(FEATURE_NAMES), threshold)


def success_rate(items: list[Prepped], arm: str, model=None, k: int = 5, tau: float = 0.3) -> float:
    vals = []
    for it in items:
        if not it.q.answers:
            continue
        ranked = S.rank(it.prep, arm, k, model=model)
        vals.append(first_hit_rank([Interval(r["video_id"], r["start_ms"], r["end_ms"]) for r in ranked],
                                   it.q.answers, tau) is not None)
    return float(np.mean(vals)) if vals else float("nan")


def choose_threshold(items: list[Prepped], model: LoadedRanker, tau: float = 0.3) -> float | None:
    """Abstention threshold from development data only (balanced accuracy of answer/abstain)."""
    pos, neg = [], []
    for it in items:
        ranked = S.rank(it.prep, "F", 1, model=model)
        if not ranked:
            continue
        top = ranked[0]
        if not it.q.answers:
            neg.append(top["score"])
        elif first_hit_rank([Interval(top["video_id"], top["start_ms"], top["end_ms"])], it.q.answers, tau):
            pos.append(top["score"])
    if not pos or not neg:
        return None
    best_t, best = None, -1.0
    for t in sorted(pos + neg):
        bal = (np.mean([p >= t for p in pos]) + np.mean([n < t for n in neg])) / 2
        if bal > best:
            best_t, best = t, bal
    return float(best_t)


def save(pipe: Pipeline, C: float, threshold: float | None, train_items, dev_items, metrics: dict) -> tuple[str, str]:
    _, cfg_hash = index_config()
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_queries": [it.q.id for it in train_items],
        "dev_queries": [it.q.id for it in dev_items],
        "splits_file_created": (load_splits() or {}).get("created"),
        "index_config_hash": cfg_hash,
        "C": C,
        "label_rule": "positive if IoU>=0.3 or window covers >=50% of an answer",
    }
    blob = {"pipeline": pipe, "feature_names": list(FEATURE_NAMES), "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "no_answer_threshold": threshold, "manifest": manifest}
    st = storage()
    with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False, dir=st.root) as tmp:
        joblib.dump(blob, tmp.name)
    sha = sha256_file(pathlib.Path(tmp.name))
    mid = sha[:12]
    key = f"models/ranker/{mid}.joblib"
    os.replace(tmp.name, st.ensure_parent(key))
    with connection() as conn:
        conn.execute(
            "INSERT INTO model_versions (id, name, feature_schema, model_sha256, path, train_manifest, metrics) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (mid, "logreg-relevance", json.dumps({"version": FEATURE_SCHEMA_VERSION, "features": FEATURE_NAMES}),
             sha, key, json.dumps(manifest), json.dumps(metrics)))
        conn.commit()
    return mid, key


def activate(mid: str | None) -> None:
    with connection() as conn:
        conn.execute("UPDATE model_versions SET is_active = false WHERE is_active")
        if mid:
            conn.execute("UPDATE model_versions SET is_active = true WHERE id = %s", (mid,))
        conn.commit()
    invalidate()


def main() -> int:
    ap = argparse.ArgumentParser(prog="frameseek.eval.train")
    ap.add_argument("--owner", default="demo")
    ap.add_argument("--activate", action="store_true", help="serve the new model (arm F) after training")
    ap.add_argument("--deactivate", action="store_true", help="stop serving any learned model (search uses E)")
    ap.add_argument("--min-queries", type=int, default=15)
    ap.add_argument("--force", action="store_true", help="train even below --min-queries (debugging only)")
    a = ap.parse_args()
    if a.deactivate:
        activate(None)
        print("learned scorer deactivated; search falls back to fixed fusion (arm E)")
        return 0
    if load_splits() is None:
        print("splits are not frozen yet: run `python -m frameseek.eval.labels freeze-splits` first")
        return 2
    qs = load_queries(a.owner)
    train_q = [q for q in qs if q.split == "train"]
    dev_q = [q for q in qs if q.split == "dev"]
    n_train = sum(bool(q.answers) for q in train_q)
    print(f"labeled queries: train={len(train_q)} (answerable {n_train}), dev={len(dev_q)}; test is not touched")
    if n_train < a.min_queries and not a.force:
        print(f"refusing to train on {n_train} answerable training queries (< {a.min_queries}). "
              "Label more queries in the UI (Label tab), or ship fixed fusion (arm E).")
        return 2
    train_items = prepare_all(train_q, a.owner)
    dev_items = prepare_all(dev_q, a.owner)
    results = {}
    for C in C_GRID:
        try:
            pipe = fit(train_items, C)
        except ValueError as e:
            print(f"C={C}: {e}")
            return 2
        dev_s = success_rate(dev_items, "F", as_ranker(pipe)) if dev_items else float("nan")
        results[C] = (dev_s, pipe)
        print(f"C={C:<6} dev success@5 (IoU 0.3) = {dev_s:.3f}")
    # Highest dev score; ties go to the smaller C (stronger regularization).
    best_C = sorted(results, key=lambda c: (-(results[c][0] if results[c][0] == results[c][0] else -1), c))[0]
    pipe = results[best_C][1]
    model = as_ranker(pipe)
    threshold = choose_threshold(dev_items, model) if dev_items else None
    e_dev = success_rate(dev_items, "E") if dev_items else float("nan")
    metrics = {"dev_success@5_iou0.3": {"E": e_dev, "F": results[best_C][0]}, "C": best_C,
               "train_rows": int(training_rows(train_items)[0].shape[0])}
    mid, key = save(pipe, best_C, threshold, train_items, dev_items, metrics)
    coefs = dict(zip(FEATURE_NAMES, np.round(pipe.named_steps["clf"].coef_[0], 3).tolist()))
    print(f"saved model {mid} -> {key}; C={best_C}; abstention threshold={threshold}")
    print(f"dev success@5: fixed fusion E={e_dev:.3f} vs learned F={results[best_C][0]:.3f}")
    print("largest standardized coefficients:",
          sorted(coefs.items(), key=lambda kv: -abs(kv[1]))[:8])
    if a.activate:
        activate(mid)
        print(f"activated {mid}: search 'auto' now uses arm F")
    return 0


if __name__ == "__main__":
    sys.exit(main())
