"""
What happened to the exceptions after they were found.

run_reconciliation.py identifies 230 differences between the ERP and the
subledger and stops there, which is where most reconciliation tooling stops.
It is also the point at which the only question a controller actually has
begins: is the close getting better or worse, and whose queue is holding it up.

An exception log without an owner, an opened date and a cleared date cannot
answer either. It can tell you how many problems there were. It cannot tell you
whether they were fixed, how long they took, or which of the four exception
types is quietly consuming the month.

What this measures
------------------
    Ageing        Open exceptions bucketed by how long they have been open, on
                  the standard 0-7 / 8-14 / 15-30 / 30+ ladder used in an AR
                  ageing, because the shape of the tail is what tells you
                  whether a queue is being worked or accumulating.
    Clearing SLA  Days from opened to cleared against a 10-day target, by
                  owner and by exception type. Reported as a MEDIAN alongside
                  the mean: one exception that sat for eleven weeks drags an
                  average into uselessness and a median not at all.
    Throughput    Opened against cleared per period, and the backlog that
                  leaves behind. Two months of clearing fewer than you open is
                  a trend; one is a bad month.
    Method mix    How exceptions were resolved. "Accepted as timing" and
                  "written off below threshold" are resolutions that close a
                  ticket without fixing anything, and a close where they
                  dominate has a process problem rather than a queue problem.

Why median and mean are both here
---------------------------------
Because they disagree, and the disagreement is the finding. Where the mean is
far above the median, one owner's queue has a long tail rather than a slow
average - a different problem with a different fix, and invisible in either
statistic on its own.

Outputs (output/):
    exception_ageing.csv        open exceptions by age bucket
    exception_sla_by_owner.csv  clearing speed and breach rate per owner
    exception_sla_by_type.csv   the same cut by exception type
    exception_throughput.csv    opened, cleared and backlog per period
    exception_ageing_summary.json  the headline figures the report and tests read

Usage:
    python engine/exception_ageing.py
"""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

SLA_DAYS = 10

# The ageing ladder. Upper bounds are inclusive; the last bucket is open-ended.
AGE_BUCKETS = [(0, 7, "0-7 days"), (8, 14, "8-14 days"),
               (15, 30, "15-30 days"), (31, 10_000, "over 30 days")]

# Resolutions that close the ticket without changing anything upstream. A close
# where these dominate is not clearing exceptions, it is absorbing them.
ABSORBING_METHODS = {"Accepted as timing", "Written off below threshold"}


def load() -> dict:
    return {
        "workflow": pd.read_csv(DATA / "exception_workflow.csv",
                                parse_dates=["opened_date", "cleared_date"]),
        "users": pd.read_csv(DATA / "dim_user.csv"),
        "exceptions": pd.read_csv(OUT / "gl_reconciliation_exceptions.csv"),
    }


def enrich(d: dict) -> pd.DataFrame:
    # transaction_id alone does NOT identify an exception: 184 transactions
    # produce 230 exceptions because one posting can be both missing from the
    # subledger and a timing difference. Joining on it fans 230 rows out to 310
    # and every count on this page silently inflates by a third, which is
    # exactly what the first run of this module did. validate= makes that a
    # crash instead of a wrong number.
    w = d["workflow"].merge(
        d["exceptions"][["transaction_id", "period", "exception_type",
                         "erp_amount", "variance_amount"]],
        on=["transaction_id", "period", "exception_type"], how="left",
        validate="one_to_one")
    assert len(w) == len(d["workflow"])
    users = d["users"].set_index("user_id")
    w["owner"] = w.owner_id.map(users.user_name)
    w["owner_role"] = w.owner_id.map(users.role)
    w["is_open"] = (w.status == "Open").astype(int)
    # The exposure an exception carries: the variance where one was computed,
    # otherwise the whole posting, because an entry missing from the subledger
    # is the entire amount at risk rather than a difference.
    w["exposure"] = w.variance_amount.fillna(w.erp_amount).abs().round(2)
    w["absorbed"] = w.clear_method.isin(ABSORBING_METHODS).astype(int)
    return w


def bucket(age: float) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    return AGE_BUCKETS[-1][2]


def ageing(w: pd.DataFrame) -> pd.DataFrame:
    open_ = w[w.is_open == 1].copy()
    open_["age_bucket"] = open_.age_days.map(bucket)
    g = open_.groupby("age_bucket")
    out = pd.DataFrame({
        "exceptions": g.size(),
        "exposure": g.exposure.sum().round(2),
        "oldest_days": g.age_days.max(),
    })
    order = {label: i for i, (_, _, label) in enumerate(AGE_BUCKETS)}
    out = out.reindex([label for _, _, label in AGE_BUCKETS]).fillna(0)
    out["bucket_order"] = [order[i] for i in out.index]
    return out.reset_index().rename(columns={"index": "age_bucket"})


