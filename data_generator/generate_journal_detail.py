"""
Who posted it, when, who approved it - and what happened to the exception after.

source_erp_gl.csv is a general ledger with no author. It records what was
posted and to where, which is enough to reconcile it against a subledger and
nothing like enough to control it. Every journal-entry test an internal audit
function actually runs - manual versus interfaced, weekend and after-hours
postings, one person posting and approving their own entry, amounts sitting
just under an approval limit - needs to know who touched the entry and at what
hour, and none of that exists in a ledger extract that carries only a date.

The same gap sits on the other side of the reconciliation: 230 exceptions are
identified and then, as far as the data is concerned, nothing ever happens to
them. An exception log without an owner, an open date and a clear date cannot
answer the only question a controller has about it, which is whether the close
is getting better or worse.

Two files, both additive
------------------------
    dim_user.csv           who works in finance, their role, and what they may
                           approve
    journal_control.csv    one row per existing ERP transaction: author,
                           approver, timestamp, source system, and whether it
                           was reversed
    exception_workflow.csv one row per open reconciliation exception: owner,
                           when it opened, when it cleared, and how

Nothing here changes source_erp_gl.csv, source_subledger_gl.csv or either
dimension. Every existing figure in this repo keeps its value; the transaction
ids are joined to, never regenerated.

The control weaknesses are real and specific
--------------------------------------------
A generator that sprinkles violations uniformly produces a page that says
"3% of entries are risky" and nothing more. These cluster the way real ones do:
a small number of people account for most of the self-approvals, threshold
avoidance concentrates in the cost centres with the tightest budgets, and
after-hours postings spike at period end when the close is under time pressure.
That is what makes the analysis able to name a control to fix rather than a
percentage to worry about.

Synthetic only. No real people. Fixed seed.

Usage:
    python data_generator/generate_journal_detail.py
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

SEED = 20260911

# The finance team. approval_limit is what this person may sign off unaided;
# 0 means they cannot approve at all.
USERS = [
    # name, role, department, approval_limit, is_service_account
    ("R. Almeida", "Financial Controller", "Finance", 250_000, 0),
    ("S. Kowalski", "Assistant Controller", "Finance", 100_000, 0),
    ("T. Nakamura", "Senior Accountant", "Finance", 25_000, 0),
    ("U. Brennan", "Senior Accountant", "Finance", 25_000, 0),
    ("V. Adeyemi", "Staff Accountant", "Finance", 10_000, 0),
    ("W. Lindgren", "Staff Accountant", "Finance", 10_000, 0),
    ("X. Ferreira", "AP Specialist", "Operations", 5_000, 0),
    ("Y. Volkov", "AR Specialist", "Operations", 5_000, 0),
    ("Z. Osei", "FP&A Analyst", "Finance", 0, 0),
    ("SYS_ERP_INTERFACE", "Automated interface", "IT", 0, 1),
    ("SYS_RECURRING", "Recurring journal engine", "IT", 0, 1),
]

# Where an entry came from. Manual entries are the ones audit cares about:
# an interfaced entry inherits the controls of the system that raised it.
SOURCES = [("Interface", 0.58), ("Recurring", 0.16), ("Manual", 0.26)]

# The approval limit that matters for threshold avoidance. Sitting a journal at
# $9,850 against a $10,000 limit is the single most-cited red flag in journal
# entry testing, and it is invisible unless somebody looks for it.
AVOIDANCE_BAND = 0.06        # within 6% below a limit counts as "just under"
AVOIDANCE_RATE = 0.055       # share of manual entries that do it

# Postings outside 07:00-19:00 on a weekday. Rare normally, and much less rare
# in the last days of a close.
AFTER_HOURS_BASE = 0.04
AFTER_HOURS_CLOSE = 0.22
CLOSE_WINDOW_DAYS = 3        # last N days of the period, plus the first N after

# Self-approval: the same person posts and approves. Concentrated on a few
# people rather than sprinkled, because that is how a control breaks in
# practice - one team works around a bottleneck and it becomes their habit.
SELF_APPROVAL_HABITUAL = {"T. Nakamura", "X. Ferreira"}
SELF_APPROVAL_RATE = {"habitual": 0.28, "other": 0.012}

# Round numbers are how estimates and plugs look. Genuine transactional
# postings almost never land on a round thousand.
ROUND_DOLLAR_RATE = 0.021

REVERSAL_RATE = 0.035

# Exception workflow. Owners clear at different speeds, which is the point of
# measuring by owner rather than in total.
EXCEPTION_SLA_DAYS = 10
CLEAR_METHODS = [
    ("Corrected in ERP", 0.34),
    ("Subledger re-posted", 0.24),
    ("Accepted as timing", 0.22),
    ("Written off below threshold", 0.13),
    ("Escalated to Controller", 0.07),
]


def month_bounds(period: str):
    year, month = (int(p) for p in period.split("-"))
    start = datetime(year, month, 1)
    end = (datetime(year + (month == 12), (month % 12) + 1, 1)
           - timedelta(days=1))
    return start, end


def gen_users():
    return [{"user_id": f"U{i + 1:03d}", "user_name": name, "role": role,
             "department": dept, "approval_limit": limit,
             "is_service_account": svc}
            for i, (name, role, dept, limit, svc) in enumerate(USERS)]


def pick(options, rng):
    labels = [o[0] for o in options]
    weights = [o[1] for o in options]
    return rng.choices(labels, weights=weights)[0]


def gen_journal_control(erp, users, rng):
    humans = [u for u in users if not u["is_service_account"]]
    approvers = [u for u in humans if u["approval_limit"] > 0]
    by_name = {u["user_name"]: u for u in users}
    interface = by_name["SYS_ERP_INTERFACE"]
    recurring = by_name["SYS_RECURRING"]
    limits = sorted({u["approval_limit"] for u in approvers if u["approval_limit"]})

    rows = []
    for r in erp:
        period = r["period"]
        posted = datetime.strptime(r["posted_date"], "%Y-%m-%d")
        start, end = month_bounds(period)
        amount = float(r["amount"])
        source = pick(SOURCES, rng)

        if source == "Interface":
            author = interface
        elif source == "Recurring":
            author = recurring
        else:
            author = rng.choice(humans)

        # Close pressure: the last days of the period and the first days after
        # it, when the ledger is still open and everyone is behind.
        in_close = (posted >= end - timedelta(days=CLOSE_WINDOW_DAYS)
                    or posted <= start + timedelta(days=CLOSE_WINDOW_DAYS))
        after_hours_p = AFTER_HOURS_CLOSE if in_close else AFTER_HOURS_BASE
        if source != "Manual":
            # Interfaced and recurring jobs run on a schedule, at night, by
            # design. They are outside business hours and are NOT a finding -
            # counting them as one is how an after-hours report ends up 60%
            # noise and gets ignored.
            hour = rng.choice([2, 3, 4, 22, 23])
            minute = rng.randrange(60)
        elif rng.random() < after_hours_p:
            hour = rng.choice([0, 1, 5, 6, 20, 21, 22, 23])
            minute = rng.randrange(60)
        else:
            hour = rng.randrange(8, 18)
            minute = rng.randrange(60)
        stamp = posted.replace(hour=hour, minute=minute,
                               second=rng.randrange(60))

        # Approval. Service accounts are approved by the system owner.
        if source != "Manual":
            approver = by_name["R. Almeida"]
        else:
            habitual = author["user_name"] in SELF_APPROVAL_HABITUAL
            rate = SELF_APPROVAL_RATE["habitual" if habitual
                                      else "other"]
            if author["approval_limit"] > 0 and rng.random() < rate:
                approver = author
            else:
                eligible = [u for u in approvers
                            if u["user_name"] != author["user_name"]]
                approver = rng.choice(eligible)

        # Threshold avoidance and round dollars only make sense on a manual
        # entry - nobody hand-tunes an interfaced amount.
        adjusted = amount
        if source == "Manual" and rng.random() < AVOIDANCE_RATE and limits:
            limit = min(limits, key=lambda v: abs(abs(amount) - v))
            target = limit * (1 - rng.uniform(0.002, AVOIDANCE_BAND))
            adjusted = target if amount >= 0 else -target
        elif source == "Manual" and rng.random() < ROUND_DOLLAR_RATE:
            adjusted = round(amount / 1000.0) * 1000.0 or amount

        rows.append({
            "transaction_id": r["transaction_id"],
            "period": period,
            "journal_source": source,
            "posted_by": author["user_id"],
            "approved_by": approver["user_id"],
            "posted_timestamp": stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "posting_hour": stamp.hour,
            "is_weekend": int(stamp.weekday() >= 5),
            "days_from_period_end": (stamp - end).days,
            "control_amount": round(adjusted, 2),
            "is_reversal": int(rng.random() < REVERSAL_RATE),
        })
    return rows


def gen_exception_workflow(exceptions, users, rng):
    """Every open exception gets an owner and a life.

    Owners clear at genuinely different speeds. A close report that gives one
    average days-to-clear has answered a question nobody asked; the useful
    number is which queue is the bottleneck."""
    owners = [u for u in users if not u["is_service_account"]
              and u["department"] in ("Finance", "Operations")]
    # Each owner's own clearing speed, drawn once. Some are fast, one is a
    # bottleneck, and the analysis has to be able to find the bottleneck.
    speed = {u["user_id"]: rng.uniform(0.6, 3.1) for u in owners}
    as_of = datetime(2025, 7, 15)

    rows = []
    for e in exceptions:
        period = e["period"]
        _, end = month_bounds(period)
        owner = rng.choice(owners)
        opened = end + timedelta(days=rng.randrange(1, 6))
        base = rng.expovariate(1 / 6.0) * speed[owner["user_id"]]
        # Larger variances get chased harder.
        size = abs(float(e["variance_amount"] or e["erp_amount"] or 0.0))
        urgency = 0.55 if size > 20_000 else 1.0
        days = max(1, int(base * urgency))
        cleared = opened + timedelta(days=days)
        still_open = cleared > as_of
        rows.append({
            "transaction_id": e["transaction_id"],
            "period": period,
            "exception_type": e["exception_type"],
            "owner_id": owner["user_id"],
            "opened_date": opened.strftime("%Y-%m-%d"),
            "cleared_date": "" if still_open else cleared.strftime("%Y-%m-%d"),
            "status": "Open" if still_open else "Cleared",
            "days_to_clear": "" if still_open else days,
            "age_days": (as_of - opened).days if still_open else days,
            "clear_method": "" if still_open else pick(CLEAR_METHODS, rng),
            "breached_sla": int((days if not still_open
                                 else (as_of - opened).days)
                                > EXCEPTION_SLA_DAYS),
        })
    return rows


def write(path, rows):
    # newline="" hands line endings to the csv module, which writes CRLF on
    # every platform; lineterminator makes it LF, which is what the repository
    # stores. Without it the file's bytes record which machine ran the
    # generator, and CI compares bytes.
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()),
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path}  ({len(rows):,} rows)")


def main():
    rng = random.Random(SEED)
    erp = list(csv.DictReader((DATA / "source_erp_gl.csv").open(encoding="utf-8")))
    exceptions = list(csv.DictReader(
        (OUT / "gl_reconciliation_exceptions.csv").open(encoding="utf-8")))

    users = gen_users()
    control = gen_journal_control(erp, users, rng)
    workflow = gen_exception_workflow(exceptions, users, rng)

    write(DATA / "dim_user.csv", users)
    write(DATA / "journal_control.csv", control)
    write(DATA / "exception_workflow.csv", workflow)

    manual = [c for c in control if c["journal_source"] == "Manual"]
    self_app = [c for c in manual if c["posted_by"] == c["approved_by"]]
    print()
    print(f"{len(manual):,} manual entries of {len(control):,}; "
          f"{len(self_app)} posted and approved by the same person.")
    print(f"{sum(w['status'] == 'Open' for w in workflow)} exceptions still "
          f"open of {len(workflow)}.")


if __name__ == "__main__":
    main()
