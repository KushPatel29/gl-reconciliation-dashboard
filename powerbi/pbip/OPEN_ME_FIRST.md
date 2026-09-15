# How to open this Power BI Project

Everything is pre-built in code — no clicking required. The semantic model
(TMDL) has 11 tables, 9 relationships, and 19 DAX measures; the report (PBIR)
has 5 finished pages with 42 visuals (run `python finops/generate_focus_data.py`
and `python finops/run_finops_recon.py` too, so the Cloud Chargeback page has
data to load).

## Steps

1. Double-click **`GLReconciliationDashboard.pbip`**.
2. When the report opens, click **Refresh** to load the CSVs into the model
   (the engine outputs in `output/` and dims in `data/` — run
   `python engine/run_reconciliation.py` first if `output/` is empty).
3. If you moved/cloned this repo somewhere else: Home → Transform data →
   Edit parameters → set **DataPath** to your local
   `...\gl-reconciliation-dashboard` repo root, then Refresh.

## Verify the model loaded

- Data pane: `_Measures` table with 13 measures.
- Model view: `dim_account` and `dim_period` each relate to both
  `gl_control_totals` and `gl_reconciliation_exceptions`; a hidden
  `fact_erp_gl` (ERP transaction grain) feeds `Match Rate`'s denominator.

## Report pages

| Page | What it answers |
|---|---|
| Close Scorecard | Is this month's close healthy? (match rate, exception count/$, accounts out of tolerance) |
| Variance by Account | Where does ERP vs subledger disagree, and by how much? |
| Exception Detail | The row-level triage list an analyst hands to AP |

## If Desktop shows an error opening the project

Note the exact error text and file it mentions — the TMDL/PBIR was authored
by hand, so a version-specific syntax quirk is possible; it's a quick fix.

## Using it in Power BI Desktop

- **Refresh before you read a number.** A `.pbip` stores the model definition,
  not its data. On first open Desktop shows "Some of the tables have incomplete or
  no data"; use **Home > Refresh** so every table reloads from the CSVs. If you
  opened this project in an earlier session, Desktop may also load an old local
  cache (`.pbi/cache.abf`, not committed) and show last session's figures until
  the full refresh finishes.
- **Buttons need Ctrl+click in Desktop.** Desktop is an editor, so a plain click
  selects a button; **Ctrl+click** follows it. Previous, Next, Filters, Close and
  Clear all filters say so in their tooltips. In the Power BI service and in
  reading view a plain click works.
