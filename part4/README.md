# Part 4 — Privacy-Gated Employer Aggregation Layer

Team Stress Insight Tool · WHOOP track · Chatathon 2026

## What this does

Takes real output from Part 2 (calendar structure) and Part 3 (validated
stress correlations) and produces an employer-facing view that never
contains a per-person score or name.

Enforces:
- **Whole-team k-anonymity floor**: if fewer than 5 people were analyzed,
  the employer sees nothing at all.
- **Per-pattern k-anonymity floor**: Part 3 ships patterns *including ones
  affecting fewer than 5 people, on purpose* — Part 4 is the module that
  actually suppresses those before anything reaches a manager.
- Causes attach to the calendar/schedule structure, not to a person.
- Recommendations are always a *process* ("run a workload check-in"),
  never a diagnosis of a specific person.

**No opt-in escalation exists in this pipeline.** Part 3's own privacy
contract states Part 4 must never read `employee_insight.json` — that file
is private to Part 5 only. This is stricter than the original design doc's
opt-in idea, and Part 4 has been built to honor it: this module never reads
any per-person file, full stop.

## Inputs

| File | From | Contains |
|---|---|---|
| `../data/team_structural_summary.json` | Part 2 | de-identified schedule-health metrics |
| `../part3/data/out/team_correlations.json` | Part 3 | UNGATED validated stress-correlation patterns (no `person_id`, ever) |

**Important:** Part 3's pipeline defaults to its own synthetic fixtures
(12 people), not this team's real 6-person data. To generate the real file:

```bash
cd part3/src
python -m insight.pipeline --offline \
  --stress ../../data/stress_scores.csv \
  --meetings ../../data/meeting_features.json \
  --out ../data/out
```

## Run it

```bash
python3 part4.py
```

Reads both real input files and writes `employer_view.json`.

## Output shape (-> Part 5 / the UI)

```json
{
  "gate_passed": bool,
  "team_size": int,
  "min_team_size": 5,
  "categories": [ { "category": str, "severity": "low|moderate|high" } ],
  "recommended_actions": [ str ],
  "validated_patterns": [
    { "calendar_fact": str, "severity_band": str,
      "n_people_affected": int, "mean_lift_points": float }
  ],
  "gating_applied": true
}
```

`validated_patterns` only ever contains patterns where
`n_people_affected >= 5` — anything below that floor from Part 3's raw
output is dropped here and never reaches this file.

## Testing the k-anonymity gates

Whole-team gate: pass a `team_correlations.json` with `n_people_analysed < 5`
to `build_employer_view(team_correlations_path=...)` — returns an empty view.

Per-pattern gate: any pattern in the input with `n_people_affected < 5` is
silently dropped from `validated_patterns`, even when the overall team
passes the floor.