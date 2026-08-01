"""
Generate the Tableau workbook for the GL close scorecard.

The workbook is *generated*, not clicked together, for the same reason the
Power BI report is: a hand-clicked .twbx is a binary blob nobody can review,
and its data connections pin absolute paths from whoever last saved it. This
emits a plain-XML .twb next to the extracts, with relative connections, so it
opens on any machine that has the repo checked out.

Run after the reconciliation engine and the extract prep:

    python engine/run_reconciliation.py
    python tableau/prepare_tableau_data.py
    python tableau/build_workbook.py

Produces `tableau/GLCloseScorecard.twb` (4 sheets + 3 KPI tiles + dashboard).
`tests/test_tableau_integrity.py` asserts every field the workbook references
actually exists in the extracts.
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
TWB = HERE / "GLCloseScorecard.twb"

CONTROLS_CSV = "control_totals_tableau.csv"
EXCEPTIONS_CSV = "exceptions_tableau.csv"

CONTROLS_DS = "federated.controltotals"
CONTROLS_CONN = "textscan.controltotals"
EXCEPTIONS_DS = "federated.exceptions"
EXCEPTIONS_CONN = "textscan.exceptions"

# Meridian palette — same brand as the Power BI reports and the portfolio site.
NAVY = "#12436d"
TEAL = "#28a197"
ORANGE = "#f46a25"
PLUM = "#801650"
RED = "#c0392b"

# remote-type codes Tableau writes for text-file columns
REMOTE_TYPE = {"string": "130", "real": "5", "integer": "20"}
AGGREGATION = {"string": "Count", "real": "Sum", "integer": "Sum"}

CONTROL_COLUMNS = [
    ("account_code", "string"),
    ("account_name", "string"),
    ("account_type", "string"),
    ("statement", "string"),
    ("period", "string"),
    ("erp_total", "real"),
    ("subledger_total", "real"),
    ("variance_amount", "real"),
    ("variance_pct", "real"),
    ("out_of_tolerance", "integer"),
]

EXCEPTION_COLUMNS = [
    ("transaction_id", "string"),
    ("account_code", "string"),
    ("account_name", "string"),
    ("account_type", "string"),
    ("period", "string"),
    ("exception_type", "string"),
    ("erp_amount", "real"),
    ("variance_amount", "real"),
    ("impact_amount", "real"),
]


def esc(value):
    """Escape a string for use inside a single-quoted XML attribute."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def periods():
    with open(HERE / CONTROLS_CSV, encoding="utf-8") as f:
        return sorted({r["period"] for r in csv.DictReader(f)})


# ---------------------------------------------------------------- data sources


def metadata_records(csv_name, columns):
    out = []
    for ordinal, (name, dtype) in enumerate(columns):
        out.append(
            f"""        <metadata-record class='column'>
          <remote-name>{name}</remote-name>
          <remote-type>{REMOTE_TYPE[dtype]}</remote-type>
          <local-name>[{name}]</local-name>
          <parent-name>[{csv_name}]</parent-name>
          <remote-alias>{name}</remote-alias>
          <ordinal>{ordinal}</ordinal>
          <local-type>{dtype}</local-type>
          <aggregation>{AGGREGATION[dtype]}</aggregation>
          <contains-null>true</contains-null>
        </metadata-record>"""
        )
    return "\n".join(out)


def base_columns(columns):
    out = []
    for name, dtype in columns:
        role = "dimension" if dtype == "string" else "measure"
        ctype = "nominal" if dtype == "string" else "quantitative"
        fmt = "" if dtype == "string" else " default-format='#,##0'"
        out.append(
            f"      <column datatype='{dtype}'{fmt} name='[{name}]' "
            f"role='{role}' type='{ctype}' />"
        )
    return "\n".join(out)


def calc_column(name, caption, dtype, role, formula):
    ctype = "nominal" if dtype == "string" else "quantitative"
    if dtype == "boolean":
        ctype = "ordinal"
    fmt = " default-format='#,##0'" if dtype == "real" else ""
    return (
        f"      <column caption='{esc(caption)}' datatype='{dtype}'{fmt} "
        f"name='[{name}]' role='{role}' type='{ctype}'>\n"
        f"        <calculation class='tableau' formula='{esc(formula)}' />\n"
        f"      </column>"
    )


