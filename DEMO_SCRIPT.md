# 5-minute demo script — Team Stress Insight

**Setup before you present:** run `./serve.sh`, open http://localhost:8080/ui/, scroll back to the
top, and make sure the employer view's team size is set to **9**. Zoom to ~90% so the hero card and
the first chart are both reachable in two scrolls.

Timings are cumulative. Practise the transitions — the two view switches are the whole argument.

---

## 0:00 – 0:45 · The problem

> "Companies already buy wearables for their employees. The moment that data flows to an employer,
> it turns into a surveillance tool — a per-person stress score a manager can rank people by. That
> fails twice: employees stop trusting it, and managers get a number they cannot act on. Knowing
> Alex is at 78 doesn't tell you what to change.
>
> We built the version that doesn't fail that way. Same data, two views, and a hard wall between
> them."

**Say the word "simulated" here and point at the badge.** Every screen carries it.

## 0:45 – 2:00 · Employee view (private)

Start at the top of the employee view.

> "This is what the employee sees, and only the employee. Today's stress score is 78, twenty-two
> points above their own two-week baseline, built from simulated WHOOP-style recovery, HRV, sleep
> and strain."

**Scroll slowly.** The dashboard tightens, rounds, and pops into the pill at the top.

> "As you move down, the summary collapses into that pill — the number stays reachable, the detail
> gets out of the way."

Land on the trend chart. Hover two or three points.

> "Fourteen days. The two peaks are both Wednesdays. The dips are weekends — no meetings."

Move to *Your insight*.

> "This is the part that matters: **the cause is in the calendar, not in the person.** Stress spikes
> on days with four or more meetings and no gap longer than fifteen minutes. Days with the same
> number of working hours but one protected midday block land twenty-seven points lower.
>
> That's an actionable sentence. 'Alex is stressed' is not."

Show the opt-in toggle. Leave it **off**.

> "Off by default. Consent is never inferred."

## 2:00 – 3:30 · Employer view (aggregate)

Click **Employer view**.

> "Same underlying data. Here is what the manager gets."

Point at the gate banner, then the causal category cards.

> "No score. No name. No per-person row — not hidden in the UI, never computed into this payload.
> What the manager gets is a cause attached to the team's calendar structure, a coarse severity
> band, and a process change.
>
> 'Wednesdays average 5.2 meetings per person with no gap over thirty minutes' — that is a shareable
> fact. The recommended action is 'declare a protected no-meeting block', not 'go check on someone.'"

Scroll to the heatmap, hover a cell.

> "Team averages only. A cell never represents one person."

Scroll to **Recommended actions**.

> "Every output is a process change. The tool structurally cannot name a person to check in on."

## 3:30 – 4:20 · The design-principle callout — **this is the moment that wins**

Scroll back to the gate. Click the **−** button down to 3.

> "Here's the question every one of these tools fails. What happens with a tiny team — two people at
> very different stress levels? Averaging them is worse than useless; it's re-identification with
> extra steps.
>
> Watch." *(click down to 3)* **"Nothing. The employer sees nothing at all.** Below our k-anonymity
> floor of five, we don't blur it, we don't round it, we don't show a range. Anything we showed would
> be a per-person number wearing a team-level label."

Click back up to 9. Scroll to *What this view deliberately cannot show*.

> "And the voluntary-share channel has its own floor — two people opted in, below the reporting floor
> of three, so their content is withheld too."

## 4:20 – 5:00 · Impact

> "The honest version of this product is more useful, not less. A manager who gets 'your Wednesdays
> are structurally broken, here is the calendar change' can fix something this week. A manager who
> gets a list of stress scores can only apply pressure.
>
> All data here is simulated — no real biometrics, calendars, or employees. The architecture is the
> deliverable: the employer view is non-personal **by construction**, not by policy, and that is the
> only version of this that an employee would ever agree to wear."

---

## If you are asked

- **"Is the stress score validated science?"** No, and we say so on screen — it is an explainable
  weighted formula over HRV, recovery, sleep and strain, labelled synthetic everywhere. The
  contribution is the privacy architecture, not the coefficient values.
- **"What if the manager just asks who?"** There is no field to answer from. The employer payload has
  no person dimension at all.
- **"Why k=5?"** A demo choice. It is one constant, in `data/employer_view.json`, and the whole view
  is driven off it — which is the point: the floor is a parameter, not scattered logic.
- **"Could someone infer an individual from the heatmap?"** It is a team mean over ≥5 people and
  carries no per-person variance. The gate blocks it below the floor along with everything else.

## Cut list if you are over time

Drop in this order: the heatmap hover → the opt-in toggle demo → the trend chart hover.
**Never cut** the team-size gate demo at 3:30 or the "simulated data" callout at 0:45.
