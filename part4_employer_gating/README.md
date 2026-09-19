# Part 4 — Privacy-Gated Employer Aggregation Layer

Team Stress Insight Tool · WHOOP track · Chatathon 2026

## What this does

Takes per-person stress scores + meeting features and produces an
**employer-facing view that never contains a per-person score or name**,
unless that person has explicitly opted in.

Enforces:
- k-anonymity floor (team_size < 5 → employer sees nothing at all)
- causes attach to the calendar/schedule structure, not to a person
- recommendations are always a *process* ("run a workload check-in"),
  never a diagnosis of a specific person
- opt-in escalation: only employees who set `opted_in: true` have their
  private insight (from Part 3) surfaced upward

## Run it

```bash
python3 part4.py
```

This reads everything from `mock_inputs/` and writes `employer_view.json`.

## Test the k-anonymity gate

```bash
python3 -c "
from part4 import build_employer_view
import json
print(json.dumps(build_employer_view(input_dir='mock_inputs_small_team'), indent=2))
"
```

With only 3 people, this correctly returns `gate_passed: false` and an
empty view — including hiding opt-in specifics, even though one of the
3 mock people has `opted_in: true`.

## Swapping in real data from Parts 1 & 2

`part4.py` never has to change. Just point it at a folder with real files
using the **same field names** as the mock files:

| File | Must contain |
|---|---|
| `stress_scores.csv` | `person_id, date, stress_score, contributing_factors` |
| `meeting_features.json` | `{ person_id: { date, meetings: [{title, start, duration_min, has_agenda, recurring, attendee_count}] } }` |
| `employee_insight.json` | `{ person_id: "insight text" }` |
| `employees.json` | `{ "employees": [{ person_id, opted_in }] }` |

Then run:

```python
from part4 import build_employer_view
view = build_employer_view(input_dir="/path/to/real/data")
```

## Output shape (what Part 5 / the UI consumes)

```json
{
  "gate_passed": bool,
  "team_size": int,
  "min_team_size": 5,
  "categories": [ { "category": str, "severity": "low|moderate|high" } ],
  "recommended_actions": [ str ],
  "opted_in_specifics": [ { "person_id": str, "insight": str } ]
}
```

## Things to decide before the demo

1. Should the UI map `person_id` → real name for `opted_in_specifics`,
   or is masked ID fine? Currently kept as `person_id` to keep this
   module itself PII-minimal.
2. Severity band thresholds in `_band()` calls are tuned to produce a
   good demo result with the mock data — revisit if real data from
   Parts 1/2 shifts the distribution.