"""Draw architecture_diagram.png (matplotlib only)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def box(ax, x, y, w, h, text, fc, ec="#333", fs=9):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.15", fc=fc, ec=ec, lw=1.3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs)


def arrow(ax, a, b, text="", dy=0.12):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="->", lw=1.3, color="#333"))
    if text:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + dy, text, ha="center", fontsize=7.5, color="#444")


fig, ax = plt.subplots(figsize=(14, 8.2))
ax.set_xlim(0, 14)
ax.set_ylim(0, 8.2)
ax.axis("off")
ax.text(7, 7.9, "ARES ACCORD - architecture", ha="center", fontsize=15, weight="bold")

box(ax, 0.3, 6.0, 3.2, 1.3, "Judge / Dashboard (Streamlit)\nMission Control · Transcript\nMode Board · Validation\nJudge Controls (start, event, reset, export)", "#e8f1ff")
box(ax, 5.0, 6.0, 4.0, 1.3, "Council orchestrator (council.py)\nrounds · plan versions · vote clearing\nround limit + timeout · event handling", "#fff3d6")
box(ax, 10.5, 6.0, 3.2, 1.3, "Storage\ndata/transcript.jsonl (persistent)\nexports: final_allocation.json\ntranscript .json/.csv · crisis log", "#eee")
arrow(ax, (3.5, 6.65), (5.0, 6.65), "start / event / reset")
arrow(ax, (5.0, 6.35), (3.5, 6.35), "live messages")
arrow(ax, (9.0, 6.65), (10.5, 6.65), "every message")

box(ax, 4.6, 3.9, 4.8, 1.2, "COMMANDER agent\nshares crisis · finds conflicts · picks plan order\ncollects commitments · approves only on PASS + 4 ACCEPT", "#ffe0e0")
arrow(ax, (7, 6.0), (7, 5.1), "messages")

xs = [0.4, 3.9, 7.4, 10.9]
names = ["Life Support", "Medical", "Food Production", "Engineering"]
for x, n in zip(xs, names):
    box(ax, x, 1.9, 2.7, 1.2, f"{n} agent\nown goals · state · memory\nproposal · objection · commitment · vote", "#e3f7e3")
    arrow(ax, (7, 3.9), (x + 1.35, 3.1))

box(ax, 0.4, 0.2, 4.0, 1.1, "LLM client (llm.py)\nGemini / Anthropic / OpenAI-compatible\nJSON output · timeout · validation", "#f0e6ff")
box(ax, 4.9, 0.2, 4.2, 1.1, "FALLBACK rules (labelled in the UI)\nused only if the model fails,\ntimes out or returns invalid output", "#fdebd0")
box(ax, 10.0, 3.7, 3.8, 1.6, "Deterministic VALIDATOR + OPTIMIZER\n(outside the language model)\nmodes unchanged · resources · risk ≤ 24\none Sacrifice · 2 commitments\n4 votes on the same plan version", "#ffd6e7")
arrow(ax, (2.4, 1.3), (2.4, 1.9), "ask")
arrow(ax, (6.9, 1.3), (5.2, 1.9), "if LLM fails", dy=-0.35)
arrow(ax, (9.4, 4.5), (10.0, 4.5), "")
ax.text(9.7, 4.65, "checks\nevery plan", ha="center", fontsize=7, color="#444")
fig.savefig("architecture_diagram.png", dpi=140, bbox_inches="tight")
print("wrote architecture_diagram.png")