CONTROL_CALCS = [
    ("Calculation_absvariance", "Abs Variance", "real", "measure",
     "ABS([variance_amount])"),
    ("Calculation_tolerance", "Tolerance Status", "string", "dimension",
     'IF [out_of_tolerance] = 1 THEN "Out of tolerance" '
     'ELSE "Within tolerance" END'),
    ("Calculation_ootrate", "Out of Tolerance Rate", "real", "measure",
     "SUM([out_of_tolerance]) / COUNT([out_of_tolerance])"),
    ("Calculation_ctlperiod", "Period Filter", "boolean", "dimension",
     '[Parameters].[Parameter 1] = "All" '
     'OR [period] = [Parameters].[Parameter 1]'),
]

EXCEPTION_CALCS = [
    ("Calculation_exccount", "Total Exceptions", "integer", "measure",
     "COUNT([transaction_id])"),
    ("Calculation_excperiod", "Period Filter", "boolean", "dimension",
     '[Parameters].[Parameter 1] = "All" '
     'OR [period] = [Parameters].[Parameter 1]'),
]


def datasource(name, caption, conn_name, csv_name, columns, calcs,
               style="", instances=()):
    calc_xml = "\n".join(calc_column(*c) for c in calcs)
    style_xml = f"\n      <style>\n{style}\n      </style>" if style else ""
    # A colour palette binds to a column-instance declared on the DATASOURCE.
    # Declaring the instance only inside a worksheet's datasource-dependencies
    # leaves the style rule's `field` unresolved and Tableau silently falls
    # back to its default categorical palette.
    inst_xml = "".join(
        f"\n      <column-instance column='[{col}]' derivation='None' "
        f"name='[{inst}]' pivot='key' type='nominal' />"
        for col, inst in instances
    )
    return f"""    <datasource caption='{esc(caption)}' inline='true' name='{name}' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='{esc(csv_name)}' name='{conn_name}'>
            <connection class='textscan' directory='.' filename='{esc(csv_name)}' password='' server='' />
          </named-connection>
        </named-connections>
        <relation connection='{conn_name}' name='{esc(csv_name)}' table='[{csv_name.replace(".csv", "#csv")}]' type='table' />
        <metadata-records>
{metadata_records(csv_name, columns)}
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
{base_columns(columns)}
{calc_xml}{inst_xml}
      <layout dim-ordering='alphabetic' dim-percentage='0.5' measure-ordering='alphabetic' measure-percentage='0.4' show-structure='true' />{style_xml}
    </datasource>"""


def parameters_datasource():
    members = "\n".join(
        f"          <member value='&quot;{p}&quot;' />" for p in ["All"] + periods()
    )
    return f"""    <datasource hasconnection='false' inline='true' name='Parameters' version='18.1'>
      <aliases enabled='yes' />
      <column caption='Period' datatype='string' name='[Parameter 1]' param-domain-type='list' role='measure' type='nominal' value='&quot;All&quot;'>
        <calculation class='tableau' formula='&quot;All&quot;' />
        <members>
{members}
        </members>
      </column>
    </datasource>"""


# ------------------------------------------------------------------ worksheets


def dep_column(name, dtype, role, formula=None, caption=None):
    ctype = "nominal" if dtype == "string" else "quantitative"
    if dtype == "boolean":
        ctype = "ordinal"
    cap = f"caption='{esc(caption)}' " if caption else ""
    if formula is None:
        return (
            f"          <column {cap}datatype='{dtype}' name='[{name}]' "
            f"role='{role}' type='{ctype}' />"
        )
    return (
        f"          <column {cap}datatype='{dtype}' name='[{name}]' "
        f"role='{role}' type='{ctype}'>\n"
        f"            <calculation class='tableau' formula='{esc(formula)}' />\n"
        f"          </column>"
    )


def dep_instance(column, derivation, name, ctype):
    return (
        f"          <column-instance column='[{column}]' derivation='{derivation}' "
        f"name='[{name}]' pivot='key' type='{ctype}' />"
    )


def period_filter_xml(ds, instance):
    """Boolean parameter-driven filter, kept to TRUE."""
    return f"""        <filter class='categorical' column='[{ds}].[{instance}]'>
          <groupfilter function='member' level='[{instance}]' member='true' />
        </filter>"""


