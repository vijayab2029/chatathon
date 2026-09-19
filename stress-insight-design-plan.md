# Project Design Plan: Team Stress Insight Tool
**Track:** WHOOP — "A healthier future of work"
**Build window:** 12:45–3:45 PM (submit + freeze repo by 3:45 PM ET)

---

## 1. Concept in one line

Simulated WHOOP biometrics + calendar semantics → individual stress insight (private) → non-identifying, action-oriented signal to the employer (aggregate, causal, opt-in for anything specific).

## 2. Design principles (the part that makes this defensible, not creepy)

These come directly out of the surveillance-tool failure mode and should show up explicitly in your demo pitch:

1. **Employee view is always full detail, private by default.** The person owns their own data.
2. **Employer view is never a per-person number.** No score, no name, ever, unless the employee opts in.
3. **k-anonymity floor.** No aggregate is shown to the employer unless the team is above a minimum size (pick 5 for the demo). Below that, employer sees nothing — this directly answers "what happens with 2 employees at different stress levels."
4. **Causes attach to the calendar, not the person.** "6 back-to-back meetings, no buffer" is a shareable fact. "Alex is stressed" is not. Route almost all employer-facing specificity through this channel.
5. **Recommend process, not people.** The tool tells a manager to run a workload check-in — it never tells them who to check in on.
6. **Opt-in escalation path.** An employee can voluntarily share their own specifics upward; the system never infers consent from the aggregate.

Have one slide/line ready that states this explicitly to judges — it hits "be clear and honest" and "make the value credible" in the rubric directly.

## 3. System architecture (what you're actually building)

```
[Synthetic WHOOP CSV] --> [Stress Score Engine] --\
                                                     --> [Correlation Layer] --> [Employee View (private)]
[Synthetic Calendar JSON] --> [Meeting Semantic Analyzer] --/                --> [Employer View (aggregate, gated)]
```

Three inputs, one correlation layer, two outputs. Keep it to this — don't add scope.

## 4. Work breakdown — 5 parts

Each part is scoped to be buildable solo in ~2.5–3 hours, with clear interfaces so parts integrate without blocking each other. Assign one owner per part; agree on data formats (below) in the first 10 minutes of the build sprint.

### Part 1 — Synthetic biometric data + stress scoring
**Owner focus:** data generation + scoring logic
- Generate a synthetic WHOOP-style dataset (CSV): per-person, per-day HRV, resting heart rate, sleep duration/quality, recovery %, strain.
- Build a stress score formula from these (reference WHOOP's public recovery/strain framing — low HRV + low recovery + high strain = high stress). Doesn't need to be validated science, needs to be *explainable* in the demo.
- Output: `stress_scores.csv` with `person_id, date, stress_score (0-100), contributing_factors`.
- **Explicitly label this synthetic** in code comments and in a data dictionary — you'll need this for the honesty requirement.

### Part 2 — Calendar ingestion + semantic meeting analysis
**Owner focus:** NLP / classification logic
- Build 5–10 realistic synthetic calendar events per person (JSON, not live Outlook API — don't burn time on auth).
- Classify each meeting for stress-relevant features: back-to-back density, after-hours timing, agenda presence/absence, sentiment of title/description, recurring vs. one-off, attendee count.
- Output: `meeting_features.json` per person, plus a **team-level structural summary** (e.g., "Wednesdays average 5.2 meetings/person, 40% with no agenda") — this team-level file is what eventually feeds the employer view, since it's non-personal by construction.

### Part 3 — Correlation & individual insight engine
**Owner focus:** the analytical core
- Join Part 1 (stress scores) with Part 2 (meeting features) per person to find correlations: which meeting types/patterns precede stress spikes for that individual.
- Produce the **private employee-facing insight**: e.g., "Your stress tends to spike after back-to-back days with 4+ meetings and no lunch buffer."
- This is the most "AI-powered" piece worth making genuinely good — it's your strongest demo moment for "show the working core."

### Part 4 — Privacy-gated employer aggregation layer
**Owner focus:** the aggregation/gating logic — this is the part that answers the design principles above
- Implement the k-anonymity threshold (block employer view below N people).
- Implement aggregation that reports **causal categories + coarse severity bands** (not scores, not counts of affected people below a safe threshold).
- Implement the opt-in mechanism: a toggle each simulated employee can set to share specifics upward.
- Implement the "recommend process not people" output: turn detected patterns into manager-facing actions ("schedule a workload rebalancing check-in this week") rather than diagnoses.
- This module is what you'll walk judges through to show you engineered around the surveillance/averaging failure modes — worth over-investing time here relative to polish elsewhere.

### Part 5 — UI + demo assembly
**Owner focus:** two views, one clean narrative
- **Employee view:** private dashboard — full stress trend, causal insight from Part 3, opt-in toggle.
- **Employer view:** team-level dashboard — gated by Part 4, showing causal categories tied to calendar structure and recommended actions, no individual data.
- Wire in a clear "SIMULATED DATA" label wherever biometrics or calendar data appear.
- Build the 5-minute demo script: problem → employee view → employer view → the design principle callout (why the employer view looks the way it does) → impact statement.
- Start this after ~90 minutes even with partial data from other parts, so you're not assembling from scratch at 3:30.

## 5. Interfaces to agree on immediately (avoid integration pain)

| From → To | Format |
|---|---|
| Part 1 → Part 3 | `stress_scores.csv`: `person_id, date, stress_score, contributing_factors`; plus `whoop_biometrics.csv` (recovery_score, hrv_rmssd_milli, resting_heart_rate, sleep_efficiency_percentage, sleep_performance_percentage, total_sleep_hours, respiratory_rate, strain) |
| Part 2 → Part 3 | `meeting_features.json`: per-person event list with tagged features |
| Part 2 → Part 4 | `team_structural_summary.json`: non-personal, team-level only |
| Part 3 → Part 5 | `employee_insight.json`: per-person private insight text/data |
| Part 4 → Part 5 | `employer_view.json`: category + severity band + recommended action, no PII |

**Part 3 join note (important):** WHOOP recovery/HRV/RHR are measured overnight and
reported the next morning, so Part 1's biometrics for date D reflect Part 2's meeting
features for date **D-1**, not D. When joining `meeting_features.json` against
`stress_scores.csv`/`whoop_biometrics.csv`, shift the calendar side back one day —
a same-date join misses the relationship that's actually in the data (verified lag-1
correlation: -0.98). Same-day meeting load only drives that day's own `strain` value.
See `data/README.md` for the full data dictionary.

## 6. Cut list if time runs short

Drop in this order: demo polish → opt-in toggle (hardcode as off) → multi-day trends (use single-day snapshot) → per-person nuance in Part 3 (fall back to team-level correlation only). **Never cut** the k-anonymity gate or the "synthetic data" labeling — those are your credibility anchors with judges.
