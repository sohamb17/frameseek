"""Structured intent for one conversational turn, plus a deterministic rule-based parser.

The language model (when configured) only fills this schema. It never produces segment or
video IDs: references are ordinals into the last displayed result list, and code resolves
them. If the model is unavailable or returns something invalid, the rule parser is used,
so search keeps working with no API key at all.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["search", "refine", "expand", "exclude_selected", "within_video", "reset", "help"]
ContentType = Literal["talk", "screencast", "demo"]


class Intent(BaseModel):
    action: Action = Field(description=(
        "search: a new topic. refine: keep the topic but change filters or add detail. "
        "expand: show more context around a result. exclude_selected: same topic but in other videos. "
        "within_video: restrict to one video. reset: clear everything. help: the request is unclear."))
    query: str | None = Field(default=None, description="Search text for search/refine; null keeps the current topic.")
    content_types: list[ContentType] | None = Field(
        default=None, description="Only these content types (talk, screencast, demo); null leaves the filter unchanged.")
    clear_content_types: bool = Field(default=False, description="True when the user wants all content types again.")
    ordinal: int | None = Field(default=None, description="1-based position in the last result list (-1 = last).")
    expand_seconds: int | None = Field(default=None, ge=5, le=300, description="Extra seconds on each side.")


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "last": -1, "top": 1}
_ORD_RE = re.compile(
    r"\b(?:(?P<word>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|top)"
    r"|(?P<num>\d{1,2})(?:st|nd|rd|th))\s*(?:result|one|clip|moment|hit|match)?\b"
    r"|\b(?:result|number|#)\s*(?P<num2>\d{1,2})\b", re.I)
_CT_PATTERNS: list[tuple[re.Pattern, ContentType]] = [
    (re.compile(r"\b(live\s+)?(demo|demos|demonstrations?|hands[- ]on)\b", re.I), "demo"),
    (re.compile(r"\b(talks?|presentations?|lectures?|slides?)\b", re.I), "talk"),
    (re.compile(r"\b(screencasts?|screen recordings?|terminal sessions?)\b", re.I), "screencast"),
]
_FILTER_HINT = re.compile(r"\b(only|just|limit|restrict|filter)\b", re.I)
_CLEAR_CT = re.compile(r"\b(all|any|every)\s+(types?|kinds?|videos?|content)\b|\bremove (the )?filters?\b", re.I)
_EXPAND = re.compile(r"\b(more context|expand|zoom out|wider|longer clip|before and after|surrounding|around it)\b", re.I)
_SECONDS = re.compile(r"\b(\d{1,3})\s*(?:s|sec|secs|seconds)\b", re.I)
_EXCLUDE = re.compile(r"\b(another|other|different|a different)\s+videos?\b|\bnot (?:in )?(?:this|that) video\b"
                      r"|\belsewhere\b|\bin other videos\b", re.I)
_WITHIN = re.compile(r"\b(?:within|in|inside)\s+(?:this|that|the same|its|the)\s+video\b|\bsame video\b", re.I)
_RESET = re.compile(r"^\s*(reset|start over|new conversation|clear( all| everything)?)\s*[.!]?\s*$", re.I)
_HELP = re.compile(r"^\s*(help|\?|what can you do\??)\s*$", re.I)
_SAME_TOPIC = re.compile(r"\b(same|that|this)\s+(topic|thing|idea|concept)\b", re.I)
_STOP = set("""a an the me show find search look for please can you i want to see where do does is are was were
 of in on at it its that this those these about with only just some any results result video videos moment moments
 clip clips again now then also and but or one ones part bit around more give""".split())


def _strip(text: str, *patterns: re.Pattern) -> str:
    for p in patterns:
        text = p.sub(" ", text)
    return " ".join(text.split())


def meaningful(text: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
    return len(words) >= 1


def parse_rules(text: str) -> Intent:
    t = text.strip()
    if _RESET.match(t):
        return Intent(action="reset")
    if _HELP.match(t):
        return Intent(action="help")

    ordinal = None
    m = _ORD_RE.search(t)
    if m:
        if m.group("word"):
            ordinal = ORDINALS[m.group("word").lower()]
        else:
            ordinal = int(m.group("num") or m.group("num2"))

    if _EXPAND.search(t):
        secs = _SECONDS.search(t)
        return Intent(action="expand", ordinal=ordinal,
                      expand_seconds=max(5, min(300, int(secs.group(1)))) if secs else None)

    content_types: list[ContentType] | None = None
    clear = bool(_CLEAR_CT.search(t))
    has_filter_hint = bool(_FILTER_HINT.search(t))
    found = [ct for p, ct in _CT_PATTERNS if p.search(t)]
    # A content-type word is a filter when phrased as one ("only the demos") or when it is all
    # the user said ("demos?"); otherwise it may be part of the topic ("slides about raft").
    leftover = _strip(t, _ORD_RE, _EXCLUDE, _WITHIN, _FILTER_HINT, _CLEAR_CT, _SAME_TOPIC,
                      *(p for p, _ in _CT_PATTERNS))
    if found and (has_filter_hint or not meaningful(leftover)):
        content_types = sorted(set(found))  # type: ignore[assignment]
    else:
        leftover = _strip(t, _ORD_RE, _EXCLUDE, _WITHIN, _FILTER_HINT, _CLEAR_CT, _SAME_TOPIC)
    query = leftover if meaningful(leftover) else None

    if _EXCLUDE.search(t):
        return Intent(action="exclude_selected", query=query, ordinal=ordinal, content_types=content_types,
                      clear_content_types=clear)
    if _WITHIN.search(t):
        return Intent(action="within_video", query=query, ordinal=ordinal, content_types=content_types,
                      clear_content_types=clear)
    if content_types or clear:
        return Intent(action="refine", query=query, content_types=content_types, clear_content_types=clear)
    if query:
        return Intent(action="search", query=query)
    return Intent(action="help")
