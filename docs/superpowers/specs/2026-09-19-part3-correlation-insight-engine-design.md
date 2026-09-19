# Part 3 — Correlation & Individual Insight Engine

**Project:** Team Stress Insight Tool
**Track:** WHOOP — "A healthier future of work"
**Date:** 2026-09-19
**Owner:** Callum Johnson (branch `CallumJ`)
**Build window:** 12:45–3:45 PM ET

---

## 1. Purpose

Join Part 1 (synthetic WHOOP stress scores) with Part 2 (calendar meeting
features) to find, for each person, which calendar patterns precede stress
spikes. Emit two products:

- a **private, per-person insight** for the employee view (Part 5)
- a **pattern-level, non-personal correlation rollup** for the employer
  aggregation layer (Part 4)

## 2. Central design commitment

**Gemini agents propose and explain. Python proves.**

No number that reaches a user is produced by a language model. The LLM
generates candidate hypotheses and narrates validated results; every
statistic is computed by deterministic code. This is what makes the demo
answerable when a judge asks "how do you know that?".

The mechanism is a **hypothesis DSL**. The hypothesis agent cannot emit
prose. It emits an object the validator can execute:

```json
{
  "id": "h1",
  "feature": "back_to_back_blocks",
  "operator": ">=",
  "threshold": 2,
  "lag_days": 1,
  "rationale": "recovery debt from a packed day accumulates overnight"
}
```

A hallucinated pattern is not a risk to the output, because a hypothesis
that does not survive validation never reaches the narrator.

## 3. Privacy architecture

The two outputs are **two projections of one analysis**, not one derived
from the other.

| | `employee_insight.json` | `team_correlations.json` |
|---|---|---|
| Unit of record | one person | one *pattern* |
| `person_id` present | yes | **never** |
| Example | "Your stress rose 34% on days after 4+ back-to-back meetings" | "`back_to_back_density` → recovery decline; 6 people affected; moderate" |
| Audience | that employee only | Part 4 → employer view |

The team file is built by **aggregating across people**. It is never built
by concatenating employee insights. Concatenation would reconstruct a
surveillance tool: a manager reading twelve "anonymous" narratives can
re-identify individuals from calendar specifics.

**Part 3 does not gate.** It hands Part 4 honest counts, including counts
below the k-anonymity floor. Part 4 owns the k≥5 policy and the manager-
facing language. One module owns analytics, one module owns policy.

## 4. Pipeline

```
stress_scores.csv (Part 1) ─┐
                            ├─→ loaders ─→ per-person daily feature vector
meeting_features.json (P2) ─┘                        │
                                                     ▼
                    [A1] Hypothesis Agent (Gemini)
                         + deterministic baseline hypothesis set
                                                     ▼
                    [A2] Validator (pure Python, no LLM)
                         lift · Pearson r · permutation p · support
                                                     ▼  survivors only
                    [A3] Narrator Agent (Gemini)
                                                     ▼
                    [A4] Privacy Critic Agent (Gemini)
                                                     ▼
              employee_insight.json + team_correlations.json
```

### A1 — Hypothesis Agent
Input: the feature vocabulary and a compact digest of the person's
timeline (not raw rows — keeps the prompt small and cheap).
Output: 4–8 candidate hypotheses in the DSL above.
A **deterministic baseline hypothesis set** is always included regardless
of LLM availability, so the pipeline produces real insights with the LLM
switched off entirely.

### A2 — Validator (deterministic; the integrity gate)
For each hypothesis, partition the person's days into *exposed*
(`feature <op> threshold` on day `D - lag_days`) and *unexposed*, then
compute on day `D`:

- `mean_exposed`, `mean_unexposed`
- `lift` = absolute difference in mean stress score
- `pearson_r` between the raw feature value and lagged stress
- `p_value` via a 1000-iteration permutation test (label shuffle; no
  scipy dependency, and defensible to explain)
- `n_exposed`, `n_unexposed`

**Rejection rules:** discard if `n_exposed < 3` (insufficient support) or
`p_value > 0.1`. Survivors are ranked by `|lift|`.

