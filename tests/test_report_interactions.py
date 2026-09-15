"""The interaction layer a report adds on top of its charts: SVG measures drawn
as images, button slicers that switch a measure, and a filter panel that
bookmarks open and close. Every one of these fails without an error, so each
rule below reproduces a defect met while building it.

  * An SVG whose text contains "%" renders every fill BLACK unless "%" is
    encoded before "#". The data URI stops decoding, so "%23" colours stay
    encoded while the shapes and text still draw.
  * An image visual bound to a measure without dataCategory ImageUrl renders
    blank.
  * A bookmark or button naming a page, visual or bookmark that no longer
    exists does nothing when clicked.
  * A panel toggled by bookmarks that also capture data resets the reader's
    filters every time it opens.
  * Slicers moved into a hidden panel hide the filter state, unless something
    still on the page says what is filtered.
  * A chart switched by a metric button has to say which metric it is showing.

Pages, bookmarks, theme and model are read off disk, so this file drops into
any PBIP repo unchanged.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "powerbi" / "pbip"
SM = next(PBIP.glob("*.SemanticModel")) / "definition"
RPT = next(PBIP.glob("*.Report"))
DEF = RPT / "definition"
PAGES = DEF / "pages"
BOOKMARKS = DEF / "bookmarks"

SLICER_TYPES = {"slicer", "listSlicer", "advancedSlicerVisual"}

# A measure runs to the next declaration, so its block includes its own
# properties - which is where dataCategory lives.
MEASURE_BLOCK = re.compile(
    r"^\tmeasure\s+(?:'([^']+)'|([A-Za-z_][\w ]*?))\s*=(.*?)"
    r"(?=^\t(?:measure|column|partition|annotation)\s|^\t///|\Z)",
    re.M | re.S,
)
ENCODED = re.compile(
    r'SUBSTITUTE\(\s*SUBSTITUTE\(\s*[^,]+,\s*"%"\s*,\s*"%25"\s*\)\s*,\s*"#"\s*,\s*"%23"\s*\)'
)


def _measures():
    out = {}
    for f in sorted((SM / "tables").glob("*.tmdl")):
        for quoted, bare, block in MEASURE_BLOCK.findall(f.read_text(encoding="utf-8")):
            out[(quoted or bare).strip()] = block
    return out


MEASURES = _measures()
IMAGE_MEASURES = {
    n: b for n, b in MEASURES.items() if re.search(r"^\t\tdataCategory:\s*ImageUrl\s*$", b, re.M)
}
SVG_MEASURES = {n: b for n, b in IMAGE_MEASURES.items() if "data:image/svg+xml" in b}


def _page_visuals():
    out = {}
    for vf in sorted(PAGES.glob("*/visuals/*/visual.json")):
        doc = json.loads(vf.read_text(encoding="utf-8"))
        out.setdefault(vf.parent.parent.parent.name, {})[doc["name"]] = doc
    return out


PAGE_VISUALS = _page_visuals()


def _bookmarks():
    out = {}
    for f in sorted(BOOKMARKS.glob("*.bookmark.json")) if BOOKMARKS.is_dir() else []:
        doc = json.loads(f.read_text(encoding="utf-8"))
        out[doc["name"]] = doc
    return out


BOOKMARK_DOCS = _bookmarks()


def _refs(doc):
    """Every measure a visual names - wells, titles, image sources, fills."""
    return set(re.findall(
        r'"Measure":\s*\{\s*"Expression":\s*\{\s*"SourceRef":\s*\{[^}]*\}\s*\},'
        r'\s*"Property":\s*"([^"]+)"',
        json.dumps(doc),
    ))


def _calls(name, seen=None):
    """A measure and every measure it calls, transitively."""
    seen = set() if seen is None else seen
    if name in seen or name not in MEASURES:
        return seen
    seen.add(name)
    for ref in re.findall(r"\[([^\]]+)\]", MEASURES[name]):
        _calls(ref, seen)
    return seen


def _theme_colours():
    report = json.loads((DEF / "report.json").read_text(encoding="utf-8"))
    name = report["themeCollection"]["customTheme"]["name"]
    path = next(
        (item["path"] for pkg in report.get("resourcePackages", [])
         if pkg.get("name") == "RegisteredResources"
         for item in pkg.get("items", []) if item.get("name") == name),
        name,
    )
    theme = json.loads((RPT / "StaticResources" / "RegisteredResources" / path).read_text(encoding="utf-8"))
    colours = {c.upper() for c in theme.get("dataColors", [])}
    colours |= {v.upper() for v in theme.values() if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
    colours |= {tc["color"].upper() for tc in theme.get("textClasses", {}).values() if "color" in tc}
    return colours | {"#FFFFFF"}


def _links():
    """(page, visual, action type, bookmark, destination page) for every button."""
    for page, docs in PAGE_VISUALS.items():
        for name, doc in docs.items():
            vco = doc.get("visual", {}).get("visualContainerObjects", {})
            for entry in vco.get("visualLink", []):
                props = entry.get("properties", {})

                def value(key):
                    return props.get(key, {}).get("expr", {}).get("Literal", {}).get("Value", "").strip("'")

                yield page, name, value("type"), value("bookmark"), value("navigationSection")


def _hidden_panels():
    for page, docs in PAGE_VISUALS.items():
        for name, doc in docs.items():
            if "visualGroup" in doc and doc.get("isHidden"):
                yield page, name


def _parameter_tables():
    """Calculated DATATABLE tables that no relationship touches: switches, not filters."""
    rel_file = SM / "relationships.tmdl"
    rel = rel_file.read_text(encoding="utf-8") if rel_file.exists() else ""
    related = {a or b for a, b in re.findall(r"(?:from|to)Column:\s*(?:'([^']+)'|([^.\s]+))\.", rel)}
    out = set()
    for f in (SM / "tables").glob("*.tmdl"):
        text = f.read_text(encoding="utf-8")
        if f.stem not in related and re.search(r"^\tpartition .+ = calculated", text, re.M) and "DATATABLE(" in text:
            out.add(f.stem)
    return out


def test_the_report_and_model_were_actually_read():
    """Every rule below passes vacuously if the traversal finds nothing."""
    assert MEASURES, "no measures parsed out of the model"
    assert PAGE_VISUALS, "no visuals found"


def test_svg_measures_encode_percent_before_hash():
    if not SVG_MEASURES:
        pytest.skip("this model draws no SVG")
    wrong = sorted(n for n, b in SVG_MEASURES.items() if not ENCODED.search(b))
    assert not wrong, (
        'these SVG measures do not encode "%" as "%25" before "#" as "%23"; any '
        f"percentage in their text will turn every fill black: {wrong}"
    )


def test_svg_measures_only_draw_theme_colours():
    if not SVG_MEASURES:
        pytest.skip("this model draws no SVG")
    allowed = _theme_colours()
    strays = sorted({
        (n, h) for n, b in SVG_MEASURES.items()
        for h in re.findall(r"#[0-9A-Fa-f]{6}\b", b) if h.upper() not in allowed
    })
    assert not strays, f"SVG measures draw colours the theme does not define: {strays}"


def test_image_visuals_bind_image_url_measures():
    wrong = []
    for page, docs in PAGE_VISUALS.items():
        for name, doc in docs.items():
            visual = doc.get("visual", {})
            if visual.get("visualType") != "image":
                continue
            for measure in _refs(visual.get("objects", {})):
                if measure not in IMAGE_MEASURES:
                    wrong.append((page, name, measure))
    assert not wrong, f"image visuals bound to measures without dataCategory ImageUrl render blank: {wrong}"


def test_bookmark_list_matches_the_bookmark_files():
    if not BOOKMARKS.is_dir():
        pytest.skip("no bookmarks")
    meta = json.loads((BOOKMARKS / "bookmarks.json").read_text(encoding="utf-8"))
    listed = []
    for item in meta["items"]:
        listed.extend(item["children"] if "children" in item else [item["name"]])
    assert sorted(listed) == sorted(BOOKMARK_DOCS)


def test_bookmarks_point_at_pages_and_visuals_that_exist():
    if not BOOKMARK_DOCS:
        pytest.skip("no bookmarks")
    wrong = []
    for name, bm in BOOKMARK_DOCS.items():
        state = bm["explorationState"]
        page = state["activeSection"]
        if page not in PAGE_VISUALS:
            wrong.append(f"{name}: page {page} does not exist")
            continue
        for target in bm.get("options", {}).get("targetVisualNames", []):
            if target not in PAGE_VISUALS[page]:
                wrong.append(f"{name}: target {target} is not on {page}")
        for section, section_state in state.get("sections", {}).items():
            if section not in PAGE_VISUALS:
                wrong.append(f"{name}: section {section} does not exist")
                continue
            for kind in ("visualContainers", "visualContainerGroups"):
                for visual in section_state.get(kind, {}):
                    if visual not in PAGE_VISUALS[section]:
                        wrong.append(f"{name}: {visual} is not on {section}")
    assert not wrong, "bookmarks that would do nothing:\n  " + "\n  ".join(wrong)


def test_buttons_link_to_bookmarks_and_pages_that_exist():
    wrong = []
    for page, name, kind, bookmark, section in _links():
        if kind == "Bookmark" and bookmark not in BOOKMARK_DOCS:
            wrong.append(f"{page}/{name} opens missing bookmark {bookmark!r}")
        if kind == "PageNavigation" and section not in PAGE_VISUALS:
            wrong.append(f"{page}/{name} navigates to missing page {section!r}")
    assert not wrong, "buttons that would do nothing:\n  " + "\n  ".join(wrong)


def test_hidden_panels_open_and_close_without_touching_filters():
    panels = list(_hidden_panels())
    if not panels:
        pytest.skip("no hidden panels")
    wrong = []
    for page, group in panels:
        opens, closes = [], []
        for name, bm in BOOKMARK_DOCS.items():
            groups = bm["explorationState"].get("sections", {}).get(page, {}).get("visualContainerGroups", {})
            if group not in groups:
                continue
            if not bm.get("options", {}).get("suppressData"):
                wrong.append(f"{name} toggles {group} but captures data, so it resets the page's filters")
            (closes if groups[group].get("isHidden") else opens).append(name)
        linked = {b for p, _n, kind, b, _s in _links() if p == page and kind == "Bookmark"}
        if not set(opens) & linked:
            wrong.append(f"{page}/{group}: no button opens it")
        if not set(closes) & linked:
            wrong.append(f"{page}/{group}: no button closes it")
    assert not wrong, "\n  ".join(wrong)


def test_panel_children_fit_inside_their_panel():
    wrong = []
    for page, docs in PAGE_VISUALS.items():
        for name, doc in docs.items():
            parent = doc.get("parentGroupName")
            if not parent:
                continue
            box, pos = docs[parent]["position"], doc["position"]
            if (pos["x"] < 0 or pos["y"] < 0 or pos["x"] + pos["width"] > box["width"]
                    or pos["y"] + pos["height"] > box["height"]):
                wrong.append(f"{page}/{name} spills out of {parent}")
    assert not wrong, "\n  ".join(wrong)


def test_filters_hidden_in_a_panel_are_still_stated_on_the_page():
    """Hiding slicers behind a button hides what is filtered. Something that
    stays on the page must read each of those slicer's columns with ISFILTERED."""
    wrong, checked = [], 0
    for page, group in _hidden_panels():
        docs = PAGE_VISUALS[page]
        held = set()
        for doc in docs.values():
            visual = doc.get("visual", {})
            if doc.get("parentGroupName") == group and visual.get("visualType") in SLICER_TYPES:
                for proj in visual["query"]["queryState"]["Values"]["projections"]:
                    col = proj["field"]["Column"]
                    held.add((col["Expression"]["SourceRef"]["Entity"], col["Property"]))
        shown = set()
        for doc in docs.values():
            if doc.get("parentGroupName") != group:
                for measure in _refs(doc):
                    shown |= _calls(measure)
        dax = " ".join(MEASURES[m] for m in shown)
        for table, column in sorted(held):
            checked += 1
            if not re.search(rf"ISFILTERED\(\s*'?{re.escape(table)}'?\[{re.escape(column)}\]\s*\)", dax):
                wrong.append(f"{page}: nothing outside {group} says whether {table}[{column}] is filtered")
    if not checked:
        pytest.skip("no slicers live in a hidden panel")
    assert not wrong, "\n  ".join(wrong)


def test_charts_switched_by_a_metric_button_say_which_metric_they_show():
    params = _parameter_tables()
    if not params:
        pytest.skip("no parameter tables")
    wrong, checked = [], 0
    for page, docs in PAGE_VISUALS.items():
        for name, doc in docs.items():
            visual = doc.get("visual", {})
            if visual.get("visualType") in SLICER_TYPES or "query" not in visual:
                continue
            bound = set()
            for role in visual["query"].get("queryState", {}).values():
                for proj in role.get("projections", []):
                    if "Measure" in proj["field"]:
                        bound |= _calls(proj["field"]["Measure"]["Property"])
            if not any(re.search(rf"'{re.escape(t)}'\[|\b{re.escape(t)}\[", MEASURES[m]) for m in bound for t in params):
                continue
            checked += 1
            title = (visual.get("visualContainerObjects", {}).get("title") or [{}])[0]
            if "Measure" not in json.dumps(title.get("properties", {}).get("text", {})):
                wrong.append(f"{page}/{name} changes with a metric switch but its title is fixed text")
    if not checked:
        pytest.skip("no visual reads a parameter table")
    assert not wrong, "\n  ".join(wrong)
