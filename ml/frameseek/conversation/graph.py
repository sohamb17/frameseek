"""LangGraph workflow for conversational search.

    interpret -> resolve -> (clarify) -> retrieve | expand | reset | help -> validate -> respond

Bounded by design: one pass per turn, a recursion limit, HTTP timeouts, at most one model
call (with one retry) per turn, and no tools beyond the scoped search retriever.

State is checkpointed in Postgres per thread (conversation id), so a restart of this
service keeps "the second result" pointing at the same item the user saw. Clarification
uses `interrupt()`: the graph pauses, the question goes to the UI, and the answer resumes
the same thread. The clarify node re-runs from its start on resume, so everything before
`interrupt()` in it is side-effect free.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import llm
from .intent import Intent, parse_rules
from .retriever import FrameSeekRetriever

MAX_MESSAGES = 20
DEFAULT_EXPAND_S = 20
CT_LABEL = {"talk": "talks", "screencast": "screencasts", "demo": "demonstrations"}


def _bounded(old: list, new: list) -> list:
    return (old + new)[-MAX_MESSAGES:]


class ConvState(TypedDict, total=False):
    owner_id: str
    collection_id: str | None
    seq: int
    text: str                      # the current user message
    messages: Annotated[list[dict], _bounded]
    query: str | None              # current topic
    filters: dict                  # content_types / video_ids / exclude_video_ids
    last_cards: list[dict]         # ordered snapshot of the last displayed results
    last_search_id: str | None
    selected: dict | None          # the result the user referred to most recently
    intent: dict
    parser: str
    plan: dict                     # resolved action for this turn
    cards: list[dict]
    response: dict
    invalid_citations: Annotated[int, operator.add]


def _fmt(ms: int) -> str:
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _card_label(c: dict) -> str:
    return f"#{c['rank']} {c['video_title']} ({_fmt(c['start_ms'])}-{_fmt(c['end_ms'])})"


# --------------------------------------------------------------------------- nodes
def interpret(state: ConvState) -> dict:
    text = state["text"]
    context = {
        "current_topic": state.get("query"),
        "filters": state.get("filters") or {},
        "last_results": [_card_label(c) for c in (state.get("last_cards") or [])[:8]],
    }
    parser = "rules"
    if llm.enabled():
        try:
            intent = llm.parse_llm(text, context)
            parser = llm.model_label()
        except Exception as e:  # provider down, rate limit, invalid output: never block search
            intent, parser = parse_rules(text), f"rules (llm unavailable: {type(e).__name__})"
    else:
        intent = parse_rules(text)
    return {"intent": intent.model_dump(), "parser": parser,
            "messages": [{"role": "user", "text": text}]}


def _pick(cards: list[dict], ordinal: int | None) -> dict | None:
    if ordinal is None or not cards:
        return None
    if ordinal == -1:
        return cards[-1]
    return cards[ordinal - 1] if 1 <= ordinal <= len(cards) else None


def resolve(state: ConvState) -> dict:
    """Turn the intent into a concrete plan, or a clarification request."""
    intent = Intent.model_validate(state["intent"])
    cards = state.get("last_cards") or []
    filters = dict(state.get("filters") or {})
    query = state.get("query")
    plan: dict[str, Any] = {"action": intent.action}

    if intent.action in ("reset", "help"):
        return {"plan": plan}

    referenced = _pick(cards, intent.ordinal)
    if intent.ordinal is not None and referenced is None:
        plan.update(action="no_answer", reason=(
            "There are no results yet to refer to." if not cards else
            f"There are only {len(cards)} results; I can't find result #{intent.ordinal}."))
        return {"plan": plan}

    if intent.content_types:
        filters["content_types"] = intent.content_types
    if intent.clear_content_types:
        filters.pop("content_types", None)

    if intent.action == "expand":
        target = referenced or state.get("selected") or (cards[0] if len(cards) == 1 else None)
        if target is None:
            if not cards:
                plan.update(action="no_answer", reason="There is no result to expand yet. Search for something first.")
                return {"plan": plan}
            return {"plan": {**plan, "action": "clarify", "kind": "which_result",
                             "question": "Which result should I show more context around?",
                             "options": [{"label": _card_label(c), "value": str(c["rank"])} for c in cards[:5]],
                             "then": {"action": "expand", "seconds": intent.expand_seconds}}}
        plan.update(target=target, seconds=intent.expand_seconds or DEFAULT_EXPAND_S)
        return {"plan": plan, "selected": target}

    if intent.action in ("within_video", "exclude_selected"):
        target = referenced or state.get("selected")
        if target is None:
            videos: dict[str, str] = {}
            for c in cards:
                videos.setdefault(c["video_id"], c["video_title"])
            if len(videos) == 1:
                target = cards[0]
            elif not videos:
                plan.update(action="no_answer", reason="Search for something first, then I can narrow it to a video.")
                return {"plan": plan}
            else:
                verb = "search within" if intent.action == "within_video" else "exclude"
                return {"plan": {**plan, "action": "clarify", "kind": "which_video",
                                 "question": f"Which video should I {verb}?",
                                 "options": [{"label": t, "value": v} for v, t in list(videos.items())[:6]],
                                 "then": {"action": intent.action, "query": intent.query,
                                          "content_types": filters.get("content_types")}}}
        if intent.action == "within_video":
            filters["video_ids"] = [target["video_id"]]
            filters.pop("exclude_video_ids", None)
        else:
            filters["exclude_video_ids"] = sorted(set(filters.get("exclude_video_ids") or []) | {target["video_id"]})
            filters.pop("video_ids", None)
        plan["target"] = target

    if intent.action == "search":
        query = intent.query
        # A new topic searches the whole collection again, unless this message itself sets a type filter.
        filters.pop("video_ids", None)
        filters.pop("exclude_video_ids", None)
        if not intent.content_types:
            filters.pop("content_types", None)
    elif intent.query:
        query = intent.query if intent.action != "refine" or not query else f"{query} {intent.query}"
    if not query:
        plan.update(action="no_answer", reason="What should I search for?")
        return {"plan": plan}
    plan.update(action="retrieve", query=query, filters=filters)
    return {"plan": plan, "selected": plan.get("target") or state.get("selected")}


def clarify(state: ConvState) -> dict:
    plan = state["plan"]
    # --- side-effect free above this line: this node restarts from the top on resume ---
    answer = interrupt({"kind": plan["kind"], "question": plan["question"], "options": plan["options"]})
    if isinstance(answer, dict) and answer.get("cancel"):
        return {"plan": {"action": "cancelled"}}
    value = answer.get("answer") if isinstance(answer, dict) else str(answer)
    seq_update = {"seq": answer["seq"]} if isinstance(answer, dict) and "seq" in answer else {}
    cards = state.get("last_cards") or []
    then = plan["then"]
    msgs = [{"role": "user", "text": str(value)}]
    if plan["kind"] == "which_result":
        chosen = next((c for c in cards if str(c["rank"]) == str(value).strip().lstrip("#")), None)
        if chosen is None:
            return {**seq_update, "messages": msgs,
                    "plan": {"action": "no_answer", "reason": f"I couldn't match '{value}' to a result."}}
        return {**seq_update, "messages": msgs, "selected": chosen,
                "plan": {"action": "expand", "target": chosen, "seconds": then.get("seconds") or DEFAULT_EXPAND_S}}
    # which_video: accept the option value (video id) or a title fragment
    v = str(value).strip()
    chosen = next((c for c in cards if c["video_id"] == v), None) or next(
        (c for c in cards if v.lower() in c["video_title"].lower()), None)
    if chosen is None:
        return {**seq_update, "messages": msgs,
                "plan": {"action": "no_answer", "reason": f"I couldn't match '{value}' to a video in the results."}}
    filters = dict(state.get("filters") or {})
    if then.get("content_types"):
        filters["content_types"] = then["content_types"]
    if then["action"] == "within_video":
        filters["video_ids"] = [chosen["video_id"]]
        filters.pop("exclude_video_ids", None)
    else:
        filters["exclude_video_ids"] = sorted(set(filters.get("exclude_video_ids") or []) | {chosen["video_id"]})
        filters.pop("video_ids", None)
    query = then.get("query") or state.get("query")
    return {**seq_update, "messages": msgs, "selected": chosen,
            "plan": {"action": "retrieve", "query": query, "filters": filters, "target": chosen}}


def retrieve(state: ConvState) -> dict:
    plan = state["plan"]
    f = plan["filters"]
    retriever = FrameSeekRetriever(owner_id=state["owner_id"], collection_id=state.get("collection_id"),
                                   video_ids=f.get("video_ids"), exclude_video_ids=f.get("exclude_video_ids"),
                                   content_types=f.get("content_types"))
    docs = retriever.invoke(plan["query"])
    cards = [d.metadata for d in docs]
    return {"cards": cards, "query": plan["query"], "filters": f,
            "last_search_id": cards[0]["search_id"] if cards else None,
            "plan": {**plan, "search_meta": retriever.last_response}}


def expand(state: ConvState) -> dict:
    plan = state["plan"]
    t = plan["target"]
    pad = int(plan["seconds"]) * 1000
    dur = t.get("video_duration_ms") or (t["end_ms"] + pad)
    card = dict(t)
    card.update(start_ms=max(0, t["start_ms"] - pad), end_ms=min(dur, t["end_ms"] + pad),
                expanded_from={"start_ms": t["start_ms"], "end_ms": t["end_ms"]})
    return {"cards": [card]}


def validate(state: ConvState) -> dict:
    """Only cite results that exist in what was actually retrieved/displayed."""
    plan = state["plan"]
    cards = state.get("cards") or []
    if plan["action"] == "retrieve":
        allowed = {(c["segment_id"], c["video_id"]) for c in cards}  # retrieved this turn
    else:
        allowed = {(c["segment_id"], c["video_id"]) for c in (state.get("last_cards") or [])}
    ok = [c for c in cards if (c.get("segment_id"), c.get("video_id")) in allowed]
    return {"cards": ok, "invalid_citations": len(cards) - len(ok)}


def respond(state: ConvState) -> dict:
    plan = state.get("plan") or {}
    action = plan.get("action")
    cards = state.get("cards") or []
    update: dict[str, Any] = {}
    if action == "retrieve":
        f = plan["filters"]
        parts = [f'Searching for "{plan["query"]}"']
        if f.get("content_types"):
            parts.append("in " + " and ".join(CT_LABEL[c] for c in f["content_types"]))
        if f.get("video_ids") and plan.get("target"):
            parts.append(f'within "{plan["target"]["video_title"]}"')
        if f.get("exclude_video_ids") and plan.get("target"):
            parts.append(f'excluding "{plan["target"]["video_title"]}"')
        msg = " ".join(parts) + (f" - {len(cards)} moments found." if cards else
                                 ". No matching moments found; try different words or remove a filter.")
        update["last_cards"] = cards           # the snapshot that ordinals refer to next turn
        update["selected"] = None              # references now point into the new list
    elif action == "expand":
        c = cards[0]
        msg = (f"Result #{c['rank']} with {plan['seconds']} s more context on each side "
               f"({_fmt(c['start_ms'])}-{_fmt(c['end_ms'])} of \"{c['video_title']}\"). "
               "This only widens the playback window; it is not a new search.")
    elif action == "reset":
        msg = "Cleared the conversation. What would you like to find?"
        update.update(query=None, filters={}, last_cards=[], selected=None, last_search_id=None)
    elif action in ("no_answer",):
        msg = plan["reason"]
    elif action == "cancelled":
        msg = ""
    else:
        msg = ("Ask for a moment, e.g. \"where do they explain replication?\". Then refine: "
               "\"only the demos\", \"show more context around the second result\", "
               "\"find the same topic in another video\".")
    response = {
        "status": "ok",
        "message": msg,
        "results": cards if action in ("retrieve", "expand") else [],
        "interpretation": {"parser": state.get("parser"), "intent": state.get("intent"),
                           "action": action, "query": plan.get("query"), "filters": plan.get("filters")},
        "search": plan.get("search_meta"),
        "state": {"query": update.get("query", state.get("query")),
                  "filters": update.get("filters", state.get("filters") or {}),
                  "selected_rank": (state.get("selected") or {}).get("rank")},
    }
    update["response"] = response
    if msg:
        update["messages"] = [{"role": "assistant", "text": msg}]
    return update


def route_after_resolve(state: ConvState) -> str:
    a = state["plan"]["action"]
    return {"clarify": "clarify", "retrieve": "retrieve", "expand": "expand"}.get(a, "respond")


def route_after_clarify(state: ConvState) -> str:
    a = state["plan"]["action"]
    return {"retrieve": "retrieve", "expand": "expand"}.get(a, "respond")


def build(checkpointer=None):
    g = StateGraph(ConvState)
    g.add_node("interpret", interpret)
    g.add_node("resolve", resolve)
    g.add_node("clarify", clarify)
    g.add_node("retrieve", retrieve)
    g.add_node("expand", expand)
    g.add_node("validate", validate)
    g.add_node("respond", respond)
    g.add_edge(START, "interpret")
    g.add_edge("interpret", "resolve")
    g.add_conditional_edges("resolve", route_after_resolve, ["clarify", "retrieve", "expand", "respond"])
    g.add_conditional_edges("clarify", route_after_clarify, ["retrieve", "expand", "respond"])
    g.add_edge("retrieve", "validate")
    g.add_edge("expand", "validate")
    g.add_edge("validate", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)
