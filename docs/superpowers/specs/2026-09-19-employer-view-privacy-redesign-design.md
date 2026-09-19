# Employer View Privacy Redesign — Part 3

**Date:** 2026-09-19
**Status:** approved design, pending implementation
**Supersedes:** §3.1 of `2026-09-19-part3-correlation-insight-engine-design.md`
(that section's gate stays; this document explains why the gate alone was never
going to be enough)

> ALL DATA IN THIS SYSTEM IS SYNTHETIC.

---

## 1. The problem

A pattern in the employer rollup can describe exactly one person. A manager
reading it learns that *someone specific* is struggling, and often which
someone. That is the surveillance failure mode the whole project is built to
avoid, reached through the front door.

`gating.py` already enforces k-anonymity at `k = 5`. It is not sufficient, and
the run that proves it is on disk.

### 1.1 What a 12-person run actually produced

```
raw   : 12 people, 12 patterns
gated : 12 people,  2 patterns   (10 of 12 withheld for covering < 5 people)
```

A twelve-person team is comfortably above the size floor, and the gate still
suppressed 83% of the findings. That is not a small-team problem. Grouping the
patterns by feature shows why:

```
meeting_count      >= 2.5  ->  2 people
meeting_count      >= 3.5  ->  5 people      <- same phenomenon, split in two
context_switches   >= 1.0  ->  1 person
context_switches   >= 1.5  ->  3 people      <- same phenomenon, split in two
```

Seven people experience elevated meeting load. The rollup reports two groups of
two and five, because they were counted against different cut points.

### 1.2 Root cause: the cut point is a fingerprint

`hypotheses.py` builds adaptive thresholds from each person's own distribution:

```python
cut = (lower + upper) / 2.0     # hypotheses.py:327
```

The cut is the **midpoint between two values that one person actually
recorded**. Its own generated rationale says so: `"data-driven cut at {cut:g},
chosen from this person's own ..."`.

This has two consequences, and the second has stayed invisible because the
first one masks it:

1. **The threshold identifies.** `focus_time_minutes >= 407.5` is not a
   threshold that happens to single someone out. It is a number that could only
   have come from one person's data. A manager who sits in those meetings can
   work backwards from it. §3.1 of the original spec spotted this
   (*"days whose meetings average 6.83+ attendees"*) and treated it as a text
   problem to be gated around.

2. **The threshold deflates `n_people_affected`.** The grouping key in
   `emit.aggregate_team_patterns` is
   `(feature, operator, exact_threshold, lag_days)`. Two people with the same
   underlying problem and different bespoke cut points land in different
   buckets. `n_people_affected` therefore measures *threshold coincidence*, not
   prevalence, and the k-gate suppresses real shared patterns as though they
   were individual ones.

### 1.2b Correction: the dominant cause was somewhere else

*Added after implementing §2.1. The diagnosis above is real but was not the
main driver, and the ladder alone changed the coverage histogram not at all.*

`aggregate_team_patterns` was being fed `res["patterns"]` — which is
`_dedupe_by_feature(survivors)[:n_patterns]`, **each person's narrative top 3,
one per feature**. The team rollup therefore never saw what was statistically
true for a person, only what was most worth *telling* them. A pattern true for
nine people but ranked fourth for most of them arrived at the gate looking as
though it covered two.

Measured on the 12-person fixtures:

| Aggregation input | distinct patterns | clearing `k=5` | max coverage | fingerprint thresholds |
|---|---|---|---|---|
| top-3, no ladder (**what shipped**) | 22 | **0** | 3 | 5 |
| all validated, no ladder | 108 | 25 | 11 | 29 |
| all validated + ladder | 45 | **30** | 11 | **0** |

The shared team signal was present all along and was discarded before the gate
ever ran:

```
11/12 people   no_agenda_meetings  >= 1  (next day)
11/12 people   context_switches    >= 2  (next day)
10/12 people   back_to_back_blocks >= 1  (next day)
```