def period_slice_xml(ds, instance):
    """Tableau's <slices> content model is (column+), as element text."""
    return f"""        <slices>
          <column>[{ds}].[{instance}]</column>
        </slices>"""


def sort_xml(ds, dimension, measure):
    return (f"        <sort class='computed' column='[{ds}].[{dimension}]' "
            f"direction='DESC' using='[{ds}].[{measure}]' />")


def worksheet(name, title, ds, ds_caption, deps, instances, rows, cols,
              mark, encodings="", filters="", sorts="", slices="", style=""):
    dep_xml = "\n".join(deps + instances)
    enc_xml = f"\n            <encodings>\n{encodings}\n            </encodings>" if encodings else ""
    style_xml = f"\n      <style>\n{style}\n      </style>" if style else "\n      <style />"
    # Order is fixed by Tableau's content model for <view>:
    # datasources, datasource-dependencies*, filter, sort, slices?, aggregation
    ordered = "\n".join(x for x in (filters, sorts, slices) if x)
    return f"""    <worksheet name='{esc(name)}'>
      <layout-options>
        <title>
          <formatted-text>
            <run fontname='Segoe UI Semibold' fontsize='11' bold='true'>{esc(title)}</run>
          </formatted-text>
        </title>
      </layout-options>
      <table>
        <view>
          <datasources>
            <datasource caption='{esc(ds_caption)}' name='{ds}' />
            <datasource name='Parameters' />
          </datasources>
          <datasource-dependencies datasource='{ds}'>
{dep_xml}
          </datasource-dependencies>
          <datasource-dependencies datasource='Parameters'>
            <column caption='Period' datatype='string' name='[Parameter 1]' param-domain-type='list' role='measure' type='nominal' value='&quot;All&quot;'>
              <calculation class='tableau' formula='&quot;All&quot;' />
            </column>
          </datasource-dependencies>
{ordered}
          <aggregation value='true' />
        </view>{style_xml}
        <panes>
          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='{mark}' />{enc_xml}
          </pane>
        </panes>
        <rows>{rows}</rows>
        <cols>{cols}</cols>
      </table>
    </worksheet>"""


def color_style(instance, mapping):
    """
    Note the asymmetry, which cost a debugging round: the <color> encoding
    inside <panes> is datasource-qualified, but the style rule's `field` is
    the BARE column-instance name. Qualifying it here silently does nothing
    and Tableau falls back to its default categorical palette.
    """
    maps = "\n".join(
        f"            <map to='{colour}'>\n"
        f"              <bucket>&quot;{value}&quot;</bucket>\n"
        f"            </map>"
        for value, colour in mapping
    )
    return f"""        <style-rule element='mark'>
          <encoding attr='color' field='[{instance}]' type='palette'>
{maps}
          </encoding>
        </style-rule>"""


CTL_PERIOD_INSTANCE = "none:Calculation_ctlperiod:ok"
EXC_PERIOD_INSTANCE = "none:Calculation_excperiod:ok"

CTL_PERIOD_DEP = dep_column(
    "Calculation_ctlperiod", "boolean", "dimension",
    '[Parameters].[Parameter 1] = "All" OR [period] = [Parameters].[Parameter 1]',
    "Period Filter",
)
EXC_PERIOD_DEP = dep_column(
    "Calculation_excperiod", "boolean", "dimension",
    '[Parameters].[Parameter 1] = "All" OR [period] = [Parameters].[Parameter 1]',
    "Period Filter",
)
CTL_PERIOD_INST = dep_instance(
    "Calculation_ctlperiod", "None", CTL_PERIOD_INSTANCE, "ordinal")
EXC_PERIOD_INST = dep_instance(
    "Calculation_excperiod", "None", EXC_PERIOD_INSTANCE, "ordinal")


CONTROLS_STYLE_MAP = [
    ("Out of tolerance", RED),
    ("Within tolerance", NAVY),
]
EXCEPTIONS_STYLE_MAP = [
    ("Missing in subledger", NAVY),
    ("Amount mismatch", ORANGE),
    ("Timing difference", TEAL),
    ("Duplicate posting", PLUM),
]


