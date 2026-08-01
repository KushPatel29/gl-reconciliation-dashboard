# Tableau close scorecard — build & publish

The same close scorecard as the Power BI report, rebuilt in Tableau to show
tool range. Both the data prep and the workbook itself are scripted.

## Build it

```bash
python engine/run_reconciliation.py
python tableau/prepare_tableau_data.py
python tableau/build_workbook.py
```

`prepare_tableau_data.py` writes two flat extracts:

- `control_totals_tableau.csv` — account × period with ERP/subledger totals,
  variance, and an `out_of_tolerance` flag (0.5% materiality, precomputed)
- `exceptions_tableau.csv` — exception log with `impact_amount`
  (`ABS(COALESCE(variance, erp_amount))`, precomputed)

`build_workbook.py` writes `GLCloseScorecard.twb`: two data sources, one
parameter, seven sheets, one 1280×800 dashboard.

## What's in it

| Sheet | Mark | Reads |
| --- | --- | --- |
| KPI Exceptions / KPI Impact / KPI Out of Tolerance | Text | both extracts |
| Variance by Account | Bar, coloured by tolerance status | control totals |
| Impact by Exception Type | Bar, coloured by type | exceptions |
| Variance Trend | Line | control totals |
| Impact Matrix | Text table, account × exception type | exceptions |

A single **Period** parameter drives a boolean `Period Filter` calculation in
*each* data source (`[Parameters].[Parameter 1] = "All" OR [period] = …`).
Tableau cannot apply one quick filter across unrelated data sources, and two
separate quick filters drift apart the moment someone changes one; a parameter
is the honest way to keep them in step. The trend sheet deliberately has no
period filter — a trend that filters itself to one period is a single dot.

Palette is the portfolio's Meridian brand: `#12436D` navy · `#28A197` teal ·
`#F46A25` orange · `#801650` plum · `#C0392B` red for out-of-tolerance.

## Publish to Tableau Public

Tableau Desktop **Free Edition** can do this — a paid licence is only needed
for Tableau Server/Cloud.

1. Open `tableau/GLCloseScorecard.twb`.
2. **Server → Tableau Public → Save to Tableau Public As…**
3. Sign in with a free Tableau Public account.
4. Tableau converts the two text connections to an extract automatically.
5. Copy the public URL into this README.

## Why a generated .twb and not a hand-built .twbx

A `.twbx` is a zip: every save is an unreviewable binary diff, and it bakes in
absolute paths from whoever last saved it. A generated `.twb` is plain XML with
`directory='.'` connections, so it opens from a fresh clone and every change is
a readable diff.

`tests/test_tableau_integrity.py` (11 tests) parses the workbook and asserts
every declared column exists in the extracts, every calculation references a
real field, every shelf and encoding resolves to a declared column-instance,
every dashboard zone points at a real sheet, the parameter's period list
matches the data, and the committed file is byte-identical to generator output.

### Three things Tableau's format punishes

Learned while building this; all three are pinned by tests now.

1. **No `<windows>` block → "Internal Error" with no line number.** The
   document parser asserts `!m_activeSheet.empty()`. Every sheet needs a
   window and exactly one needs `maximized='true'`.
2. **Colour palettes live on the `<datasource>`, not the worksheet**, and the
   style rule's `field` is the *bare* column-instance name — while the
   `<color>` encoding inside `<panes>` is datasource-qualified. Get this wrong
   and nothing errors; Tableau just silently uses its default palette.
3. **A palette binds to a column-instance declared on the datasource.**
   Declaring the instance only inside a worksheet's `datasource-dependencies`
   leaves the rule unresolved — again silently.

Tableau validates the XML against its schema on load and reports exact
line/column numbers and content models, which makes it a fast feedback loop
once you stop guessing and read the error.
