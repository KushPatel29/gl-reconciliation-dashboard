"""
Tableau workbook integrity: the generated .twb must stay in sync with the
extracts it reads, so it always opens and every shelf binds to a real field.

Tableau fails a broken workbook at *load* time with a modal dialog rather than
degrading gracefully, so a stale field reference is not a cosmetic problem —
it makes the workbook unopenable. These tests are the cheap version of that
feedback loop.
"""
import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAB = ROOT / "tableau"
TWB = TAB / "GLCloseScorecard.twb"

CSV_FOR_DS = {
    "federated.controltotals": "control_totals_tableau.csv",
    "federated.exceptions": "exceptions_tableau.csv",
}


@pytest.fixture(scope="module")
def wb():
    return ET.parse(TWB).getroot()


@pytest.fixture(scope="module")
def headers():
    out = {}
    for ds, name in CSV_FOR_DS.items():
        with open(TAB / name, encoding="utf-8") as f:
            out[ds] = set(next(csv.reader(f)))
    return out


def _data_datasources(wb):
    return [d for d in wb.find("datasources") if d.get("name") in CSV_FOR_DS]


def test_workbook_exists_and_is_well_formed(wb):
    assert wb.tag == "workbook"
    # Tableau rejects a workbook with no source-build attribute outright.
    assert wb.get("source-build")


def test_declared_columns_exist_in_the_extracts(wb, headers):
    """Every non-calculated column must be a real CSV header."""
    for ds in _data_datasources(wb):
        declared = {
            c.get("name").strip("[]")
            for c in ds.findall("column")
            if c.find("calculation") is None
        }
        missing = declared - headers[ds.get("name")]
        assert not missing, f"{ds.get('name')} declares absent columns: {missing}"


def test_calculations_only_reference_real_fields(wb, headers):
    """A calc referencing a dropped column makes the workbook unopenable."""
    for ds in _data_datasources(wb):
        known = set(headers[ds.get("name")])
        known |= {c.get("name").strip("[]") for c in ds.findall("column")}
        for col in ds.findall("column"):
            calc = col.find("calculation")
            if calc is None:
                continue
            formula = calc.get("formula")
            for ref in _bracket_refs(formula):
                if ref.startswith("Parameters") or ref == "Parameter 1":
                    continue
                assert ref in known, (
                    f"{ds.get('name')} calc {col.get('name')} references "
                    f"unknown field [{ref}]"
                )


def _bracket_refs(formula):
    out, depth, buf = [], 0, ""
    for ch in formula:
        if ch == "[":
            depth, buf = depth + 1, ""
        elif ch == "]" and depth:
            depth -= 1
            out.append(buf)
        elif depth:
            buf += ch
    return out


def test_every_shelf_reference_resolves_to_a_declared_instance(wb):
    """
    rows/cols/encodings must name a column-instance the worksheet declares in
    its datasource-dependencies. This is the check that would have caught a
    renamed calculation before Tableau's load-time modal did.
    """
    for ws in wb.find("worksheets"):
        view = ws.find("table/view")
        declared = set()
        for dep in view.findall("datasource-dependencies"):
            ds = dep.get("datasource")
            for ci in dep.findall("column-instance"):
                declared.add(f"[{ds}].{ci.get('name')}")
            for c in dep.findall("column"):
                declared.add(f"[{ds}].{c.get('name')}")

        refs = []
        for shelf in ("rows", "cols"):
            text = (ws.find(f"table/{shelf}").text or "").strip()
            refs += [r for r in _shelf_refs(text)]
        for enc in ws.findall("table/panes/pane/encodings/*"):
            refs.append(enc.get("column"))
        for f in view.findall("filter"):
            refs.append(f.get("column"))

        for ref in refs:
            if not ref:
                continue
            assert ref in declared, (
                f"worksheet {ws.get('name')!r} references {ref}, "
                f"which it never declares"
            )


