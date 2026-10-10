"""The council: Commander + four departments + deterministic validator + optimizer.

Flow of one negotiation (see README for the diagram):
  R1  Commander publishes the crisis; every department requests a mode and explains what it would lose.
  R2  Commander reports the numeric conflict; departments revise.
  R3  Commander (using the solver as an optimizer) drafts numbered plans; the sacrificing department may
      refuse; return commitments are requested from other agents; a new plan version replaces the old one.
  R4  Validator checks the plan; all four departments vote on that exact version; Commander approves only
      on PASS + four ACCEPT. Otherwise the next candidate plan is tried until the round limit.
Every message is stored (data/transcript.jsonl) and nothing is scripted: proposals, objections, commitments
and votes come from the model (or from a labelled rule-based fallback if the model fails).
"""
import copy
import csv
import io
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import llm
from agents import SPECS, Agent
from solver import explain_infeasible, feasible_plans, solve
from validator import RESOURCES, commitment_problem, get_mode, load_config, sacrificing, totals, validate_plan

DEPTS = ["life_support", "medical", "food", "engineering"]
ALIASES = {"power": "power", "water": "water", "oxygen": "oxygen", "o2": "oxygen", "robot": "robot",
           "robot_time": "robot", "robot time": "robot", "bandwidth": "bandwidth"}
SCHEMA_IDS = {"commander": "COMMANDER", "life_support": "LIFE_SUPPORT", "medical": "MEDICAL_OFFICER",
              "food": "FOOD_PRODUCTION", "engineering": "ENGINEERING"}


