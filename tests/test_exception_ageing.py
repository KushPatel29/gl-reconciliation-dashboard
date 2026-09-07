"""
Every count on the ageing page has to add back to 230 exceptions.

This file exists because the first run of exception_ageing.py reported 310.
transaction_id does not identify an exception - 184 postings produce 230 of
them, because one entry can be both missing from the subledger and a timing
difference - so a join on it fanned the workflow out by a third, and every
count, every median and every dollar on the page inflated silently. Nothing
errored. The tests below add the cuts back up and demand they land on the log
they came from.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import exception_ageing as ea  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "output"
PUBLISHED = OUT / "exception_ageing_summary.json"


@pytest.fixture(scope="module")
def built():
    return ea.build()


@pytest.fixture(scope="module")
def workflow():
    return ea.enrich(ea.load())


# --- it reconciles to the exception log -------------------------------------

def test_the_workflow_covers_every_exception_exactly_once(workflow):
    log = pd.read_csv(OUT / "gl_reconciliation_exceptions.csv")
    assert len(workflow) == len(log)
    assert not workflow.duplicated(
        ["transaction_id", "period", "exception_type"]).any()


def test_transaction_id_alone_would_not_have_been_enough(workflow):
    """The defect this file was written for. If a posting ever stops carrying
    two exception types, this test stops proving anything - so it says so."""
    assert workflow.transaction_id.nunique() < len(workflow), (
        "no transaction carries two exception types on this data - the "
        "fan-out this file guards against cannot occur, and the guard is "
        "now untested")


def test_open_and_cleared_account_for_every_exception(built):
    summary, _, _, _, _, _ = built
    assert summary["cleared"] + summary["still_open"] == summary["exceptions"]


def test_the_owner_cut_adds_back_to_the_whole_log(built, workflow):
    summary, _, owners, _, _, _ = built
    assert owners.cleared.sum() == summary["cleared"]
    assert owners.still_open.sum() == summary["still_open"]
    assert owners.cleared.sum() + owners.still_open.sum() == len(workflow)


def test_the_type_cut_adds_back_to_the_whole_log(built):
    summary, _, _, types, _, _ = built
    assert types.cleared.sum() == summary["cleared"]
    assert types.still_open.sum() == summary["still_open"]


def test_the_ageing_buckets_hold_every_open_exception(built):
    summary, age, _, _, _, _ = built
    assert age.exceptions.sum() == summary["still_open"]
    assert age.exposure.sum() == pytest.approx(summary["open_exposure"], abs=1.0)
    assert age.bucket_order.tolist() == sorted(age.bucket_order.tolist())


def test_the_clear_methods_account_for_every_cleared_exception(built):
    summary, _, _, _, _, methods = built
    assert methods["count"].sum() == summary["cleared"]
    assert methods.share.sum() == pytest.approx(1.0, abs=1e-3)


def test_throughput_opens_and_clears_balance_to_the_log(built):
    summary, _, _, _, flow, _ = built
    assert flow.opened.sum() == summary["exceptions"]
    assert flow.cleared.sum() == summary["cleared"]
    assert flow.backlog.iloc[-1] == summary["final_backlog"]
    assert flow.backlog.iloc[-1] == summary["exceptions"] - summary["cleared"]


# --- the statistics say what the page says they say -------------------------

def test_nothing_clears_before_it_opens(workflow):
    cleared = workflow[workflow.is_open == 0]
    assert (cleared.cleared_date >= cleared.opened_date).all()
    assert (cleared.days_to_clear > 0).all()


def test_an_open_exception_has_no_clear_date_and_a_closed_one_does(workflow):
    open_ = workflow[workflow.is_open == 1]
    cleared = workflow[workflow.is_open == 0]
    assert open_.cleared_date.isna().all()
    assert open_.days_to_clear.isna().all()
    assert cleared.cleared_date.notna().all()
    assert (open_.age_days > 0).all()


def test_the_sla_breach_flag_matches_the_published_threshold(workflow):
    cleared = workflow[workflow.is_open == 0]
    assert (cleared.breached_sla == (cleared.days_to_clear > ea.SLA_DAYS)).all()
    open_ = workflow[workflow.is_open == 1]
    assert (open_.breached_sla == (open_.age_days > ea.SLA_DAYS)).all()


def test_median_and_mean_are_both_reported_because_they_disagree(built):
    """The page argues that averaging a long tail with a slow average produces
    one number describing neither. That argument needs the two to differ."""
    summary, _, owners, _, _, _ = built
    assert summary["mean_days_to_clear"] > summary["median_days_to_clear"]
    assert (owners.tail_ratio > 1.0).any()
    assert summary["worst_tail_ratio"] == pytest.approx(
        float(owners.tail_ratio.max()), abs=0.01)


def test_the_owner_spread_is_real_enough_to_report(built):
    """Reporting by owner only earns its place if the owners actually differ."""
    summary, _, owners, _, _, _ = built
    assert summary["owner_spread_multiple"] > 2.0
    assert owners.median_days.is_monotonic_decreasing
    assert summary["slowest_owner"] != summary["fastest_owner"]


def test_percentiles_are_ordered_within_every_owner(built):
    _, _, owners, _, _, _ = built
    assert (owners.median_days <= owners.p90_days).all()
    assert (owners.p90_days <= owners.worst_days).all()


def test_absorbed_exceptions_are_the_ones_that_fix_nothing(built, workflow):
    summary, _, _, _, _, _ = built
    cleared = workflow[workflow.is_open == 0]
    absorbed = cleared[cleared.clear_method.isin(ea.ABSORBING_METHODS)]
    assert summary["absorbed"] == len(absorbed)
    assert 0 < summary["absorbed_share"] < 1
    assert summary["absorbed_exposure"] == pytest.approx(
        float(absorbed.exposure.sum()), abs=1.0)


def test_exposure_falls_back_to_the_posting_when_no_variance_was_computed(workflow):
    """A missing entry puts the whole amount at risk, not a difference - there
    is nothing to difference it against."""
    log = pd.read_csv(OUT / "gl_reconciliation_exceptions.csv")
    missing = log[log.variance_amount.isna()]
    assert len(missing) > 0
    joined = workflow.merge(missing[["transaction_id", "exception_type"]],
                            on=["transaction_id", "exception_type"])
    assert len(joined) == len(missing)
    assert (joined.exposure > 0).all()


# --- what the README and the report quote -----------------------------------

def test_the_published_summary_is_what_this_code_produces(built):
    summary, _, _, _, _, _ = built
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for key, value in summary.items():
        if isinstance(value, float):
            assert published[key] == pytest.approx(value, rel=1e-6), key
        else:
            assert published[key] == value, key