def sla(w: pd.DataFrame, by: str) -> pd.DataFrame:
    cleared = w[w.is_open == 0]
    g = cleared.groupby(by)
    out = pd.DataFrame({
        "cleared": g.size(),
        "median_days": g.days_to_clear.median().round(1),
        "mean_days": g.days_to_clear.mean().round(1),
        "p90_days": g.days_to_clear.quantile(0.9).round(1),
        "worst_days": g.days_to_clear.max(),
        "breaches": g.breached_sla.sum(),
        "exposure": g.exposure.sum().round(2),
    })
    out["breach_rate"] = (out.breaches / out.cleared).round(4)
    # Where the mean runs well above the median, the queue has a long tail
    # rather than a slow average - a different problem with a different fix.
    out["tail_ratio"] = (out.mean_days / out.median_days).round(2)
    still_open = w[w.is_open == 1].groupby(by).size()
    out["still_open"] = still_open.reindex(out.index).fillna(0).astype(int)
    # `by` is the group key and is unique, so it settles the ties in
    # median_days - two owners clearing at the same median must not swap rows
    # between one machine and another.
    return out.sort_values(["median_days", by],
                           ascending=[False, True]).reset_index()


def throughput(w: pd.DataFrame) -> pd.DataFrame:
    opened = w.groupby(w.opened_date.dt.to_period("M")).size()
    cleared = (w[w.is_open == 0]
               .groupby(w[w.is_open == 0].cleared_date.dt.to_period("M")).size())
    idx = opened.index.union(cleared.index).sort_values()
    out = pd.DataFrame({
        "period": [str(p) for p in idx],
        "opened": opened.reindex(idx, fill_value=0).values,
        "cleared": cleared.reindex(idx, fill_value=0).values,
    })
    out["net"] = out.opened - out.cleared
    out["backlog"] = out.net.cumsum()
    return out


def build() -> tuple:
    d = load()
    w = enrich(d)
    age = ageing(w)
    owners = sla(w, "owner")
    types = sla(w, "exception_type")
    flow = throughput(w)

    cleared = w[w.is_open == 0]
    open_ = w[w.is_open == 1]
    slowest = owners.iloc[0]
    fastest = owners.iloc[-1]
    worst_tail = owners.loc[owners.tail_ratio.idxmax()]
    absorbed = cleared[cleared.absorbed == 1]

    methods = (cleared.groupby("clear_method")
               .agg(count=("transaction_id", "size"),
                    exposure=("exposure", "sum"))
               .sort_values("count", ascending=False, kind="stable"))
    methods = methods.sort_index().sort_values("count", ascending=False,
                                               kind="stable")
    methods["share"] = (methods["count"] / methods["count"].sum()).round(4)

    summary = {
        "exceptions": int(len(w)),
        "cleared": int(len(cleared)),
        "still_open": int(len(open_)),
        "open_exposure": round(float(open_.exposure.sum()), 2),
        "total_exposure": round(float(w.exposure.sum()), 2),
        "sla_days": SLA_DAYS,
        "median_days_to_clear": float(cleared.days_to_clear.median()),
        "mean_days_to_clear": round(float(cleared.days_to_clear.mean()), 1),
        "p90_days_to_clear": float(cleared.days_to_clear.quantile(0.9)),
        "worst_days_to_clear": int(cleared.days_to_clear.max()),
        "breaches": int(w.breached_sla.sum()),
        "breach_rate": round(float(w.breached_sla.mean()), 4),
        "slowest_owner": slowest.owner,
        "slowest_owner_median": float(slowest.median_days),
        "slowest_owner_cleared": int(slowest.cleared),
        "slowest_owner_breach_rate": float(slowest.breach_rate),
        "fastest_owner": fastest.owner,
        "fastest_owner_median": float(fastest.median_days),
        # The spread between the fastest and slowest queue, which is what makes
        # this worth reporting by owner rather than in total.
        "owner_spread_multiple": round(
            float(slowest.median_days / fastest.median_days), 1)
        if fastest.median_days else None,
        "worst_tail_owner": worst_tail.owner,
        "worst_tail_ratio": float(worst_tail.tail_ratio),
        "worst_tail_median": float(worst_tail.median_days),
        "worst_tail_mean": float(worst_tail.mean_days),
        "slowest_type": types.iloc[0].exception_type,
        "slowest_type_median": float(types.iloc[0].median_days),
        "fastest_type": types.iloc[-1].exception_type,
        "fastest_type_median": float(types.iloc[-1].median_days),
        "over_30_days_open": int(
            age.loc[age.age_bucket == "over 30 days", "exceptions"].iloc[0]),
        "oldest_open_days": int(open_.age_days.max()) if len(open_) else 0,
        "absorbed": int(len(absorbed)),
        "absorbed_share": round(float(len(absorbed) / len(cleared)), 4),
        "absorbed_exposure": round(float(absorbed.exposure.sum()), 2),
        "top_method": methods.index[0],
        "top_method_share": float(methods.iloc[0].share),
        "months_backlog_grew": int((flow.net > 0).sum()),
        "final_backlog": int(flow.backlog.iloc[-1]),
    }
    return summary, age, owners, types, flow, methods.reset_index()


