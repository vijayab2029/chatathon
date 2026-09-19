"""Orchestration for the Part 3 correlation & insight engine.

ALL DATA IS SYNTHETIC.

The pipeline, in one line: LLM agents propose and explain, Python proves.

    load -> digest -> [A1 hypothesise] -> [A2 validate] -> [A3 narrate] -> [A4 critique]
                            (OpenAI)        (pure Python)     (OpenAI)      (OpenAI +
                                            THE GATE                         deterministic)

Every stage degrades. With no API key, `--offline`, or a rate-limited account, the
baseline hypotheses still run, the validator still proves them, and template narration
still produces a real insight backed by real numbers. The demo cannot hard-fail on a quota.

Usage:
    python -m insight.pipeline                    # from part3/src
    python part3/src/insight/pipeline.py --offline
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Running as a plain script (`python path/to/pipeline.py`). Re-exec through the
    # package so that the relative imports inside the functions below resolve too --
    # putting src on sys.path only fixes module-level imports, which left the usage
    # line in this very docstring broken.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import runpy

    runpy.run_module("insight.pipeline", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

from .models import PersonTimeline, ValidatedPattern, CriticVerdict  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURES = REPO_ROOT / "part3" / "data" / "fixtures"
DEFAULT_OUT = REPO_ROOT / "part3" / "data" / "out"


# --------------------------------------------------------------------------
# Timeline digest -- the compact summary sent to the hypothesis agent.
# We deliberately do NOT send raw day rows: it keeps the prompt small (free-tier
# friendly) and means the model reasons about structure rather than memorising data.
# --------------------------------------------------------------------------

def build_digest(timeline: PersonTimeline) -> dict[str, Any]:
    scored = [d for d in timeline.days if d.stress_score is not None]
    digest: dict[str, Any] = {
        "n_days": len(timeline.days),
        "n_days_with_stress": len(scored),
        "mean_stress": round(statistics.fmean(
            [d.stress_score for d in scored]), 1) if scored else None,
        "features": {},
    }
    if not timeline.days:
        return digest

    for name in timeline.days[0].features:
        series = timeline.feature_series(name)
        if not series:
            continue
        digest["features"][name] = {
            "min": round(min(series), 1),
            "max": round(max(series), 1),
            "mean": round(statistics.fmean(series), 1),
        }
    return digest


# --------------------------------------------------------------------------
# Template narration -- the no-LLM fallback. Still backed by validated numbers.
# --------------------------------------------------------------------------

_ACTION_TEMPLATES = {
    "back_to_back_blocks": "Add a 15-minute buffer between consecutive meetings on your busiest day.",
    "longest_back_to_back_run": "Break up your longest meeting chain with a scheduled gap.",
    "after_hours_meetings": "Set a calendar boundary that declines meetings ending after 18:00.",
    "no_agenda_meetings": "Ask for an agenda before accepting; decline meetings that arrive without one.",
    "large_meetings": "Ask whether you need to attend large meetings live, or can read the notes.",
    "meeting_count": "Cap your meeting count on your heaviest weekday.",
    "total_meeting_minutes": "Shorten default meeting length from 60 to 45 minutes.",
    "negative_sentiment_meetings": "Schedule a recovery gap after difficult conversations.",
    "has_lunch_buffer": "Block a recurring 30-minute lunch hold that meetings cannot overwrite.",
    "focus_time_minutes": "Protect one 90-minute focus block each morning.",
    "context_switches": "Group similar meetings onto the same day to reduce context switching.",
    "recurring_meetings": "Audit your recurring meetings and drop the ones without a clear purpose.",
}


def template_narration(patterns: list[ValidatedPattern],
                       avg_stress: float) -> tuple[str, str]:
    if not patterns:
        return (
            f"Your average stress score over this period was {avg_stress:.0f}. "
            "No calendar pattern showed a statistically distinguishable relationship "
            "with your stress in this window.",
            "Keep logging. A longer window may reveal patterns a short one cannot.",
        )

    top = patterns[0]
    ev = top.evidence_dict()
    when = "on the same day" if top.hypothesis.lag_days == 0 else "the following day"
    feature_label = top.hypothesis.feature.replace("_", " ")
    text = (
        f"Your stress score averages {ev['mean_stress_when_present']} {when} after "
        f"days with {feature_label} {top.hypothesis.operator} "
        f"{top.hypothesis.threshold:g}, compared with "
        f"{ev['mean_stress_when_absent']} otherwise - a difference of "
        f"{abs(ev['lift_points'])} points across {ev['days_observed']} such days. "
        "This is a correlation in your own simulated data, not a diagnosis."
    )
    action = _ACTION_TEMPLATES.get(
        top.hypothesis.feature,
        "Review how this pattern shows up in your week and adjust one recurring block.",
    )
    return text, action


# --------------------------------------------------------------------------
# Per-person analysis
# --------------------------------------------------------------------------

def analyse_person(timeline: PersonTimeline, client, *, n_patterns: int = 3) -> dict[str, Any]:
    from .hypotheses import baseline_hypotheses, adaptive_hypotheses, dedupe
    from .validator import validate_all, apply_fdr, robustness_rank
    from .llm.agent_hypothesis import propose_hypotheses
    from .llm.agent_narrator import narrate
    from .llm.agent_critic import review

    # A1 -- baseline always present; the LLM only ADDS candidates.
    # Adaptive hypotheses derive their thresholds from this person's own range.
    # Without them a curated threshold can be dead on arrival: emp_007's
    # back_to_back_blocks never exceeds 1, so the ">= 2" test had zero exposed
    # days and their real driver was invisible.
    hyps = list(baseline_hypotheses()) + adaptive_hypotheses(timeline)
    if client.available:
        try:
            hyps += propose_hypotheses(client, timeline.person_id, build_digest(timeline))
        except Exception as exc:  # never let the LLM break the run
            print(f"  [warn] hypothesis agent failed for {timeline.person_id}: {exc}")
    hyps = dedupe(hyps)

    # A2 -- the gate. Nothing unproven gets past here.
    # validate_all deliberately retains failures so the demo can show killed
    # hypotheses; only the survivors go forward.
    results = validate_all(timeline, hyps)

    # Testing ~60 adaptive candidates instead of 14 would hand back several
    # spurious findings per person at p <= 0.1, so control the false-discovery
    # rate across the whole family before anything is called a finding.
    apply_fdr(results)

    # Rank on effect size weighted by support, not raw lift. Raw lift let a
    # 3-day pattern outrank a 5-day one at nearly identical effect and pushed
    # emp_004's real driver out of the top 3.
    survivors = sorted(
        (r for r in results if r.passed), key=robustness_rank, reverse=True
    )
    passing = _dedupe_by_feature(survivors)[:n_patterns]

    scored = [d.stress_score for d in timeline.days if d.stress_score is not None]
    avg_stress = statistics.fmean(scored) if scored else 0.0

    # A3 -- narration. Falls back to templates built from the same validated numbers.
    llm_used = False
    text, action = template_narration(passing, avg_stress)
    if client.available and passing:
        try:
            out = narrate(client, timeline.person_id, passing, avg_stress)
            if out and out.get("insight_text"):
                text = out["insight_text"]
                action = out.get("suggested_action") or action
                llm_used = True
        except Exception as exc:
            print(f"  [warn] narrator failed for {timeline.person_id}: {exc}")

    # A4 -- the critic runs ALWAYS. It is a safety control, so it must not depend
    # on an available API; the deterministic half works offline.
    allowed = _allowed_numbers(passing, avg_stress)
    verdict: CriticVerdict | None = None
    try:
        verdict = review(client, text, action, allowed)
        if verdict and not verdict.approved and verdict.revised_text:
            text = verdict.revised_text
    except Exception as exc:
        print(f"  [warn] critic failed for {timeline.person_id}: {exc}")

    return {
        "timeline": timeline,
        "patterns": passing,
        "all_results": results,
        "insight_text": text,
        "suggested_action": action,
        "critic": verdict,
        "llm_used": llm_used,
        "avg_stress": avg_stress,
    }


def _dedupe_by_feature(patterns: list[ValidatedPattern]) -> list[ValidatedPattern]:
    """Keep only the strongest surviving pattern per feature.

    Without this, `meeting_count >= 5 (lag 0)` and `meeting_count >= 5 (lag 1)` both
    surface and a person is shown the same finding twice. Results arrive sorted by
    |lift| descending, so the first occurrence of a feature is its strongest.
    """
    seen: set[str] = set()
    out: list[ValidatedPattern] = []
    for p in patterns:
        if p.hypothesis.feature in seen:
            continue
        seen.add(p.hypothesis.feature)
        out.append(p)
    return out


def _allowed_numbers(patterns: list[ValidatedPattern], avg_stress: float) -> list[float]:
    """Every number the narrator is permitted to cite. The critic enforces this."""
    nums: list[float] = [round(avg_stress, 1), round(avg_stress)]
    for p in patterns:
        ev = p.evidence_dict()
        nums += [
            ev["lift_points"], abs(ev["lift_points"]),
            ev["mean_stress_when_present"], ev["mean_stress_when_absent"],
            float(ev["days_observed"]), float(ev["days_compared"]),
            ev["correlation_r"], ev["p_value"],
            float(p.hypothesis.threshold), float(p.hypothesis.lag_days),
        ]
    return [float(n) for n in nums]


# --------------------------------------------------------------------------
# Full run
# --------------------------------------------------------------------------

def run_pipeline(stress_csv: Path | None = None,
                 meetings_json: Path | None = None,
                 out_dir: Path | None = None,
                 offline: bool = False,
                 limit: int | None = None) -> dict[str, Any]:
    from .loaders import load_stress_scores, load_meeting_events, build_timelines
    from .emit import build_employee_insight, aggregate_team_patterns, write_outputs
    from .llm.openai_client import LLMClient

    stress_csv = Path(stress_csv) if stress_csv else DEFAULT_FIXTURES / "stress_scores.csv"
    meetings_json = Path(meetings_json) if meetings_json else DEFAULT_FIXTURES / "meeting_features.json"
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    stress = load_stress_scores(stress_csv)
    events = load_meeting_events(meetings_json)
    timelines = build_timelines(stress, events)

    person_ids = sorted(timelines)
    if limit:
        person_ids = person_ids[:limit]

    client = LLMClient(offline=offline)
    mode = "OFFLINE (deterministic only)" if not client.available else "LLM agents ENABLED"
    print(f"Analysing {len(person_ids)} people | {mode}")

    insights = {}
    per_person_patterns: dict[str, list[ValidatedPattern]] = {}

    for pid in person_ids:
        tl = timelines[pid]
        res = analyse_person(tl, client)
        per_person_patterns[pid] = res["patterns"]
        insights[pid] = build_employee_insight(
            person_id=pid,
            timeline_days=tl.days,
            patterns=res["patterns"],
            insight_text=res["insight_text"],
            suggested_action=res["suggested_action"],
            critic=res["critic"],
            llm_used=res["llm_used"],
        )
        print(f"  {pid}: {len(res['patterns'])} validated pattern(s)")

    all_dates = sorted(
        d.date.isoformat() for tl in timelines.values() for d in tl.days
    )
    date_range = (all_dates[0], all_dates[-1]) if all_dates else ("", "")

    team = aggregate_team_patterns(per_person_patterns, len(person_ids), date_range)
    emp_path, team_path = write_outputs(insights, team, out_dir)

    summary = {
        "n_people": len(person_ids),
        "n_patterns_found": len(team.patterns),
        "llm_calls": getattr(client, "calls_made", 0),
        "cache_hits": getattr(client, "cache_hits", 0),
        "llm_enabled": client.available,
        "employee_insight_path": str(emp_path),
        "team_correlations_path": str(team_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_provenance": "SIMULATED",
    }
    print(f"\nWrote {emp_path.name} and {team_path.name} to {out_dir}")
    print(f"LLM calls: {summary['llm_calls']} | cache hits: {summary['cache_hits']}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 3 correlation & insight engine (SYNTHETIC DATA)")
    ap.add_argument("--stress", type=Path, default=None)
    ap.add_argument("--meetings", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--offline", action="store_true",
                    help="run with no LLM calls at all (deterministic + templates)")
    ap.add_argument("--limit", type=int, default=None, help="only analyse the first N people")
    args = ap.parse_args()
    run_pipeline(args.stress, args.meetings, args.out, args.offline, args.limit)


if __name__ == "__main__":
    main()
