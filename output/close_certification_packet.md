# GL-REC-01 Close Certification Packet

> Synthetic portfolio evidence. This packet records deterministic policy results, not a human approval or audit opinion.

## Release decision

**NO-GO** — 43 of 60 account-periods are blocked; 9 require review and 8 satisfy the bounded auto-certification policy.

Register fingerprint: `5d53de904ac0b1cf53b026fa4223fa5e67b52a0762b5199fe9475780045b9238`

## Control evidence

- Open exceptions: **11** carrying **$20,372.13**.
- Absorbing closures: **72**; SLA breaches: **95**.
- Segregation-of-duties pass rate: **100%**.
- Auto-certification requires LOW risk, variance within 0.50%, zero open exceptions, zero absorbing closures and zero SLA breaches.

## Highest-risk blocked account-periods

| Period | Account | Risk | Variance | Exceptions | Decision reason |
|---|---|---:|---:|---:|---|
| 2025-05 | 4010 · Service Revenue | CRITICAL | 12.18% | 3 | variance exceeds 0.50% tolerance |
| 2025-04 | 2000 · Accounts Payable | CRITICAL | 11.39% | 3 | variance exceeds 0.50% tolerance |
| 2025-02 | 5000 · COGS - Materials | CRITICAL | 11.25% | 6 | variance exceeds 0.50% tolerance |
| 2025-03 | 6020 · Marketing | CRITICAL | 6.91% | 5 | variance exceeds 0.50% tolerance |
| 2025-02 | 5010 · COGS - Freight | CRITICAL | 6.02% | 3 | variance exceeds 0.50% tolerance |
| 2025-06 | 4000 · Product Revenue | CRITICAL | 5.00% | 3 | variance exceeds 0.50% tolerance; 1 exception(s) remain open |
| 2025-03 | 6030 · IT & Software | HIGH | 4.90% | 6 | variance exceeds 0.50% tolerance |
| 2025-05 | 4000 · Product Revenue | HIGH | 4.71% | 7 | variance exceeds 0.50% tolerance |
| 2025-03 | 2000 · Accounts Payable | HIGH | 4.56% | 8 | variance exceeds 0.50% tolerance |
| 2025-06 | 6020 · Marketing | HIGH | 4.29% | 2 | variance exceeds 0.50% tolerance |

## Operating response

1. Preparer resolves the stated blocking reason and attaches corrected source evidence.
2. The engine is rerun; any source, workflow, exception or policy change produces a new fingerprint.
3. A reviewer independent of the preparer assesses MEDIUM/HIGH/CRITICAL items. This repository records no human sign-off.
4. Release remains NO-GO while any account-period is blocked.