def headline(summary: dict) -> pd.DataFrame:
    scalars = {k: v for k, v in summary.items()
               if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    return pd.DataFrame([scalars])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary, age, owners, types, flow, methods = build()

    age.to_csv(OUT / "exception_ageing.csv", index=False)
    owners.to_csv(OUT / "exception_sla_by_owner.csv", index=False)
    types.to_csv(OUT / "exception_sla_by_type.csv", index=False)
    flow.to_csv(OUT / "exception_throughput.csv", index=False)
    methods.to_csv(OUT / "exception_clear_methods.csv", index=False)
    (OUT / "exception_ageing_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    headline(summary).to_csv(OUT / "exception_ageing_headline.csv", index=False)

    print()
    print("=" * 74)
    print("EXCEPTION AGEING AND CLEARING SLA")
    print("=" * 74)
    print(f"  {summary['exceptions']} exceptions, "
          f"{summary['cleared']} cleared, {summary['still_open']} still open "
          f"(${summary['open_exposure']:,.0f} of exposure)")
    print(f"  median {summary['median_days_to_clear']:.0f} days to clear, "
          f"mean {summary['mean_days_to_clear']:.1f}, "
          f"P90 {summary['p90_days_to_clear']:.0f}, worst "
          f"{summary['worst_days_to_clear']}")
    print(f"  {summary['breaches']} of {summary['exceptions']} breached the "
          f"{SLA_DAYS}-day SLA ({summary['breach_rate']:.1%})")
    print()
    print("WHAT IS STILL OPEN")
    print("=" * 74)
    print(f"  {'age':>14s} {'exceptions':>11s} {'exposure':>13s} "
          f"{'oldest':>8s}")
    for _, r in age.iterrows():
        print(f"  {r.age_bucket:>14s} {r.exceptions:>11,.0f} "
              f"${r.exposure:>12,.0f} {r.oldest_days:>8,.0f}")
    print()
    print("WHOSE QUEUE IS HOLDING THE CLOSE UP")
    print("=" * 74)
    print(f"  {'owner':16s} {'cleared':>8s} {'median':>8s} {'mean':>7s} "
          f"{'P90':>6s} {'breach':>8s} {'open':>6s}")
    for _, r in owners.iterrows():
        print(f"  {r.owner:16s} {r.cleared:>8,} {r.median_days:>8.1f} "
              f"{r.mean_days:>7.1f} {r.p90_days:>6.1f} {r.breach_rate:>8.0%} "
              f"{r.still_open:>6,}")
    print()
    if summary["owner_spread_multiple"]:
        print(f"  {summary['slowest_owner']} takes "
              f"{summary['owner_spread_multiple']:.1f}x as long as "
              f"{summary['fastest_owner']} on the same kind of work.")
    print(f"  {summary['worst_tail_owner']} has the worst tail: a median of "
          f"{summary['worst_tail_median']:.0f} days against a mean of "
          f"{summary['worst_tail_mean']:.1f}.")
    print("  A slow average and a long tail are different problems. Averaging "
          "them together")
    print("  produces one number that describes neither.")
    print()
    print("BY EXCEPTION TYPE")
    print("=" * 74)
    print(f"  {'type':22s} {'cleared':>8s} {'median':>8s} {'breach':>8s} "
          f"{'exposure':>13s}")
    for _, r in types.iterrows():
        print(f"  {r.exception_type:22s} {r.cleared:>8,} {r.median_days:>8.1f} "
              f"{r.breach_rate:>8.0%} ${r.exposure:>12,.0f}")
    print()
    print("HOW THEY WERE RESOLVED")
    print("=" * 74)
    for _, r in methods.iterrows():
        absorbing = "   <- closes the ticket, fixes nothing" \
            if r.clear_method in ABSORBING_METHODS else ""
        print(f"  {r.clear_method:30s} {r['count']:>5,}  {r.share:>6.1%}"
              f"{absorbing}")
    print()
    print(f"  {summary['absorbed']} of {summary['cleared']} "
          f"({summary['absorbed_share']:.0%}) were absorbed rather than fixed, "
          f"carrying ${summary['absorbed_exposure']:,.0f}.")
    print("  Those exceptions will be back next month, because nothing upstream "
          "changed.")
    print()
    print("THROUGHPUT")
    print("=" * 74)
    print(f"  {'period':>10s} {'opened':>8s} {'cleared':>9s} {'net':>6s} "
          f"{'backlog':>9s}")
    for _, r in flow.iterrows():
        print(f"  {r.period:>10s} {r.opened:>8,} {r.cleared:>9,} "
              f"{r.net:>+6,} {r.backlog:>9,}")
    print()
    print(f"  The backlog grew in {summary['months_backlog_grew']} of "
          f"{len(flow)} months and finished at {summary['final_backlog']}.")
    print()
    print(f"wrote 7 files to {OUT}")


if __name__ == "__main__":
    main()
