"""
A published file whose row order is not fully determined is not reproducible.

CI regenerates everything and asserts the result is byte-identical to what was
committed. That gate passed on Windows and failed on the Linux runner, and the
reason was not the numbers - it was the ORDER. `journal_risk_entries.csv` sorts
on risk_score, 1,375 of its 1,412 rows share a score, and pandas' default sort
is not stable, so the tied block came out in a different order on a different
machine.

Sorting stably would hide it rather than fix it: a stable sort preserves the
INPUT order, and the input order is itself a groupby result. The fix is a sort
key that admits no ties at all, and this file checks that every published table
has one.
"""
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

# (file, the columns the module sorts on). Ties in these break reproducibility.
SORTED_FILES = [
    ("journal_risk_entries.csv", ["risk_score", "transaction_id"]),
    ("journal_risk_flags.csv", ["manual_entries_flagged", "flag"]),
    ("journal_risk_by_user.csv", ["risk_score", "poster"]),
    ("benford_by_dimension.csv", ["chi_square", "cost_center"]),
    ("exception_sla_by_owner.csv", ["median_days", "owner"]),
    ("exception_sla_by_type.csv", ["median_days", "exception_type"]),
    ("exception_clear_methods.csv", ["count", "clear_method"]),
    ("exception_throughput.csv", ["period"]),
    ("exception_ageing.csv", ["bucket_order"]),
]


@pytest.mark.parametrize("name,keys", SORTED_FILES, ids=[f for f, _ in SORTED_FILES])
def test_the_sort_key_admits_no_ties(name, keys):
    """With a unique key the order is a property of the data, not of the
    machine that happened to write the file."""
    df = pd.read_csv(OUT / name)
    missing = [k for k in keys if k not in df.columns]
    assert not missing, f"{name} has no {missing} to sort on"
    dupes = df[df.duplicated(keys, keep=False)]
    assert dupes.empty, (
        f"{name} has {len(dupes)} rows tied on {keys}; the published row order "
        "depends on which machine wrote it")


@pytest.mark.parametrize("name,keys", SORTED_FILES, ids=[f for f, _ in SORTED_FILES])
def test_the_file_is_actually_in_that_order(name, keys):
    """A unique key is only worth having if the file is sorted by it. This
    re-sorts and demands the same row order back."""
    df = pd.read_csv(OUT / name)
    # Direction is inferred from the file: whichever way the first key already
    # runs is the way it is meant to run.
    first = df[keys[0]]
    ascending = [bool(first.is_monotonic_increasing)] + [True] * (len(keys) - 1)
    resorted = df.sort_values(keys, ascending=ascending, kind="stable")
    assert resorted.index.tolist() == df.index.tolist(), (
        f"{name} is not in {keys} order")


def test_there_are_files_to_check():
    """A list that pointed at nothing would make every case above vacuous."""
    for name, _ in SORTED_FILES:
        assert (OUT / name).exists(), f"{name} was not published"
