"""
A control test that fires on everything has told you nothing.

The point of this module is not that it can flag journal entries - anything
can flag journal entries - but that it knows which of its own tests are worth
running on this ledger and says so. So the tests below check the flags, and
then check the thing that makes the flags trustworthy: that each one is
measured against a population it should not fire on, and that the ones which
fire equally on both are reported as non-findings rather than as several
hundred exceptions.

The second half pins Benford against its closed form. A first-digit test with
a mis-transcribed expected distribution or a wrong critical value looks exactly
like a working one and quietly condemns or exonerates whole cost centres.
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import journal_risk as jr  # noqa: E402

DATA = ROOT / "data"
PUBLISHED = ROOT / "output" / "journal_risk_summary.json"


@pytest.fixture(scope="module")
def built():
    return jr.build()


# --- the flags --------------------------------------------------------------

def test_every_entry_in_the_ledger_is_tested(built):
    """Journal testing on a sample of forty is the practice this replaces. If
    the join ever drops rows, it quietly becomes a sample again."""
    summary, e, _, _, _ = built
    erp = pd.read_csv(DATA / "source_erp_gl.csv")
    assert summary["entries"] == len(erp)
    assert len(e) == len(erp)
    assert e.transaction_id.nunique() == len(erp)


def test_no_flag_fires_on_an_interfaced_entry(built):
    """Everything except the manual flag is conditioned on a human having
    chosen something. Scoring a scheduled job for posting at 03:00 is how a
    monitoring programme fills with noise and stops being read."""
    _, e, _, _, _ = built
    auto = e[e.manual_entry == 0]
    assert len(auto) > 0
    for flag in jr.WEIGHTS:
        assert auto[flag].sum() == 0, flag
    assert auto.risk_score.sum() == 0


def test_the_score_is_exactly_its_published_weights(built):
    _, e, _, _, _ = built
    rebuilt = sum(e[f] * w for f, w in jr.WEIGHTS.items())
    assert (rebuilt == e.risk_score).all()


def test_the_weekend_test_carries_no_weight_because_it_carries_no_signal():
    """The one flag deliberately scored at zero. If somebody gives it a weight
    without re-checking the baseline, 406 entries join the review list on the
    strength of a calendar artefact."""
    assert jr.WEIGHTS["weekend"] == 0


def test_self_approval_means_the_same_person_twice(built):
    _, e, _, _, _ = built
    flagged = e[e.self_approved == 1]
    assert len(flagged) > 0
    assert (flagged.posted_by == flagged.approved_by).all()
    assert (flagged.manual_entry == 1).all()


def test_threshold_avoidance_really_sits_just_under_a_limit(built):
    summary, e, _, _, _ = built
    users = pd.read_csv(DATA / "dim_user.csv")
    limits = sorted(v for v in users.approval_limit.unique() if v > 0)
    flagged = e[e.threshold_avoidance == 1]
    assert len(flagged) > 0
    for amount in flagged.abs_amount:
        assert any(lim * (1 - jr.AVOIDANCE_BAND) <= amount < lim
                   for lim in limits), amount
    # ...and the band really is a band: nothing at or above a limit qualifies.
    assert not any(amount >= max(limits) for amount in flagged.abs_amount)


def test_round_dollar_means_round(built):
    _, e, _, _, _ = built
    flagged = e[e.round_dollar == 1]
    assert len(flagged) > 0
    assert (flagged.abs_amount % jr.ROUND_TO == 0).all()
    assert (flagged.abs_amount > 0).all()


def test_above_approver_limit_reads_the_approvers_authority_not_the_posters(built):
    _, e, _, _, _ = built
    flagged = e[e.above_approver_limit == 1]
    assert len(flagged) > 0
    assert (flagged.abs_amount > flagged.approver_limit).all()


# --- the baselines, which are the point -------------------------------------

def test_every_flag_is_measured_against_a_named_baseline(built):
    _, _, flags, _, _ = built
    assert set(flags.flag) == set(jr.WEIGHTS)
    assert flags.baseline.notna().all()
    assert (flags.baseline.str.len() > 0).all()


def test_the_after_hours_baseline_is_not_the_automated_population(built):
    """A scheduled job posts at 03:00 by design, so the interfaced population
    is 100% out of hours. Comparing a human to it proves nothing, and the first
    version of this module reported after-hours as a non-finding because of
    it."""
    _, _, flags, _, _ = built
    row = flags[flags.flag == "after_hours"].iloc[0]
    assert "manual" in row.baseline
    assert row.baseline_rate < 0.5
    assert row.discriminates == 1


def test_some_tests_are_reported_as_useless_and_that_is_the_feature(built):
    """If every test discriminated, the baseline machinery would be doing
    nothing and this page would be a checklist with a chart on it."""
    summary, _, flags, _, _ = built
    assert summary["flags_that_do_not"] > 0
    assert "weekend" in summary["non_discriminating_flags"]
    useless = flags[flags.discriminates == 0]
    for _, r in useless.iterrows():
        assert r.excess_ratio is None or r.excess_ratio < jr.DISCRIMINATION_RATIO


def test_the_weekend_rates_really_are_indistinguishable(built):
    """The specific claim the page makes out loud."""
    summary, _, _, _, _ = built
    gap = abs(summary["weekend_manual_rate"] - summary["weekend_interfaced_rate"])
    assert gap < 0.02, "the weekend rates differ - the non-finding is now wrong"


def test_a_flag_that_cannot_fire_on_the_baseline_reports_null_not_infinity(built):
    """Round dollars and threshold avoidance are tests of a human choosing a
    number; an interface does not choose. An infinite excess ratio would sort
    to the top of any chart and mean nothing."""
    _, _, flags, _, _ = built
    unbounded = flags[flags.baseline_can_fire == 0]
    assert len(unbounded) > 0
    assert unbounded.excess_ratio.isna().all()
    assert (unbounded.discriminates == 1).all()


# --- the review list --------------------------------------------------------

def test_the_review_list_is_a_mornings_work_not_a_years(built):
    """A control report nobody can finish is a control report nobody starts."""
    summary, e, _, _, _ = built
    assert 0 < summary["for_review"] < 500
    review = e[e.for_review == 1]
    assert (review.risk_score >= jr.REVIEW_THRESHOLD).all()
    assert (e[e.for_review == 0].risk_score < jr.REVIEW_THRESHOLD).all()


def test_the_control_weakness_is_concentrated_not_universal(built):
    """"Two people work around a bottleneck" and "nobody understands the
    policy" are different findings with different fixes, and the page claims
    the first one."""
    summary, _, _, _, _ = built
    assert summary["self_approval_top2_share"] > 0.5
    assert summary["self_approved"] > 0


def test_by_user_reconciles_to_the_entry_level(built):
    summary, e, _, users, _ = built
    manual = e[e.manual_entry == 1]
    assert users.entries.sum() == len(manual)
    assert users.risk_score.sum() == manual.risk_score.sum()
    assert users.for_review.sum() == summary["for_review"]


# --- Benford ----------------------------------------------------------------

def test_benfords_expected_distribution_is_the_real_one():
    assert jr.BENFORD[1] == pytest.approx(0.30103, abs=1e-5)
    assert jr.BENFORD[9] == pytest.approx(0.04576, abs=1e-5)
    assert sum(jr.BENFORD.values()) == pytest.approx(1.0, abs=1e-12)
    assert all(jr.BENFORD[d] > jr.BENFORD[d + 1] for d in range(1, 9))


def test_the_critical_values_are_the_published_ones():
    """Eight degrees of freedom - nine first digits minus one. A wrong value
    here condemns or exonerates whole cost centres and looks identical either
    way."""
    assert jr.CHI2_CRITICAL[0.05] == pytest.approx(15.507, abs=0.01)
    assert jr.CHI2_CRITICAL[0.01] == pytest.approx(20.090, abs=0.01)
    assert jr.CHI2_CRITICAL[0.10] < jr.CHI2_CRITICAL[0.05] < jr.CHI2_CRITICAL[0.01]


def test_the_chi_square_matches_a_hand_computation(built):
    _, e, _, _, ben = built
    row = ben.iloc[0]
    grp = e[e.cost_center == row.cost_center]
    amounts = grp.loc[grp.abs_amount > 0, "abs_amount"]
    first = amounts.map(lambda v: int(f"{v:.10e}"[0]))
    observed = first.value_counts().reindex(range(1, 10), fill_value=0)
    expected = pd.Series({d: jr.BENFORD[d] * len(amounts) for d in range(1, 10)})
    chi2 = float((((observed - expected) ** 2) / expected).sum())
    assert row.chi_square == pytest.approx(chi2, abs=0.01)
    assert row.entries == len(amounts)


def test_the_digit_shares_are_a_distribution(built):
    _, _, _, _, ben = built
    for _, r in ben.iterrows():
        shares = sum(r[f"digit_{d}_share"] for d in range(1, 10))
        assert shares == pytest.approx(1.0, abs=1e-3), r.cost_center
        counts = sum(r[f"digit_{d}"] for d in range(1, 10))
        assert counts == r.entries


def test_the_verdict_follows_the_statistic(built):
    _, _, _, _, ben = built
    for _, r in ben.iterrows():
        expected = "deviates" if r.chi_square > jr.CHI2_CRITICAL[0.05] \
            else "conforms"
        assert r.verdict == expected, r.cost_center


def test_a_deliberately_skewed_population_is_caught():
    """The null case in reverse: if Benford passes everything, it is not
    testing anything. Feed it a population that is 80% leading-9 and it has to
    fail."""
    rigged = pd.DataFrame({
        "cost_center": ["Rigged"] * 500,
        "abs_amount": [9100.0] * 400 + [1100.0] * 100,
    })
    out = jr.benford(rigged, "cost_center")
    assert len(out) == 1
    assert out.iloc[0].verdict == "deviates"
    assert out.iloc[0].chi_square > jr.CHI2_CRITICAL[0.01]


def test_small_populations_are_skipped_rather_than_guessed_at(built):
    tiny = pd.DataFrame({"cost_center": ["Tiny"] * 20,
                         "abs_amount": [float(i + 1) for i in range(20)]})
    assert len(jr.benford(tiny, "cost_center")) == 0


# --- what the README and the report quote -----------------------------------

def test_the_published_summary_is_what_this_code_produces(built):
    summary, _, _, _, _ = built
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for key, value in summary.items():
        if isinstance(value, float):
            assert published[key] == pytest.approx(value, rel=1e-6), key
        else:
            assert published[key] == value, key
