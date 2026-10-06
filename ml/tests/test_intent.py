import pytest

from frameseek.conversation.intent import parse_rules


@pytest.mark.parametrize("text,action,extra", [
    ("where do they explain why the cache becomes stale", "search", {}),
    ("only the live demonstrations", "refine", {"content_types": ["demo"]}),
    ("just talks please", "refine", {"content_types": ["talk"]}),
    ("show more context around the second result", "expand", {"ordinal": 2}),
    ("expand the last one by 30 seconds", "expand", {"ordinal": -1, "expand_seconds": 30}),
    ("find the same topic in another video", "exclude_selected", {"query": None}),
    ("within this video", "within_video", {}),
    ("search result 3's video for the config file", "search", {}),
    ("show all types again", "refine", {"clear_content_types": True}),
    ("start over", "reset", {}),
    ("help", "help", {}),
])
def test_rule_parser(text, action, extra):
    it = parse_rules(text)
    assert it.action == action
    for k, v in extra.items():
        assert getattr(it, k) == v


def test_topic_words_are_not_mistaken_for_filters():
    it = parse_rules("slides about raft leader election")
    assert it.action == "search" and it.content_types is None and "raft" in it.query
