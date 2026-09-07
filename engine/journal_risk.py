"""
Journal-entry risk: the controls test, run on every entry rather than a sample.

Reconciliation asks whether two ledgers agree. This asks a different question -
whether the entries that produced them were posted under control - and it is
the question an internal audit function, an external auditor's journal-entry
testing procedure, and a continuous-controls-monitoring programme all open
with. Historically it was answered on a sample of forty entries pulled by hand.
There are 5,400 here and every one of them is tested, which is the entire
argument for doing it in the warehouse.

The nine tests
--------------
    manual_entry          Not raised by an interface or a recurring job.
                          Everything else on this list is conditioned on it,
                          because an interfaced entry inherits the controls of
                          the system that raised it.
    self_approved         Posted and approved by the same person.
    above_approver_limit  Approved by somebody whose delegated authority does
                          not cover the amount.
    after_hours           Posted outside 07:00-19:00.
    weekend               Posted on a Saturday or Sunday.
    round_dollar          A round thousand. Transactions are rarely round;
                          estimates, accruals and plugs are.
    threshold_avoidance   Sitting just under an approval limit. The most-cited
                          red flag in journal testing and invisible unless
                          something looks for it deliberately.
    close_window          Posted in the days either side of period end, when
                          the ledger is still open and everybody is behind.
    reversal              Reversed after posting.

Every flag is measured against a baseline, and some of them fail
---------------------------------------------------------------
A control test that fires on 28% of entries is not a finding, it is a
description of the population - and the usual way a monitoring programme dies
is by reporting hundreds of "exceptions" that turn out to be how the business
normally runs. So every flag is compared to its own rate on a population it
should NOT fire on, and the report says plainly which tests discriminate and
which do not.

The baseline is interfaced entries for most tests: same ledger, same accounts,
same calendar, no human deciding anything. Two exceptions, both stated in the
output rather than buried:

  * after_hours cannot use it. A scheduled job posts at 03:00 by design, so the
    interfaced population is 100% out of hours and the comparison is
    meaningless. Manual entries in the close window are compared to manual
    entries away from it instead - same people, same authority, different time
    pressure - and the excess is real: 23.6% against 7.4%.
  * round_dollar and threshold_avoidance cannot fire on the baseline at all,
    because both are tests of a human choosing a number and an interface does
    not choose. Their excess ratio is reported as null rather than as infinity,
    which would sort to the top of a chart and mean nothing.

On this ledger the weekend test does not discriminate: the posting dates in the
source extract are spread evenly across all seven days, so weekend posting
carries no information here and is reported as a non-finding rather than as 406
exceptions. Neither does close_window, or reversal. Three of eight tests earn
their place on this population and the page says so - which is the outcome the
baseline exists to produce, and the reason the weekend test carries a weight of
zero in the score.

Benford's law, and what it is worth
-----------------------------------
First-digit frequencies are tested per cost centre with a chi-square statistic
against Benford's expected distribution, on 8 degrees of freedom. The critical
values are hard-coded rather than pulled from scipy, so this module has no
dependency beyond pandas, and they are the standard published ones.

Benford is a screen, not evidence. A population that conforms is not clean and
one that deviates is not fraudulent - deviation is a reason to look, and the
places worth looking are what this reports.

Outputs (output/):
    journal_risk_entries.csv    every entry, its flags and its risk score
    journal_risk_by_user.csv    who posts the risk, and how much of it
    journal_risk_flags.csv      each test's rate, its baseline, and whether it
                                discriminates at all on this population
    benford_by_dimension.csv    first-digit conformity by cost centre
    journal_risk_summary.json   the headline figures the report and tests read

Usage:
    python engine/journal_risk.py
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

BUSINESS_HOURS = (7, 19)          # inclusive start, exclusive end
CLOSE_WINDOW_DAYS = 3
AVOIDANCE_BAND = 0.06             # within this far below a limit
ROUND_TO = 1000

# What each test is worth in the composite score. Published, arguable, and
# weighted by how much a control person would actually care: an entry somebody
# approved for themselves is a different order of problem from one posted at
# seven in the evening.
WEIGHTS = {
    "self_approved": 30,
    "above_approver_limit": 25,
    "threshold_avoidance": 20,
    "round_dollar": 10,
    "after_hours": 8,
    "close_window": 4,
    "reversal": 3,
    "weekend": 0,          # see the module docstring: no signal on this data
}

# An entry at or above this score goes on the review list. Set so the list is
# a morning's work rather than a year's - a control report nobody can finish is
# a control report nobody starts.
REVIEW_THRESHOLD = 30

# A flag has to fire at least this many times more often on manual entries than
# on interfaced ones to be worth reporting as a signal rather than as the shape
# of the population.
DISCRIMINATION_RATIO = 1.5

# Chi-square critical values on 8 degrees of freedom (nine first digits, minus
# one). Standard published values; hard-coded so this module needs no scipy.
CHI2_CRITICAL = {0.10: 13.362, 0.05: 15.507, 0.01: 20.090}

BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

# Benford needs a population with a wide, smooth spread of magnitudes. Below
# this many rows the test reports noise with three decimal places on it.
BENFORD_MIN_ROWS = 300


def load() -> dict:
    return {
        "erp": pd.read_csv(DATA / "source_erp_gl.csv",
                           parse_dates=["posted_date"]),
        "control": pd.read_csv(DATA / "journal_control.csv",
                               parse_dates=["posted_timestamp"]),
        "users": pd.read_csv(DATA / "dim_user.csv"),
        "accounts": pd.read_csv(DATA / "dim_account.csv"),
        "cost_centers": pd.read_csv(DATA / "dim_cost_center.csv"),
    }


def entries(d: dict) -> pd.DataFrame:
    """One row per journal entry, with every test applied."""
    e = d["erp"].merge(d["control"], on=["transaction_id", "period"],
                       how="inner", validate="one_to_one")
    users = d["users"].set_index("user_id")
    e["poster"] = e.posted_by.map(users.user_name)
    e["poster_role"] = e.posted_by.map(users.role)
    e["approver"] = e.approved_by.map(users.user_name)
    e["approver_limit"] = e.approved_by.map(users.approval_limit)
    e["account_name"] = e.account_id.map(
        d["accounts"].set_index("account_id").account_name)
    e["cost_center"] = e.cost_center_id.map(
        d["cost_centers"].set_index("cost_center_id").cost_center_name)
    # control_amount is what the entry was actually posted at, which is the
    # number a threshold test has to read. It equals `amount` except where a
    # manual entry was tuned.
    e["abs_amount"] = e.control_amount.abs()

    e["manual_entry"] = (e.journal_source == "Manual").astype(int)
    e["self_approved"] = (e.posted_by == e.approved_by).astype(int)
    e["above_approver_limit"] = (e.abs_amount > e.approver_limit).astype(int)
    e["after_hours"] = (~e.posting_hour.between(BUSINESS_HOURS[0],
                                                BUSINESS_HOURS[1] - 1)).astype(int)
    e["weekend"] = e.is_weekend.astype(int)
    e["round_dollar"] = ((e.abs_amount > 0)
                         & (e.abs_amount % ROUND_TO == 0)).astype(int)
    e["close_window"] = (e.days_from_period_end.abs()
                         <= CLOSE_WINDOW_DAYS).astype(int)
    e["reversal"] = e.is_reversal.astype(int)

    limits = sorted(v for v in d["users"].approval_limit.unique() if v > 0)
    e["threshold_avoidance"] = [
        int(any(lim * (1 - AVOIDANCE_BAND) <= a < lim for lim in limits))
        for a in e.abs_amount]

    # Everything except the manual flag itself is only meaningful on a manual
    # entry. Scoring an interfaced entry for posting at 03:00 - which is when
    # its scheduled job runs, by design - is how a monitoring programme fills
    # up with noise and stops being read.
    for flag in WEIGHTS:
        e[flag] = e[flag] * e.manual_entry

    e["risk_score"] = sum(e[f] * w for f, w in WEIGHTS.items())
    e["flags_fired"] = sum(e[f] for f in WEIGHTS if WEIGHTS[f] > 0)
    e["for_review"] = (e.risk_score >= REVIEW_THRESHOLD).astype(int)
    return e


def flag_baselines(e: pd.DataFrame) -> pd.DataFrame:
    """Each test's rate on manual entries against its rate on interfaced ones.

    The interfaced population is the control group: same ledger, same accounts,
    same calendar, no human deciding when to post. A flag that fires as often
    there as it does on manual entries is describing the data rather than the
    behaviour."""
    manual = e[e.manual_entry == 1]
    auto = e[e.manual_entry == 0]
    # After-hours cannot be judged against interfaced entries: a scheduled job
    # runs at 03:00 by design, so the automated population is 100% "after
    # hours" and the comparison is meaningless. The informative contrast for a
    # human-posted entry is the close window against the rest of the month -
    # same people, same authority, different time pressure.
    in_close = manual[manual.days_from_period_end.abs() <= CLOSE_WINDOW_DAYS]
    out_close = manual[manual.days_from_period_end.abs() > CLOSE_WINDOW_DAYS]
    rows = []
    for flag in WEIGHTS:
        baseline = "interfaced entries"
        # Recompute on the raw column, undoing the manual-only masking above -
        # otherwise the interfaced rate would be zero by construction and every
        # flag would look brilliantly discriminating.
        if flag == "self_approved":
            auto_rate = float((auto.posted_by == auto.approved_by).mean())
        elif flag == "above_approver_limit":
            auto_rate = float((auto.abs_amount > auto.approver_limit).mean())
        elif flag == "after_hours":
            baseline = "manual entries away from the close"
            auto_rate = float((~out_close.posting_hour.between(
                BUSINESS_HOURS[0], BUSINESS_HOURS[1] - 1)).mean())
        elif flag == "weekend":
            auto_rate = float(auto.is_weekend.mean())
        elif flag == "round_dollar":
            auto_rate = float(((auto.abs_amount > 0)
                               & (auto.abs_amount % ROUND_TO == 0)).mean())
        elif flag == "close_window":
            auto_rate = float((auto.days_from_period_end.abs()
                               <= CLOSE_WINDOW_DAYS).mean())
        elif flag == "reversal":
            auto_rate = float(auto.is_reversal.mean())
        else:  # threshold_avoidance
            limits = sorted(set(auto.approver_limit) - {0})
            auto_rate = float(np.mean([
                any(lim * (1 - AVOIDANCE_BAND) <= a < lim for lim in limits)
                for a in auto.abs_amount]))

        if flag == "after_hours":
            manual_rate = float((~in_close.posting_hour.between(
                BUSINESS_HOURS[0], BUSINESS_HOURS[1] - 1)).mean())
        else:
            manual_rate = float(manual[flag].mean())
        ratio = manual_rate / auto_rate if auto_rate > 0 else float("inf")
        rows.append({
            "flag": flag,
            "weight": WEIGHTS[flag],
            "manual_entries_flagged": int(manual[flag].sum()),
            "manual_rate": round(manual_rate, 4),
            "baseline": baseline,
            "baseline_rate": round(auto_rate, 4),
            # An infinite ratio means the test cannot fire on the baseline
            # population at all - round dollars and threshold avoidance are
            # tests of a human choosing a number, and an interface does not
            # choose. Recorded as null rather than as a huge number that would
            # sort to the top of a chart and mean nothing.
            "excess_ratio": round(ratio, 2) if math.isfinite(ratio) else None,
            "baseline_can_fire": int(math.isfinite(ratio)),
            "discriminates": int(ratio >= DISCRIMINATION_RATIO),
        })
    return pd.DataFrame(rows).sort_values("manual_entries_flagged",
                                          ascending=False).reset_index(drop=True)


def by_user(e: pd.DataFrame) -> pd.DataFrame:
    manual = e[e.manual_entry == 1]
    g = manual.groupby(["posted_by", "poster", "poster_role"])
    u = g.agg(entries=("transaction_id", "size"),
              value=("abs_amount", "sum"),
              risk_score=("risk_score", "sum"),
              for_review=("for_review", "sum"),
              **{f: (f, "sum") for f in WEIGHTS if WEIGHTS[f] > 0}).reset_index()
    u["avg_risk_score"] = (u.risk_score / u.entries).round(1)
    u["review_rate"] = (u.for_review / u.entries).round(4)
    u["value"] = u.value.round(2)
    return u.sort_values("risk_score", ascending=False).reset_index(drop=True)


def benford(e: pd.DataFrame, by: str) -> pd.DataFrame:
    """First-digit conformity, per group, with a chi-square statistic."""
    rows = []
    for name, grp in e.groupby(by):
        amounts = grp.loc[grp.abs_amount > 0, "abs_amount"]
        n = len(amounts)
        if n < BENFORD_MIN_ROWS:
            continue
        first = amounts.map(lambda v: int(f"{v:.10e}"[0]))
        observed = first.value_counts().reindex(range(1, 10), fill_value=0)
        expected = pd.Series({d: BENFORD[d] * n for d in range(1, 10)})
        chi2 = float((((observed - expected) ** 2) / expected).sum())
        # Mean absolute deviation, the other convention in forensic work -
        # reported alongside because chi-square grows with n and will condemn a
        # large, near-conforming population that a MAD would pass.
        mad = float((observed / n - pd.Series(BENFORD)).abs().mean())
        row = {by: name, "entries": n, "chi_square": round(chi2, 2),
               "mad": round(mad, 5)}
        for level, crit in sorted(CHI2_CRITICAL.items(), reverse=True):
            row[f"exceeds_{str(level).replace('0.', 'p')}"] = int(chi2 > crit)
        row["verdict"] = ("conforms" if chi2 <= CHI2_CRITICAL[0.05]
                          else "deviates")
        for d in range(1, 10):
            row[f"digit_{d}"] = int(observed[d])
            row[f"digit_{d}_share"] = round(float(observed[d] / n), 5)
        rows.append(row)
    if not rows:
        # Every group too small to test. Returning an empty frame with no
        # columns makes the caller crash on a sort; returning the right shape
        # lets it report "nothing testable" and carry on, which is the honest
        # outcome rather than an outage.
        cols = [by, "entries", "chi_square", "mad", "verdict"] + [
            f"exceeds_{str(lvl).replace('0.', 'p')}" for lvl in CHI2_CRITICAL]
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("chi_square",
                                          ascending=False).reset_index(drop=True)


def build() -> tuple:
    d = load()
    e = entries(d)
    flags = flag_baselines(e)
    users = by_user(e)
    ben = benford(e, "cost_center")

    manual = e[e.manual_entry == 1]
    review = e[e.for_review == 1]
    useful = flags[flags.discriminates == 1]
    useless = flags[flags.discriminates == 0]
    worst_user = users.iloc[0]
    # Concentration of the self-approval weakness: how much of it sits with the
    # two worst offenders. A control that two people work around is a different
    # fix from one everybody works around.
    self_by_user = (manual[manual.self_approved == 1]
                    .groupby("poster").size().sort_values(ascending=False))
    top2_share = (float(self_by_user.head(2).sum() / self_by_user.sum())
                  if len(self_by_user) else 0.0)

    summary = {
        "entries": int(len(e)),
        "periods": int(e.period.nunique()),
        "manual_entries": int(len(manual)),
        "manual_share": round(float(len(manual) / len(e)), 4),
        "manual_value": round(float(manual.abs_amount.sum()), 2),
        "for_review": int(len(review)),
        "for_review_share_of_manual": round(float(len(review) / len(manual)), 4),
        "for_review_value": round(float(review.abs_amount.sum()), 2),
        "review_threshold": REVIEW_THRESHOLD,
        "self_approved": int(manual.self_approved.sum()),
        "self_approved_value": round(
            float(manual.loc[manual.self_approved == 1, "abs_amount"].sum()), 2),
        "self_approval_top2_share": round(top2_share, 4),
        "self_approval_worst": self_by_user.index[0] if len(self_by_user) else None,
        "self_approval_worst_count": int(self_by_user.iloc[0])
        if len(self_by_user) else 0,
        "above_approver_limit": int(manual.above_approver_limit.sum()),
        "above_approver_limit_value": round(float(
            manual.loc[manual.above_approver_limit == 1, "abs_amount"].sum()), 2),
        "threshold_avoidance": int(manual.threshold_avoidance.sum()),
        "threshold_avoidance_value": round(float(
            manual.loc[manual.threshold_avoidance == 1, "abs_amount"].sum()), 2),
        "round_dollar": int(manual.round_dollar.sum()),
        "after_hours": int(manual.after_hours.sum()),
        "flags_that_discriminate": int(len(useful)),
        "flags_that_do_not": int(len(useless)),
        "non_discriminating_flags": useless.flag.tolist(),
        "weekend_manual_rate": float(
            flags.loc[flags.flag == "weekend", "manual_rate"].iloc[0]),
        "weekend_interfaced_rate": float(
            flags.loc[flags.flag == "weekend", "baseline_rate"].iloc[0]),
        "after_hours_close_rate": float(
            flags.loc[flags.flag == "after_hours", "manual_rate"].iloc[0]),
        "after_hours_normal_rate": float(
            flags.loc[flags.flag == "after_hours", "baseline_rate"].iloc[0]),
        "worst_poster": worst_user.poster,
        "worst_poster_role": worst_user.poster_role,
        "worst_poster_score": float(worst_user.risk_score),
        "worst_poster_entries": int(worst_user.entries),
        "benford_groups": int(len(ben)),
        "benford_deviating": int((ben.verdict == "deviates").sum()),
        "benford_worst": ben.iloc[0].cost_center if len(ben) else None,
        "benford_worst_chi2": float(ben.iloc[0].chi_square) if len(ben) else None,
        "benford_critical_5pct": CHI2_CRITICAL[0.05],
        "weights": WEIGHTS,
    }
    return summary, e, flags, users, ben


def headline(summary: dict) -> pd.DataFrame:
    scalars = {k: v for k, v in summary.items()
               if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    return pd.DataFrame([scalars])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary, e, flags, users, ben = build()

    cols = ["transaction_id", "period", "posted_timestamp", "account_name",
            "cost_center", "journal_source", "poster", "poster_role",
            "approver", "approver_limit", "control_amount", "abs_amount",
            *[f for f in WEIGHTS], "flags_fired", "risk_score", "for_review"]
    e.loc[e.manual_entry == 1, cols].sort_values(
        "risk_score", ascending=False).to_csv(
            OUT / "journal_risk_entries.csv", index=False)
    users.to_csv(OUT / "journal_risk_by_user.csv", index=False)
    flags.to_csv(OUT / "journal_risk_flags.csv", index=False)
    ben.to_csv(OUT / "benford_by_dimension.csv", index=False)
    (OUT / "journal_risk_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    headline(summary).to_csv(OUT / "journal_risk_headline.csv", index=False)

    print()
    print("=" * 74)
    print("JOURNAL ENTRY RISK")
    print("=" * 74)
    print(f"  {summary['entries']:,} entries across {summary['periods']} periods, "
          f"every one tested - not a sample of forty")
    print(f"  {summary['manual_entries']:,} are manual "
          f"({summary['manual_share']:.1%}), worth "
          f"${summary['manual_value']:,.0f}. Everything below is conditioned on "
          "that,")
    print("  because an interfaced entry inherits the controls of the system "
          "that raised it.")
    print()
    print("WHICH TESTS ACTUALLY TELL YOU ANYTHING")
    print("=" * 74)
    print(f"  {'test':22s} {'weight':>7s} {'flagged':>8s} {'rate':>7s} "
          f"{'baseline':>9s} {'excess':>12s}")
    for _, r in flags.iterrows():
        verdict = "" if r.discriminates else "   <- no signal here"
        excess = (f"{r.excess_ratio:.2f}x" if r.baseline_can_fire
                  else "manual only")
        print(f"  {r.flag:22s} {r.weight:>7.0f} {r.manual_entries_flagged:>8,} "
              f"{r.manual_rate:>7.1%} {r.baseline_rate:>9.1%} "
              f"{excess:>12s}{verdict}")
    print()
    print("  Each rate is measured against the population named in the "
          "`baseline` column:")
    print("  interfaced entries for most tests, and - for after-hours - manual "
          "entries away")
    print("  from the close, because a scheduled job posts at 03:00 by design "
          "and comparing")
    print("  a human to it proves nothing.")
    print()
    print(f"  {summary['flags_that_discriminate']} of "
          f"{summary['flags_that_discriminate'] + summary['flags_that_do_not']} "
          "tests discriminate on this ledger.")
    if summary["non_discriminating_flags"]:
        print(f"  {', '.join(summary['non_discriminating_flags'])} fire at "
              "roughly the same rate on")
        print("  entries no human chose the timing of, so they are describing "
              "the population")
        print(f"  rather than the behaviour - weekend runs "
              f"{summary['weekend_manual_rate']:.1%} on manual entries against "
              f"{summary['weekend_interfaced_rate']:.1%} on interfaced ones.")
        print("  Reported as a non-finding instead of several hundred "
              "exceptions nobody would")
        print("  have read.")
    print()
    print("WHAT IS ACTUALLY WRONG")
    print("=" * 74)
    print(f"  self-approved              {summary['self_approved']:>5,} entries"
          f"   ${summary['self_approved_value']:>12,.0f}")
    print(f"     {summary['self_approval_top2_share']:.0%} of it is two people; "
          f"{summary['self_approval_worst']} alone accounts for "
          f"{summary['self_approval_worst_count']}.")
    print("     That is a bottleneck being worked around, not a policy nobody "
          "understands -")
    print("     and it is a different fix.")
    print(f"  above approver's limit     "
          f"{summary['above_approver_limit']:>5,} entries"
          f"   ${summary['above_approver_limit_value']:>12,.0f}")
    print(f"  just under a limit         "
          f"{summary['threshold_avoidance']:>5,} entries"
          f"   ${summary['threshold_avoidance_value']:>12,.0f}")
    print(f"  round thousands            {summary['round_dollar']:>5,} entries")
    print(f"  outside business hours     {summary['after_hours']:>5,} entries")
    print()
    print(f"  {summary['for_review']} entries score {REVIEW_THRESHOLD} or more "
          f"and go on the review list")
    print(f"  ({summary['for_review_share_of_manual']:.1%} of manual entries, "
          f"${summary['for_review_value']:,.0f}). A list of a few hundred is a "
          "morning's")
    print("  work; a list of several thousand is a report nobody opens.")
    print()
    print("WHO POSTS THE RISK")
    print("=" * 74)
    print(f"  {'poster':16s} {'role':22s} {'entries':>8s} {'avg score':>10s} "
          f"{'for review':>11s}")
    for _, r in users.head(6).iterrows():
        print(f"  {r.poster:16s} {r.poster_role:22s} {r.entries:>8,} "
              f"{r.avg_risk_score:>10.1f} {r.for_review:>11,}")
    print()
    print("BENFORD'S LAW BY COST CENTRE")
    print("=" * 74)
    print(f"  {'cost centre':16s} {'entries':>8s} {'chi-square':>11s} "
          f"{'MAD':>8s}  verdict")
    for _, r in ben.iterrows():
        print(f"  {r.cost_center:16s} {r.entries:>8,} {r.chi_square:>11.2f} "
              f"{r.mad:>8.4f}  {r.verdict}")
    print()
    print(f"  Critical value at 5% on 8 degrees of freedom is "
          f"{CHI2_CRITICAL[0.05]}. "
          f"{summary['benford_deviating']} of {summary['benford_groups']}")
    print("  cost centres exceed it. Benford is a screen, not evidence: a "
          "population that")
    print("  conforms is not clean and one that deviates is not fraudulent. It "
          "says where")
    print("  to look.")
    print()
    print(f"wrote 6 files to {OUT}")


if __name__ == "__main__":
    main()