def _shelf_refs(text):
    """Shelf text is like '[ds].[field]' or '([ds].[a] / [ds].[b])'."""
    out = []
    for part in text.replace("(", "").replace(")", "").split("/"):
        part = part.strip()
        if part.startswith("["):
            out.append(part)
    return out


def test_dashboard_zones_point_at_real_worksheets(wb):
    sheets = {ws.get("name") for ws in wb.find("worksheets")}
    dash = wb.find("dashboards/dashboard")
    named = [z.get("name") for z in dash.iter("zone") if z.get("name")]
    assert named, "dashboard has no worksheet zones"
    for name in named:
        assert name in sheets, f"dashboard zone points at missing sheet {name!r}"


def test_every_sheet_has_a_window_and_exactly_one_is_active(wb):
    """
    Tableau's document parser asserts !m_activeSheet.empty(); a workbook whose
    <windows> block is missing or has no maximized window fails to load with a
    bare "Internal Error" and no line number.
    """
    windows = wb.find("windows")
    assert windows is not None
    sheets = {ws.get("name") for ws in wb.find("worksheets")}
    windowed = {w.get("name") for w in windows if w.get("class") == "worksheet"}
    assert sheets == windowed, f"sheets without windows: {sheets ^ windowed}"
    assert sum(1 for w in windows if w.get("maximized") == "true") == 1


def test_parameter_members_match_the_periods_in_the_data(wb):
    """A stale period list would silently offer a filter that matches nothing."""
    with open(TAB / "control_totals_tableau.csv", encoding="utf-8") as f:
        periods = sorted({r["period"] for r in csv.DictReader(f)})
    param = wb.find("datasources/datasource[@name='Parameters']/column")
    members = [m.get("value").strip('"') for m in param.findall("members/member")]
    assert members == ["All"] + periods


def test_colour_palette_buckets_are_values_that_actually_occur(wb, headers):
    """
    The palette binds by literal value, so a renamed exception type does not
    error — it just silently loses its brand colour and picks up a Tableau
    default. Assert every bucket is a value present in the data.
    """
    with open(TAB / "exceptions_tableau.csv", encoding="utf-8") as f:
        types = {r["exception_type"] for r in csv.DictReader(f)}
    ds = wb.find("datasources/datasource[@name='federated.exceptions']")
    buckets = {b.text.strip('"') for b in ds.iter("bucket")}
    assert buckets == types, f"palette/data mismatch: {buckets ^ types}"


def test_palette_fields_resolve_to_a_datasource_level_instance(wb):
    """
    A style rule's `field` binds to a column-instance declared on the
    DATASOURCE. Declaring it only inside a worksheet leaves the rule
    unresolved and Tableau falls back to its default palette — silently.
    """
    for ds in _data_datasources(wb):
        style = ds.find("style")
        if style is None:
            continue
        instances = {ci.get("name") for ci in ds.findall("column-instance")}
        for enc in style.iter("encoding"):
            assert enc.get("field") in instances, (
                f"{ds.get('name')} palette field {enc.get('field')} has no "
                f"datasource-level column-instance"
            )


def test_workbook_is_reproducible_from_the_generator():
    """The committed .twb must be exactly what build_workbook.py emits."""
    before = TWB.read_bytes()
    subprocess.run(
        [sys.executable, str(TAB / "build_workbook.py")], check=True,
        capture_output=True,
    )
    assert TWB.read_bytes() == before, (
        "GLCloseScorecard.twb differs from generator output — "
        "re-run python tableau/build_workbook.py and commit the result"
    )


def test_connections_are_relative_so_the_workbook_opens_anywhere(wb):
    """An absolute directory would pin the workbook to my machine."""
    for conn in wb.iter("connection"):
        if conn.get("class") != "textscan":
            continue
        assert conn.get("directory") == ".", (
            f"non-relative connection directory: {conn.get('directory')}"
        )
        assert (TAB / conn.get("filename")).exists()
