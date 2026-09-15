"""Risk-tiered close certification over the reconciliation evidence.

This module turns the existing control totals, exception population, and
workflow history into an account-period certification register.  It keeps the
portfolio boundary explicit: system certifications are deterministic policy
results over synthetic evidence; human preparer/reviewer fields model the
operating workflow but never claim a real sign-off.

Outputs (``output/``):
    close_certification_register.csv
    close_certification_summary.json
    close_certification_manifest.json
    close_certification_packet.md
    reverification_evidence.json
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
POLICY_PATH = ROOT / "governance" / "close_certification_policy.json"

REGISTER_COLUMNS = [
    "control_id",
    "account_id",
    "account_code",
    "account_name",
    "statement",
    "period",
    "erp_total",
    "subledger_total",
    "variance_amount",
    "variance_pct",
    "abs_variance_pct",
    "exception_count",
    "open_exception_count",
    "open_exposure",
    "absorbing_closure_count",
    "sla_breach_count",
    "total_exception_exposure",
    "risk_tier",
    "response_sla_hours",
    "certification_status",
    "decision_reason",
    "preparer_id",
    "preparer_name",
    "reviewer_id",
    "reviewer_name",
    "segregation_of_duties_pass",
    "prepared_at_utc",
    "review_due_at_utc",
    "certified_at_utc",
    "row_fingerprint",
]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    # Git may materialize text files with CRLF on Windows and LF on Linux.
    # Certification fingerprints must represent logical evidence content, not
    # the checkout platform's newline convention.
    return _sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "control_id",
        "account_variance_tolerance_pct",
        "risk_tiers",
        "preparer_by_account_id",
        "auto_certification",
        "required_evidence",
        "reverification_triggers",
        "demonstration_boundary",
    }
    missing = required - set(policy)
    if missing:
        raise ValueError(f"Certification policy is missing {sorted(missing)}")
    if policy["schema_version"] != 1:
        raise ValueError("Certification policy schema_version must be 1")
    tiers = policy["risk_tiers"]
    if not isinstance(tiers, list) or [tier["name"] for tier in tiers] != [
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
    ]:
        raise ValueError("Risk tiers must be ordered CRITICAL, HIGH, MEDIUM, LOW")
    thresholds = [float(tier["minimum_abs_variance_pct"]) for tier in tiers]
    if thresholds != sorted(thresholds, reverse=True) or thresholds[-1] != 0:
        raise ValueError("Risk-tier thresholds must descend to zero")
    if float(policy["account_variance_tolerance_pct"]) <= 0:
        raise ValueError("Account variance tolerance must be positive")
    return policy


def load_inputs() -> dict[str, pd.DataFrame]:
    return {
        "control_totals": pd.read_csv(OUT / "gl_control_totals.csv"),
        "exceptions": pd.read_csv(OUT / "gl_reconciliation_exceptions.csv"),
        "workflow": pd.read_csv(DATA / "exception_workflow.csv"),
        "accounts": pd.read_csv(DATA / "dim_account.csv"),
        "users": pd.read_csv(DATA / "dim_user.csv"),
    }


def _enrich_exceptions(inputs: dict[str, pd.DataFrame], policy: dict[str, Any]) -> pd.DataFrame:
    exceptions = inputs["exceptions"].merge(
        inputs["workflow"],
        on=["transaction_id", "period", "exception_type"],
        how="left",
        validate="one_to_one",
    )
    if len(exceptions) != len(inputs["exceptions"]) or exceptions["status"].isna().any():
        raise ValueError("Every reconciliation exception requires exactly one workflow record")
    exceptions["exposure"] = exceptions["variance_amount"].fillna(exceptions["erp_amount"]).abs().round(2)
    exceptions["is_open"] = (exceptions["status"] == "Open").astype(int)
    exceptions["open_exposure"] = exceptions["exposure"].where(exceptions["is_open"] == 1, 0.0)
    exceptions["is_absorbing"] = exceptions["clear_method"].isin(policy["absorbing_clear_methods"]).astype(int)
    return exceptions


def _exception_summary(exceptions: pd.DataFrame) -> pd.DataFrame:
    return (
        exceptions.groupby(["account_id", "period"], as_index=False)
        .agg(
            exception_count=("transaction_id", "size"),
            open_exception_count=("is_open", "sum"),
            open_exposure=("open_exposure", "sum"),
            absorbing_closure_count=("is_absorbing", "sum"),
            sla_breach_count=("breached_sla", "sum"),
            total_exception_exposure=("exposure", "sum"),
        )
    )


def _tier_index(policy: dict[str, Any]) -> dict[str, int]:
    return {tier["name"]: index for index, tier in enumerate(policy["risk_tiers"])}


def _risk_tier(row: pd.Series, policy: dict[str, Any]) -> dict[str, Any]:
    tiers = policy["risk_tiers"]
    tier = next(
        candidate
        for candidate in tiers
        if float(row["abs_variance_pct"]) >= float(candidate["minimum_abs_variance_pct"])
    )
    by_name = {candidate["name"]: candidate for candidate in tiers}
    severity = _tier_index(policy)
    floors: list[str] = []
    if int(row["open_exception_count"]) > 0:
        floors.append(policy["open_exception_minimum_risk_tier"])
    if int(row["absorbing_closure_count"]) > 0:
        floors.append(policy["absorbing_closure_minimum_risk_tier"])
    if int(row["sla_breach_count"]) > 0:
        floors.append(policy["sla_breach_minimum_risk_tier"])
    for floor in floors:
        if severity[floor] < severity[tier["name"]]:
            tier = by_name[floor]
    return tier


def _decision(row: pd.Series, policy: dict[str, Any], tier: dict[str, Any]) -> tuple[str, str]:
    tolerance = float(policy["account_variance_tolerance_pct"])
    blockers: list[str] = []
    review: list[str] = []
    if float(row["abs_variance_pct"]) > tolerance:
        blockers.append("variance exceeds 0.50% tolerance")
    if int(row["open_exception_count"]) > 0:
        blockers.append(f"{int(row['open_exception_count'])} exception(s) remain open")
    if blockers:
        return "BLOCKED", "; ".join(blockers)
    if int(row["absorbing_closure_count"]) > 0:
        review.append(f"{int(row['absorbing_closure_count'])} closure(s) absorbed rather than fixed")
    if int(row["sla_breach_count"]) > 0:
        review.append(f"{int(row['sla_breach_count'])} exception(s) breached workflow SLA")
    if tier["name"] != policy["auto_certification"]["allowed_risk_tier"]:
        review.append(f"{tier['name']} risk requires human review")
    if review:
        return "PREPARED — REVIEW REQUIRED", "; ".join(review)
    return "AUTO-CERTIFIED", "low risk; within tolerance; no open, absorbing, or SLA-breached exceptions"


def _utc_stamp(period: str, offset_days: int) -> str:
    stamp = pd.Period(period, freq="M").end_time.normalize() + pd.Timedelta(days=offset_days, hours=17)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_fingerprint(row: dict[str, Any]) -> str:
    fields = {
        key: row[key]
        for key in (
            "control_id",
            "account_id",
            "period",
            "variance_amount",
            "variance_pct",
            "exception_count",
            "open_exception_count",
            "absorbing_closure_count",
            "sla_breach_count",
            "risk_tier",
            "certification_status",
            "preparer_id",
            "reviewer_id",
        )
    }
    return _sha256_bytes(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8"))[:16].upper()


def prepare_register(inputs: dict[str, pd.DataFrame], policy: dict[str, Any]) -> pd.DataFrame:
    exceptions = _enrich_exceptions(inputs, policy)
    grouped = _exception_summary(exceptions)
    register = inputs["control_totals"].merge(
        inputs["accounts"][["account_id", "account_code", "account_name", "statement"]],
        on="account_id",
        how="left",
        validate="many_to_one",
    ).merge(grouped, on=["account_id", "period"], how="left", validate="one_to_one")
    number_columns = [
        "exception_count",
        "open_exception_count",
        "open_exposure",
        "absorbing_closure_count",
        "sla_breach_count",
        "total_exception_exposure",
    ]
    register[number_columns] = register[number_columns].fillna(0)
    for column in ["exception_count", "open_exception_count", "absorbing_closure_count", "sla_breach_count"]:
        register[column] = register[column].astype(int)
    register["open_exposure"] = register["open_exposure"].round(2)
    register["total_exception_exposure"] = register["total_exception_exposure"].round(2)
    register["abs_variance_pct"] = register["variance_pct"].fillna(float("inf")).abs()

    users = inputs["users"].set_index("user_id")["user_name"].to_dict()
    rows: list[dict[str, Any]] = []
    for _, source in register.sort_values(["period", "account_id"], kind="stable").iterrows():
        tier = _risk_tier(source, policy)
        status, reason = _decision(source, policy, tier)
        preparer_id = policy["preparer_by_account_id"][str(int(source["account_id"]))]
        reviewer_id = (
            policy["auto_certification"]["certified_by"]
            if status == "AUTO-CERTIFIED"
            else tier["reviewer_id"]
        )
        row = {
            "control_id": policy["control_id"],
            "account_id": int(source["account_id"]),
            "account_code": int(source["account_code"]),
            "account_name": source["account_name"],
            "statement": source["statement"],
            "period": source["period"],
            "erp_total": round(float(source["erp_total"]), 2),
            "subledger_total": round(float(source["subledger_total"]), 2),
            "variance_amount": round(float(source["variance_amount"]), 2),
            "variance_pct": round(float(source["variance_pct"]), 8),
            "abs_variance_pct": round(float(source["abs_variance_pct"]), 8),
            "exception_count": int(source["exception_count"]),
            "open_exception_count": int(source["open_exception_count"]),
            "open_exposure": round(float(source["open_exposure"]), 2),
            "absorbing_closure_count": int(source["absorbing_closure_count"]),
            "sla_breach_count": int(source["sla_breach_count"]),
            "total_exception_exposure": round(float(source["total_exception_exposure"]), 2),
            "risk_tier": tier["name"],
            "response_sla_hours": int(tier["response_sla_hours"]),
            "certification_status": status,
            "decision_reason": reason,
            "preparer_id": preparer_id,
            "preparer_name": users[preparer_id],
            "reviewer_id": reviewer_id,
            "reviewer_name": "Policy engine" if reviewer_id.startswith("SYSTEM:") else users[reviewer_id],
            "segregation_of_duties_pass": preparer_id != reviewer_id,
            "prepared_at_utc": _utc_stamp(source["period"], 5),
            "review_due_at_utc": _utc_stamp(source["period"], 10),
            "certified_at_utc": _utc_stamp(source["period"], 5) if status == "AUTO-CERTIFIED" else "",
        }
        row["row_fingerprint"] = _row_fingerprint(row)
        rows.append(row)

    result = pd.DataFrame(rows, columns=REGISTER_COLUMNS)
    if not result["segregation_of_duties_pass"].all():
        raise ValueError("Preparer and reviewer must be segregated for every certification")
    return result


def register_fingerprint(register: pd.DataFrame) -> str:
    material = register.sort_values(["period", "account_id"], kind="stable")[
        ["account_id", "period", "row_fingerprint"]
    ].to_csv(index=False, lineterminator="\n")
    return _sha256_bytes(material.encode("utf-8"))


def _summary(register: pd.DataFrame, policy: dict[str, Any]) -> dict[str, Any]:
    statuses = register["certification_status"].value_counts().to_dict()
    risks = register["risk_tier"].value_counts().to_dict()
    blocked = register[register["certification_status"] == "BLOCKED"]
    return {
        "control_id": policy["control_id"],
        "account_periods": len(register),
        "auto_certified": int(statuses.get("AUTO-CERTIFIED", 0)),
        "prepared_review_required": int(statuses.get("PREPARED — REVIEW REQUIRED", 0)),
        "blocked": int(statuses.get("BLOCKED", 0)),
        "blocked_abs_variance": round(float(blocked["variance_amount"].abs().sum()), 2),
        "open_exceptions": int(register["open_exception_count"].sum()),
        "open_exposure": round(float(register["open_exposure"].sum()), 2),
        "absorbing_closures": int(register["absorbing_closure_count"].sum()),
        "sla_breaches": int(register["sla_breach_count"].sum()),
        "risk_tiers": {tier: int(risks.get(tier, 0)) for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
        "segregation_of_duties_pass_rate": round(float(register["segregation_of_duties_pass"].mean()), 4),
        "register_fingerprint": register_fingerprint(register),
        "decision": "NO-GO" if len(blocked) else ("REVIEW" if statuses.get("PREPARED — REVIEW REQUIRED", 0) else "GO"),
        "demonstration_boundary": policy["demonstration_boundary"],
    }


def reverification_probe(
    inputs: dict[str, pd.DataFrame],
    policy: dict[str, Any],
    baseline: pd.DataFrame,
) -> dict[str, Any]:
    """Prove that a changed control total invalidates the affected decision."""
    auto = baseline[baseline["certification_status"] == "AUTO-CERTIFIED"].iloc[0]
    changed_inputs = {name: frame.copy(deep=True) for name, frame in inputs.items()}
    mask = (
        (changed_inputs["control_totals"]["account_id"] == auto["account_id"])
        & (changed_inputs["control_totals"]["period"] == auto["period"])
    )
    delta = round(abs(float(changed_inputs["control_totals"].loc[mask, "erp_total"].iloc[0])) * 0.01, 2)
    changed_inputs["control_totals"].loc[mask, "subledger_total"] += delta
    changed_inputs["control_totals"].loc[mask, "variance_amount"] = (
        changed_inputs["control_totals"].loc[mask, "subledger_total"]
        - changed_inputs["control_totals"].loc[mask, "erp_total"]
    )
    changed_inputs["control_totals"].loc[mask, "variance_pct"] = (
        changed_inputs["control_totals"].loc[mask, "variance_amount"]
        / changed_inputs["control_totals"].loc[mask, "erp_total"].abs()
    )
    changed = prepare_register(changed_inputs, policy)
    keys = ["account_id", "period"]
    comparison = baseline[keys + ["certification_status", "row_fingerprint"]].merge(
        changed[keys + ["certification_status", "row_fingerprint"]],
        on=keys,
        suffixes=("_before", "_after"),
        validate="one_to_one",
    )
    changed_rows = comparison[
        comparison["row_fingerprint_before"] != comparison["row_fingerprint_after"]
    ]
    target = changed_rows.iloc[0]
    return {
        "probe": "synthetic control-total mutation",
        "trigger": "control total changed",
        "account_id": int(target["account_id"]),
        "period": target["period"],
        "simulated_delta": delta,
        "baseline_register_fingerprint": register_fingerprint(baseline),
        "changed_register_fingerprint": register_fingerprint(changed),
        "changed_rows": len(changed_rows),
        "status_before": target["certification_status_before"],
        "status_after": target["certification_status_after"],
        "reverification_required": True,
        "source_data_mutated": False,
        "boundary": "The mutation is in-memory test evidence; committed ledger files are unchanged.",
    }


def _manifest(policy: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    evidence = [ROOT / relative for relative in policy["required_evidence"]]
    missing = [path.relative_to(ROOT).as_posix() for path in evidence if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Required certification evidence is missing: {missing}")
    return {
        "schema_version": 1,
        "control_id": policy["control_id"],
        "release_decision": summary["decision"],
        "register_fingerprint": summary["register_fingerprint"],
        "policy_sha256": _sha256_path(POLICY_PATH),
        "hash_canonicalization": "Text line endings normalized to LF before SHA-256",
        "input_sha256": {
            path.relative_to(ROOT).as_posix(): _sha256_path(path)
            for path in evidence
        },
        "required_evidence_complete": True,
        "reverification_triggers": policy["reverification_triggers"],
        "human_approvals_recorded": 0,
        "data_classification": policy["data_classification"],
        "demonstration_boundary": policy["demonstration_boundary"],
    }


def _packet(summary: dict[str, Any], register: pd.DataFrame) -> str:
    blockers = register[register["certification_status"] == "BLOCKED"].nlargest(10, "abs_variance_pct")
    lines = [
        "# GL-REC-01 Close Certification Packet",
        "",
        "> Synthetic portfolio evidence. This packet records deterministic policy results, not a human approval or audit opinion.",
        "",
        "## Release decision",
        "",
        (
            f"**{summary['decision']}** — {summary['blocked']} of "
            f"{summary['account_periods']} account-periods are blocked; "
            f"{summary['prepared_review_required']} require review and "
            f"{summary['auto_certified']} satisfy the bounded auto-certification policy."
        ),
        "",
        f"Register fingerprint: `{summary['register_fingerprint']}`",
        "",
        "## Control evidence",
        "",
        f"- Open exceptions: **{summary['open_exceptions']}** carrying **${summary['open_exposure']:,.2f}**.",
        f"- Absorbing closures: **{summary['absorbing_closures']}**; SLA breaches: **{summary['sla_breaches']}**.",
        f"- Segregation-of-duties pass rate: **{summary['segregation_of_duties_pass_rate']:.0%}**.",
        "- Auto-certification requires LOW risk, variance within 0.50%, zero open exceptions, zero absorbing closures and zero SLA breaches.",
        "",
        "## Highest-risk blocked account-periods",
        "",
        "| Period | Account | Risk | Variance | Exceptions | Decision reason |",
        "|---|---|---:|---:|---:|---|",
    ]
    for _, row in blockers.iterrows():
        lines.append(
            f"| {row['period']} | {row['account_code']} · {row['account_name']} | {row['risk_tier']} | "
            f"{row['abs_variance_pct']:.2%} | {row['exception_count']} | {row['decision_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Operating response",
            "",
            "1. Preparer resolves the stated blocking reason and attaches corrected source evidence.",
            "2. The engine is rerun; any source, workflow, exception or policy change produces a new fingerprint.",
            "3. A reviewer independent of the preparer assesses MEDIUM/HIGH/CRITICAL items. This repository records no human sign-off.",
            "4. Release remains NO-GO while any account-period is blocked.",
            "",
        ]
    )
    return "\n".join(lines)


def build() -> dict[str, Any]:
    policy = load_policy()
    inputs = load_inputs()
    register = prepare_register(inputs, policy)
    summary = _summary(register, policy)
    return {
        "register": register,
        "summary": summary,
        "manifest": _manifest(policy, summary),
        "reverification": reverification_probe(inputs, policy, register),
        "packet": _packet(summary, register),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    result = build()
    result["register"].to_csv(
        OUT / "close_certification_register.csv",
        index=False,
        lineterminator="\n",
    )
    for name in ["summary", "manifest", "reverification"]:
        (OUT / f"close_certification_{name}.json" if name != "reverification" else OUT / "reverification_evidence.json").write_text(
            json.dumps(result[name], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (OUT / "close_certification_packet.md").write_text(result["packet"], encoding="utf-8")

    summary = result["summary"]
    print("\nGL-REC-01 CLOSE CERTIFICATION")
    print("=" * 42)
    print(f"Decision: {summary['decision']}")
    print(
        f"{summary['auto_certified']} auto-certified | "
        f"{summary['prepared_review_required']} review required | "
        f"{summary['blocked']} blocked"
    )
    print(f"Register fingerprint: {summary['register_fingerprint']}")
    print("Human approvals recorded: 0 (synthetic workflow boundary)")
    print(f"Wrote five certification artefacts to {OUT}")
    return result


if __name__ == "__main__":
    main()
