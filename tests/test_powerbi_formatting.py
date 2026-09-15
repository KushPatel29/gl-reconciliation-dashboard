"""Power BI report integrity: the hand-authored PBIR report and TMDL model must
stay in sync, so the .pbip always opens and every visual binds to a real field.

Beyond field resolution, these pin the things that fail SILENTLY in Power BI -
each one reproduces a defect found while building this report:

  * a formatting property whose literal is malformed is DROPPED on the next
    save with no error. A font size written as "11" vanished; "11D" survives.
  * a conditional fill without a wildcard selector is accepted, validates, and
    colours nothing.
  * a colour measure that returns a hex the theme does not define makes the
    report and the theme disagree, which no visual check catches.
  * two visuals can occupy the same rectangle and the one underneath is simply
    invisible.
  * a visual inside a group is positioned relative to the group, not the page;
    reading it as a page coordinate is how a whole filter panel first rendered
    off the canvas.
"""
import csv
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "powerbi" / "pbip"
SM = PBIP / "GLReconciliationDashboard.SemanticModel"
RPT = PBIP / "GLReconciliationDashboard.Report"
THEME = RPT / "StaticResources" / "RegisteredResources" / "MeridianNocturne.json"
PAGES = RPT / "definition" / "pages"

MAX_BUTTON_TILES = 10


@pytest.fixture(scope="module")
def model():
    """Parse TMDL into {table: set(columns)} and set(measures)."""
    cols, measures = {}, set()
    for f in (SM / "definition" / "tables").glob("*.tmdl"):
        txt = f.read_text(encoding="utf-8")
        tname = re.search(r"^table '?([^'\n]+?)'?$", txt, re.M).group(1)
        cols.setdefault(tname, set())
        for mo in re.finditer(r"^\tcolumn (?:'([^']+)'|([^\s=]+))", txt, re.M):
            cols[tname].add(mo.group(1) or mo.group(2))
        for mo in re.finditer(r"^\tmeasure (?:'([^']+)'|([^\s=]+))", txt, re.M):
            measures.add(mo.group(1) or mo.group(2))
    return cols, measures


@pytest.fixture(scope="module")
def visuals():
    """Every rendered visual. A visualGroup container draws nothing itself and
    has no `visual` block; its children are listed here in their own right."""
    out = []
    for vf in sorted(PAGES.glob("*/visuals/*/visual.json")):
        doc = json.loads(vf.read_text(encoding="utf-8"))
        if "visual" in doc:
            out.append((vf, doc))
    return out


def placed():
    """page -> {name: (x, y, width, height, hidden, parent, is_group)}, with
    group children moved into page coordinates."""
    docs = {}
    for vf in sorted(PAGES.glob("*/visuals/*/visual.json")):
        doc = json.loads(vf.read_text(encoding="utf-8"))
        docs.setdefault(vf.parent.parent.parent.name, {})[doc["name"]] = doc
    out = {}
    for page, by_name in docs.items():
        rects = {}
        for name, doc in by_name.items():
            pos = doc["position"]
            x, y, hidden = pos["x"], pos["y"], bool(doc.get("isHidden"))
            parent = doc.get("parentGroupName")
            if parent:
                group = by_name[parent]
                x += group["position"]["x"]
                y += group["position"]["y"]
                hidden = hidden or bool(group.get("isHidden"))
            rects[name] = (x, y, pos["width"], pos["height"], hidden, parent, "visualGroup" in doc)
        out[page] = rects
    return out


def _measure_refs(doc):
    """Every measure named anywhere in a visual - wells AND formatting."""
    return set(re.findall(
        r'"Measure":\s*\{\s*"Expression":\s*\{\s*"SourceRef":\s*\{[^}]*\}\s*\},'
        r'\s*"Property":\s*"([^"]+)"',
        json.dumps(doc),
    ))


