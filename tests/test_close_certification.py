"""Control tests for the risk-tiered account-period certification register."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import close_certification as cc


@pytest.fixture(scope="module")
def built():
    return cc.build()


@pytest.fixture(scope="module")
def register(built):
    return built["register"]


@pytest.fixture(scope="module")
def policy():
    return cc.load_policy()


def test_policy_is_versioned_and_bound_to_the_reconciliation_control(policy):
    assert policy["schema_version"] == 1
    assert policy["control_id"] == "GL-REC-01"
    assert policy["account_variance_tolerance_pct"] == pytest.approx(0.005)


def test_risk_tiers_are_complete_and_ordered(policy):
    assert [tier["name"] for tier in policy["risk_tiers"]] == [
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
    ]
    assert [tier["minimum_abs_variance_pct"] for tier in policy["risk_tiers"]] == [
        0.05,
        0.02,
        0.005,
        0.0,
    ]


def test_register_has_one_row_per_account_period(register):
    assert len(register) == 60
    assert not register.duplicated(["account_id", "period"]).any()
    assert register.account_id.nunique() == 10
    assert register.period.nunique() == 6


def test_every_row_has_named_accountability(register):
    required = ["preparer_id", "preparer_name", "reviewer_id", "reviewer_name"]
    assert not register[required].isna().any().any()
    assert (register[required].astype(str).apply(lambda col: col.str.len() > 0)).all().all()


def test_preparer_and_reviewer_are_segregated(register):
    assert register.segregation_of_duties_pass.all()
    assert (register.preparer_id != register.reviewer_id).all()


def test_certification_statuses_cover_the_register_once(register):
    assert register.certification_status.value_counts().to_dict() == {
        "BLOCKED": 43,
        "PREPARED — REVIEW REQUIRED": 9,
        "AUTO-CERTIFIED": 8,
    }


@pytest.mark.parametrize(
    "column",
    [
        "open_exception_count",
        "absorbing_closure_count",
        "sla_breach_count",
    ],
)
def test_auto_certification_requires_zero_exception_control_flags(register, column):
    auto = register[register.certification_status == "AUTO-CERTIFIED"]
    assert (auto[column] == 0).all()


def test_auto_certification_is_bounded_to_low_risk_and_tolerance(register, policy):
    auto = register[register.certification_status == "AUTO-CERTIFIED"]
    assert set(auto.risk_tier) == {"LOW"}
    assert (auto.abs_variance_pct <= policy["account_variance_tolerance_pct"]).all()
    assert (auto.reviewer_id == "SYSTEM:GL-REC-01").all()
    assert auto.certified_at_utc.str.endswith("Z").all()


def test_review_required_rows_have_no_false_certification_timestamp(register):
    review = register[register.certification_status == "PREPARED — REVIEW REQUIRED"]
    assert (review.reviewer_id.str.startswith("U")).all()
    assert (review.certified_at_utc == "").all()
    assert review.decision_reason.str.len().gt(0).all()


def test_blocked_rows_have_an_evidence_based_reason(register, policy):
    blocked = register[register.certification_status == "BLOCKED"]
    objective_block = (
        (blocked.abs_variance_pct > policy["account_variance_tolerance_pct"])
        | (blocked.open_exception_count > 0)
    )
    assert objective_block.all()
    assert blocked.decision_reason.str.contains("variance|remain open", regex=True).all()
    assert (blocked.certified_at_utc == "").all()


def test_open_exceptions_cannot_be_scored_below_high(register):
    severity = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    open_rows = register[register.open_exception_count > 0]
    assert len(open_rows) > 0
    assert open_rows.risk_tier.map(severity).ge(severity["HIGH"]).all()


def test_absorbing_closures_cannot_be_scored_low(register):
    absorbing = register[register.absorbing_closure_count > 0]
    assert len(absorbing) > 0
    assert not (absorbing.risk_tier == "LOW").any()


def test_response_sla_matches_the_risk_policy(register, policy):
    expected = {tier["name"]: tier["response_sla_hours"] for tier in policy["risk_tiers"]}
    assert (register.response_sla_hours == register.risk_tier.map(expected)).all()


def test_summary_reconciles_every_status_and_risk_tier(built, register):
    summary = built["summary"]
    assert summary["auto_certified"] + summary["prepared_review_required"] + summary["blocked"] == len(register)
    assert sum(summary["risk_tiers"].values()) == len(register)
    assert summary["segregation_of_duties_pass_rate"] == 1.0


def test_published_control_headlines_are_reproducible(built):
    summary = built["summary"]
    assert summary["decision"] == "NO-GO"
    assert summary["open_exceptions"] == 11
    assert summary["open_exposure"] == pytest.approx(20372.13)
    assert summary["absorbing_closures"] == 72
    assert summary["sla_breaches"] == 95


def test_register_fingerprint_is_deterministic(register):
    assert cc.register_fingerprint(register) == cc.register_fingerprint(register.sample(frac=1, random_state=7))
    assert len(cc.register_fingerprint(register)) == 64


def test_committed_register_matches_the_engine(register):
    published = pd.read_csv(ROOT / "output" / "close_certification_register.csv", keep_default_na=False)
    pd.testing.assert_frame_equal(published, register, check_dtype=False, atol=1e-8)


def test_committed_summary_matches_the_engine(built):
    published = json.loads((ROOT / "output" / "close_certification_summary.json").read_text(encoding="utf-8"))
    assert published == built["summary"]


def test_release_manifest_hashes_every_required_input(built):
    manifest = built["manifest"]
    assert manifest["required_evidence_complete"] is True
    assert len(manifest["input_sha256"]) == 5
    for relative, claimed in manifest["input_sha256"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert claimed == actual


def test_release_manifest_never_claims_a_human_approval(built):
    manifest = built["manifest"]
    assert manifest["human_approvals_recorded"] == 0
    assert manifest["data_classification"] == "Synthetic portfolio demonstration"
    assert "No human approval" in manifest["demonstration_boundary"]


def test_reverification_probe_invalidates_exactly_the_affected_decision(built):
    probe = built["reverification"]
    assert probe["trigger"] == "control total changed"
    assert probe["changed_rows"] == 1
    assert probe["status_before"] == "AUTO-CERTIFIED"
    assert probe["status_after"] == "BLOCKED"
    assert probe["reverification_required"] is True


def test_reverification_probe_does_not_mutate_committed_source(built):
    probe = built["reverification"]
    assert probe["baseline_register_fingerprint"] != probe["changed_register_fingerprint"]
    assert probe["source_data_mutated"] is False
    published = json.loads((ROOT / "output" / "reverification_evidence.json").read_text(encoding="utf-8"))
    assert published == probe


def test_packet_states_the_boundary_and_operating_response(built):
    packet = built["packet"]
    assert "not a human approval or audit opinion" in packet
    assert "Release decision" in packet
    assert "**NO-GO**" in packet
    assert "Preparer resolves" in packet
    assert "This repository records no human sign-off" in packet


def test_policy_validation_fails_closed_on_unknown_schema(tmp_path):
    policy = json.loads(cc.POLICY_PATH.read_text(encoding="utf-8"))
    policy["schema_version"] = 99
    broken = tmp_path / "policy.json"
    broken.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        cc.load_policy(broken)
