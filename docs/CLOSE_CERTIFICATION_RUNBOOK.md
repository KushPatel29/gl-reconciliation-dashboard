# GL-REC-01 close-certification runbook

## Purpose

Use this runbook to turn reconciliation evidence into an explicit monthly
close decision. The output is an account-period register, not a substitute for
the organization's formal sign-off system.

## 90-second review route

1. Open `output/close_certification_packet.md` and read the overall decision.
2. Confirm the 60 account-periods reconcile to the three certification states.
3. Start with CRITICAL and HIGH rows in `close_certification_register.csv`.
4. For every blocked row, inspect its `blocking_reasons`, named preparer,
   reviewer and response SLA.
5. Reconcile the packet to `close_certification_summary.json`.
6. Confirm the evidence hashes and register fingerprint in
   `close_certification_manifest.json` before relying on the packet.

## State model

| State | Entry rule | Required action |
|---|---|---|
| AUTO-CERTIFIED | LOW risk, variance within 0.5%, zero open exceptions, zero absorbing closures and zero SLA breaches | Retain evidence and monitor for source changes |
| PREPARED — REVIEW REQUIRED | No blocking threshold, but the policy requires independent review | Reviewer assesses the evidence in the formal workflow |
| BLOCKED | Any policy threshold or unresolved control condition fails | Owner remediates, regenerates evidence and re-runs certification |

The engine never creates a human approval timestamp. A reviewer assignment is
an accountability route, not proof that a person approved the close.

## Operating procedure

Run the upstream controls first, then build certification evidence:

```bash
python engine/run_reconciliation.py
python data_generator/generate_journal_detail.py
python engine/journal_risk.py
python engine/exception_ageing.py
python engine/close_certification.py
```

Treat a changed source hash, policy version, exception state, tolerance,
account balance or workflow assignment as a re-verification trigger. Re-run
the complete chain; do not edit the generated register manually.

## Evidence retained

- Versioned policy and control ID
- Five source-file SHA-256 hashes, with text newlines normalized to LF so the
  same evidence has the same fingerprint on Windows and Linux
- Row-level and whole-register fingerprints
- Named preparer and reviewer assignments with segregation-of-duties result
- Blocking reasons, exposure and response SLA by account-period
- Controlled re-verification probe showing a source change invalidates a prior decision

## Demonstration boundary

All data, role assignments and decisions are deterministic simulations. This
repository demonstrates control design and operating evidence; it does not
claim a real human approval, ERP posting, financial close or audit opinion.
