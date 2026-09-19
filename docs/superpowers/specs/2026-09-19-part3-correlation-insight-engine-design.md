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

**LLM agents propose and explain. Python proves.**

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

### 3.1 Safe-by-default gating (revised 2026-09-19)

The original design said *"Part 3 does not gate"* — analytics here, policy in
Part 4. That separation is still right, but shipping the ungated file as the
**default** output was wrong, for three reasons found after integrating the
real Part 1/2 data:

1. **8 of 9 patterns covered exactly one person.** With six people on the team,
   almost every "aggregate" was an individual.
2. **Adaptive thresholds made those patterns identifying.** Cut points are
   derived from one person's own distribution, so they read as fingerprints:
   *"days whose meetings average 6.83+ attendees"*. This was a side effect of
   the adaptive-threshold work in §A1 — it improved detection and simultaneously
   made employer-facing text more re-identifying.
3. **Part 4 did not exist.** The gate that the plan's cut-list says must *never*
   be cut was, at that point, implemented by nobody.

The literal wording of the project design plan — *"no aggregate is shown unless
the team is above a minimum size (pick 5)"* — **passes** on a team of 6. A
faithful implementation of that sentence publishes all eight single-person
patterns. The sentence describes only half the control.

**Two conditions, both required:**

| Condition | Checks | Failure mode it prevents |
|---|---|---|
| Team-size floor | `n_people_analysed >= k` | a "team" view of two people |
| Cell suppression | `n_people_affected >= k` per pattern | a single person's pattern shown as an aggregate |

`k = 5` for the demo. Part 2's `team_structural_summary.json` asserts
`k_anonymity_status: "Passed (k=6)"`; that is a true claim about *their* file,
which holds only team-wide percentages, but it satisfies only the first
condition and must not be read as covering the second.

**Outputs.** The safe artifact takes the plain name; the dangerous one must be
asked for by name:

- `team_correlations.json` — **gated**. Sub-threshold patterns removed,
  `gating_applied: true`, and a count of what was suppressed so the omission is
  visible rather than silent.
- `team_correlations_raw.json` — ungated, full counts, **Part 4's input.**

`GET /team/correlations` returns the gated view; `?raw=true` returns the full
one. Part 4 still owns the real policy and the manager-facing language — this
is defence in depth, so that the accidental path is the safe path.

Suppression is reported, never hidden. A judge asking "what are you not showing
me?" gets a number.

## 4. Pipeline

```
stress_scores.csv (Part 1) ─┐
                            ├─→ loaders ─→ per-person daily feature vector
meeting_features.json (P2) ─┘                        │
                                                     ▼
                    [A1] Hypothesis Agent (OpenAI)
                         + deterministic baseline hypothesis set
                                                     ▼
                    [A2] Validator (pure Python, no LLM)
                         lift · Pearson r · permutation p · support
                                                     ▼  survivors only
                    [A3] Narrator Agent (OpenAI)
                                                     ▼
                    [A4] Privacy Critic Agent (OpenAI + deterministic)
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

**Rejection rules:** discard if `n_exposed < 3` or `n_unexposed < 3`
(insufficient support) or `p_value > 0.1`.

**Multiple-comparisons control.** Adaptive per-person thresholds (below) raise
the family from ~14 tests to ~55. At p ≤ 0.1 that alone manufactures several
spurious findings per person, so Benjamini-Hochberg FDR control is applied
across each person's family before anything is called a finding.

**Adaptive thresholds.** Absolute cut points are dead for anyone whose feature
never reaches them — `back_to_back_blocks >= 2` had zero exposed days for a
person peaking at 1, so their real driver was invisible. Cut points are also
derived from each person's own distribution, emitted only where they leave ≥3
days on each side.

**Ranking.** Survivors sort on `|pearson_r|` then `|lift|`. r is scale-free and
threshold-independent, so minutes compare fairly against counts and no pattern
is flattered by a lucky cut point; lift then picks which threshold to display.
Ranking on lift alone let a 3-day pattern outrank a 5-day one at nearly equal
effect. Verified on five held-out fixture seeds: 57/60 planted-correlation
recovery.

Known limitation: Pearson r measures *linear* association, so a genuinely
threshold-shaped effect ranks below its importance. The lift tiebreak partly
compensates.

### A3 — Narrator Agent
Receives only validated hypotheses with their computed statistics.
Produces the employee-facing insight sentence plus one concrete suggested
action. Second person, non-clinical, addressed to the data owner.

### A4 — Privacy Critic Agent
Runs a deterministic rule pass **plus** an optional LLM second opinion, on
three grounds:

1. **Diagnosis** — medical or psychological claims ("you're burning out",
   "symptoms of anxiety")
2. **Blame** — framing that faults the person rather than the schedule
3. **Unsupported numbers** — any figure not present in the validated
   payload handed to A3

**The deterministic pass is the gate; the LLM is advisory.** Measured on a
live 12-person run the LLM raised 16 issues and nearly all were false — it
flagged "higher stress levels" as a diagnosis seven times after being told
that phrase is permitted, and called four numbers unsupported that were in the
evidence. A reviewer rejecting 75% of correct output cannot be a gate; it only
teaches people to click through. LLM findings are recorded with an `ADVISORY:`
prefix and do not block. The deterministic half needs no API key, so the safety
control still works fully offline.

Verdicts are retained under `critic_log` so the demo can show a rejection
beside its approved replacement.

## 5. Degradation strategy

The pipeline spends 3 agent calls per person, so a 12-person run costs ~36
calls. A live demo must not fail on a rate limit. Three safeguards:

1. **Disk cache** keyed on `sha256(model + prompt)`. Re-running the demo
   costs zero API calls.
2. **Baseline hypotheses always run.** The statistical layer is fully
   functional with no LLM.
3. **Graceful fallback chain:** primary → on 429, exponential backoff →
   `OPENAI_FALLBACK_MODEL` → on exhaustion, deterministic templates. An
   `--offline` flag forces the template path. Only models verified against the
   project key belong in the chain: two dead entries in an earlier Gemini
   configuration turned 9 HTTP calls into 39.

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

`team_correlations.json` — **gated** (see §3.1). Per pattern: feature,
operator, threshold, aggregate lift, severity band, `n_people_affected`,
`n_people_total`. Patterns below the k floor are removed and counted in
`n_patterns_suppressed`. No `person_id` key exists anywhere in this file.

`team_correlations_raw.json` — ungated, full counts including sub-threshold
patterns. **Part 4's input.** Also carries no `person_id`.

### API (FastAPI)

- `GET  /health`
- `POST /analyze` — run pipeline, write both files, return a summary
- `GET  /employee/{person_id}` — private insight
- `GET  /team/correlations` — gated pattern-level rollup (safe default)
- `GET  /team/correlations?raw=true` — ungated full counts, for Part 4

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

- the authoritative k-anonymity policy and manager phrasing (Part 4). Part 3
  applies a safe default gate as defence in depth (§3.1); it does not replace
  Part 4's policy layer.
- Any UI (Part 5)
- Real WHOOP or Outlook API integration
- Validated clinical stress science — the scoring is explainable, not
  medically validated, and is labeled as such

## 11. Configuration

`OPENAI_API_KEY` is read from the environment, loaded from a gitignored
repo-root `.env` (or `part3/.env`). The key is never committed and never
printed. Provider swaps touch one file: `llm/openai_client.py` behind the
provider-neutral `LLMClient` alias.
