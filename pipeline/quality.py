"""Stage 5 - data quality & relationship validation.

Each check is a SQL query returning a single value plus an expectation.
`error` checks fail the pipeline; `warn` checks are reported only.
Results go to output/data_quality_report.md and output/reconciliation_vs_cdph.csv.
"""
import duckdb
import pandas as pd

from .paths import DB_PATH, OUTPUT

# (name, severity, sql returning one number, predicate on that number, description)
CHECKS = [
    # --- primary keys
    ("pk_fact_penalty_number", "error",
     "SELECT count(*) - count(DISTINCT penalty_number) FROM fact_enforcement_action", lambda v: v == 0,
     "PENALTY_NUMBER is unique in the fact"),
    ("pk_dim_facility", "error", "SELECT count(*) - count(DISTINCT facid) FROM dim_facility", lambda v: v == 0,
     "FACID is unique in dim_facility"),
    ("pk_narrative", "error",
     "SELECT count(*) - count(DISTINCT penalty_number) FROM fact_citation_narrative", lambda v: v == 0,
     "PENALTY_NUMBER is unique in the narratives"),
    # --- functional dependency that justifies dim_facility
    ("fd_facid_attributes", "error",
     """SELECT count(*) FROM (SELECT facid FROM stg_enforcement_actions GROUP BY facid
        HAVING count(DISTINCT facility_name) > 1 OR count(DISTINCT ltc) > 1 OR count(DISTINCT fac_type_code) > 1
            OR count(DISTINCT fac_fdr) > 1 OR count(DISTINCT district_office) > 1)""", lambda v: v == 0,
     "FACID -> name/LTC/type/FDR/district is a functional dependency"),
    ("fd_detail_class_1to1", "error",
     """SELECT count(*) FROM (SELECT class_assessed_initial FROM stg_enforcement_actions
        GROUP BY 1 HAVING count(DISTINCT penalty_detail) > 1)""", lambda v: v == 0,
     "PENALTY_DETAIL <-> CLASS_ASSESSED_INITIAL is 1:1"),
    # --- referential integrity (orphans)
    ("fk_fact_facility", "error",
     "SELECT count(*) FROM fact_enforcement_action f ANTI JOIN dim_facility d USING (facid)", lambda v: v == 0,
     "every fact row has a facility"),
    ("fk_fact_class", "error",
     "SELECT count(*) FROM fact_enforcement_action f ANTI JOIN dim_penalty_class d USING (class_code)", lambda v: v == 0,
     "every fact row has a penalty class"),
    ("fk_fact_date", "error",
     "SELECT count(*) FROM fact_enforcement_action f ANTI JOIN dim_date d ON d.date_key = f.issue_date", lambda v: v == 0,
     "every issue date is in dim_date"),
    ("fk_fact_category", "error",
     """SELECT count(*) FROM fact_enforcement_action f WHERE category_key IS NOT NULL
        AND category_key NOT IN (SELECT category_key FROM dim_penalty_category)""", lambda v: v == 0,
     "every non-null category resolves"),
    ("fk_facility_type", "error",
     "SELECT count(*) FROM dim_facility f ANTI JOIN dim_facility_type t USING (fac_type_code)", lambda v: v == 0,
     "every facility type resolves"),
    ("fk_facility_district", "error",
     "SELECT count(*) FROM dim_facility f ANTI JOIN dim_district_office d USING (district_office)", lambda v: v == 0,
     "every district office resolves"),
    # --- join fan-out / coverage
    ("mart_rowcount_preserved", "error",
     "SELECT (SELECT count(*) FROM mart_enforcement_enriched) - (SELECT count(*) FROM fact_enforcement_action)",
     lambda v: v == 0, "mart has exactly one row per fact row (no join fan-out)"),
    ("staging_rowcount_preserved", "error",
     "SELECT (SELECT count(*) FROM fact_enforcement_action) - (SELECT count(*) FROM stg_enforcement_actions)",
     lambda v: v == 0, "no rows lost between staging and fact"),
    ("narrative_match_rate", "error",
     "SELECT avg(has_fact_match::INT) FROM fact_citation_narrative", lambda v: v >= 0.99,
     "share of narratives that join to a fact row on PENALTY_NUMBER"),
    ("narrative_facid_agreement", "error",
     """SELECT avg((n.facid = f.facid)::INT) FROM fact_citation_narrative n
        JOIN fact_enforcement_action f USING (penalty_number)""", lambda v: v == 1.0,
     "FACID agrees between narrative and fact for matched rows"),
    ("narrative_class_agreement", "warn",
     """SELECT avg((n.narrative_class_code = f.class_code)::INT) FROM fact_citation_narrative n
        JOIN fact_enforcement_action f USING (penalty_number)""", lambda v: v >= 0.995,
     "initial class agrees between narrative and fact"),
    ("narrative_coverage_of_eligible", "warn",
     """SELECT avg(has_narrative::INT) FROM mart_enforcement_enriched
        WHERE is_ltc AND class_code IN ('AA','A','B') AND issue_date BETWEEN DATE '2012-01-01' AND DATE '2017-12-31'""",
     lambda v: v >= 0.98, "share of eligible LTC AA/A/B citations 2012-2017 that have a narrative"),
    ("category_lookup_match_rate", "warn",
     """SELECT sum(CASE WHEN c.lookup_match <> 'not_in_lookup' THEN 1 ELSE 0 END)::DOUBLE / count(*)
        FROM fact_enforcement_action f JOIN dim_penalty_category c USING (category_key)""", lambda v: v >= 0.75,
     "share of categorised fact rows whose category is found in the published lookup"),
    ("district_crosswalk_complete", "error",
     "SELECT count(*) FROM dim_district_office WHERE match_type = 'missing_from_crosswalk'", lambda v: v == 0,
     "every district office in the data is covered by the crosswalk"),
    # --- business rules
    ("ledger_identity", "error",
     """SELECT count(*) FROM fact_enforcement_action
        WHERE abs(amount_due_final + amount_offset - amount_collected - amount_balance_due) > 0.01""",
     lambda v: v == 0, "final + offset - collected - balance == 0 for every action"),
    ("no_negative_amounts", "warn",
     "SELECT count(*) FROM fact_enforcement_action WHERE amount_initial < 0 OR amount_due_final < 0",
     lambda v: v == 0, "no negative assessed amounts"),
    ("violation_dates_ordered", "warn",
     "SELECT count(*) FROM fact_enforcement_action WHERE violation_to_date < violation_from_date",
     lambda v: v == 0, "violation_to_date >= violation_from_date"),
    ("eventid_placeholder_removed", "error",
     "SELECT count(*) FROM fact_enforcement_action WHERE eventid = 'A99999'", lambda v: v == 0,
     "legacy EVENTID placeholder nulled before any join"),
]

