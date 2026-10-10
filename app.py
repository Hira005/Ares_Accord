"""ARES ACCORD dashboard.  Run:  streamlit run app.py"""
import json
import os

import pandas as pd
import streamlit as st

import llm
from council import DEPTS, Council
from validator import RESOURCES, load_config

try:  # optional: load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

st.set_page_config(page_title="ARES ACCORD - Council", page_icon="🪐", layout="wide")

ICON = {"Crisis": "📡", "Event": "⚡", "Proposal": "📋", "Objection": "✋", "Counteroffer": "🔁", "Commitment": "🤝",
        "Vote": "🗳️", "Approval": "✅", "Validation": "🛡️", "Notice": "ℹ️", "Start": "▶️", "Reset": "♻️"}
STATUS_COLOR = {"APPROVED": "green", "NEGOTIATING": "orange", "STALE": "red", "INFEASIBLE": "red",
                "DEADLOCK": "red", "TIMEOUT": "red", "IDLE": "gray"}

if "council" not in st.session_state:
    st.session_state.council = Council(on_message=None)
council: Council = st.session_state.council
CFG = load_config()


def render_msg(m, container=st):
    with container.chat_message("assistant" if m["sender"] != "judge" else "user", avatar=ICON.get(m["type"], "💬")):
        badge = "🤖 model" if m["source"].startswith("llm:") else ("⚠️ FALLBACK (rule-based)" if m["source"] == "fallback-rules" else "⚙️ system")
        st.markdown(f"**{council.label(m['sender']) if m['sender'] in CFG['labels'] else m['sender'].title()}** · {m['type']} · "
                    f"{m['phase']} · round {m['round']} · plan v{m['plan_version']} · {badge}")
        st.write(m["content"])


# ---------------------------------------------------------------- header placeholders (filled after actions run)
header = st.empty()


def render_header():
    with header.container():
        c = council
        color = STATUS_COLOR.get(c.status, "gray")
        st.title("🪐 ARES ACCORD – Council Dashboard")
        a, b, d, e, f = st.columns(5)
        a.markdown(f"**Status**<br>:{color}[**{c.status}**]", unsafe_allow_html=True)
        b.metric("Round", c.round)
        d.metric("Plan version", c.plan_version)
        e.markdown(f"**Event**<br>{c.events[-1]['event_name'] if c.events else 'Baseline storm'}", unsafe_allow_html=True)
        f.markdown(f"**Model**<br>{llm.describe()}", unsafe_allow_html=True)
        if c.override_active:
            st.warning("Crisis Override active: risk limit 28, up to two Sacrifice modes (both still need return commitments).")
        if llm.provider() is None:
            st.info("No API key found: agents run in clearly-labelled FALLBACK (rule-based) mode. Set a key in .env to use a language model.")


live = st.container()

# ---------------------------------------------------------------- Judge Controls (sidebar)
sb = st.sidebar
sb.header("Judge Controls")
sb.caption("Enter resources, start the crisis, inject events, reset and export.")
res_in = {}
sb.subheader("Resource pool")
for r in RESOURCES:
    res_in[r] = sb.number_input(r.title(), min_value=0, value=int(CFG["resources"][r]), step=1, key=f"res_{r}")
allow_override = sb.checkbox("Allow Commander to use Crisis Override", value=True)


def run_with_live(fn, title):
    box = live.status(title, expanded=True)
    council.on_message = lambda m: render_msg(m, box)
    try:
        fn()
        box.update(label=f"{title} – finished: {council.status}", state="complete", expanded=False)
    except Exception as e:
        box.update(label=f"{title} – error: {e}", state="error")
        st.session_state["last_error"] = str(e)
    finally:
        council.on_message = None


if sb.button("▶ Start scenario", type="primary"):
    run_with_live(lambda: council.start(resources=dict(res_in), allow_override=allow_override), "Negotiating baseline crisis")

