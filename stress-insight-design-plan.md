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

**Persistence note:** once section 6 lands, these files become *seed input* to
Postgres rather than the runtime contract. The formats do not change — Parts 4
and 5 just read the equivalent tables instead of the files.

**Part 3 join note (important):** WHOOP recovery/HRV/RHR are measured overnight and
reported the next morning, so Part 1's biometrics for date D reflect Part 2's meeting
features for date **D-1**, not D. When joining `meeting_features.json` against
`stress_scores.csv`/`whoop_biometrics.csv`, shift the calendar side back one day —
a same-date join misses the relationship that's actually in the data (verified lag-1
correlation: -0.98). Same-day meeting load only drives that day's own `strain` value.
See `data/README.md` for the full data dictionary.

## 6. Supabase data model & access control

Supabase (Postgres + Auth + Row Level Security) is the **system of record** at
runtime. The CSV/JSON artifacts in section 5 stay exactly as they are, but they
become *seed input* rather than the live contract: the pipeline loads them, and
Parts 4 and 5 read Postgres.

The reason to put a database under this at all is that it converts design
principles 1–3 from promises into schema. **The employer view is not the
employee view with names stripped off — it is a different set of tables, and
the employer's session has no policy that reaches the private ones.** That
distinction is the thing to say out loud in the demo, and it is verifiable in
one command rather than by reading application code.

### 6.1 Three tiers, and the tier *is* the access rule

| Tier | Tables | Who can ever read a row |
|---|---|---|
| Identity | `teams`, `profiles` | own row; a manager sees their team's names/roles — no health data lives here |
| **Per-person (private)** | `biometrics`, `stress_scores`, `meeting_events`, `day_features`, `employee_insights` | **only the subject**, plus an explicit opt-in grant |
| Person-free aggregate | `team_patterns` | team members, gated on k |
| Consent | `sharing_consents` | the employee who granted it, and the manager it names |

Every table carries `synthetic boolean not null default true check (synthetic)`.
The schema is structurally incapable of storing a real biometric — the strong
form of the "label it synthetic" requirement the cut list calls non-negotiable.

### 6.2 Schema

```sql
create type app_role as enum ('employee', 'manager');

create table teams (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  k_threshold  int  not null default 5,          -- design principle 3
  synthetic    boolean not null default true check (synthetic)
);

-- Bridges Supabase Auth to the synthetic cohort ids used by Parts 1-3.
create table profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  person_id    text not null unique,             -- 'user_101', 'emp_001', ...
  team_id      uuid not null references teams(id),
  role         app_role not null default 'employee',
  display_name text,
  synthetic    boolean not null default true check (synthetic),
  created_at   timestamptz not null default now()
);

-- ---- Per-person tier. Columns mirror the Part 1/2 contracts verbatim. ----

create table biometrics (                        -- whoop_biometrics.csv
  person_id text not null references profiles(person_id) on delete cascade,
  date date not null,
  recovery_score numeric, hrv_rmssd_milli numeric, resting_heart_rate numeric,
  sleep_efficiency_percentage numeric, sleep_performance_percentage numeric,
  total_sleep_hours numeric, respiratory_rate numeric, strain numeric,
  synthetic boolean not null default true check (synthetic),
  primary key (person_id, date)
);

create table stress_scores (                     -- stress_scores.csv / StressRecord
  person_id text not null references profiles(person_id) on delete cascade,
  date date not null,
  stress_score numeric not null check (stress_score between 0 and 100),
  contributing_factors text[] not null default '{}',
  synthetic boolean not null default true check (synthetic),
  primary key (person_id, date)
);

create table meeting_events (                    -- meeting_features.json / MeetingEvent
  event_id text primary key,
  person_id text not null references profiles(person_id) on delete cascade,
  date date not null,
  start_time time not null, end_time time not null,
  title text not null, category text, attendee_count int,
  has_agenda boolean, is_recurring boolean,
  is_after_hours boolean, is_back_to_back boolean,
  sentiment numeric check (sentiment between -1 and 1),
  synthetic boolean not null default true check (synthetic)
);

create table day_features (                      -- DayFeatures; keys drawn from FEATURE_VOCABULARY
  person_id text not null references profiles(person_id) on delete cascade,
  date date not null,
  features jsonb not null,
  synthetic boolean not null default true check (synthetic),
  primary key (person_id, date)
);

create table employee_insights (                 -- employee_insight.json, one row per person
  person_id text primary key references profiles(person_id) on delete cascade,
  date_range daterange not null,
  stress_trend jsonb not null,
  validated_hypotheses jsonb not null,
  narrative text,
  generated_at timestamptz not null default now(),
  synthetic boolean not null default true check (synthetic)
);

-- ---- Person-free tier. Note what is absent: there is no person_id column. ----

create table team_patterns (                     -- team_correlations.json
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references teams(id) on delete cascade,
  feature text not null, operator text not null,
  threshold numeric not null, lag_days int not null,
  n_people_analysed int not null,
  affected_band text not null check (affected_band in ('some','many','most')),
  mean_lift_points numeric, max_lift_points numeric,
  severity_band text not null
    check (severity_band in ('minimal','low','moderate','elevated','high')),
  calendar_fact text not null,                   -- principle 4: the fact is about the calendar
  recommended_action text,                       -- principle 5: process, not people
  generated_at timestamptz not null default now(),
  synthetic boolean not null default true check (synthetic)
);

-- ---- Consent tier. The only bridge across the wall. ----

create table sharing_consents (
  id uuid primary key default gen_random_uuid(),
  person_id text not null references profiles(person_id) on delete cascade,
  granted_to_team_id uuid not null references teams(id) on delete cascade,
  scope text not null check (scope in ('pattern_only','full_detail')),
  granted_at timestamptz not null default now(),
  revoked_at timestamptz                         -- revocable, never deleted
);
```