def build_worksheets():
    sheets = []

    # 1. Variance by account -------------------------------------------------
    sheets.append(worksheet(
        name="Variance by Account",
        title="Absolute variance by account ($)",
        ds=CONTROLS_DS, ds_caption="Control Totals",
        deps=[
            dep_column("account_name", "string", "dimension"),
            dep_column("variance_amount", "real", "measure"),
            dep_column("out_of_tolerance", "integer", "measure"),
            dep_column("period", "string", "dimension"),
            dep_column("Calculation_absvariance", "real", "measure",
                       "ABS([variance_amount])", "Abs Variance"),
            dep_column("Calculation_tolerance", "string", "dimension",
                       'IF [out_of_tolerance] = 1 THEN "Out of tolerance" '
                       'ELSE "Within tolerance" END', "Tolerance Status"),
            CTL_PERIOD_DEP,
        ],
        instances=[
            dep_instance("account_name", "None", "none:account_name:nk", "nominal"),
            dep_instance("Calculation_absvariance", "Sum",
                         "sum:Calculation_absvariance:qk", "quantitative"),
            dep_instance("Calculation_tolerance", "None",
                         "none:Calculation_tolerance:nk", "nominal"),
            CTL_PERIOD_INST,
        ],
        rows=f"[{CONTROLS_DS}].[none:account_name:nk]",
        cols=f"[{CONTROLS_DS}].[sum:Calculation_absvariance:qk]",
        mark="Bar",
        encodings=f"              <color column='[{CONTROLS_DS}].[none:Calculation_tolerance:nk]' />",
        filters=period_filter_xml(CONTROLS_DS, CTL_PERIOD_INSTANCE),
        sorts=sort_xml(CONTROLS_DS, "none:account_name:nk",
                       "sum:Calculation_absvariance:qk"),
        slices=period_slice_xml(CONTROLS_DS, CTL_PERIOD_INSTANCE),
    ))

    # 2. Exception impact by type -------------------------------------------
    sheets.append(worksheet(
        name="Impact by Exception Type",
        title="Exception impact by type ($)",
        ds=EXCEPTIONS_DS, ds_caption="Exceptions",
        deps=[
            dep_column("exception_type", "string", "dimension"),
            dep_column("impact_amount", "real", "measure"),
            dep_column("period", "string", "dimension"),
            EXC_PERIOD_DEP,
        ],
        instances=[
            dep_instance("exception_type", "None",
                         "none:exception_type:nk", "nominal"),
            dep_instance("impact_amount", "Sum",
                         "sum:impact_amount:qk", "quantitative"),
            EXC_PERIOD_INST,
        ],
        rows=f"[{EXCEPTIONS_DS}].[none:exception_type:nk]",
        cols=f"[{EXCEPTIONS_DS}].[sum:impact_amount:qk]",
        mark="Bar",
        encodings=f"              <color column='[{EXCEPTIONS_DS}].[none:exception_type:nk]' />",
        filters=period_filter_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
        sorts=sort_xml(EXCEPTIONS_DS, "none:exception_type:nk",
                       "sum:impact_amount:qk"),
        slices=period_slice_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
    ))

    # 3. Variance trend ------------------------------------------------------
    sheets.append(worksheet(
        name="Variance Trend",
        title="Absolute variance by period ($)",
        ds=CONTROLS_DS, ds_caption="Control Totals",
        deps=[
            dep_column("period", "string", "dimension"),
            dep_column("variance_amount", "real", "measure"),
            dep_column("Calculation_absvariance", "real", "measure",
                       "ABS([variance_amount])", "Abs Variance"),
        ],
        instances=[
            dep_instance("period", "None", "none:period:nk", "nominal"),
            dep_instance("Calculation_absvariance", "Sum",
                         "sum:Calculation_absvariance:qk", "quantitative"),
        ],
        rows=f"[{CONTROLS_DS}].[sum:Calculation_absvariance:qk]",
        cols=f"[{CONTROLS_DS}].[none:period:nk]",
        mark="Line",
    ))

    # 4. Impact matrix -------------------------------------------------------
    # A 230-row transaction register is the wrong visual for a fixed-height
    # dashboard zone: every worksheet window is set to fit-entire-view, so
    # Tableau squeezes all 230 rows into the zone and the text renders as an
    # unreadable grey band. Aggregating to account x exception type gives 10
    # rows by 4 columns, fits, and answers the better question — where is the
    # impact concentrated?
    sheets.append(worksheet(
        name="Impact Matrix",
        title="Impact by account and exception type ($)",
        ds=EXCEPTIONS_DS, ds_caption="Exceptions",
        deps=[
            dep_column("account_name", "string", "dimension"),
            dep_column("exception_type", "string", "dimension"),
            dep_column("period", "string", "dimension"),
            dep_column("impact_amount", "real", "measure"),
            EXC_PERIOD_DEP,
        ],
        instances=[
            dep_instance("account_name", "None",
                         "none:account_name:nk", "nominal"),
            dep_instance("exception_type", "None",
                         "none:exception_type:nk", "nominal"),
            dep_instance("impact_amount", "Sum",
                         "sum:impact_amount:qk", "quantitative"),
            EXC_PERIOD_INST,
        ],
        rows=f"[{EXCEPTIONS_DS}].[none:account_name:nk]",
        cols=f"[{EXCEPTIONS_DS}].[none:exception_type:nk]",
        mark="Text",
        encodings=f"              <text column='[{EXCEPTIONS_DS}].[sum:impact_amount:qk]' />",
        filters=period_filter_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
        sorts=sort_xml(EXCEPTIONS_DS, "none:account_name:nk",
                       "sum:impact_amount:qk"),
        slices=period_slice_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
    ))

    # 5-7. KPI tiles ---------------------------------------------------------
    sheets.append(worksheet(
        name="KPI Exceptions",
        title="Open exceptions",
        ds=EXCEPTIONS_DS, ds_caption="Exceptions",
        deps=[
            dep_column("transaction_id", "string", "dimension"),
            dep_column("period", "string", "dimension"),
            dep_column("Calculation_exccount", "integer", "measure",
                       "COUNT([transaction_id])", "Total Exceptions"),
            EXC_PERIOD_DEP,
        ],
        instances=[
            dep_instance("Calculation_exccount", "User",
                         "usr:Calculation_exccount:qk", "quantitative"),
            EXC_PERIOD_INST,
        ],
        rows="",
        cols="",
        mark="Text",
        encodings=f"              <text column='[{EXCEPTIONS_DS}].[usr:Calculation_exccount:qk]' />",
        filters=period_filter_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
        slices=period_slice_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
    ))

    sheets.append(worksheet(
        name="KPI Impact",
        title="Total exception impact ($)",
        ds=EXCEPTIONS_DS, ds_caption="Exceptions",
        deps=[
            dep_column("impact_amount", "real", "measure"),
            dep_column("period", "string", "dimension"),
            EXC_PERIOD_DEP,
        ],
        instances=[
            dep_instance("impact_amount", "Sum",
                         "sum:impact_amount:qk", "quantitative"),
            EXC_PERIOD_INST,
        ],
        rows="",
        cols="",
        mark="Text",
        encodings=f"              <text column='[{EXCEPTIONS_DS}].[sum:impact_amount:qk]' />",
        filters=period_filter_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
        slices=period_slice_xml(EXCEPTIONS_DS, EXC_PERIOD_INSTANCE),
    ))

    sheets.append(worksheet(
        name="KPI Out of Tolerance",
        title="Account-periods out of tolerance",
        ds=CONTROLS_DS, ds_caption="Control Totals",
        deps=[
            dep_column("out_of_tolerance", "integer", "measure"),
            dep_column("period", "string", "dimension"),
            CTL_PERIOD_DEP,
        ],
        instances=[
            dep_instance("out_of_tolerance", "Sum",
                         "sum:out_of_tolerance:qk", "quantitative"),
            CTL_PERIOD_INST,
        ],
        rows="",
        cols="",
        mark="Text",
        encodings=f"              <text column='[{CONTROLS_DS}].[sum:out_of_tolerance:qk]' />",
        filters=period_filter_xml(CONTROLS_DS, CTL_PERIOD_INSTANCE),
        slices=period_slice_xml(CONTROLS_DS, CTL_PERIOD_INSTANCE),
    ))

    return sheets