def distinct_values(table, column):
    """The values a slicer on table[column] can offer, read from the data the
    model actually loads: the DATATABLE of a calculated table, otherwise the
    source CSVs its partition reads. None when neither can be read."""
    text = (SM / "definition" / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
    if re.search(r"^\tpartition .+ = calculated", text, re.M) and "DATATABLE(" in text:
        args = text[text.index("DATATABLE(") + len("DATATABLE("):]
        first_column = re.match(r'\s*"([^"]+)"', args).group(1)
        if first_column != column:
            return None
        return set(re.findall(r'\{\s*"([^"]*)"', args.split("{", 1)[1]))
    for rel in re.findall(r'File\.Contents\(DataPath & "([^"]+\.csv)"\)', text):
        path = ROOT / rel.strip("\\").replace("\\", "/")
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if column in (reader.fieldnames or []):
                return {row[column] for row in reader}
    return None


def test_pbip_and_core_files_exist():
    assert (PBIP / "GLReconciliationDashboard.pbip").exists()
    assert (RPT / "definition" / "report.json").exists()
    assert (SM / "definition" / "model.tmdl").exists()


def test_every_visual_field_resolves(model, visuals):
    cols, measures = model
    unresolved = []
    for vf, doc in visuals:
        v = doc["visual"]
        for _role, body in v.get("query", {}).get("queryState", {}).items():
            for proj in body["projections"]:
                fld = proj["field"]
                if "Measure" in fld:
                    if fld["Measure"]["Property"] not in measures:
                        unresolved.append((vf.parent.name, "measure",
                                           fld["Measure"]["Property"]))
                elif "Column" in fld:
                    ent = fld["Column"]["Expression"]["SourceRef"]["Entity"]
                    prop = fld["Column"]["Property"]
                    if prop not in cols.get(ent, set()):
                        unresolved.append((vf.parent.name, f"column {ent}", prop))
    assert not unresolved, f"unresolved field references: {unresolved}"


def test_every_formatting_measure_resolves(model, visuals):
    """Card subtitles, measure-bound titles, SVG image sources and conditional
    fills bind to measures too, and a broken one there fails at render time
    rather than at open time."""
    _cols, measures = model
    missing = []
    for vf, doc in visuals:
        for name in _measure_refs(doc):
            if name not in measures:
                missing.append((vf.parent.name, name))
    assert not missing, f"formatting expressions name missing measures: {missing}"


def test_numeric_formatting_literals_carry_a_type_suffix(visuals):
    """A bare numeric literal is silently discarded by Desktop on save."""
    bad = []
    for vf, doc in visuals:
        for m in re.finditer(r'"Literal":\s*\{\s*"Value":\s*"([^"]*)"', json.dumps(doc)):
            val = m.group(1)
            if re.fullmatch(r"-?\d+(\.\d+)?", val):
                bad.append((vf.parent.name, val))
    assert not bad, (
        "numeric literals must be suffixed (11 -> 11D) or Power BI drops them: "
        f"{bad}"
    )


def test_conditional_fills_use_the_wildcard_selector(visuals):
    """On a chart with a category axis, a measure-driven dataPoint.fill without
    this selector validates and colours nothing. A gauge has one data point and
    needs no selector, so the rule applies only where there are points to match.
    A literal colour scoped to one category or series is not conditional - it is
    how the report's colour map is applied - and needs no wildcard."""
    wrong = []
    for vf, doc in visuals:
        v = doc["visual"]
        if "Category" not in v.get("query", {}).get("queryState", {}):
            continue
        for entry in v.get("objects", {}).get("dataPoint", []):
            value = entry.get("properties", {}).get("fill")
            if value is None or "Measure" not in json.dumps(value):
                continue
            sel = entry.get("selector", {}).get("data", [])
            if not any("dataViewWildcard" in s for s in sel):
                wrong.append(vf.parent.name)
    assert not wrong, f"conditional fills missing the wildcard selector: {wrong}"


def test_colour_measures_only_return_theme_colours():
    """The report and the theme cannot drift apart, because every hex a colour
    measure can return is one the theme defines."""
    theme = json.loads(THEME.read_text(encoding="utf-8"))
    allowed = {c.upper() for c in theme["dataColors"]}
    allowed |= {theme[k].upper() for k in ("good", "neutral", "bad", "maximum",
                                           "center", "minimum", "null")}
    allowed |= {"#454A82", "#FFFFFF"}
    text = (SM / "definition" / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    strays = set()
    for block in re.split(r"(?=^\tmeasure )", text, flags=re.M):
        if not re.match(r"^\tmeasure '[^']*Colour'", block):
            continue
        for hexv in re.findall(r'"(#[0-9A-Fa-f]{6})"', block):
            if hexv.upper() not in allowed:
                strays.add(hexv)
    assert not strays, f"colour measures return hexes the theme does not define: {strays}"


def test_theme_is_registered_and_present():
    report = json.loads((RPT / "definition" / "report.json").read_text(encoding="utf-8"))
    custom = report["themeCollection"]["customTheme"]["name"]
    assert custom == "MeridianNocturne.json", f"custom theme is {custom}"
    assert THEME.exists(), "the registered theme file is missing from the package"
    registered = {
        item["name"]
        for pkg in report.get("resourcePackages", [])
        if pkg.get("name") == "RegisteredResources"
        for item in pkg.get("items", [])
    }
    assert custom in registered, "theme is selected but not in resourcePackages"


def test_no_two_visuals_overlap():
    """A visual placed on top of another simply hides it, with no warning.
    A panel hidden until a bookmark opens it floats over the page on purpose;
    test_report_interactions checks it opens, closes and fits itself."""
    clashes = []
    for page, rects in placed().items():
        items = [(n, r) for n, r in rects.items() if not r[4] and r[5] is None]
        for i, (an, a) in enumerate(items):
            for bn, b in items[i + 1:]:
                if (a[0] < b[0] + b[2] and a[0] + a[2] > b[0]
                        and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]):
                    clashes.append((page, an, bn))
    assert not clashes, f"overlapping visuals: {clashes}"


def test_visuals_stay_inside_the_page():
    sizes = {
        p.name: json.loads((p / "page.json").read_text(encoding="utf-8"))
        for p in PAGES.iterdir() if p.is_dir()
    }
    outside = []
    for page, rects in placed().items():
        meta = sizes[page]
        for name, (x, y, w, h, _hidden, _parent, _group) in rects.items():
            if x < 0 or y < 0 or x + w > meta["width"] or y + h > meta["height"]:
                outside.append((page, name))
    assert not outside, f"visuals outside the canvas: {outside}"


def test_slicers_are_dropdowns_or_short_button_rows(visuals):
    """Vertical list slicers were eating about a quarter of every canvas, so a
    classic slicer is a dropdown. A button slicer is the exception the design
    uses on purpose - one row of tiles - and it only works while the field has
    few values: past ten the tiles shrink until their labels cannot be read.
    The count comes from the data the model loads, not from a list kept here."""
    wrong = []
    for vf, doc in visuals:
        v = doc["visual"]
        kind = v.get("visualType")
        if kind == "slicer":
            modes = [
                e.get("properties", {}).get("mode", {}).get("expr", {})
                 .get("Literal", {}).get("Value")
                for e in v.get("objects", {}).get("data", [])
            ]
            if "'Dropdown'" not in modes:
                wrong.append((vf.parent.name, "not a dropdown"))
        elif kind == "listSlicer":
            wrong.append((vf.parent.name, "list slicer"))
        elif kind == "advancedSlicerVisual":
            field = v["query"]["queryState"]["Values"]["projections"][0]["field"]["Column"]
            table, column = field["Expression"]["SourceRef"]["Entity"], field["Property"]
            values = distinct_values(table, column)
            if values is None:
                wrong.append((vf.parent.name, f"cannot read the values of {table}[{column}]"))
            elif len(values) > MAX_BUTTON_TILES:
                wrong.append((vf.parent.name, f"{len(values)} tiles for {table}[{column}]"))
    assert not wrong, f"slicers that will not fit their space: {wrong}"


def test_currency_measures_carry_a_currency_symbol():
    """A dollar figure beside a rate and a day count is ambiguous without it."""
    text = (SM / "definition" / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    bare = []
    for block in re.split(r"(?=^\tmeasure )", text, flags=re.M):
        m = re.match(r"^\tmeasure '([^']+)'", block)
        if not m or "$" not in m.group(1):
            continue
        fs = re.search(r"^\t\tformatString: (.+)$", block, re.M)
        if fs and "$" not in fs.group(1):
            bare.append((m.group(1), fs.group(1)))
    assert not bare, f"$-named measures without a currency format: {bare}"


def test_page_order_matches_page_folders():
    order = json.loads((PAGES / "pages.json").read_text(encoding="utf-8"))["pageOrder"]
    folders = {p.name for p in PAGES.iterdir() if p.is_dir()}
    assert set(order) == folders
    assert len(order) == len(set(order))


def test_every_model_table_referenced_in_model_tmdl():
    model_txt = (SM / "definition" / "model.tmdl").read_text(encoding="utf-8")
    refs = {m.group(1) or m.group(2)
            for m in re.finditer(r"^ref table (?:'([^']+)'|(\S+))", model_txt, re.M)}
    files = {f.stem for f in (SM / "definition" / "tables").glob("*.tmdl")}
    assert files == refs, f"tables vs refs mismatch: {files ^ refs}"


def test_conditional_fills_are_not_on_series_charts(visuals):
    """A single unscoped fill paints every data point one colour, which erases
    the legend on a chart that has a Series. A fill scoped to one series value
    keeps the legend - that is how the colour map gives each status its hue."""
    wrong = []
    for vf, doc in visuals:
        v = doc["visual"]
        unscoped = any("fill" in e.get("properties", {}) and not e.get("selector")
                       for e in v.get("objects", {}).get("dataPoint", []))
        if unscoped and "Series" in v.get("query", {}).get("queryState", {}):
            wrong.append(vf.parent.name)
    assert not wrong, f"unscoped fill on a series chart: {wrong}"


def test_every_colour_measure_is_actually_used(visuals):
    """A colour measure nobody reads is dead weight in a published model. It is
    read when a visual binds it, or when another measure - an SVG status chip,
    say - calls it."""
    text = (SM / "definition" / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\tmeasure '([^']*Colour)'", text, re.M))
    used = set()
    for _vf, doc in visuals:
        used |= _measure_refs(doc)
    blocks = re.split(r"(?=^\tmeasure )", text, flags=re.M)
    for name in declared:
        if any(f"[{name}]" in b for b in blocks if not b.startswith(f"\tmeasure '{name}'")):
            used.add(name)
    orphans = sorted(declared - used)
    assert not orphans, f"colour measures nothing reads: {orphans}"


def test_the_report_opens_on_its_first_page():
    """A saved Desktop session stamps whichever page was on screen, so a report
    can silently start opening on page 4. This pins it to page 1."""
    meta = json.loads((PAGES / "pages.json").read_text(encoding="utf-8"))
    assert meta["activePageName"] == meta["pageOrder"][0], (
        f"report opens on {meta['activePageName']}, "
        f"but page 1 is {meta['pageOrder'][0]}"
    )


# DAX reserves the four KPI property names, so none of them can name a VAR.
# Power BI does not reject the model on load: the measure carries a
# SemanticError, every measure that depends on it inherits a DependencyError,
# and the visuals bound to them render "Something's wrong with one or more
# fields" at runtime. Verified against the engine - VAR Gap parses, VAR
# Variance does not.
DAX_KPI_KEYWORDS = {"goal", "status", "trend", "variance"}


def test_no_var_uses_a_reserved_dax_keyword():
    offenders = []
    for f in sorted((SM / "definition" / "tables").glob("*.tmdl")):
        for name in re.findall(r"\bVAR\s+([A-Za-z_]\w*)", f.read_text(encoding="utf-8")):
            if name.lower() in DAX_KPI_KEYWORDS:
                offenders.append((f.name, name))
    assert not offenders, (
        "these VAR names are reserved DAX KPI keywords and make the measure "
        f"fail silently: {offenders}"
    )


def test_the_watch_band_sits_between_on_target_and_off_target():
    """A three-state verdict only means anything if Watch is the NEAR miss.
    Generated with the comparison flipped, it labelled everything far from
    target as Watch and only near-misses as Off target - so a rate 14.6 points
    short of its goal rendered amber instead of red."""
    text = (SM / "definition" / "tables" / "_Measures.tmdl").read_text(encoding="utf-8")
    wrong = []
    for block in re.split(r"(?=^	measure )", text, flags=re.M):
        m = re.match(r"^	measure '([^']+ Status)'", block)
        if not m or "Watch" not in block:
            continue
        on = re.search(r"Actual (>=|<=) Target, \"On target\"", block)
        watch = re.search(r"Actual (>=|<=|>|<) Target [-+] [\d.]+, \"Watch\"", block)
        if not (on and watch):
            continue
        if on.group(1) != watch.group(1):
            wrong.append((m.group(1), on.group(1), watch.group(1)))
    assert not wrong, (
        "Watch must use the same comparison as On target, or the band is "
        f"inverted: {wrong}"
    )
