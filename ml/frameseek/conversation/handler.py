"""Turn handling: stale-turn protection, owner checks, interrupt/resume, durable checkpoints."""
from __future__ import annotations

import collections
import threading

from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import settings
from ..db import wait_for_schema
from .graph import build

_graph = None
_graph_lock = threading.Lock()
_thread_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)


def graph():
    """Compile once per process with a Postgres checkpointer (survives restarts)."""
    global _graph
    with _graph_lock:
        if _graph is None:
            from langgraph.checkpoint.postgres import PostgresSaver

            wait_for_schema()
            pool = ConnectionPool(settings().database_url, min_size=1, max_size=4, open=True,
                                  kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
            saver = PostgresSaver(pool)
            saver.setup()  # creates checkpoint tables if missing (idempotent)
            _graph = build(saver)
        return _graph


def handle_turn(thread_id: str, owner_id: str, collection_id: str | None, seq: int, text: str,
                resume: bool = False) -> dict:
    g = graph()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 12}
    # One turn at a time per thread inside this process; the seq check below handles the rest.
    with _thread_locks[thread_id]:
        snap = g.get_state(config)
        st = snap.values or {}
        if st.get("owner_id") and st["owner_id"] != owner_id:
            raise PermissionError("thread belongs to another owner")
        if st.get("seq", 0) >= seq:
            # An older request arrived after a newer one was answered: never overwrite newer state.
            return {"status": "stale", "message": "superseded by a newer turn", "latest_seq": st.get("seq")}
        pending = bool(getattr(snap, "interrupts", ()))
        if pending and resume:
            g.invoke(Command(resume={"answer": text, "seq": seq}), config)
        else:
            if pending:
                # The user typed something new instead of answering: close the open question first.
                g.invoke(Command(resume={"cancel": True}), config)
            start = {"text": text, "seq": seq, "owner_id": owner_id}
            # A collection switch invalidates every reference into the old result list.
            if st.get("collection_id") != collection_id:
                start.update(collection_id=collection_id, last_cards=[], selected=None, filters={},
                             last_search_id=None, query=None)
            g.invoke(start, config)
        snap = g.get_state(config)
        interrupts = getattr(snap, "interrupts", ())
        if interrupts:
            q = interrupts[0].value
            return {"status": "clarify", "message": q["question"], "clarification": q, "results": [],
                    "interpretation": {"parser": snap.values.get("parser"), "intent": snap.values.get("intent"),
                                       "action": "clarify"}}
        return dict(snap.values.get("response") or {})
