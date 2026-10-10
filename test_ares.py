"""Run with:  python3 test_ares.py   (or: python3 -m pytest -q)"""
import copy

from solver import solve
from validator import load_config, totals, validate_plan

CFG = load_config()


def plan(modes, commitments=None, version=1):
    return {"version": version, "modes": modes, "commitments": commitments or []}


def two_returns(beneficiary, owners=("engineering", "commander")):
    return [{"owner": o, "beneficiary": beneficiary, "text": "x", "accepted": True} for o in owners]


PATH_A = {"life_support": "L3", "medical": "M2", "food": "F2", "engineering": "E2"}
PATH_B = {"life_support": "L2", "medical": "M2", "food": "F2", "engineering": "E3"}
ALL_RESTRICTED = {"life_support": "L2", "medical": "M2", "food": "F2", "engineering": "E2"}


def test_path_a_totals_match_guide():
    t = totals(CFG, PATH_A)
    assert (t["power"], t["water"], t["oxygen"], t["robot"], t["bandwidth"], t["risk"]) == (79, 50, 57, 26, 17, 24)


def test_path_b_totals_match_guide():
    t = totals(CFG, PATH_B)
    assert (t["power"], t["water"], t["oxygen"], t["robot"], t["bandwidth"], t["risk"]) == (79, 50, 59, 26, 16, 24)


def test_path_a_passes_with_returns():
    assert validate_plan(CFG, plan(PATH_A, two_returns("life_support")))["result"] == "PASS"


def test_path_b_passes_with_returns():
    assert validate_plan(CFG, plan(PATH_B, two_returns("engineering", ("food", "commander"))))["result"] == "PASS"


def test_sacrifice_without_returns_fails():
    r = validate_plan(CFG, plan(PATH_A))
    assert r["result"] == "FAIL" and r["checks"]["returns"]


def test_returns_from_same_agent_fail():
    c = two_returns("life_support", ("engineering", "engineering"))
    assert validate_plan(CFG, plan(PATH_A, c))["result"] == "FAIL"


def test_unaccepted_returns_fail():
    c = two_returns("life_support")
    c[1]["accepted"] = False
    assert validate_plan(CFG, plan(PATH_A, c))["result"] == "FAIL"


def test_all_restricted_overshoots_four_resources():
    r = validate_plan(CFG, plan(ALL_RESTRICTED))
    assert r["result"] == "FAIL"
    assert len(r["checks"]["resources"]) == 4  # power, water? see guide: P83 W51 O60 R30 B18
    t = r["totals"]
    assert (t["power"], t["water"], t["oxygen"], t["robot"], t["bandwidth"]) == (83, 51, 60, 30, 18)


def test_two_sacrifices_fail_baseline():
    modes = {"life_support": "L3", "medical": "M2", "food": "F2", "engineering": "E3"}
    r = validate_plan(CFG, plan(modes, two_returns("life_support") + two_returns("engineering", ("food", "commander"))))
    assert r["result"] == "FAIL"
    assert r["checks"]["risk"]


def test_risk_limit():
    modes = {"life_support": "L3", "medical": "M3", "food": "F2", "engineering": "E2"}
    r = validate_plan(CFG, plan(modes))
    assert any("risk" in x for x in r["reasons"])


def test_missing_department_fails():
    r = validate_plan(CFG, {"version": 1, "modes": {"life_support": "L1"}})
    assert r["result"] == "FAIL"


def test_edited_package_rejected():
    p = plan(PATH_A, two_returns("life_support"))
    p["package_overrides"] = {"life_support": {"power": 1}}
    assert validate_plan(CFG, p)["result"] == "FAIL"


def test_votes_must_match_version():
    p = plan(PATH_A, two_returns("life_support"), version=4)
    p["votes"] = {d: {"vote": "ACCEPT", "version": 4} for d in CFG["departments"]}
    assert validate_plan(CFG, p, check_vote_records=True)["result"] == "PASS"
    p["votes"]["medical"]["version"] = 3  # stale vote
    assert validate_plan(CFG, p, check_vote_records=True)["result"] == "FAIL"
    p["votes"]["medical"] = {"vote": "REJECT", "version": 4}
    assert validate_plan(CFG, p, check_vote_records=True)["result"] == "FAIL"


def test_solver_baseline_finds_both_paths():
    res = solve(CFG)
    assert res["status"] == "FEASIBLE"
    found = [p["modes"] for p in res["plans"]]
    assert PATH_A in found and PATH_B in found


def test_solver_infeasible_after_big_event():
    cfg = copy.deepcopy(CFG)
    cfg["resources"]["power"] = 60
    res = solve(cfg)
    assert res["status"] == "INFEASIBLE"
    assert res["blocking_constraints"] and res["needed_change"]


def test_crisis_override_reopens_options():
    cfg = copy.deepcopy(CFG)
    cfg["resources"]["power"] -= 4          # solar aftershock
    base = solve(cfg)
    cfg["limits"]["max_risk"] = 28           # Crisis Override
    cfg["limits"]["max_sacrifice"] = 2
    override = solve(cfg)
    assert override["status"] == "FEASIBLE"
    assert len(override["plans"]) >= (len(base["plans"]) if base["status"] == "FEASIBLE" else 0)


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS ", name)
            except AssertionError as e:
                failed += 1
                print("FAIL ", name, e)
    sys.exit(1 if failed else 0)
