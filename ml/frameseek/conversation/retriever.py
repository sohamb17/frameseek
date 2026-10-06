"""A LangChain retriever backed by FrameSeek's scoped Go /api/search endpoint.

It calls the public API (not the Python search function directly) so authorization and
scope checks stay in one place. The owner comes from the server-side conversation record
and is passed with the internal service token; model output cannot change it. The Go
search endpoint calls the Python retrieval handler, never the conversation handler, so
there is no recursion.
"""
from __future__ import annotations

from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from ..config import settings


class FrameSeekRetriever(BaseRetriever):
    owner_id: str
    collection_id: str | None = None
    video_ids: list[str] | None = None
    exclude_video_ids: list[str] | None = None
    content_types: list[str] | None = None
    k: int = 8
    timeout_s: float = 15.0
    last_response: dict[str, Any] | None = None

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        s = settings()
        body = {"query": query, "k": self.k, "collection_id": self.collection_id, "video_ids": self.video_ids,
                "exclude_video_ids": self.exclude_video_ids, "content_types": self.content_types}
        body = {k: v for k, v in body.items() if v not in (None, [])}
        resp = httpx.post(f"{s.api_url}/api/search", json=body, timeout=self.timeout_s,
                          headers={"X-FrameSeek-Internal": s.internal_token, "X-FrameSeek-Owner": self.owner_id})
        resp.raise_for_status()
        data = resp.json()
        self.last_response = {k: v for k, v in data.items() if k != "results"}
        docs = []
        for r in data["results"]:
            ev = r.get("evidence") or {}
            text = " ".join(x for x in [ev.get("transcript", ""), ev.get("ocr", "")] if x)
            docs.append(Document(page_content=text, metadata={**r, "search_id": data["search_id"]}))
        return docs