So there are two fixes, not one, and the larger is the cheaper:

1. **Feed the rollup every passing validated pattern** (§2.0). This is what
   takes `k=5`-clearing patterns from 0 to 30.
2. **Bin the thresholds** (§2.1). This is what removes the fingerprints, and it
   also consolidates 108 patterns into 45 while *raising* the clearing count
   from 25 to 30.

Neither is sufficient alone. The ladder without fix 1 leaves the employer view
empty; fix 1 without the ladder publishes 29 fingerprint thresholds.

### 1.2c Why the gate looked like the problem

The existing gate is simultaneously **too weak** — the rows that survive carry
fingerprint decimals and one-person magnitudes — and **too strong**, because it
suppresses aggregates that were only ever deflated by §1.2 and §1.2b. Tuning
`k` fixes neither. Both fixes are upstream of the gate.

This matters because the gate had already been *loosened* in response: cell
suppression was removed at one point, on the reasoning that it emptied the
employer view. That reasoning was correct at the time and is obsolete now. With
both fixes in place the same fixtures yield 18 published patterns, so
suppression no longer costs a working feature and both conditions are enforced
again.

### 2.0 Feed the rollup everything that validated

`analyse_person` now returns two pattern lists, and the distinction is the
point:

| Key | Contents | Consumer |
|---|---|---|
| `patterns` | narrative top 3, one per feature | the employee's insight text |
| `team_patterns` | every pattern that passed validation | `aggregate_team_patterns` |

Conflating the two is what deflated every `n_people_affected` count. "Most
worth telling this person" and "statistically true for this person" are
different questions, and only the second one belongs in an aggregate.

The employee view is unchanged: they still see their top 3.

### 1.3 The residual leak the gate cannot reach

Even with a correct count, three things in the current employer payload
describe individuals:

| Field | Why it identifies |
|---|---|
| `max_lift_points` | It is *always* one person's number, by definition. There is no `k` at which the maximum of a set stops being a single member of it. |
| `threshold` | A fingerprint, per §1.2. |
| the pattern *set* | Which patterns appear, and how many, varies with the data. Eight rows sharing `lag_days: 1` and near-identical lifts (19.6, 19.6, 19.0, 16.4) is one person's week enumerated eight ways. Set membership is itself a channel. |

---

## 2. Design

Three changes, in the order the data flows.

### 2.1 Bin the cut point at birth

Snap every adaptive cut to a shared per-feature ladder **before the hypothesis
is emitted**, so the validator computes lift, Pearson r and the permutation
p-value against the ladder value.

This is deliberately not a post-hoc scrub of the output. If the threshold were
rewritten after validation, the reported number would no longer be the number
the statistics were computed against — the employer would be shown a claim that
nothing had tested. Binning at birth keeps every reported figure attached to
the test that produced it, which is the project's central commitment
(*"LLM agents propose and explain; Python proves"*).

**Ladders.** Rungs sit inside the observed range of each feature, measured from
the 12-person fixture set:

| Feature | Observed (min/med/max) | Ladder |
|---|---|---|
| `meeting_count` | 0 / 2 / 8 | 2, 4, 6 |
| `total_meeting_minutes` | 0 / 105 / 420 | 60, 120, 180, 240, 360 |
| `back_to_back_blocks` | 0 / 0 / 6 | 1, 2, 3, 4 |
| `longest_back_to_back_run` | 0 / 0 / 7 | 2, 3, 4 |
| `context_switches` | 0 / 2 / 5 | 2, 3, 4 |
| `focus_time_minutes` | 60 / 395 / 600 | 120, 240, 360, 480 |
| `after_hours_meetings` | 0 / 0 / 2 | 1, 2 |
| `no_agenda_meetings` | 0 / 1 / 7 | 1, 2, 3, 4 |
| `large_meetings` | 0 / 0 / 7 | 1, 2, 3 |
| `negative_sentiment_meetings` | 0 / 1 / 6 | 1, 2, 3 |
| `recurring_meetings` | 0 / 0 / 4 | 1, 2, 3 |
| `avg_attendee_count` | day-aggregate input only | 3, 5, 8, 12 |
| `longest_meeting_stretch_min` | day-aggregate input only | 60, 120, 180 |
| `has_lunch_buffer` | binary | not binned |