**Two transforms are required on the way in, not optional:**

1. `team_correlations.json` currently emits `n_people_affected` as a raw count,
   and in the sample output that count is `1`. In a team at the k=5 floor, "one
   person was affected" is close to naming them. The loader must bucket it into
   `affected_band` — the schema above has no raw-count column to write to.
2. Parts 1/2 and the Part 3 fixtures currently disagree on cohort ids
   (`user_101`–`user_106` in `data/`, `emp_001`… in `part3/data/fixtures/`).
   `profiles.person_id` is the single place that has to be reconciled before
   seeding; everything else keys off it by foreign key.

### 6.3 Who am I — helper functions

RLS policies that read `profiles` would re-trigger RLS on `profiles`. Declaring
the helpers `security definer` breaks that recursion; `stable` lets the planner
call them once per statement instead of once per row.

```sql
create or replace function current_person_id() returns text
  language sql stable security definer set search_path = '' as
  $fn$ select person_id from public.profiles where id = auth.uid() $fn$;

create or replace function current_team_id() returns uuid
  language sql stable security definer set search_path = '' as
  $fn$ select team_id from public.profiles where id = auth.uid() $fn$;

create or replace function current_user_role() returns public.app_role
  language sql stable security definer set search_path = '' as
  $fn$ select role from public.profiles where id = auth.uid() $fn$;

create or replace function is_manager_of(t uuid) returns boolean
  language sql stable security definer set search_path = '' as
  $fn$ select exists (
         select 1 from public.profiles
         where id = auth.uid() and team_id = t and role = 'manager') $fn$;
```

### 6.4 The employee/employer split

Enable RLS on every table (`alter table <t> enable row level security;`), then
grant exactly one policy per private table:

```sql
create policy own_rows_only on stress_scores
  for select using (person_id = current_person_id());
-- identical policy on biometrics, meeting_events, day_features, employee_insights
```

**There is no manager policy on any per-person table.** Not a policy that
filters out identifiers — no grant at all. RLS denies by default, so a manager's
session gets zero rows from `stress_scores` whether they query through Part 5,
through the REST endpoint, or through `psql` with a valid token. The employer
view is reachable only because `team_patterns` is a different table with a
different policy.

This is what makes principles 1 and 2 checkable rather than asserted, and it is
worth demoing directly: **Part 5 issues the same query from two logged-in
accounts and Postgres returns different rows.** No role branching in the UI —
the UI cannot leak what the database will not return.

### 6.5 The k-anonymity gate, as a database floor

```sql
create policy team_aggregate_k_gated on team_patterns
  for select using (
    team_id = current_team_id()
    and n_people_analysed >= (select k_threshold from teams where id = team_id)
  );
```

An under-k aggregate is **invisible, not filtered after the fact**. This is the
direct answer to "what happens with 2 employees at different stress levels": the
rows exist, and no employer session can select them.

Part 4 keeps its Python gate — two independent gates, and the database one holds
even if Part 4 is bypassed. `part3/tests/test_privacy.py` gains a mirror test
that authenticates as a manager and asserts `stress_scores` returns empty and an
under-k `team_patterns` returns empty.

The gate is deliberately role-independent: employees read their own team's
aggregate under the same k floor. Nothing in it is personal.

### 6.6 Opt-in escalation (principle 6)

A consent row enables a *second, narrow* policy, on `employee_insights` only —
never on raw biometrics:

```sql
create policy shared_by_explicit_consent on employee_insights
  for select using (
    exists (select 1 from sharing_consents c
            where c.person_id = employee_insights.person_id
              and c.scope = 'full_detail'
              and c.revoked_at is null
              and is_manager_of(c.granted_to_team_id))
  );
```

Employees write their own consent rows (`for insert with check (person_id =
current_person_id())`) and revoke by setting `revoked_at`, which takes effect on
the next query. Consent is never inferred from the aggregate — a `team_patterns`
row grants nothing.

### 6.7 Keys and the write path

| Actor | Key | Reach |
|---|---|---|
| Part 5 browser client | `anon` key + the user's JWT | RLS applies — sees only what 6.4–6.6 allow |
| Pipeline / seeder (Parts 1–4) | `service_role` key | Bypasses RLS; writes every table |

`service_role` runs only in the seeding script and the pipeline, server-side.
**It must never reach the browser bundle or the repo** — shipping it voids every
policy above at once, and it is the one mistake that would make this whole
section decorative. It belongs in `.env` alongside `OPENAI_API_KEY`.

### 6.8 Seeding and demo accounts

`supabase/migrations/0001_schema.sql`, `0002_rls.sql`, and a
`scripts/seed_supabase.py` that loads the existing `data/*.csv` and `data/*.json`
straight into the tables above. The cohort stays deterministic
(`random.seed(42)`), so the demo database is reproducible from a clean project.

Seed seven accounts against one team: six employees (the existing cohort) and
one manager. Logging in as an employee and then as the manager, against the same
screen, *is* the privacy demo.

### 6.9 What this buys in the pitch

Judges hear "we don't show managers individual data" from every team. The
answerable version is: here is the policy list, here is the manager's session
returning zero rows from the private tables, here is the aggregate disappearing
when the team drops below five. Section 2's principles stop being a slide and
become `\dp` output.

## 7. Cut list if time runs short

Drop in this order: demo polish → opt-in toggle (hardcode as off) → multi-day trends (use single-day snapshot) → per-person nuance in Part 3 (fall back to team-level correlation only). If Supabase Auth runs long, seed two hardcoded JWTs rather than wiring the full login flow. **Never cut** the k-anonymity gate, the RLS policies in section 6, or the "synthetic data" labeling — those are your credibility anchors with judges.
