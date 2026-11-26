from bench.control_envelopes import WIDTH, combine
from bench.lever_transforms import SOURCE_MARK


def _item(body_claim, roles, score):
    turns = "\n".join(f"{r}: line {i}" for i, r in enumerate(roles))
    return {"node": {"title": body_claim, "body": f"{body_claim}\n\n{SOURCE_MARK}\n{turns}"},
            "score": score}


def test_adds_only_wholly_assistant_memories_in_score_order():
    base = {"results": [_item("u1", ["user"], 0.9), _item("u2", ["user"], 0.5)]}
    asst = {"results": [_item("a1", ["assistant"], 0.7), _item("m1", ["user", "assistant"], 0.8),
                        _item("u3", ["user"], 0.95)]}
    env, added = combine(base, asst)
    assert [it["node"]["title"] for it in env["results"]] == ["u1", "a1", "u2"]
    assert added == 1


def test_cut_to_the_shipped_width():
    base = {"results": [_item(f"u{i}", ["user"], 0.5) for i in range(20)]}
    asst = {"results": [_item(f"a{i}", ["assistant"], 0.9) for i in range(10)]}
    env, added = combine(base, asst)
    assert len(env["results"]) == WIDTH and added == 10