`context_switches` starts at 2 rather than 1 on purpose: "days that switch
between 1+ meeting topics" is true of every day with a meeting and means
nothing.

**Snapping rule.** For `>=` and `>`, snap **down** to the nearest rung at or
below the cut; if the cut falls below the lowest rung, snap up to it. For `<=`
and `<`, snap up. Snapping down widens the exposed set, which is the direction
that merges people rather than splitting them.

Snapping runs **before** the existing `_dedupe_key` pass, so two cuts from one
person that land on the same rung collapse to one hypothesis for free.

**Accepted cost, now measured.** Some hypotheses that passed at a bespoke cut
fail at a ladder rung. Across the 12-person fixture set:

- hypotheses tested: 627 → 524
- per-person patterns passing validation: **337 → 277 (−18%)**
- team patterns clearing `k=5`: **25 → 30 (+5)**
- fingerprint thresholds: **29 → 0**

Individual sensitivity drops by about a fifth, and team-level coverage goes
*up*, because the survivors share grouping keys instead of sitting one decimal
apart. That is a favourable trade rather than a neutral one, but the loss on
the employee side is real and is the reason the employee view keeps its full
per-person detail rather than being rebuilt on laddered patterns alone.

### 2.2 Roll patterns into five fixed themes

The employer's unit of record stops being a pattern. The 14-feature vocabulary
maps onto a closed set of five:

| Theme | Fed by |
|---|---|
| Meeting load | `meeting_count`, `total_meeting_minutes`, `large_meetings`, `avg_attendee_count` |
| Fragmentation & focus | `back_to_back_blocks`, `longest_back_to_back_run`, `context_switches`, `focus_time_minutes`, `longest_meeting_stretch_min` |
| After-hours encroachment | `after_hours_meetings` |
| Meeting hygiene | `no_agenda_meetings`, `recurring_meetings`, `negative_sentiment_meetings` |
| Recovery buffers | `has_lunch_buffer` |

**All five themes always render, including those with no signal.** This is the
control that answers §1.3's third row. When the theme list is fixed, set
membership carries no information: the employer cannot learn anything from
*which* themes appear, because the same five appear every time, for every team.
A varying-length list of whatever happened to pass is a channel; a constant list
is not.

**Severity.** Computed per theme from its gated patterns whose lift is
**positive** (feature present → more strain), as the mean lift weighted by each
pattern's `n_people_affected`, then banded with the existing five-level
vocabulary (`minimal / low / moderate / elevated / high`). Weighting by affected
count stops a pattern that cleared `k` by one person from outweighing one that
covers the whole team. A theme with no gated risk-direction pattern bands as
`no signal`.

**A pattern is "gated" when it clears both conditions from §3.1 of the original
spec** — the team-size floor (`n_people_analysed >= k`) and cell suppression
(`n_people_affected >= k`). Both still apply; the rollup consumes their output
rather than replacing them.

**Below the team-size floor, the five themes still render**, all at
`no signal`, alongside an explicit `below_floor: true` and the existing
suppression note. Emitting nothing would make "team too small" and "team is
fine" look identical to Part 5 and would break the fixed-shape guarantee that
§1.3 depends on. The flag distinguishes the two cases without revealing
anything about either.

**Direction is preserved, not flattened.** `focus_time_minutes` carries a
*negative* lift — more focus time, less strain. Banding on `abs(lift)` would
report a protective finding as a severity. Negative-lift patterns instead
populate a `protective_fact` on the theme and drive a "preserve this" action.

**Prevalence.** Banded, never counted:

