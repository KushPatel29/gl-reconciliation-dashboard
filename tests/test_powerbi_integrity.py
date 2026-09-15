"""
Power BI project integrity: the hand-authored PBIR report and TMDL model must
stay in sync, so the .pbip always opens and every visual binds to a real field.
Catches a renamed measure or a mistyped column before Power BI Desktop throws
a broken-visual error.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "powerbi" / "pbip"
SM = PBIP / "GLReconciliationDashboard.SemanticModel"
RPT = PBIP / "GLReconciliationDashboard.Report"


@pytest.fixture(scope="module")
def model():
    """Parse TMDL into {table: set(columns)} and set(measures)."""
    cols, measures = {}, set()
    for f in (SM / "definition" / "tables").glob("*.tmdl"):
        txt = f.read_text(encoding="utf-8")
        tname = re.search(r"^table (\S+)", txt, re.M).group(1)
        cols.setdefault(tname, set())
        for mo in re.finditer(r"^\tcolumn ([^\s=]+)", txt, re.M):
            cols[tname].add(mo.group(1))
        # Desktop only quotes names that need it: measure 'Margin %' but
        # measure Orders — accept both serializations.
        for mo in re.finditer(r"^\tmeasure (?:'([^']+)'|([^\s=]+))", txt, re.M):
            measures.add(mo.group(1) or mo.group(2))
    return cols, measures


def _visual_files():
    return list((RPT / "definition" / "pages").glob("**/visual.json"))


def test_pbip_and_core_files_exist():
    assert (PBIP / "GLReconciliationDashboard.pbip").exists()
    assert (RPT / "definition" / "report.json").exists()
    assert (SM / "definition" / "model.tmdl").exists()


def test_every_visual_field_resolves(model):
    cols, measures = model
    unresolved = []
    for vf in _visual_files():
        v = json.loads(vf.read_text(encoding="utf-8"))
        qs = v.get("visual", {}).get("query", {}).get("queryState", {})
        for _, body in qs.items():
            for proj in body["projections"]:
                fld = proj["field"]
                if "Measure" in fld:
                    if fld["Measure"]["Property"] not in measures:
                        unresolved.append((vf.parent.name, "measure",
                                           fld["Measure"]["Property"]))
                else:
                    ent = fld["Column"]["Expression"]["SourceRef"]["Entity"]
                    prop = fld["Column"]["Property"]
                    if prop not in cols.get(ent, set()):
                        unresolved.append((vf.parent.name, f"column {ent}", prop))
    assert not unresolved, f"unresolved field references: {unresolved}"


def test_every_model_table_referenced_in_model_tmdl():
    model_txt = (SM / "definition" / "model.tmdl").read_text(encoding="utf-8")
    refs = set(re.findall(r"^ref table (\S+)", model_txt, re.M))
    files = {f.stem for f in (SM / "definition" / "tables").glob("*.tmdl")}
    assert files == refs, f"tables vs refs mismatch: {files ^ refs}"


def test_page_order_matches_page_folders():
    pages_dir = RPT / "definition" / "pages"
    order = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))["pageOrder"]
    folders = {p.name for p in pages_dir.iterdir() if p.is_dir()}
    assert set(order) == folders
    assert len(order) == len(set(order))


def test_model_tables_load_from_committed_outputs(model):
    """Every CSV path referenced by an M partition must exist in the repo, so
    a fresh clone + pipeline run can refresh the dashboard."""
    missing = []
    for f in (SM / "definition" / "tables").glob("*.tmdl"):
        for mo in re.finditer(r'DataPath & "\\\\([^"]+)"', f.read_text(encoding="utf-8")):
            rel = mo.group(1).replace("\\\\", "/").replace("\\", "/")
            if not (ROOT / rel).exists():
                missing.append((f.stem, rel))
    assert not missing, f"M partitions point at missing files: {missing}"
