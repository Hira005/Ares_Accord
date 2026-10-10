"""Brute-force solver: tries every mode combination (3^4 = 81 in the baseline).

It only searches modes; return commitments are negotiated by the agents afterwards.
"""
from itertools import product

from validator import RESOURCES, load_config, sacrificing, totals


def all_combinations(config):
    depts = config["departments"]
    for combo in product(*[list(config["modes"][d].keys()) for d in depts]):
        yield dict(zip(depts, combo))


def shortfall(config, modes):
    t = totals(config, modes)
    return {r: max(0, t[r] - config["resources"][r]) for r in RESOURCES}


def feasible_plans(config):
    """All combinations that fit resources, risk and the sacrifice-count limit, best (lowest risk) first."""
    out = []
    for modes in all_combinations(config):
        t = totals(config, modes)
        s = sacrificing(config, modes)
        if any(t[r] > config["resources"][r] for r in RESOURCES):
            continue
        if t["risk"] > config["limits"]["max_risk"]:
            continue
        if len(s) > config["limits"]["max_sacrifice"]:
            continue
        out.append({"modes": modes, "totals": t, "sacrificing": s})
    out.sort(key=lambda p: (p["totals"]["risk"], len(p["sacrificing"])))
    return out


def solve(config):
    plans = feasible_plans(config)
    if plans:
        return {"status": "FEASIBLE", "plans": plans}
    return {"status": "INFEASIBLE", **explain_infeasible(config)}


def explain_infeasible(config):
    """Find the combination closest to working and say what would fix it."""
    best = None
    for modes in all_combinations(config):
        t = totals(config, modes)
        s = sacrificing(config, modes)
        short = shortfall(config, modes)
        risk_over = max(0, t["risk"] - config["limits"]["max_risk"])
        sac_over = max(0, len(s) - config["limits"]["max_sacrifice"])
        cost = sum(short.values()) + risk_over + 10 * sac_over
        if best is None or cost < best["cost"]:
            best = {"cost": cost, "modes": modes, "short": short,
                    "risk_over": risk_over, "sac_over": sac_over}
    blocking = [f"{r} short by {v}" for r, v in best["short"].items() if v]
    needs = {r: v for r, v in best["short"].items() if v}
    if best["risk_over"]:
        blocking.append(f"risk over limit by {best['risk_over']}")
    if best["sac_over"]:
        blocking.append(f"{best['sac_over']} extra Sacrifice mode(s)")
    request = []
    if needs:
        request.append("add resources: " + ", ".join(f"{r} +{v}" for r, v in needs.items()))
    if best["risk_over"]:
        request.append(f"raise risk limit by at least {best['risk_over']} (Crisis Override)")
    if best["sac_over"]:
        request.append("allow an additional Sacrifice mode (Crisis Override)")
    return {
        "closest_modes": best["modes"],
        "blocking_constraints": blocking,
        "needed_change": "; ".join(request),
    }


if __name__ == "__main__":
    cfg = load_config()
    result = solve(cfg)
    print(result["status"])
    if result["status"] == "FEASIBLE":
        for p in result["plans"]:
            print(p["modes"], p["totals"], "sacrifice:", p["sacrificing"])
    else:
        print(result["blocking_constraints"], "->", result["needed_change"])
