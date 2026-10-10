"""Deterministic validator for ARES ACCORD. No LLM is used here.

A plan is a plain dict:
{
  "version": 3,
  "modes": {"life_support": "L3", "medical": "M2", "food": "F2", "engineering": "E2"},
  "commitments": [
     {"owner": "engineering", "beneficiary": "life_support", "text": "...", "accepted": true},
     {"owner": "commander",   "beneficiary": "life_support", "text": "...", "accepted": true}
  ],
  "votes": {"life_support": {"vote": "ACCEPT", "version": 3}, ...}   # optional
}
"""
import json
from pathlib import Path

RESOURCES = ["power", "water", "oxygen", "robot", "bandwidth"]
AGENTS = ["commander", "life_support", "medical", "food", "engineering"]


def load_config(path=None):
    path = path or Path(__file__).with_name("config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_mode(config, dept, mode_id):
    return config["modes"][dept][mode_id]


def totals(config, modes):
    """Sum resources and risk for a {dept: mode_id} selection."""
    t = {r: 0 for r in RESOURCES}
    t["risk"] = 0
    for dept, mode_id in modes.items():
        m = get_mode(config, dept, mode_id)
        for r in RESOURCES:
            t[r] += m[r]
        t["risk"] += m["risk"]
    return t


def sacrificing(config, modes):
    return [d for d, mid in modes.items() if get_mode(config, d, mid)["type"] == "sacrifice"]


def check_modes(config, plan):
    """Exactly one published package per department, values unchanged."""
    reasons = []
    modes = plan.get("modes", {})
    for dept in config["departments"]:
        if dept not in modes:
            reasons.append(f"{dept}: no mode selected")
        elif modes[dept] not in config["modes"][dept]:
            reasons.append(f"{dept}: '{modes[dept]}' is not a published mode")
    for dept in modes:
        if dept not in config["departments"]:
            reasons.append(f"unknown department '{dept}'")
    # Reject any attempt to edit package values inside the plan.
    for dept, custom in (plan.get("package_overrides") or {}).items():
        reasons.append(f"{dept}: package values were edited (not allowed)")
    return reasons


def check_resources(config, plan):
    t = totals(config, plan["modes"])
    reasons = []
    for r in RESOURCES:
        pool = config["resources"][r]
        if t[r] > pool:
            reasons.append(f"{r}: needs {t[r]} but only {pool} available (short by {t[r] - pool})")
    return reasons


def check_risk(config, plan):
    t = totals(config, plan["modes"])
    reasons = []
    if t["risk"] > config["limits"]["max_risk"]:
        reasons.append(f"risk {t['risk']} exceeds limit {config['limits']['max_risk']}")
    s = sacrificing(config, plan["modes"])
    if len(s) > config["limits"]["max_sacrifice"]:
        reasons.append(f"{len(s)} departments in Sacrifice ({', '.join(s)}); max {config['limits']['max_sacrifice']}")
    return reasons


def commitment_problem(config, plan, c):
    """Return a reason if this promise can no longer be kept under the current pool and plan, else None."""
    res, amt = c.get("resource"), c.get("amount") or 0
    if not res or not amt:
        return None
    t = totals(config, plan["modes"])
    if c["owner"] == "commander":
        reserve = config["resources"][res] - t[res]
        if reserve < amt:
            return f"commander cannot reserve {amt} {res}: reserve is only {reserve}"
        return None
    owner_mode = get_mode(config, c["owner"], plan["modes"][c["owner"]])
    if owner_mode[res] < amt:
        return f"{c['owner']} cannot commit {amt} {res}: its package only has {owner_mode[res]}"
    return None


def check_returns(config, plan):
    """Every sacrificing department needs N accepted, still-valid commitments from different owners."""
    reasons = []
    need = config["limits"]["min_return_commitments"]
    for dept in sacrificing(config, plan["modes"]):
        good = []
        for c in plan.get("commitments", []):
            if c.get("beneficiary") != dept or not c.get("accepted") or c.get("owner") == dept:
                continue
            problem = commitment_problem(config, plan, c)
            if problem:
                reasons.append(f"invalid commitment to {dept}: {problem}")
                continue
            good.append(c)
        owners = {c["owner"] for c in good}
        if len(owners) < need:
            reasons.append(f"{dept} is sacrificing but has {len(owners)} valid accepted commitment(s) "
                           f"from different agents; needs {need}")
    return reasons


def check_votes(config, plan):
    reasons = []
    votes = plan.get("votes", {})
    for agent in AGENTS[1:]:  # four departments vote
        v = votes.get(agent)
        if v is None:
            reasons.append(f"{agent}: no vote")
        elif v.get("version") != plan.get("version"):
            reasons.append(f"{agent}: voted on version {v.get('version')}, current is {plan.get('version')}")
        elif v.get("vote") != "ACCEPT":
            reasons.append(f"{agent}: voted {v.get('vote')}")
    return reasons


def validate_plan(config, plan, check_vote_records=False):
    """Return {"result": "PASS"|"FAIL", "checks": {...}, "reasons": [...], "totals": {...}}.

    check_vote_records=False validates the plan itself (used before voting).
    check_vote_records=True also requires four ACCEPT votes on this version (used for approval).
    """
    checks = {}
    mode_reasons = check_modes(config, plan)
    checks["modes"] = mode_reasons
    if mode_reasons:  # other checks need valid modes
        return {"result": "FAIL", "checks": checks, "reasons": mode_reasons, "totals": None}
    checks["resources"] = check_resources(config, plan)
    checks["risk"] = check_risk(config, plan)
    checks["returns"] = check_returns(config, plan)
    if check_vote_records:
        checks["votes"] = check_votes(config, plan)
    reasons = [r for rs in checks.values() for r in rs]
    return {
        "result": "PASS" if not reasons else "FAIL",
        "checks": checks,
        "reasons": reasons,
        "totals": totals(config, plan["modes"]),
    }