### A3 — Narrator Agent
Receives only validated hypotheses with their computed statistics.
Produces the employee-facing insight sentence plus one concrete suggested
action. Second person, non-clinical, addressed to the data owner.

### A4 — Privacy Critic Agent
Reviews A3's output and rejects or rewrites on three grounds:

1. **Diagnosis** — medical or psychological claims ("you're burning out",
   "symptoms of anxiety")
2. **Blame** — framing that faults the person rather than the schedule
3. **Unsupported numbers** — any figure not present in the validated
   payload handed to A3

Rejected narrations are retained in the output under `critic_log` so the
demo can show a rejection beside its approved replacement.

## 5. Degradation strategy

Free-tier `gemini-2.5-flash` allows roughly 10 requests/minute and 250/day.
A live demo must not fail on a rate limit. Three safeguards:

1. **Disk cache** keyed on `sha256(model + prompt)`. Re-running the demo
   costs zero API calls.
2. **Baseline hypotheses always run.** The statistical layer is fully
   functional with no LLM.
3. **Graceful fallback chain:** `gemini-2.5-flash` → on 429, exponential
   backoff → `gemini-2.5-flash-lite` → on exhaustion, deterministic
   templates. An `--offline` flag forces the template path.

Concurrency is capped by a semaphore (default 5 in flight).

## 6. Interfaces

### Consumed

`stress_scores.csv` (Part 1)
```
person_id, date, stress_score, contributing_factors
```

`meeting_features.json` (Part 2) — per-person event list with tagged
features: back-to-back density, after-hours flag, agenda presence,
title sentiment, recurring flag, attendee count.

### Produced

`employee_insight.json` — per person: stress trend series, top 3 validated
patterns with full evidence numbers, plain-language insight, one suggested
action, `critic_log`.

`team_correlations.json` — per pattern: feature, operator, threshold,
aggregate lift, severity band, `n_people_affected`, `n_people_total`.
No `person_id` key exists anywhere in this file.

### API (FastAPI)

- `GET  /health`
- `POST /analyze` — run pipeline, write both files, return a summary
- `GET  /employee/{person_id}` — private insight
- `GET  /team/correlations` — pattern-level rollup for Part 4

Both JSON files are also written to `part3/data/out/`. If the server
misbehaves near the deadline, Part 5 reads the files directly.

## 7. Unblocking the team

Parts 1 and 2 do not exist yet. Part 3 therefore ships `part3/contracts/`
first: JSON Schemas for every interface plus a fixture generator producing
realistic mock `stress_scores.csv` and `meeting_features.json`.

This folder is the integration contract for the whole team. It is
distributed to the Part 1, 2, 4 and 5 owners immediately so that no part
is blocked on any other.

## 8. Honesty requirements (rubric: "be clear and honest")

- All input data is synthetic. Every generated file carries a
  `"synthetic": true` field and a header comment.
- The API surfaces a `data_provenance: "SIMULATED"` field on every
  response so Part 5 can render the label without special-casing.
- Correlational findings are labeled correlational. No causal language
  reaches the employee insight text; the critic agent enforces this.

## 9. Repository layout

```
part3/
  contracts/
    schemas.py          JSON Schemas for all five interfaces
    make_fixtures.py    mock Part 1 + Part 2 generator
  src/insight/
    models.py           dataclasses for all interfaces
    loaders.py          read + join inputs into a Timeline
    features.py         derive per-person-per-day feature vectors
    hypotheses.py       DSL + deterministic baseline set
    validator.py        lift / r / permutation p  (no LLM)
    llm/
      gemini.py         cached, backoff-wrapped client
      agent_hypothesis.py
      agent_narrator.py
      agent_critic.py
    pipeline.py         orchestration + degradation
    emit.py             output assembly for both files
  api/main.py           FastAPI
  data/fixtures/        mock inputs
  data/out/             generated outputs
  tests/
```

## 10. Out of scope

- k-anonymity gating and manager phrasing (Part 4)
- Any UI (Part 5)
- Real WHOOP or Outlook API integration
- Validated clinical stress science — the scoring is explainable, not
  medically validated, and is labeled as such

## 11. Configuration

`GEMINI_API_KEY` is read from the environment, loaded from a gitignored
`part3/.env`. The key is never committed and never printed.