# Published by CDPH: 'State Enforcement Actions' visuals PDFs, data extract 16 Jul 2019 (SFY2016-17..2018-19).
# (penalty_detail, sfy, count, dollars) - dollars are TOTAL_AMOUNT_DUE_FINAL at extract time.
CDPH_PUBLISHED = [
    ("Citation AA (HSC 1424)", "SFY2016-17", 15, 912279.04), ("Citation AA (HSC 1424)", "SFY2017-18", 16, 1380000.00),
    ("Citation AA (HSC 1424)", "SFY2018-19", 21, 1586975.00),
    ("Citation A (HSC 1424)", "SFY2016-17", 223, 3737652.00), ("Citation A (HSC 1424)", "SFY2017-18", 199, 4076250.00),
    ("Citation A (HSC 1424)", "SFY2018-19", 175, 3672750.00),
    ("Citation B (HSC 1424)", "SFY2016-17", 446, 811330.00), ("Citation B (HSC 1424)", "SFY2017-18", 397, 817060.00),
    ("Citation B (HSC 1424)", "SFY2018-19", 466, 880485.00),
    ("AP - 3.2 Nursing-Hours-per-Patient-Day AP (HSC 1276.5)", "SFY2016-17", 11, 180000.00),
    ("AP - 3.2 Nursing-Hours-per-Patient-Day AP (HSC 1276.5)", "SFY2017-18", 25, 420000.00),
    ("AP - 3.2 Nursing-Hours-per-Patient-Day AP (HSC 1276.5)", "SFY2018-19", 51, 825000.00),
    ("AP - Immediate Jeopardy (HSC 1280.3)", "SFY2016-17", 30, 1761935.00),
    ("AP - Immediate Jeopardy (HSC 1280.3)", "SFY2017-18", 31, 1996330.00),
    ("AP - Immediate Jeopardy (HSC 1280.3)", "SFY2018-19", 20, 1070220.00),
    ("AP - Non-Immediate Jeopardy (HSC 1280.3)", "SFY2016-17", 44, 471204.75),
    ("AP - Non-Immediate Jeopardy (HSC 1280.3)", "SFY2017-18", 42, 459557.50),
    ("AP - Non-Immediate Jeopardy (HSC 1280.3)", "SFY2018-19", 31, 391089.50),
    ("Failure to Report Adverse Events to CDPH (HSC 1280.4)", "SFY2016-17", 90, 667931.25),
    ("Failure to Report Adverse Events to CDPH (HSC 1280.4)", "SFY2017-18", 57, 107800.00),
    ("Failure to Report Adverse Events to CDPH (HSC 1280.4)", "SFY2018-19", 82, 309300.00),
    ("AP - Breach (HSC 1280.15)", "SFY2016-17", 4, 102500.00), ("AP - Breach (HSC 1280.15)", "SFY2017-18", 28, 863500.00),
    ("AP - Breach (HSC 1280.15)", "SFY2018-19", 8, 94500.00),
]


