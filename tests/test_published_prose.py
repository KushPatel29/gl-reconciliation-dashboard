"""
Every number the README states in prose must still be the number the engine
produces, formatted the way a reader sees it.

The suite already pins the published FIGURES: change the analysis and
`test_journal_risk.py` and `test_exception_ageing.py` fail. What nothing pinned
was the other direction — the prose. A figure can move, the JSON assertion can
be updated, and the document can go on quoting last month's run with a green
build the whole way. That is not hypothetical; it happened in a sibling
repository, and the test that caught it there is the model for this one.

Two of the assertions below are about the shape of a claim rather than its
value, because the README does not just quote figures — it argues that some
control tests are worthless on this ledger, and that the self-approval weakness
is concentrated rather than universal. If either stops being true, the sentence
has to be rewritten rather than left standing with a fresh number in it.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
OUT = ROOT / "output"


@pytest.fixture(scope="module")
def prose():
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def risk():
    return json.loads((OUT / "journal_risk_summary.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ageing():
    return json.loads((OUT / "exception_ageing_summary.json").read_text(
        encoding="utf-8"))


def quoted(prose, needle):
    """Match across line breaks: prose wraps, and a figure that lands either
    side of a wrap is still quoted."""
    return " ".join(needle.split()) in " ".join(prose.split())


def test_the_readme_is_the_real_one(prose):
    assert len(prose) > 10_000
    assert "## Whether the ledgers agree is one question." in prose
    assert "## What happened to the exceptions afterwards" in prose


def test_the_population_is_quoted_as_published(risk, prose):
    r = risk
    assert quoted(prose, f"tests all {r['entries']:,}")
    assert quoted(prose, f"{r['manual_entries']:,} of the {r['entries']:,} entries "
                         "are manual")
    assert quoted(prose, f"({r['manual_share']:.1%})")
    assert quoted(prose, f"${r['manual_value']:,.0f}")


def test_the_useless_tests_are_named_as_published(risk, prose):
    """The argument the section is built on: some control tests carry no signal
    here and the page says which. If they all start discriminating, the claim
    is no longer true."""
    r = risk
    assert r["flags_that_do_not"] > 0
    total = r["flags_that_discriminate"] + r["flags_that_do_not"]
    assert quoted(prose, f"**{r['flags_that_do_not']} of {total} fail that check**")
    for flag in r["non_discriminating_flags"]:
        assert quoted(prose, flag), flag


def test_the_weekend_non_finding_is_quoted_as_published(risk, prose):
    r = risk
    assert quoted(prose, f"{r['weekend_manual_rate']:.1%} on manual entries")
    assert quoted(prose, f"{r['weekend_interfaced_rate']:.1%} on interfaced ones")
    # The README quotes the count of exceptions the naive test would have
    # produced; it has to be the count the data actually supports.
    assert quoted(prose, str(int(r["weekend_manual_rate"] * r["manual_entries"])))


def test_the_after_hours_baseline_figures_are_quoted_as_published(risk, prose):
    r = risk
    assert quoted(prose, f"{r['after_hours_close_rate']:.1%} against "
                         f"{r['after_hours_normal_rate']:.1%}")
    assert r["after_hours_close_rate"] > r["after_hours_normal_rate"]


def test_the_findings_table_is_quoted_as_published(risk, prose):
    r = risk
    for count, value in [
            (r["self_approved"], r["self_approved_value"]),
            (r["above_approver_limit"], r["above_approver_limit_value"]),
            (r["threshold_avoidance"], r["threshold_avoidance_value"])]:
        assert quoted(prose, f"| {count} | ${value:,.0f} |"), count
    assert quoted(prose, f"| round thousands | {r['round_dollar']} |")
    assert quoted(prose, f"| outside business hours | {r['after_hours']} |")


def test_the_concentration_claim_is_quoted_as_published(risk, prose):
    """"Two people work around a bottleneck" and "nobody understands the
    policy" are different findings with different fixes. The README claims the
    first, so the data has to support it."""
    r = risk
    assert r["self_approval_top2_share"] > 0.5
    assert quoted(prose, f"**{r['self_approval_top2_share']:.0%} of it is two people**")
    assert quoted(prose, f"{r['self_approval_worst']} alone accounts for")
    assert quoted(prose, str(r["self_approval_worst_count"]))


def test_the_review_list_size_is_quoted_as_published(risk, prose):
    r = risk
    assert quoted(prose, f"{r['for_review']} entries clear the review threshold")
    assert quoted(prose, f"{r['for_review_share_of_manual']:.1%} of manual entries")
    assert quoted(prose, f"${r['for_review_value']:,.0f}")


def test_the_benford_verdict_is_quoted_as_published(risk, prose):
    r = risk
    assert quoted(prose, str(r["benford_critical_5pct"]))
    assert quoted(prose, f"{r['benford_deviating']} of {r['benford_groups']} "
                         "cost centres exceed it")


def test_the_close_quality_figures_are_quoted_as_published(ageing, prose):
    a = ageing
    assert quoted(prose, f"identifies {a['exceptions']} differences")
    assert quoted(prose, f"{a['cleared']} cleared and {a['still_open']} are still open")
    assert quoted(prose, f"${a['open_exposure']:,.0f}")
    assert quoted(prose, f"median clears in {a['median_days_to_clear']:.0f} days")
    assert quoted(prose, f"mean of {a['mean_days_to_clear']:.1f}")
    assert quoted(prose, f"P90 of {a['p90_days_to_clear']:.0f}")
    assert quoted(prose, f"worst of {a['worst_days_to_clear']}")
    assert quoted(prose, f"{a['breaches']} ({a['breach_rate']:.1%}) breached")
    assert quoted(prose, f"{a['sla_days']}-day SLA")


def test_the_owner_spread_and_the_tail_are_quoted_as_published(ageing, prose):
    a = ageing
    assert quoted(prose, f"{a['slowest_owner']} takes")
    assert quoted(prose, f"{a['owner_spread_multiple']:.1f}× as long as "
                         f"{a['fastest_owner']}")
    assert quoted(prose, f"{a['worst_tail_owner']} has the opposite")
    assert quoted(prose, f"median of {a['worst_tail_median']:.0f} days against a mean "
                         f"of {a['worst_tail_mean']:.1f}")


def test_the_absorbed_resolutions_are_quoted_as_published(ageing, prose):
    a = ageing
    assert quoted(prose, f"{a['absorbed']} of {a['cleared']} "
                         f"({a['absorbed_share']:.0%})")
    assert quoted(prose, f"${a['absorbed_exposure']:,.0f}")
    assert quoted(prose, f"grew in {a['months_backlog_grew']} of six months")
    assert quoted(prose, f"finished at {a['final_backlog']}")


def test_the_join_bug_is_recorded_with_its_real_numbers(ageing, prose):
    """The README tells the story of a fan-out that inflated every count by a
    third. The numbers in that story have to be this repository's numbers."""
    a = ageing
    assert quoted(prose, f"184 postings produce {a['exceptions']} of them")
    assert quoted(prose, "310 rows")
    assert quoted(prose, f"demands it land on {a['exceptions']}")
