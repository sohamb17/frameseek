"""Private Python HTTP service (not exposed to browsers).

The Go API is the only caller: it authenticates the user, resolves owner/collection
scope, applies deadlines, and forwards requests here. This service owns query
embeddings, retrieval orchestration, the fitted scorer and the conversation graph.
"""
from __future__ import annotations

import hmac
import threading

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..retrieval import search as search_mod

app = FastAPI(title="FrameSeek ML service", version="0.1.0")
_ready = threading.Event()


def internal_auth(x_frameseek_internal: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_frameseek_internal, settings().internal_token):
        raise HTTPException(status_code=401, detail="internal token required")


@app.on_event("startup")
def _startup() -> None:
    def warm():
        try:
            search_mod.warmup()
        finally:
            _ready.set()
    threading.Thread(target=warm, daemon=True).start()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "models_ready": _ready.is_set()}


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    owner_id: str
    collection_id: str | None = None
    video_ids: list[str] | None = None
    exclude_video_ids: list[str] | None = None
    content_types: list[str] | None = None
    k: int = Field(default=10, ge=1, le=50)
    ranker: str = "auto"
    record: bool = True


@app.post("/search", dependencies=[Depends(internal_auth)])
def search(body: SearchBody) -> dict:
    req = search_mod.SearchRequest(**body.model_dump())
    return search_mod.search(req)


class TurnBody(BaseModel):
    thread_id: str
    owner_id: str
    collection_id: str | None = None
    seq: int
    text: str = Field(min_length=1, max_length=1000)
    resume: bool = False


@app.post("/conversation/turn", dependencies=[Depends(internal_auth)])
def conversation_turn(body: TurnBody) -> dict:
    from ..conversation.handler import handle_turn
    return handle_turn(**body.model_dump())