# ------------------------------------------------------------------- dashboard

# x, y, w, h in 1/100000 units of the 1280x800 dashboard
DASH_ZONES = [
    ("KPI Exceptions", 0, 7000, 33333, 14000),
    ("KPI Impact", 33333, 7000, 33334, 14000),
    ("KPI Out of Tolerance", 66667, 7000, 33333, 14000),
    ("Variance by Account", 0, 21000, 50000, 39000),
    ("Impact by Exception Type", 50000, 21000, 50000, 39000),
    ("Variance Trend", 0, 60000, 50000, 40000),
    ("Impact Matrix", 50000, 60000, 50000, 40000),
]


def dashboard():
    zones = [
        # One parameter control drives both data sources — the reason the
        # period filter is a parameter and not two separate quick filters.
        "          <zone custom-title='true' h='7000' id='10' mode='compact' "
        "param='[Parameters].[Parameter 1]' type-v2='paramctrl' "
        "w='100000' x='0' y='0'>\n"
        "            <formatted-text>\n"
        "              <run fontcolor='#12436d' fontsize='11'>Period</run>\n"
        "            </formatted-text>\n"
        "          </zone>"
    ]
    zid = 10
    for name, x, y, w, h in DASH_ZONES:
        zid += 1
        zones.append(
            f"          <zone h='{h}' id='{zid}' name='{esc(name)}' "
            f"w='{w}' x='{x}' y='{y}' />"
        )
    zone_xml = "\n".join(zones)
    return f"""    <dashboard name='GL Close Scorecard'>
      <style />
      <size maxheight='800' maxwidth='1280' minheight='800' minwidth='1280' sizing-mode='fixed' />
      <datasources>
        <datasource name='Parameters' />
      </datasources>
      <datasource-dependencies datasource='Parameters'>
        <column caption='Period' datatype='string' name='[Parameter 1]' param-domain-type='list' role='measure' type='nominal' value='&quot;All&quot;'>
          <calculation class='tableau' formula='&quot;All&quot;' />
        </column>
      </datasource-dependencies>
      <zones>
        <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>
{zone_xml}
        </zone>
      </zones>
    </dashboard>"""


