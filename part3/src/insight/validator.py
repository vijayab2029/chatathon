"""Deterministic hypothesis testing -- the integrity gate of the system.

Every number a narrator is allowed to say about a person originates here. A
language model may propose a hypothesis; only this module decides whether it
survived contact with the data, and it does so with arithmetic that is
reproducible, inspectable and seeded.

Design commitments:
  * Pure stdlib. No numpy, no scipy, no pandas.
  * Never raise on degenerate input. Empty timelines, all-None stress, constant
    features -- all return a *failed* ``ValidatedPattern`` with a plain-English
    ``rejection_reason``.
  * Reproducible. The permutation test is seeded from the hypothesis id, so the
    same hypothesis on the same data yields the same p-value on every run and
    on every machine. A demo that prints different numbers each time cannot be
    defended to a judge.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Iterable, Sequence

from .models import Hypothesis, PersonTimeline, ValidatedPattern

__all__ = ["validate", "validate_all", "top_patterns", "apply_fdr", "robustness_rank"]

# Tuning knobs. Kept module-level and named so the methodology is auditable.
N_PERMUTATIONS = 1000
P_VALUE_THRESHOLD = 0.1
MIN_GROUP_SIZE = 3

# Absorbs floating-point noise when comparing a permuted lift to the observed
# one. Without it, a null dataset (every lift exactly 0.0) can report p < 1.0
# purely from summation order.
_EPS = 1e-12


# --------------------------------------------------------------------------
# Small numeric helpers (all division-guarded)
# --------------------------------------------------------------------------

def _mean(xs: Sequence[float]) -> float:
    n = len(xs)
    if n == 0:
        return 0.0
    return sum(xs) / n


def _pearson_r(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation, returning 0.0 rather than raising or producing NaN.

    Zero variance in either series means "no linear relationship can be
    measured", which we report as 0.0 -- an honest null, not an error.
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0

    mx = _mean(xs)
    my = _mean(ys)

    sxy = 0.0
    sxx = 0.0
    syy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy

    if sxx <= 0.0 or syy <= 0.0:
        return 0.0

    denom = math.sqrt(sxx) * math.sqrt(syy)
    if denom <= 0.0:
        return 0.0

    r = sxy / denom
    # Clamp: accumulated float error can nudge a perfect correlation past 1.0.
    if r > 1.0:
        return 1.0
    if r < -1.0:
        return -1.0
    return r


def _comparator(operator: str) -> Callable[[float, float], bool]:
    if operator == ">=":
        return lambda v, t: v >= t
    if operator == ">":
        return lambda v, t: v > t
    if operator == "<=":
        return lambda v, t: v <= t
    if operator == "<":
        return lambda v, t: v < t
    if operator == "==":
        # Tolerant equality: features are floats, thresholds often integral.
        return lambda v, t: abs(v - t) < 1e-9
    # Unknown operator: nothing is ever exposed, so the hypothesis fails for
    # lack of support rather than blowing up mid-run.
    return lambda v, t: False


def _seed_from_id(hypothesis_id: str) -> int:
    """Stable 32-bit seed from a string.

    Deliberately NOT ``hash()``: CPython salts string hashing per process, so
    ``hash()`` would make p-values differ between runs. This FNV-1a fold is
    fixed for all time.
    """
    h = 2166136261
    for ch in hypothesis_id:
        h ^= ord(ch) & 0xFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _failed(
    h: Hypothesis,
    reason: str,
    *,
    n_exposed: int = 0,
    n_unexposed: int = 0,
    mean_exposed: float = 0.0,
    mean_unexposed: float = 0.0,
    lift: float = 0.0,
    pearson_r: float = 0.0,
    p_value: float = 1.0,
) -> ValidatedPattern:
    return ValidatedPattern(
        hypothesis=h,
        n_exposed=n_exposed,
        n_unexposed=n_unexposed,
        mean_exposed=mean_exposed,
        mean_unexposed=mean_unexposed,
        lift=lift,
        pearson_r=pearson_r,
        p_value=p_value,
        passed=False,
        rejection_reason=reason,
    )


# --------------------------------------------------------------------------
# Pair building
# --------------------------------------------------------------------------

def _build_pairs(
    timeline: PersonTimeline, h: Hypothesis
) -> tuple[list[float], list[float]]:
    """Return (lagged feature values, same-index stress scores).

    For each day index ``i`` with ``i - lag >= 0``, the feature is read from day
    ``i - lag`` and the stress from day ``i``. Days whose stress is missing are
    dropped -- we never impute a stress score.
    """
    days = getattr(timeline, "days", None) or []
    lag = int(h.lag_days)
    if lag < 0:
        return [], []

    feature_vals: list[float] = []
    stress_vals: list[float] = []

    for i in range(lag, len(days)):
        source_day = days[i - lag]
        target_day = days[i]

        stress = getattr(target_day, "stress_score", None)
        if stress is None:
            continue
        try:
            stress_f = float(stress)
        except (TypeError, ValueError):
            continue
        if stress_f != stress_f:  # NaN
            continue

        features = getattr(source_day, "features", None) or {}
        try:
            value_f = float(features.get(h.feature, 0.0))
        except (TypeError, ValueError):
            value_f = 0.0
        if value_f != value_f:  # NaN feature
            continue

        feature_vals.append(value_f)
        stress_vals.append(stress_f)

    return feature_vals, stress_vals


# --------------------------------------------------------------------------
# Permutation test
# --------------------------------------------------------------------------

def _permutation_p_value(
    stress: Sequence[float],
    n_exposed: int,
    n_unexposed: int,
    observed_lift: float,
    seed: int,
    n_permutations: int = N_PERMUTATIONS,
) -> float:
    """Fraction of label shuffles whose |lift| >= the observed |lift|.

    The exposure mask is held fixed and the stress labels are reshuffled, which
    is the same null as permuting group membership. Only floats are moved --
    no objects are rebuilt inside the loop.
    """
    if n_exposed <= 0 or n_unexposed <= 0 or n_permutations <= 0:
        return 1.0

    total = math.fsum(stress)
    target = abs(observed_lift) - _EPS
    rnd = random.Random(seed)
    sample = rnd.sample
    pool = list(stress)

    # Draw from whichever side is smaller; the other group's sum follows from
    # the total, so each permutation costs O(min(n_exposed, n_unexposed)).
    draw_exposed = n_exposed <= n_unexposed
    k = n_exposed if draw_exposed else n_unexposed

    hits = 0
    for _ in range(n_permutations):
        drawn = math.fsum(sample(pool, k))
        if draw_exposed:
            sum_exposed = drawn
        else:
            sum_exposed = total - drawn
        lift = (sum_exposed / n_exposed) - ((total - sum_exposed) / n_unexposed)
        if abs(lift) >= target:
            hits += 1

    # (hits + 1) / (n + 1), the standard unbiased permutation estimator. A plain
    # hits/n can report p == 0.0, which is not a claim 1000 shuffles can support:
    # the most it can say is p < 1/1001. Reporting an exact zero to a judge is a
    # credibility problem, so the estimator floors itself honestly.
    return (hits + 1) / (n_permutations + 1)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def validate(timeline: PersonTimeline, h: Hypothesis) -> ValidatedPattern:
    """Test one hypothesis against one person's timeline.

    Returns a ``ValidatedPattern`` in every case. A hypothesis that cannot be
    tested is a *failed* pattern with a reason, not an exception.
    """
    if h is None:
        raise TypeError("validate() requires a Hypothesis")

    feature_vals, stress_vals = _build_pairs(timeline, h)
    n_pairs = len(feature_vals)

    if n_pairs == 0:
        return _failed(h, "no usable data: no days with both a feature value and a stress score")

    satisfies = _comparator(str(h.operator))
    threshold = float(h.threshold)

    # Precompute the exposure mask once; the permutation loop never re-evaluates
    # the operator.
    exposed_stress: list[float] = []
    unexposed_stress: list[float] = []
    for value, stress in zip(feature_vals, stress_vals):
        if satisfies(value, threshold):
            exposed_stress.append(stress)
        else:
            unexposed_stress.append(stress)

    n_exposed = len(exposed_stress)
    n_unexposed = len(unexposed_stress)

    mean_exposed = _mean(exposed_stress)
    mean_unexposed = _mean(unexposed_stress)

    # Correlation is over ALL pairs, using the raw lagged feature value -- it is
    # independent of the threshold and so survives a badly chosen cut point.
    pearson_r = _pearson_r(feature_vals, stress_vals)

    if n_exposed == 0 or n_unexposed == 0:
        empty_side = "exposed" if n_exposed == 0 else "comparison"
        return _failed(
            h,
            f"insufficient support: only {min(n_exposed, n_unexposed)} {empty_side} days "
            f"(need {MIN_GROUP_SIZE} on each side)",
            n_exposed=n_exposed,
            n_unexposed=n_unexposed,
            mean_exposed=mean_exposed,
            mean_unexposed=mean_unexposed,
            lift=0.0,
            pearson_r=pearson_r,
            p_value=1.0,
        )

    lift = mean_exposed - mean_unexposed

    p_value = _permutation_p_value(
        stress_vals,
        n_exposed,
        n_unexposed,
        lift,
        seed=_seed_from_id(h.id),
    )

    rejection_reason: str | None = None
    if n_exposed < MIN_GROUP_SIZE:
        rejection_reason = (
            f"insufficient support: only {n_exposed} exposed "
            f"{'day' if n_exposed == 1 else 'days'}"
        )
    elif n_unexposed < MIN_GROUP_SIZE:
        rejection_reason = (
            f"insufficient support: only {n_unexposed} comparison "
            f"{'day' if n_unexposed == 1 else 'days'}"
        )
    elif p_value > P_VALUE_THRESHOLD:
        rejection_reason = f"not statistically distinguishable (p={p_value:.2f})"

    passed = rejection_reason is None

    return ValidatedPattern(
        hypothesis=h,
        n_exposed=n_exposed,
        n_unexposed=n_unexposed,
        mean_exposed=mean_exposed,
        mean_unexposed=mean_unexposed,
        lift=lift,
        pearson_r=pearson_r,
        p_value=p_value,
        passed=passed,
        rejection_reason=rejection_reason,
    )


def validate_all(
    timeline: PersonTimeline, hypotheses: Iterable[Hypothesis]
) -> list[ValidatedPattern]:
    """Validate every hypothesis, strongest first.

    Failures are retained deliberately: being able to show a judge the
    hypotheses that were *killed* is as important as showing the survivors.
    """
    results = [validate(timeline, h) for h in hypotheses if h is not None]
    results.sort(key=lambda r: (r.passed, abs(r.lift)), reverse=True)
    return results


def top_patterns(
    timeline: PersonTimeline, hypotheses: Iterable[Hypothesis], n: int = 3
) -> list[ValidatedPattern]:
    """The ``n`` strongest patterns that actually passed. Never pads with fails."""
    if n <= 0:
        return []
    return [r for r in validate_all(timeline, hypotheses) if r.passed][:n]


# --------------------------------------------------------------------------
# Multiple-comparisons control
# --------------------------------------------------------------------------

def apply_fdr(results: list[ValidatedPattern], q: float = 0.10) -> list[ValidatedPattern]:
    """Benjamini-Hochberg false-discovery-rate control, applied in place.

    Adaptive hypotheses let us test ~60 candidates per person instead of 14. At
    p <= 0.10 that alone would hand back roughly six spurious "findings" per
    person purely by chance -- and telling someone their calendar causes stress
    on the strength of a coin flip is exactly the failure this tool exists to
    avoid. BH controls the expected proportion of false discoveries among the
    patterns we report, rather than the per-test error rate.

    Patterns that fail the correction keep their computed numbers but are marked
    not passed, with the reason recorded.
    """
    candidates = [r for r in results if r.passed]
    m = len(candidates)
    if m == 0:
        return results

    ordered = sorted(candidates, key=lambda r: r.p_value)
    # Largest k where p_(k) <= (k/m) * q
    cutoff_rank = 0
    for index, result in enumerate(ordered, start=1):
        if result.p_value <= (index / m) * q:
            cutoff_rank = index
    threshold = ordered[cutoff_rank - 1].p_value if cutoff_rank else -1.0

    for result in candidates:
        if result.p_value > threshold:
            result.passed = False
            result.rejection_reason = (
                f"did not survive false-discovery correction across {m} tested "
                f"patterns (p={result.p_value:.3f})"
            )
    return results


def robustness_rank(result: ValidatedPattern) -> tuple[float, float]:
    """Sort key for surviving patterns: standardized effect first, then size.

    Two things are being asked, and they should not be mixed into one number:

      * IS IT REAL?  Already settled -- the permutation test plus `apply_fdr`
        decided that before anything reaches this ranking.
      * WHICH RELATIONSHIP IS STRONGEST?  That is what this answers.

    Primary key is |pearson_r|, computed on the raw (unthresholded) feature
    against lagged stress. It is scale-free, so features measured in minutes
    and features measured in counts compare fairly, and it is threshold-
    independent, so a pattern is not flattered by a lucky cut point. Because
    callers de-duplicate by feature, ranking on r effectively asks "which
    feature matters most", which is the question the output actually poses.

    Secondary key is |lift|, which picks the threshold that best expresses that
    feature -- the number the employee is shown.

    Rejected alternatives, for the record: ranking on |lift| alone let a 3-day
    pattern outrank a 5-day one at nearly identical effect. Weighting by
    sqrt(support) over-corrected and let high-count, low-effect confounders
    bury the true driver. Weighting by support**0.25 scored better on our
    fixtures but the exponent is unjustifiable -- that is fitting the ranking
    to the benchmark, not to the problem.

    Known limitation: Pearson r measures LINEAR association, so a genuinely
    threshold-shaped effect (nothing until 4 meetings, then a cliff) ranks lower
    than its importance warrants. The lift tiebreak partly compensates.
    """
    return (abs(result.pearson_r), abs(result.lift))
