"""Council tests. Run with: python3 test_council.py   (or python3 -m pytest -q)

The LLM path is tested with a stand-in function patched over llm.call_json (tests only; the app never fakes
a model). The fallback path is tested with no API key at all.
"""
import json
import re
import tempfile

import llm
from council import Council, normalize_event, parse_description
from llm import LLMError
from validator import validate_plan


def ctx_of(prompt):
    return json.loads(re.search(r"CONTEXT_JSON: (.*)\n", prompt).group(1))


def kind_of(prompt):
    return re.search(r"TASK_KIND: (\w+)", prompt).group(1)


def good_model(system, prompt, timeout=25):
    """Behaves like a sensible model: Standard first, then Restricted; refuses sacrifice until commitments exist."""
    k, c = kind_of(prompt), ctx_of(prompt)
    if k == "proposal":
        ids = list(c["your_modes"])
        return {"mode": ids[0] if c["round"] == 1 else ids[1], "consequence": "loses recovery margin", "reason": "numbers"}, "test:model"
    if k == "pick_plan":
        return {"choice": len(c["candidates"]) - 1, "reason": "protect the habitat first"}, "test:model"
    if k == "sacrifice_consent":
        return {"accept": len(c["commitments_attached"]) >= 2, "reason": "needs fair return"}, "test:model"
    if k == "commitment":
        return {"commit": c["can_afford"], "reason": "fair"}, "test:model"
    if k == "vote":
        return {"vote": "ACCEPT", "reason": "plan is valid"}, "test:model"
    raise AssertionError(k)


def new_council():
    return Council(data_dir=tempfile.mkdtemp())


def check_baseline(c):
    assert c.status == "APPROVED"
    msgs = c.messages()
    assert max(m["round"] for m in msgs if m["phase"] == "baseline") >= 3
    assert any(m["type"] == "Objection" and m["data"].get("refused") for m in msgs), "someone must refuse Sacrifice"
    assert len([m for m in msgs if m["type"] == "Commitment"]) >= 2
    plan = c.approved_plan
    v = validate_plan(c.cfg, plan, check_vote_records=True)
    assert v["result"] == "PASS"
    assert all(vote["version"] == plan["version"] and vote["vote"] == "ACCEPT" for vote in plan["votes"].values())
    assert sum(1 for p in c.plans if p["status"] in ("SUPERSEDED", "REJECTED")) >= 1, "a revised plan is expected"


def test_baseline_with_model():
    llm.call_json, saved = good_model, llm.call_json
    try:
        c = new_council()
        c.start()
        check_baseline(c)
        assert all(m["source"].startswith("llm:") for m in c.messages() if m["type"] in ("Vote", "Commitment"))
    finally:
        llm.call_json = saved


def test_baseline_fallback_without_api_key():
    c = new_council()
    c.start()
    check_baseline(c)
    assert any(m["source"] == "fallback-rules" for m in c.messages()), "fallback must be labelled"


def test_invalid_model_output_falls_back():
    def bad(system, prompt, timeout=25):
        if kind_of(prompt) == "proposal":
            return {"mode": "Z9"}, "test:model"      # not a published mode
        raise LLMError("timeout")
    saved, llm.call_json = llm.call_json, bad
    try:
        c = new_council()
        c.start()
        assert c.status == "APPROVED"
        assert all(m["source"] == "fallback-rules" for m in c.messages() if m["type"] == "Proposal" and m["sender"] != "commander")
    finally:
        llm.call_json = saved


def test_event_makes_plan_stale_and_renegotiates():
    c = new_council()
    c.start()
    old = c.approved_plan
    c.inject_event({"event_name": "Solar aftershock", "description": "Available Power decreases by 4"})
    assert old["status"] == "SUPERSEDED" or old["status"] == "STALE"
    assert any("STALE" in m["content"] for m in c.messages() if m["phase"] == "event-1")
    assert max(m["round"] for m in c.messages() if m["phase"] == "event-1") >= 2
    assert c.status in ("APPROVED", "INFEASIBLE")
    if c.status == "APPROVED":
        assert validate_plan(c.cfg, c.approved_plan, check_vote_records=True)["result"] == "PASS"
        assert c.approved_plan["version"] > old["version"]


def test_starter_kit_event_is_infeasible_with_clear_reason():
    c = new_council()
    c.start()
    c.inject_event({"event_id": "CRISIS_002", "event_name": "Solar Aftershock",
                    "impact": {"power_reduction_percent": 30, "duration_hours": 6}})
    assert c.resources["power"] == 55
    assert c.status == "INFEASIBLE"
    assert "power" in c.result["message"] and "Needed external change" in c.result["message"]
    assert c.export_plan()["final_plan"] is not None or c.export_plan()["result"]


def test_unknown_event_text_is_parsed():
    ch = parse_description("Dust storm: available bandwidth falls by 3 and water drops by 10%")
    assert {"resource": "bandwidth", "delta": -3.0} in ch and {"resource": "water", "percent": -10.0} in ch
    assert normalize_event({"event_name": "x", "description": "Relay interference: Available Bandwidth decreases by 2"})["changes"]


def test_event_without_numbers_is_rejected():
    c = new_council()
    c.start()
    try:
        c.inject_event({"event_name": "Mystery", "description": "something bad happened"})
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_reset_keeps_history_and_clears_state():
    c = new_council()
    c.start()
    n = len(c.history)
    c.reset()
    assert c.status == "IDLE" and c.approved_plan is None and len(c.history) > n


def test_round_limit_gives_deadlock():
    c = new_council()
    c.runtime["max_rounds"] = 3
    c.start()
    assert c.status == "DEADLOCK"


def test_export_shapes():
    c = new_council()
    c.start()
    out = c.export_plan()
    assert out["final_plan"]["version"] == c.approved_plan["version"]
    rec = out["agent_records"][0]
    assert set(["round", "agent_id", "proposal", "vote", "risk_score", "flag_human_review"]) <= set(rec)
    assert json.loads(c.transcript_json()) and "id,time" in c.transcript_csv()


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS ", name)
            except Exception as e:  # noqa
                failed += 1
                print("FAIL ", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)