sb.divider()
sb.subheader("Inject emergency event")
PRESETS = {"Custom": {}, "Solar aftershock (Power -4)": {"power": -4}, "Water contamination (Water -3)": {"water": -3},
           "Secondary oxygen leak (Oxygen -3)": {"oxygen": -3}, "Rover actuator failure (Robot -4)": {"robot": -4},
           "Relay interference (Bandwidth -2)": {"bandwidth": -2}}
preset = sb.selectbox("Practice preset (testing only)", list(PRESETS))
ev_name = sb.text_input("Event name", value="" if preset == "Custom" else preset.split(" (")[0])
ev_desc = sb.text_area("Description (numbers like 'Power decreases by 4' are parsed automatically)", value="")
sb.caption("Or set exact changes (negative = decrease):")
deltas, pcts = {}, {}
for r in RESOURCES:
    c1, c2 = sb.columns(2)
    deltas[r] = c1.number_input(f"{r.title()} Δ", value=float(PRESETS[preset].get(r, 0)), step=1.0, key=f"d_{r}_{preset}")
    pcts[r] = c2.number_input(f"{r.title()} %", value=0.0, step=5.0, key=f"p_{r}_{preset}")
ev_json = sb.text_area("…or paste event JSON (starter-kit format)", value="", height=80)
if sb.button("⚡ Inject event"):
    try:
        if ev_json.strip():
            event = json.loads(ev_json)
        else:
            event = {"event_name": ev_name or "Judge event", "description": ev_desc,
                     "changes": [{"resource": r, "delta": deltas[r], "percent": pcts[r]} for r in RESOURCES]}
        run_with_live(lambda: council.inject_event(event, allow_override=allow_override), "Renegotiating after event")
    except json.JSONDecodeError as e:
        sb.error(f"Event JSON is not valid: {e}")

sb.divider()
c1, c2 = sb.columns(2)
if c1.button("♻ Reset"):
    council.reset()
    st.rerun()
if c2.button("🗑 Clear history"):
    council.clear_history()
    st.rerun()
if st.session_state.get("last_error"):
    sb.error(st.session_state["last_error"])
    st.session_state["last_error"] = None

sb.divider()
sb.subheader("Export")
sb.download_button("final_allocation.json", json.dumps(council.export_plan(), indent=2), "final_allocation.json", "application/json")
sb.download_button("council_transcript.json", council.transcript_json(), "council_transcript.json", "application/json")
sb.download_button("council_transcript.csv", council.transcript_csv(), "council_transcript.csv", "text/csv")
sb.download_button("crisis_handling_log.txt", council.crisis_log_text(), "crisis_handling_log.txt", "text/plain")
sb.download_button("opening_negotiation_log.txt", council.opening_log_text(), "opening_negotiation_log.txt", "text/plain")

render_header()

# ---------------------------------------------------------------- views
tab_mc, tab_tr, tab_mb, tab_val = st.tabs(["Mission Control", "Council Transcript", "Mode Board", "Validation"])
plan = council.current_plan or council.approved_plan
S = council.plan_summary(plan) if plan else None

with tab_mc:
    st.subheader("Resources")
    used = S["totals"] if S else {r: 0 for r in RESOURCES}
    rows = [{"Resource": r.title(), "Available": council.resources[r], "Used by plan": used[r],
             "Reserve": council.resources[r] - used[r]} for r in RESOURCES]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    cols = st.columns(5)
    for col, r in zip(cols, RESOURCES):
        pool = max(1, council.resources[r])
        col.progress(min(1.0, used[r] / pool), text=f"{r.title()} {used[r]}/{council.resources[r]}")
    a, b, c = st.columns(3)
    a.metric("Total risk", f"{S['risk']} / {council.limits['max_risk']}" if S else f"– / {council.limits['max_risk']}")
    b.metric("Plan shown", f"v{S['version']} ({S['status']})" if S else "none")
    c.metric("Approved plan", f"v{council.approved_plan['version']} ({council.approved_plan['status']})" if council.approved_plan else "none")
    if council.result and council.result["status"] in ("INFEASIBLE", "DEADLOCK", "TIMEOUT"):
        st.error(council.result["message"])
    if council.events:
        st.subheader("Events this scenario")
        st.dataframe(pd.DataFrame([{"Event": e["event_name"], "Description": e["description"],
                                    "Changes": ", ".join(f"{c['resource']} {c.get('delta', '')}{'%+d' % c['percent'] if 'percent' in c else ''}" for c in e["changes"])}
                                   for e in council.events]), hide_index=True)

with tab_tr:
    scope = st.radio("Show", ["This scenario", "All scenarios (persistent history)"], horizontal=True)
    msgs = council.messages(all_scenarios=scope.startswith("All"))
    types = st.multiselect("Filter by message type", sorted({m["type"] for m in msgs}), default=[])
    st.caption(f"{len(msgs)} messages · fallback messages in history: {council.fallback_count()}")
    for m in msgs:
        if not types or m["type"] in types:
            render_msg(m)

with tab_mb:
    if not S:
        st.info("No plan yet. Press **Start scenario** in the sidebar.")
    else:
        st.caption(f"Plan v{S['version']} – {S['status']}")
        cols = st.columns(4)
        for col, d in zip(cols, DEPTS):
            pk = S["modes"][d]
            with col.container(border=True):
                st.markdown(f"### {council.label(d)}")
                st.markdown(f"**{pk['mode']} – {pk['name']}**  (risk {pk['risk']})")
                st.caption(f"Power {pk['power']} · Water {pk['water']} · Oxygen {pk['oxygen']} · Robot {pk['robot']} · Bandwidth {pk['bandwidth']}")
                if pk["type"] == "sacrifice":
                    st.error("Sacrifice: " + CFG["sacrifice_loss"][pk["mode"]])
                elif pk["type"] == "restricted":
                    st.warning("Restricted: recovery debt")
                else:
                    st.success("Standard: full mission")
                got = [c for c in S["commitments"] if c["beneficiary"] == d]
                gave = [c for c in S["commitments"] if c["owner"] == d]
                if got:
                    st.markdown("**Receives:**")
                    for c in got:
                        st.write(f"• {council.label(c['owner'])}: {c['text']} (expires {c['expiry']})")
                if gave:
                    st.markdown("**Promises:**")
                    for c in gave:
                        st.write(f"• to {council.label(c['beneficiary'])}: {c['text']}")
                v = S["votes"].get(d)
                st.markdown("**Vote:** " + (f"{v['vote']} (v{v['version']})" if v else "—"))
        st.markdown("**Commander commitments:** " + (", ".join(c["text"] for c in S["commitments"] if c["owner"] == "commander") or "—"))
        if S["status"] in ("STALE", "REVIEW"):
            st.error("This plan is STALE: an event changed the resource pool.")

with tab_val:
    lv = council.last_validation
    if not lv:
        st.info("No validation has run yet.")
    else:
        res = lv["result"]
        (st.success if res == "PASS" else st.error)(f"Validator result for plan v{lv['version']}: **{res}**")
        names = {"modes": "Mode packages unchanged (one per department)", "resources": "Resources within the pool",
                 "risk": "Risk and Sacrifice limits", "returns": "Return commitments", "votes": "Four ACCEPT votes on this version"}
        for key, label in names.items():
            if key in lv.get("checks", {}):
                bad = lv["checks"][key]
                st.markdown(f"{'❌' if bad else '✅'} **{label}**")
                for r in bad:
                    st.write(f"   - {r}")
        if res == "INFEASIBLE":
            for r in lv["reasons"]:
                st.write(f"- {r}")
            st.info(council.result["message"])
        if lv.get("totals"):
            st.caption("Totals: " + ", ".join(f"{k} {v}" for k, v in lv["totals"].items()))
    if council.approved_plan:
        st.divider()
        st.markdown("**Commander decision:** " + next((m["content"] for m in reversed(council.messages()) if m["type"] == "Approval"), ""))