def windows(sheet_names):
    """
    Tableau's document parser asserts `!m_activeSheet.empty()` — a workbook
    with no <windows> section fails to load with a bare "Internal Error".
    Every sheet needs a window, and exactly one carries maximized='true'.
    """
    # <window> content model is ((cards,viewpoint?) | (viewpoints,active,device-preview))
    out = []
    for name in sheet_names:
        out.append(
            f"""    <window class='worksheet' name='{esc(name)}'>
      <cards />
      <viewpoint>
        <zoom type='entire-view' />
      </viewpoint>
    </window>"""
        )
    viewpoints = "\n".join(
        f"        <viewpoint name='{esc(n)}'>\n"
        f"          <zoom type='entire-view' />\n"
        f"        </viewpoint>"
        for n in sheet_names
    )
    out.append(
        f"""    <window class='dashboard' maximized='true' name='GL Close Scorecard'>
      <viewpoints>
{viewpoints}
      </viewpoints>
      <active id='-1' />
    </window>"""
    )
    return "  <windows source-height='30'>\n" + "\n".join(out) + "\n  </windows>"


# ----------------------------------------------------------------------- build


def build():
    sheets = build_worksheets()
    xml = f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='18.1' source-build='2024.1.0 (20241.24.0425.1340)' source-platform='win' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <preferences>
    <preference name='ui.encoding.shelf.height' value='24' />
    <preference name='ui.shelf.height' value='26' />
  </preferences>
  <datasources>
{parameters_datasource()}
{datasource(CONTROLS_DS, "Control Totals", CONTROLS_CONN, CONTROLS_CSV,
            CONTROL_COLUMNS, CONTROL_CALCS,
            color_style("none:Calculation_tolerance:nk", CONTROLS_STYLE_MAP),
            [("Calculation_tolerance", "none:Calculation_tolerance:nk")])}
{datasource(EXCEPTIONS_DS, "Exceptions", EXCEPTIONS_CONN, EXCEPTIONS_CSV,
            EXCEPTION_COLUMNS, EXCEPTION_CALCS,
            color_style("none:exception_type:nk", EXCEPTIONS_STYLE_MAP),
            [("exception_type", "none:exception_type:nk")])}
  </datasources>
  <worksheets>
{chr(10).join(sheets)}
  </worksheets>
  <dashboards>
{dashboard()}
  </dashboards>
{windows([name for name, *_ in DASH_ZONES])}
</workbook>
"""
    TWB.write_text(xml, encoding="utf-8", newline="\n")
    print(f"wrote {TWB.name} ({len(sheets)} sheets, 1 dashboard, "
          f"{len(xml.splitlines())} lines)")


if __name__ == "__main__":
    build()
