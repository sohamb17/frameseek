"""Artifact storage.

Keys look like S3 object keys ("videos/<id>/v2/frames/00012.jpg"). The local
implementation maps them under FRAMESEEK_DATA_DIR; an S3 implementation only has to
provide the same four methods, so the pipeline code does not change when artifacts
move to a bucket.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil

from .config import settings


class LocalStorage:
    def __init__(self, root: pathlib.Path | None = None) -> None:
        self.root = (root or settings().data_dir).resolve()

    def path(self, key: str) -> pathlib.Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"key escapes storage root: {key}")
        return p

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def ensure_parent(self, key: str) -> pathlib.Path:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_bytes(self, key: str, data: bytes) -> None:
        p = self.ensure_parent(key)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # atomic publish: readers never see a half-written file

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def remove_prefix(self, prefix: str) -> None:
        p = self.path(prefix)
        if p.is_dir():
            shutil.rmtree(p)

    def sha256(self, key: str) -> str:
        return sha256_file(self.path(key))


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_storage: LocalStorage | None = None


def storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage
