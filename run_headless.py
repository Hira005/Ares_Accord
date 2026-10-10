"""Run a full scenario WITHOUT the UI and write the evidence files.  Uses the same Council the dashboard uses.

    python run_headless.py                       # baseline + Solar aftershock (Power -4)
    python run_headless.py "Power=-4"            # any event as resource=change (use 30% for percent)
    python run_headless.py "Water=-10%" "Bandwidth=-3"

Files go to ./evidence/ . Every message is labelled with where it came from (model or FALLBACK).
"""
import json
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import llm
from council import Council


def parse_args(args):
    changes = []
    for a in args:
        res, val = a.split("=")
        if val.endswith("%"):
            changes.append({"resource": res.strip().lower(), "percent": float(val[:-1])})
        else:
            changes.append({"resource": res.strip().lower(), "delta": float(val)})
    return changes


def main():
    out = Path("evidence")
    out.mkdir(exist_ok=True)
    c = Council(data_dir=tempfile.mkdtemp())
    print("model:", llm.describe())
    c.on_message = lambda m: print(f"[{m['phase']} R{m['round']} v{m['plan_version']}] {m['sender']:<13} {m['type']:<11} {m['source'][:14]:<14} {m['content'][:130]}")
    c.start()
    (out / "opening_negotiation_log.txt").write_text(c.opening_log_text(), encoding="utf-8")
    (out / "final_allocation_baseline.json").write_text(json.dumps(c.export_plan(), indent=2), encoding="utf-8")
    changes = parse_args(sys.argv[1:]) or [{"resource": "power", "delta": -4}]
    c.inject_event({"event_name": "Headless test event", "description": "Command-line event", "changes": changes})
    (out / "crisis_handling_log.txt").write_text(c.crisis_log_text(), encoding="utf-8")
    (out / "final_allocation.json").write_text(json.dumps(c.export_plan(), indent=2), encoding="utf-8")
    (out / "council_transcript.json").write_text(c.transcript_json(), encoding="utf-8")
    (out / "council_transcript.csv").write_text(c.transcript_csv(), encoding="utf-8")
    print("\nfinal status:", c.status, "| fallback messages:", c.fallback_count(), "| files written to", out.resolve())


if __name__ == "__main__":
    main()