def reconcile(con):
    pub = pd.DataFrame(CDPH_PUBLISHED, columns=["class_description", "sfy", "cdph_count", "cdph_amount"])
    ours = con.execute("""SELECT class_description, sfy, count(*) AS our_count,
                                 round(sum(amount_due_final), 2) AS our_amount_due_final
                          FROM mart_enforcement_enriched
                          WHERE sfy IN ('SFY2016-17','SFY2017-18','SFY2018-19')
                          GROUP BY ALL""").df()
    r = pub.merge(ours, on=["class_description", "sfy"], how="left")
    r["count_diff"] = r["our_count"] - r["cdph_count"]
    r["count_pct_diff"] = (r["count_diff"] / r["cdph_count"]).round(4)
    r["amount_pct_diff"] = ((r["our_amount_due_final"] - r["cdph_amount"]) / r["cdph_amount"]).round(4)
    return r


def run(log=print, strict=True):
    con = duckdb.connect(str(DB_PATH))
    rows, failed = [], []
    for name, sev, sql, pred, desc in CHECKS:
        v = con.execute(sql).fetchone()[0]
        v = float(v) if v is not None else None
        ok = v is not None and pred(v)
        rows.append({"check": name, "severity": sev, "value": v, "passed": ok, "description": desc})
        if not ok and sev == "error":
            failed.append(name)
        log(f"  {'PASS' if ok else ('FAIL' if sev == 'error' else 'WARN')}  {name:<32} {v}")

    rec = reconcile(con)
    rec.to_csv(OUTPUT / "reconciliation_vs_cdph.csv", index=False)
    exact = (rec["count_diff"] == 0).mean()
    within2 = (rec["count_diff"].abs() <= 2).mean()
    log(f"  reconciliation vs CDPH 2019 charts: {exact:.0%} of cells exact on count, {within2:.0%} within +/-2")

    res = pd.DataFrame(rows)
    md = ["# Data quality & relationship validation", "",
          "Generated by `pipeline/quality.py` on every run. `error` checks stop the pipeline.", "",
          res.to_markdown(index=False), "",
          "## Reconciliation against CDPH's own published charts (data extract 16 Jul 2019)", "",
          f"Counts: {exact:.0%} of class x SFY cells match exactly, {within2:.0%} within +/-2 actions. "
          "Dollar figures in the charts equal `TOTAL_AMOUNT_DUE_FINAL` as of 2019; later appeals/settlements "
          "move some cells, so amount differences are expected and are shown for transparency.", "",
          rec.to_markdown(index=False)]
    (OUTPUT / "data_quality_report.md").write_text("\n".join(md))
    con.close()
    if failed and strict:
        raise SystemExit(f"Data quality checks failed: {failed}")
    return res, rec