| Band | Fraction of team |
|---|---|
| `some` | < 34% |
| `about half` | 34–66% |
| `most` | > 66% |

Derived from the largest `n_people_affected` among the theme's gated patterns.
A raw count of 5 on a team of 12, combined with what a manager already knows,
narrows the field further than a band does.

### 2.3 Deterministic action table

Each theme carries a three-field action block resolved from a lookup table keyed
by `(theme, severity_band)`. **No LLM is in this path.** A model that can phrase
an action can phrase a person into one; a table cannot.

| Field | Contents |
|---|---|
| `action` | One imperative the manager executes **on the schedule**. Escalates with severity: at `moderate`, *"switch default invite length to 25/50 min so gaps exist by default"*; at `elevated`, *"declare a protected focus block Wed 13:00–16:00 and enforce it on the shared calendar"*. |
| `rationale` | The person-free `calendar_fact` justifying it. |
| `verify_metric` | What to re-measure next cycle to see whether it worked — *"longest uninterrupted gap during work hours"*. Team-level by construction. |

At `no signal` the action is a maintain line, not an empty cell, so signal
cannot be inferred from the presence of a recommendation.

**The test every row must pass: the action is executable without knowing who is
affected.** "Require agendas on recurring invites" needs no name. "Check in with
whoever is struggling" needs one — and cannot be constructed from a fixed table.
This is design principle 5 (*recommend process, not people*) made structural
rather than aspirational.

---

## 3. The two views

The employee and employer screens must be different. Part 5 owns the UI and does
not exist in this repo yet, so the separation is specified as a contract, not
drawn as a mockup.

**Principle: make the employer view incapable of resembling the employee view,
rather than asking Part 5 not to make it resemble one.** Most of the separation
is a consequence of what the payload contains:

| Rule | Enforced by |
|---|---|
| Employer view has no per-day axis | **Payload** — no dates, no per-day rows. Nothing to plot against a timeline. |
| Employer view shows no exact magnitudes | **Payload** — `severity_band` and `prevalence_band` are strings from closed vocabularies. There is no number to render. |
| Employer view has a fixed row count | **Payload** — always exactly five themes. |
| Employer view cannot drill down to a person | **Payload + RLS** — no `person_id` at any depth, and plan §6.4's "no manager policy on any per-person table". |
| Employee view keeps full detail | **Payload** — `employee_insight.json` is unchanged: exact lifts, real dates, their own trend. |

The resulting views share no axis, no vocabulary and no row count, so there is
no visual operation that lines an employer element up against an individual.

### 3.1 Part 5 obligations this repo cannot enforce

Recorded here so the owner knows they hold them:

1. **Do not render the two views in a shared frame.** Separate routes behind
   separate sessions — not a tabbed employee/employer toggle on one screen,
   which invites exactly the alignment the payload design prevents.
2. **`SIMULATED DATA` label on both views**, per the repo README.

### 3.2 Schedule numbers survive in prose, deliberately

The rule "no exact magnitudes" applies to **stress magnitudes and people
counts**, not to schedule thresholds. `calendar_fact` still reads *"Days with
2+ meetings booked without an agenda are followed by measurably higher
strain."*

Stripping the `2+` would have made the employer view unactionable — "meeting
hygiene is elevated" gives a manager nothing to do. The number is safe for a
different reason than the others: it is a shared ladder rung covering 11 of 12
people, not a value derived from anyone's distribution. That safety is
conditional on §2.1 holding, so `test_any_number_in_employer_text_is_a_shared_ladder_rung`
asserts it directly against the data-derived fields, rather than trusting it.

What stays out: lift points, `n_people_affected`, `max_lift_points`, p-values,
per-day rows.

### 3.3 Retained against advice

`date_range` stays in the employer payload. A date range combined with a
manager's memory of the calendar is a re-identification vector, and the
privacy-maximal choice is to drop it. It is retained by explicit decision, to
avoid breaking Part 4's current contract. Recorded here as a known residual risk
rather than an oversight.

