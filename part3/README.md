# Part 3 — Correlation & Insight Engine

**ALL DATA IN THIS PROJECT IS SYNTHETIC.** Nothing here reads a real calendar,
a real person, or a real wearable.

What it does, in three lines:

1. Joins Part 1's daily stress scores to Part 2's calendar features per person-day.
2. **Gemini proposes, Python proves** — the LLM only emits hypotheses in a tiny
   DSL (`feature op threshold @ lag`); every number is computed by `validator.py`.
3. Emits two files: a private per-person insight, and a person-free team aggregate.

## THE PRIVACY RULE

| file | contains | who sees it |
| --- | --- | --- |
| `data/out/employee_insight.json` | `person_id`, daily stress trend, per-person patterns | **PRIVATE.** Part 5 shows a person only their own record. Never an employer view. |
| `data/out/team_correlations.json` | **no `person_id` anywhere**, pattern-level counts only | Ungated by design. **Part 4 owns the k>=5 gate** and must enforce it before anything reaches a manager. |

The team file is built by *aggregating across people* (`emit.aggregate_team_patterns`),
never by concatenating individual insights — concatenation would let a manager
re-identify someone from calendar specifics. Part 3 hands Part 4 honest counts,
*including counts below the k floor*; suppressing them is Part 4's job, not ours.

## Setup

```bash
pip install -r part3/requirements.txt
cp part3/.env.example part3/.env      # then add your GEMINI_API_KEY
```

Everything except FastAPI/uvicorn/pytest is stdlib by design. `emit.py` is stdlib-only.

## Regenerate the synthetic fixtures

```bash
python part3/contracts/make_fixtures.py            # --people 12 --days 28
```

Writes `part3/data/fixtures/{stress_scores.csv,meeting_features.json,_ground_truth.json}`.

## Run the API

```bash
uvicorn api.main:app --reload --port 8000 --app-dir part3
```

| endpoint | does |
| --- | --- |
| `GET /health` | `{"status":"ok","data_provenance":"SIMULATED"}` |
| `POST /analyze` | body `{"offline": false, "limit": null}` — runs the pipeline over the fixtures, writes both output files, returns a summary. `offline: true` skips all LLM calls. Errors come back as a 500 with the message and traceback (demo-visible on purpose). |
| `GET /people` | `{"count": n, "people": ["emp_001", ...]}` — for Part 5's picker |
| `GET /employee/{person_id}` | that person's private insight; 404 if unknown |
| `GET /team/correlations` | the whole team file; Part 4 consumes this |

Any read endpoint returns **409** if the output files don't exist yet — POST `/analyze` first.
CORS is wide open (`allow_origins=["*"]`) so Part 5's UI can call it from anywhere.

Quick smoke:

```bash
curl localhost:8000/health
curl -X POST localhost:8000/analyze -H "Content-Type: application/json" -d '{"offline":true}'
curl localhost:8000/people
curl localhost:8000/employee/emp_001
curl localhost:8000/team/correlations
```

## Output shapes

### `data/out/employee_insight.json` (PRIVATE)

```json
{
  "synthetic": true,
  "data_provenance": "SIMULATED",
  "generated_at": "2026-09-19T14:02:11.930112+00:00",
  "people": {
    "emp_001": {
      "person_id": "emp_001",
      "date_range": ["2026-08-24", "2026-09-18"],
      "stress_trend": [
        {"date": "2026-08-24", "stress_score": 43.0},
        {"date": "2026-08-25", "stress_score": 58.5}
      ],
      "average_stress": 51.2,
      "top_patterns": [
        {
          "pattern": "back_to_back_blocks >= 2 (1 day(s) earlier)",
          "feature": "back_to_back_blocks",
          "lift_points": 11.4,
          "mean_stress_when_present": 62.1,
          "mean_stress_when_absent": 50.7,
          "days_observed": 8,
          "days_compared": 14,
          "correlation_r": 0.47,
          "p_value": 0.012,
          "severity": "moderate"
        }
      ],
      "insight_text": "Days after two or more back-to-back blocks run about 11 points higher.",
      "suggested_action": "Try protecting a 15-minute gap between your morning blocks.",
      "critic_log": [
        {"original_text": "...", "approved": true, "issues": [], "revised_text": "..."}
      ],
      "llm_used": true,
      "data_provenance": "SIMULATED",
      "synthetic": true
    }
  }
}
```

`GET /employee/{person_id}` returns exactly one of the inner `people` objects.

### `data/out/team_correlations.json` (NO person_id — Part 4)

```json
{
  "n_people_analysed": 12,
  "date_range": ["2026-08-24", "2026-09-18"],
  "patterns": [
    {
      "feature": "back_to_back_blocks",
      "pattern_description": "back_to_back_blocks >= 2 (1 day(s) earlier)",
      "operator": ">=",
      "threshold": 2.0,
      "lag_days": 1,
      "n_people_affected": 7,
      "n_people_analysed": 12,
      "mean_lift_points": 9.3,
      "max_lift_points": 14.1,
      "severity_band": "moderate",
      "calendar_fact": "Days with 2+ back-to-back meeting blocks are followed by measurably higher strain."
    }
  ],
  "gating_applied": false,
  "gating_note": "Part 3 applies no k-anonymity gate. Part 4 must enforce the k>=5 floor before any of this reaches an employer view.",
  "data_provenance": "SIMULATED",
  "synthetic": true
}
```

Patterns are sorted by `n_people_affected` desc, then `mean_lift_points` desc.
`calendar_fact` is the employer-safe sentence: it always blames the **schedule**,
never a person. `severity_band` uses the same thresholds as per-person severity
(`<3 minimal, <7 low, <12 moderate, <20 elevated, else high`).

## Note

**ALL DATA IS SYNTHETIC.** `data_provenance` is `"SIMULATED"` in every payload
and every API response — keep it that way in any UI you build on top.
