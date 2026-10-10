# ARES Accord - Final Submission Checklist

**Team Name:** ____________________   **Submission Time:** ____________   **GitHub link:** ____________________

## Core requirements
- [x] Multi-agent system, 5 agents with separate goals, state, memory (`agents.py`)
- [x] Commander coordinating all departments (`council.py`)
- [x] Validator rejecting unsafe plans (`validator.py`; Validation tab; tests)
- [x] Resource pool respected in the final allocation (validator PASS required)
- [x] Crisis event handled (event injection, STALE plan, renegotiation, Crisis Override, INFEASIBLE)

## UI requirements
- [x] Visual dashboard (Streamlit): `streamlit run app.py`
- [x] Council transcript (who, type, round, plan version, source) - persistent across scenarios
- [x] Resource pool + plan version/status always visible (header + Mission Control)
- [x] Final plan on Mode Board; validation PASS/FAIL with reasons
- [x] Event injection through the UI (form, free text, pasted JSON)

## Output files (in `evidence/`; re-generate with your API key: `python run_headless.py`)
- [ ] `final_allocation.json`  ← regenerate with a real model before submitting
- [ ] `council_transcript.json` / `.csv`
- [ ] `crisis_handling_log.txt`
- [x] `architecture_diagram.png`
- [x] `README.md`, `requirements.txt`, `.env.example`, `TEAM_DECLARATION.md`

## Still to do by hand
- [ ] Put an API key in `.env`, run once in the UI, confirm messages say "model" not "FALLBACK"
- [ ] Re-run `python run_headless.py` (or use the Export buttons) so the evidence comes from a real model run
- [ ] Fill team name and member names in `TEAM_DECLARATION.md`
- [ ] Record the 3-5 minute video (see `VIDEO_SCRIPT.md`) and check it opens
- [ ] Confirm `.env` and `data/` are NOT in the repo (`.gitignore` covers them)
- [ ] Push to GitHub and submit the link + video
- [ ] Get `judges_scoring_rubric.md` from the starter repo and compare

## Bonus
- [~] Human-in-the-loop: only `flag_human_review` is set (risk > 20); no blocking approval step
- [x] Agent memory across rounds (each agent keeps its message memory and sees it in every prompt)
- [x] Graceful handling of model failure, invalid output, timeout (labelled fallback; tested)