---

## 4. Outputs

| File | Audience | Change |
|---|---|---|
| `employee_insight.json` | that employee only, via Part 5 | **unchanged** |
| `team_themes.json` | **new** — the employer-safe projection, Part 4 → Part 5 | five themes, bands, actions; no thresholds, no counts, no magnitudes |
| `team_correlations.json` | Part 4's analytical input | k-gated pattern rows, now with laddered thresholds; `max_lift_points` **removed** (§1.3) |
| `team_correlations_raw.json` | Part 4 only | ungated, full counts and magnitudes; unchanged in shape |

`GET /team/correlations` keeps its current behaviour. A new `GET /team/themes`
serves the employer view.

---

## 5. Files touched

| File | Change |
|---|---|
| `src/insight/hypotheses.py` | `THRESHOLD_LADDER` + snap adaptive cuts before dedup (§2.1) |
| `src/insight/themes.py` | **new** — theme vocabulary, feature→theme map, rollup (§2.2) |
| `src/insight/actions.py` | **new** — deterministic `(theme, severity)` action table (§2.3) |
| `src/insight/models.py` | `TeamTheme`, `TeamThemeView`; drop `max_lift_points` from the gated projection |
| `src/insight/emit.py` | emit `team_themes.json`; strip removed fields |
| `src/insight/gating.py` | gate feeds the theme rollup; prevalence banding |
| `api/main.py` | `GET /team/themes` |
| `tests/test_privacy.py` | new assertions (§6) |

---

## 6. Tests

Added to `tests/test_privacy.py`:

1. **No bespoke thresholds.** Every threshold in any employer-facing payload is
   a rung on its feature's ladder.
2. **Fixed row count.** `team_themes.json` contains exactly five themes on every
   input, including an empty one and an under-k one.
3. **No identifying magnitudes.** The keys `max_lift_points`,
   `mean_lift_points`, `n_people_affected` and `threshold` appear nowhere in
   `team_themes.json`, at any nesting depth.
4. **No `person_id` at any depth** — existing test, extended to the new file.
5. **Under-k renders, not vanishes.** A theme whose patterns all fall below `k`
   emits `no signal` with a maintain action, rather than being omitted.
6. **Actions name no one.** No action string matches a person id, or the
   patterns `who` / `whoever` / `individual` / `employee \d`.
7. **Indistinguishability.** Two teams whose gated themes band identically
   produce byte-identical `team_themes.json` apart from `date_range`.

Test 7 is the one that would have caught the original bug: it fails loudly the
moment any per-person value finds a path into the employer payload.

---

## 7. What this does not solve

- **`date_range` remains** (§3.2), by decision.
- **Binning costs sensitivity** (§2.1). Some real patterns will stop being
  detected. The implementation reports the magnitude; it does not eliminate it.
- **A manager with out-of-band knowledge can still infer.** Nothing in a data
  pipeline prevents a manager who watched someone cancel meetings all week from
  drawing conclusions. This design removes the *tool* as a source of that
  inference; it does not make the team unobservable.
- **Part 4 still owns the authoritative policy.** Everything here is a safe
  default, so that the accidental path is the safe path.

- **`team_correlations.json` publishes a median threshold that nothing was
  tested at.** `aggregate_team_patterns` now groups on `(feature, lag_days)`
  and publishes the median cut point across the group, so a row's
  `mean_lift_points` mixes lifts measured at different rungs. This is a real
  honesty gap of the kind §2.1 was written to avoid, and it is confined to
  Part 4's analytical input: the employer *view* carries no thresholds tied to
  magnitudes and no lift points at all, so nothing reaches a manager that a
  test did not produce. Worth closing if Part 4 starts quoting those numbers
  directly.

- **The employee view was not re-examined.** It is unchanged by design — the
  person owns their own data — but nothing here audits what Part 5 does with
  it, and §3.1's two obligations remain unenforceable from this repo.