class Stop(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- events

def parse_description(text):
    """Pull resource changes out of free text such as 'Available Power decreases by 4' or 'power drops by 30%'."""
    changes = []
    pat = (r"(power|water|oxygen|robot(?:\s*time)?|bandwidth)[^.\d%\-]{0,40}?"
           r"(decreas\w*|drop\w*|fall\w*|reduc\w*|los[et]\w*|increas\w*|rise\w*|ris\w*|gain\w*)[^.\d]{0,12}?"
           r"(\d+(?:\.\d+)?)\s*(%|percent)?")
    for m in re.finditer(pat, text or "", re.I):
        res = ALIASES[re.sub(r"\s+", " ", m.group(1).lower())]
        sign = 1 if re.match(r"(increas|rise|ris|gain)", m.group(2), re.I) else -1
        val = float(m.group(3)) * sign
        changes.append({"resource": res, ("percent" if m.group(4) else "delta"): val})
    return changes


def normalize_event(ev):
    """Accept our UI form, the starter-kit format (impact.<resource>_reduction_percent) or a plain description."""
    changes = []
    for ch in ev.get("changes") or []:
        res = ALIASES.get(str(ch.get("resource", "")).lower())
        if not res:
            continue
        item = {"resource": res}
        if ch.get("delta") not in (None, "", 0):
            item["delta"] = float(ch["delta"])
        if ch.get("percent") not in (None, "", 0):
            item["percent"] = float(ch["percent"])
        if len(item) > 1:
            changes.append(item)
    impact = ev.get("impact") or {}
    for key, val in impact.items():
        m = re.match(r"([a-z_]+?)_(?:reduction|decrease|drop)_percent$", key)
        if m and m.group(1) in ALIASES and isinstance(val, (int, float)):
            changes.append({"resource": ALIASES[m.group(1)], "percent": -abs(float(val))})
        m = re.match(r"([a-z_]+?)_(?:reduction|decrease|drop)$", key)
        if m and m.group(1) in ALIASES and isinstance(val, (int, float)):
            changes.append({"resource": ALIASES[m.group(1)], "delta": -abs(float(val))})
    if not changes:
        changes = parse_description(ev.get("description", "") + " " + ev.get("event_name", ""))
    return {
        "event_id": ev.get("event_id") or f"EVT-{int(time.time())}",
        "event_name": ev.get("event_name") or "Judge event",
        "description": ev.get("description") or "",
        "changes": changes,
        "duration_hours": ev.get("duration_hours") or impact.get("duration_hours"),
        "raw": ev,
    }


def apply_changes(pool, changes):
    new, notes = dict(pool), []
    for ch in changes:
        r = ch["resource"]
        cur = new[r]
        if "delta" in ch:
            new[r] = max(0, int(math.floor(new[r] + ch["delta"])))
        if "percent" in ch:
            new[r] = max(0, int(math.floor(new[r] * (1 + ch["percent"] / 100.0))))
        notes.append(f"{r}: {cur} -> {new[r]}")
    return new, notes


# ----------------------------------------------------------------------------- council

class Council:
    def __init__(self, config=None, on_message=None, data_dir="data"):
        self.base_config = copy.deepcopy(config or load_config())
        self.runtime = self.base_config["runtime"]
        self.on_message = on_message
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = self.data_dir / "transcript.jsonl"
        self.agents = {k: Agent(k, self.runtime) for k in SPECS}
        self.history = self._load_history()
        self.allow_override = True
        self.new_scenario()

    # ---- state ---------------------------------------------------------------
    def new_scenario(self, resources=None):
        self.scenario_id = f"S{int(time.time() * 1000) % 10**10}"
        self.resources = dict(resources or self.base_config["resources"])
        self.limits = dict(self.base_config["limits"])
        self.override_active = False
        self.events, self.plans = [], []
        self.plan_version, self.round, self.phase = 0, 0, "baseline"
        self.current_plan = self.approved_plan = None
        self.status, self.result = "IDLE", None
        self.last_validation = None
        for a in self.agents.values():
            a.memory = []

    @property
    def cfg(self):
        c = copy.deepcopy(self.base_config)
        c["resources"] = dict(self.resources)
        c["limits"] = dict(self.limits)
        return c

    def _load_history(self):
        if not self.store.exists():
            return []
        out = []
        for line in self.store.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out

    def _log(self, sender, mtype, content, data=None, source="system", recipient="all"):
        msg = {"id": len(self.history) + 1, "time": now(), "scenario": self.scenario_id, "phase": self.phase,
               "round": self.round, "plan_version": self.plan_version, "sender": sender, "recipient": recipient,
               "type": mtype, "content": content, "data": data or {}, "source": source}
        self.history.append(msg)
        with open(self.store, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")
        for a in self.agents.values():
            a.observe(msg)
        if self.on_message:
            self.on_message(msg)
        return msg

    def messages(self, all_scenarios=False):
        return list(self.history) if all_scenarios else [m for m in self.history if m["scenario"] == self.scenario_id]

    def reset(self):
        self._log("judge", "Reset", "Scenario reset by judge. Transcript history is kept.")
        self.new_scenario()

    def clear_history(self):
        self.history = []
        if self.store.exists():
            self.store.unlink()
        self.new_scenario()

    # ---- helpers -------------------------------------------------------------
    def label(self, key):
        return self.base_config["labels"].get(key, key)

    def _mode_ids(self, dept):
        return {m["type"]: mid for mid, m in self.base_config["modes"][dept].items()}

    def _pkg(self, dept, mode_id):
        m = get_mode(self.base_config, dept, mode_id)
        return {"mode": mode_id, "name": m["name"], "type": m["type"],
                **{r: m[r] for r in RESOURCES}, "risk": m["risk"]}

    def _new_round(self):
        self.round += 1
        if self.round > self.runtime["max_rounds"]:
            raise Stop("DEADLOCK", f"maximum of {self.runtime['max_rounds']} rounds reached")
        if time.time() > self.deadline:
            raise Stop("TIMEOUT", f"negotiation time budget of {self.runtime['negotiation_timeout_s']}s used up")

    def _ask_many(self, jobs):
        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
            futs = [ex.submit(self.agents[k].ask, kind, ctx, fields, val, fb) for k, kind, ctx, fields, val, fb in jobs]
            return [f.result() for f in futs]

    def _meta(self, src, note, extra=None):
        d = dict(extra or {})
        if note:
            d["fallback_reason"] = note
        return d

    def plan_summary(self, plan):
        cfg = self.cfg
        t = totals(cfg, plan["modes"])
        return {"version": plan["version"], "status": plan["status"],
                "modes": {d: self._pkg(d, m) for d, m in plan["modes"].items()},
                "totals": {r: t[r] for r in RESOURCES}, "risk": t["risk"],
                "reserve": {r: cfg["resources"][r] - t[r] for r in RESOURCES},
                "sacrificing": sacrificing(cfg, plan["modes"]),
                "commitments": plan.get("commitments", []), "votes": plan.get("votes", {})}

    def _plan_text(self, plan):
        s = self.plan_summary(plan)
        modes = ", ".join(f"{self.label(d)} {v['mode']} ({v['name']})" for d, v in s["modes"].items())
        used = " / ".join(f"{r[:3].title()} {s['totals'][r]}" for r in RESOURCES)
        return f"{modes}. Totals: {used}; risk {s['risk']}."

    # ---- public actions ------------------------------------------------------
    def start(self, resources=None, allow_override=True):
        self.new_scenario(resources)
        self.allow_override = allow_override
        self.phase = "baseline"
        self.status = "NEGOTIATING"
        self._log("judge", "Start", "Judge started the scenario with resources: " +
                  ", ".join(f"{r} {v}" for r, v in self.resources.items()))
        self._negotiate()

    def inject_event(self, event, allow_override=True):
        if self.status == "IDLE":
            raise ValueError("Start the scenario before injecting an event.")
        ev = normalize_event(event)
        if not ev["changes"]:
            raise ValueError("The event has no resource change. Enter a change such as 'Power -4' or '30%'.")
        self.allow_override = allow_override
        new, notes = apply_changes(self.resources, ev["changes"])
        self.resources = new
        self.events.append(ev)
        self.phase = f"event-{len(self.events)}"
        self.round = 0
        self._log("judge", "Event", f"{ev['event_name']}: {ev['description'] or 'resource change'}. "
                  + "; ".join(notes), data=ev)
        old = self.approved_plan
        if old:
            v = validate_plan(self.cfg, old)
            if v["result"] == "FAIL":
                old["status"] = "STALE"
                self.status = "STALE"
                self._log("validator", "Validation", f"Approved plan v{old['version']} is now STALE: "
                          + "; ".join(v["reasons"]), data=v)
            else:
                old["status"] = "REVIEW"
                self._log("validator", "Validation", f"Approved plan v{old['version']} still fits the new pool, "
                          "but the event requires the council to reconvene.", data=v)
            for c in old.get("commitments", []):
                problem = commitment_problem(self.cfg, old, c)
                self._log("commander", "Notice", f"Commitment review: '{c['text']}' (owner {self.label(c['owner'])}) - "
                          + (f"INVALID, removed and must be renegotiated: {problem}" if problem
                             else "still affordable, but will be re-confirmed in the new plan."))
        self.status = "NEGOTIATING"
        self._negotiate()

    # ---- negotiation ---------------------------------------------------------
    def _negotiate(self):
        self.round = 0
        self.deadline = time.time() + self.runtime["negotiation_timeout_s"]
        self.status = "NEGOTIATING"
        self.result = None
        try:
            self._negotiate_inner()
        except Stop as s:
            self.status = s.status
            self.result = {"status": s.status, "message": s.reason}
            self._log("commander", "Approval", f"No plan approved. Result: {s.status}. {s.reason}. "
                      "The council needs more time, more resources or a policy change.", data=self.result)

    def _negotiate_inner(self):
        # R1: publish crisis, collect initial requests
        self._new_round()
        last_event = self.events[-1] if self.events else None
        self._log("commander", "Crisis",
                  f"Round {self.round}: pool is " + ", ".join(f"{r} {v}" for r, v in self.resources.items()) +
                  f". Event: {last_event['event_name'] if last_event else 'micrometeorite storm (baseline)'}. "
                  f"Plan version {self.plan_version}. Round limit {self.runtime['max_rounds']}. "
                  "Each department: request one complete mode and say what you lose in Restricted or Sacrifice mode.")
        props = self._collect_proposals("Open with the mode you want. Explain what you would lose in Restricted or Sacrifice.")
        self._commander_objection(props)

        # Is there any solution at all? (optimizer + validator)
        info = self._resolve_limits()
        if info:
            self._finish_infeasible(info)
            return

        # R2: revise after the numeric conflict
        self._new_round()
        props = self._collect_proposals(self._commander_objection(props, log=False))
        self._commander_objection(props)

        # R3+: candidate plans
        cands = feasible_plans(self.cfg)[:6]
        order = self._pick_order(cands)
        for cand in order:
            self._new_round()
            plan = self._new_plan(cand["modes"])
            self._log("commander", "Proposal", f"Draft plan v{plan['version']}: {self._plan_text(plan)}",
                      data=self.plan_summary(plan))
            first = validate_plan(self.cfg, plan)
            self.last_validation = {"version": plan["version"], **first}
            self._log("validator", "Validation", f"Plan v{plan['version']}: {first['result']}"
                      + (" - " + "; ".join(first["reasons"]) if first["reasons"] else ""), data=first)
            sac = sacrificing(self.cfg, plan["modes"])
            if sac:
                refused = self._consent(plan, attached=False)
                plan = self._commitments(plan)
                refused = self._consent(plan, attached=True)
                if refused:
                    plan["status"] = "REJECTED"
                    self._log("commander", "Counteroffer", "A department still refuses Sacrifice. "
                              "Trying a different plan; earlier votes and offers are void.")
                    continue
            self._new_round()
            v = validate_plan(self.cfg, plan)
            self.last_validation = {"version": plan["version"], **v}
            self._log("validator", "Validation", f"Plan v{plan['version']}: {v['result']}"
                      + (" - " + "; ".join(v["reasons"]) if v["reasons"] else " - all checks passed"), data=v)
            if v["result"] == "FAIL":
                plan["status"] = "REJECTED"
                self._log("commander", "Objection", "Validator blocked plan v%d. Trying another plan." % plan["version"])
                continue
            self._vote(plan)
            final = validate_plan(self.cfg, plan, check_vote_records=True)
            self.last_validation = {"version": plan["version"], **final}
            if final["result"] == "PASS":
                self._approve(plan, final)
                return
            plan["status"] = "REJECTED"
            self._log("commander", "Objection", f"Plan v{plan['version']} cannot be approved: "
                      + "; ".join(final["reasons"]) + ". Trying another plan.")
        raise Stop("DEADLOCK", f"all {len(order)} candidate plans were refused or blocked")

    # ---- rounds --------------------------------------------------------------
    def _collect_proposals(self, feedback):
        cfg = self.cfg
        jobs = []
        for d in DEPTS:
            modes = {mid: self._pkg(d, mid) for mid in cfg["modes"][d]}
            ids = self._mode_ids(d)

            def validate(data, d=d):
                if data.get("mode") not in cfg["modes"][d]:
                    raise ValueError(f"mode {data.get('mode')!r} is not a published mode")

            def fallback(d=d, ids=ids):
                mid = ids["standard"] if self.round == 1 else ids["restricted"]
                m = self._pkg(d, mid)
                return {"mode": mid, "consequence": f"{m['name']} mode (risk {m['risk']}).",
                        "reason": f"Needs P{m['power']} W{m['water']} O{m['oxygen']} R{m['robot']} B{m['bandwidth']}."}

            ctx = {"round": self.round, "pool": self.resources, "limits": self.limits,
                   "your_modes": modes, "feedback": feedback}
            jobs.append((d, "proposal", ctx,
                         '"mode" (one of ' + "/".join(modes) + '), "consequence" (what you lose), "reason" (use numbers)',
                         validate, fallback))
        results = self._ask_many(jobs)
        props = {}
        for d, (data, src, note) in zip(DEPTS, results):
            props[d] = data["mode"]
            m = self._pkg(d, data["mode"])
            self._log(d, "Proposal", f"Requests {data['mode']} {m['name']} (P{m['power']} W{m['water']} O{m['oxygen']} "
                      f"R{m['robot']} B{m['bandwidth']}, risk {m['risk']}). {data.get('consequence', '')} {data.get('reason', '')}".strip(),
                      data=self._meta(src, note, {"mode": data["mode"]}), source=src)
        return props

    def _commander_objection(self, props, log=True):
        cfg = self.cfg
        t = totals(cfg, props)
        short = {r: t[r] - cfg["resources"][r] for r in RESOURCES if t[r] > cfg["resources"][r]}
        n_sac = len(sacrificing(cfg, props))
        parts = []
        if short:
            parts.append("resources over the pool: " + ", ".join(f"{r} needs {t[r]} vs {cfg['resources'][r]} (+{v})"
                                                                 for r, v in short.items()))
        if t["risk"] > cfg["limits"]["max_risk"]:
            parts.append(f"risk {t['risk']} exceeds limit {cfg['limits']['max_risk']}")
        if n_sac > cfg["limits"]["max_sacrifice"]:
            parts.append(f"{n_sac} Sacrifice modes requested, limit {cfg['limits']['max_sacrifice']}")
        text = ("Conflict: " + "; ".join(parts) + ". Departments must change modes or accept a sacrifice."
                if parts else "No numeric conflict in the requested modes.")
        if log:
            self._log("commander", "Objection" if parts else "Notice", text, data={"totals": t})
        return text

    def _resolve_limits(self):
        """Use the optimizer. Returns None if a plan exists, or an INFEASIBLE explanation dict."""
        normal = dict(self.base_config["limits"])
        self.limits = dict(normal)
        self.override_active = False
        if solve(self.cfg)["status"] == "FEASIBLE":
            return None
        if self.allow_override:
            self.limits = {**normal, **self.base_config["override_limits"]}
            if solve(self.cfg)["status"] == "FEASIBLE":
                self.override_active = True
                self._log("commander", "Notice",
                          f"No plan fits the normal limits. Commander invokes the CRISIS OVERRIDE: risk limit "
                          f"{normal['max_risk']} -> {self.limits['max_risk']} and up to {self.limits['max_sacrifice']} "
                          "Sacrifice modes. Every sacrificing department still needs two valid return commitments.")
                return None
            info = explain_infeasible(self.cfg)
            info["override_tried"] = True
            return info
        info = explain_infeasible(self.cfg)
        info["override_tried"] = False
        return info

    def _finish_infeasible(self, info):
        msg = ("INFEASIBLE. Blocking constraints: " + "; ".join(info["blocking_constraints"]) +
               f". Needed external change: {info['needed_change']}." +
               (" Crisis Override (risk 28, two Sacrifice modes) was tried and is still not enough."
                if info.get("override_tried") else ""))
        self.status = "INFEASIBLE"
        self.result = {"status": "INFEASIBLE", "message": msg, "details": info}
        self.current_plan = None
        self.last_validation = {"version": self.plan_version, "result": "INFEASIBLE", "reasons": info["blocking_constraints"],
                                "checks": {}, "totals": None}
        self._log("commander", "Approval", f"Result: {msg}", data=self.result)

    def _pick_order(self, cands):
        cfg = self.cfg
        desc = []
        for i, c in enumerate(cands):
            sac = sacrificing(cfg, c["modes"])
            desc.append({"index": i, "modes": c["modes"], "risk": c["totals"]["risk"],
                         "sacrificing": [self.label(d) for d in sac],
                         "what_is_lost": [cfg["sacrifice_loss"][c["modes"][d]] for d in sac]})

        def validate(data):
            if not isinstance(data.get("choice"), int) or not 0 <= data["choice"] < len(cands):
                raise ValueError("choice must be a valid candidate index")

        (data, src, note), = self._ask_many([("commander", "pick_plan",
            {"candidates": desc, "question": "Which plan should the council try first? Weigh short-term habitat "
             "safety against long-term repair capacity."}, '"choice" (candidate index), "reason"', validate,
            lambda: {"choice": 0, "reason": "Lowest risk first."})])
        order = [cands[data["choice"]]] + [c for i, c in enumerate(cands) if i != data["choice"]]
        self._log("commander", "Proposal", f"Candidate plans from the optimizer: {len(cands)}. Trying plan #{data['choice']} "
                  f"first. {data.get('reason', '')}", data=self._meta(src, note, {"candidates": desc}), source=src)
        return order

    def _new_plan(self, modes, commitments=None):
        self.plan_version += 1
        plan = {"version": self.plan_version, "modes": dict(modes), "commitments": list(commitments or []),
                "votes": {}, "status": "DRAFT", "phase": self.phase, "round": self.round}
        self.plans.append(plan)
        self.current_plan = plan
        return plan

    def _consent(self, plan, attached):
        cfg = self.cfg
        sac = sacrificing(cfg, plan["modes"])
        jobs = []
        for d in sac:
            mid = plan["modes"][d]
            texts = [c["text"] for c in plan["commitments"] if c["beneficiary"] == d and c["accepted"]]

            def validate(data):
                if not isinstance(data.get("accept"), bool):
                    raise ValueError("accept must be true or false")

            ctx = {"plan_version": plan["version"], "your_mode": self._pkg(d, mid), "what_you_lose": cfg["sacrifice_loss"][mid],
                   "commitments_attached": texts, "required_returns": [r["text"] for r in cfg["return_agreements"][mid]],
                   "other_modes": plan["modes"], "question": "Do you accept Sacrifice mode in this plan?"}
            jobs.append((d, "sacrifice_consent", ctx, '"accept" (true/false), "reason"', validate,
                         lambda texts=texts: {"accept": len(texts) >= 2,
                                              "reason": "Sacrifice is acceptable only with two fair return commitments."
                                              if len(texts) < 2 else "Two return commitments are attached."}))
        refused = []
        for d, (data, src, note) in zip(sac, self._ask_many(jobs)):
            mid = plan["modes"][d]
            if data["accept"]:
                self._log(d, "Proposal", f"Accepts {mid} Sacrifice on plan v{plan['version']}. {data.get('reason', '')}",
                          data=self._meta(src, note), source=src)
            else:
                refused.append(d)
                self._log(d, "Objection", f"REFUSES Sacrifice mode {mid} ({cfg['sacrifice_loss'][mid]}). "
                          f"{data.get('reason', '')}", data=self._meta(src, note, {"refused": True}), source=src)
        return refused

    def _commitments(self, plan):
        cfg = self.cfg
        sac = sacrificing(cfg, plan["modes"])
        new_plan = self._new_plan(plan["modes"])
        reqs = [(d, r) for d in sac for r in cfg["return_agreements"][plan["modes"][d]]]
        self._log("commander", "Counteroffer", "To make the sacrifice fair, the Commander requests return commitments: "
                  + "; ".join(f"{self.label(r['owner'])} -> {self.label(d)}: {r['text']}" for d, r in reqs))
        jobs = []
        for d, r in reqs:
            problem = commitment_problem(cfg, new_plan, {**r, "beneficiary": d})

            def validate(data):
                if not isinstance(data.get("commit"), bool):
                    raise ValueError("commit must be true or false")

            ctx = {"beneficiary": self.label(d), "promise": r["text"], "cost": {"resource": r.get("resource"), "amount": r.get("amount")},
                   "can_afford": problem is None, "problem": problem or "", "plan_modes": new_plan["modes"],
                   "reserve": {k: cfg["resources"][k] - totals(cfg, new_plan["modes"])[k] for k in RESOURCES}}
            jobs.append((r["owner"], "commitment", ctx, '"commit" (true/false), "reason"', validate,
                         lambda problem=problem: {"commit": problem is None,
                                                  "reason": "Affordable and fair." if problem is None else problem}))
        for (d, r), (data, src, note) in zip(reqs, self._ask_many(jobs)):
            if data["commit"]:
                c = {"id": f"C{len(new_plan['commitments']) + 1}-v{new_plan['version']}", "owner": r["owner"], "beneficiary": d,
                     "text": r["text"], "resource": r.get("resource"), "amount": r.get("amount"),
                     "expiry": "next cycle", "accepted": True, "plan_version": new_plan["version"]}
                new_plan["commitments"].append(c)
                self._log(r["owner"], "Commitment", f"COMMITS to {self.label(d)}: {r['text']} (expires next cycle). "
                          f"{data.get('reason', '')}", data=self._meta(src, note, c), source=src)
            else:
                self._log(r["owner"], "Objection", f"Declines the commitment to {self.label(d)}: {r['text']}. "
                          f"{data.get('reason', '')}", data=self._meta(src, note, {"declined": True}), source=src)
        plan["status"] = "SUPERSEDED"
        self._log("commander", "Notice", f"Plan v{new_plan['version']} replaces v{plan['version']} (commitments added). "
                  "All earlier votes are cleared.")
        return new_plan

    def _vote(self, plan):
        cfg = self.cfg
        summary = self.plan_summary(plan)
        jobs = []
        for d in DEPTS:
            def validate(data):
                if str(data.get("vote", "")).upper() not in ("ACCEPT", "REJECT"):
                    raise ValueError("vote must be ACCEPT or REJECT")

            ctx = {"plan_version": plan["version"], "your_mode": summary["modes"][d], "plan": summary,
                   "validator": "PASS", "you_are_sacrificing": d in summary["sacrificing"]}
            jobs.append((d, "vote", ctx, '"vote" ("ACCEPT" or "REJECT"), "reason"', validate,
                         lambda: {"vote": "ACCEPT", "reason": "The validator passed and my mission is protected as well as the pool allows."}))
        for d, (data, src, note) in zip(DEPTS, self._ask_many(jobs)):
            vote = str(data["vote"]).upper()
            plan["votes"][d] = {"vote": vote, "version": plan["version"], "reason": data.get("reason", "")}
            self._log(d, "Vote", f"{vote} plan v{plan['version']}. {data.get('reason', '')}",
                      data=self._meta(src, note, {"vote": vote, "version": plan["version"]}), source=src)

    def _approve(self, plan, final):
        if self.approved_plan and self.approved_plan is not plan:
            self.approved_plan["status"] = "SUPERSEDED"
        plan["status"] = "APPROVED"
        self.approved_plan = plan
        self.status = "APPROVED"
        s = self.plan_summary(plan)
        self.result = {"status": "APPROVED", "plan_version": plan["version"], "summary": s}
        self._log("commander", "Approval", f"APPROVED plan v{plan['version']} after validator PASS and four ACCEPT votes on "
                  f"the same version. {self._plan_text(plan)} Reserve: "
                  + ", ".join(f"{r} {v}" for r, v in s["reserve"].items()) + ".", data=self.result)

    # ---- exports -------------------------------------------------------------
    def fallback_count(self):
        return sum(1 for m in self.messages(True) if m["source"] == "fallback-rules")

    def export_plan(self):
        plan = self.approved_plan
        out = {"scenario": self.scenario_id, "generated_at": now(), "status": self.status, "llm": llm.describe(),
               "fallback_messages_in_transcript": self.fallback_count(), "resources": self.resources,
               "limits": self.limits, "crisis_override_used": self.override_active, "events": [e["raw"] for e in self.events]}
        if plan:
            s = self.plan_summary(plan)
            out["final_plan"] = s
            out["validation"] = {k: v for k, v in validate_plan(self.cfg, plan, check_vote_records=True).items() if k != "totals"}
            out["commander_decision"] = next((m["content"] for m in reversed(self.messages()) if m["type"] == "Approval"), "")
            out["agent_records"] = self.schema_records(plan)
        else:
            out["final_plan"] = None
            out["result"] = self.result
        out["plan_versions"] = [{"version": p["version"], "status": p["status"], "modes": p["modes"], "phase": p["phase"]}
                                for p in self.plans]
        return out

    def schema_records(self, plan):
        """One record per agent in the shape of the starter kit's output_schema.json (extra fields added)."""
        s = self.plan_summary(plan)
        recs = []
        for d in DEPTS:
            v = plan["votes"].get(d, {})
            pk = s["modes"][d]
            recs.append({"round": self.round, "agent_id": SCHEMA_IDS[d],
                         "proposal": {"oxygen_units": pk["oxygen"], "water_liters": pk["water"], "power_kwh": pk["power"],
                                      "food_rations": None, "robot_time_units": pk["robot"], "bandwidth_units": pk["bandwidth"],
                                      "mode": pk["mode"], "mode_name": pk["name"], "plan_version": plan["version"],
                                      "justification": v.get("reason", "")},
                         "vote": "APPROVE" if v.get("vote") == "ACCEPT" else v.get("vote", "REJECT"),
                         "risk_score": s["risk"], "flag_human_review": s["risk"] > 20})
        return recs

    def transcript_json(self, all_scenarios=True):
        return json.dumps(self.messages(all_scenarios), indent=2)

    def transcript_csv(self, all_scenarios=True):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "time", "scenario", "phase", "round", "plan_version", "sender", "type", "source", "content"])
        for m in self.messages(all_scenarios):
            w.writerow([m["id"], m["time"], m["scenario"], m["phase"], m["round"], m["plan_version"], m["sender"], m["type"],
                        m["source"], m["content"]])
        return buf.getvalue()

    def crisis_log_text(self):
        lines = [f"CRISIS HANDLING LOG  scenario {self.scenario_id}  generated {now()}  model: {llm.describe()}",
                 f"Messages produced by the rule-based fallback: {self.fallback_count()}", ""]
        for m in self.messages():
            if m["phase"].startswith("event") or m["type"] == "Event":
                lines.append(f"[{m['phase']} R{m['round']} v{m['plan_version']}] {self.label(m['sender'])} "
                             f"({m['type']}, {m['source']}): {m['content']}")
        if len(lines) == 3:
            lines.append("No crisis event has been injected in this scenario yet.")
        return "\n".join(lines)

    def opening_log_text(self):
        lines = [f"OPENING NEGOTIATION LOG  scenario {self.scenario_id}  model: {llm.describe()}", ""]
        for m in self.messages():
            if m["phase"] == "baseline":
                lines.append(f"[R{m['round']} v{m['plan_version']}] {self.label(m['sender'])} ({m['type']}, {m['source']}): {m['content']}")
        return "\n".join(lines)
