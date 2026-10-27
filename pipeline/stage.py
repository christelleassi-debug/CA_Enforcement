"""Stage 2 - staging.

Loads raw files into DuckDB `stg_*` tables with light, lossless cleaning:
types, trimming, zero-padded IDs, and the structural fixes discovered while
profiling (see README "Relationships discovered"):

* The lookup workbook is five sub-tables stacked in one sheet, each with its own
  header row and its own meaning for the DESCRIPTION/VALUE columns. It is split
  into one staging table per sub-table.
* CLASS_ASSESSED_FINAL carries trailing tabs and inconsistent casing.
* EVENTID 'A99999' is a legacy placeholder shared by ~5k unrelated rows -> NULL.
* The narratives CSV is cp1252 encoded with dd-Mon-yy dates.
"""
import re

import duckdb
import pandas as pd

from .paths import CONFIG, DB_PATH, RAW, ensure_dirs

EVENTID_PLACEHOLDERS = {"A99999"}


def _clean_str(s):
    return s.astype("string").str.replace("\t", " ", regex=False).str.strip().replace({"": pd.NA})


def stage_enforcement():
    df = pd.read_excel(RAW / "health-facilities-state-enforcement-actions-1997-2023-data-file.xlsx", dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in df.columns:
        df[c] = _clean_str(df[c])
    for c in ["penalty_issue_date", "violation_from_date", "violation_to_date", "appeal_due_date", "appeal_received_date"]:
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    for c in ["total_amount_initial", "total_amount_due_final", "total_penalty_offset_amount",
              "total_collected_amount", "total_balance_due"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["facid"] = df["facid"].str.zfill(9)
    df["penalty_number"] = df["penalty_number"].str.zfill(9)
    df["eventid_raw"] = df["eventid"]
    df.loc[df["eventid"].isin(EVENTID_PLACEHOLDERS), "eventid"] = pd.NA
    # Normalised final class: upper-case, collapse spaces, keep raw alongside.
    df["class_assessed_final_raw"] = df["class_assessed_final"]
    df["class_assessed_final"] = (df["class_assessed_final"].str.upper()
                                  .str.replace(r"\s+", " ", regex=True).str.strip())
    return df


def stage_narratives():
    n = pd.read_csv(RAW / "ltc-citation-narratives-2012-2017-data-file.csv", dtype=str, encoding="cp1252")
    n.columns = [c.strip().lower().replace(" ", "_") for c in n.columns]
    n["facid"] = n["facid"].str.strip().str.zfill(9)
    n["penalty_number"] = n["penalty_number"].str.strip().str.zfill(9)
    n["penalty_issue_date"] = pd.to_datetime(n["penalty_issue_date"], format="%d-%b-%y", errors="coerce").dt.date
    n["narrative_length"] = pd.to_numeric(n["narrative_length"], errors="coerce").astype("Int64")
    n["class_assessed_initial"] = n["class_assessed_initial"].str.strip()
    return n


def stage_lookup():
    """Split the stacked lookup sheet into its sub-tables, keyed by the VARIABLE column."""
    raw = pd.read_excel(RAW / "lookup-table-health-facility-state-enforcement-actions.xlsx", header=None, dtype=str)
    raw.columns = ["variable", "c1", "c2", "c3"]
    raw = raw.apply(lambda s: s.str.strip())
    raw = raw[raw["variable"].str.upper() != "VARIABLE"]          # drop the repeated header rows
    raw["block"] = raw["variable"].str.upper()
    blocks = {k: g.drop(columns=["variable", "block"]) for k, g in raw.groupby("block")}
    expected = {"FAC_TYPE_CODE", "FAC_FDR", "CLASS_ASSESSED_FINAL", "CLASS_ASSESSED_INITIAL",
                "DISTRICT_OFFICE", "PENALTY_CATEGORY", "PENALTY_DETAIL, CLASS_ASSESSED_INITIAL"}
    missing = expected - set(blocks)
    if missing:
        raise ValueError(f"lookup sheet layout changed; missing blocks {missing}")

    ft = blocks["FAC_TYPE_CODE"].rename(columns={"c1": "fac_type_name", "c2": "fac_type_code_raw", "c3": "definition"})
    # e.g. 'ICFDD_CN: (04001317, ...)' -> code ICFDD_CN (the list is a facility enumeration, not part of the code)
    ft["fac_type_code"] = ft["fac_type_code_raw"].str.split(":").str[0].str.strip()
    ft = ft[["fac_type_code", "fac_type_name", "definition", "fac_type_code_raw"]]

    fdr = blocks["FAC_FDR"].rename(columns={"c1": "fdr_name", "c2": "elms_codes"})[["fdr_name", "elms_codes"]]
    fdr = fdr.assign(fac_type_code=fdr["elms_codes"].str.split(",")).explode("fac_type_code")
    fdr["fac_type_code"] = fdr["fac_type_code"].str.strip()
    fdr = fdr[fdr["fac_type_code"].ne("")].drop_duplicates(["fdr_name", "fac_type_code"])

    cls = []
    for key, kind in [("CLASS_ASSESSED_INITIAL", "initial"), ("CLASS_ASSESSED_FINAL", "final"),
                      ("PENALTY_DETAIL, CLASS_ASSESSED_INITIAL", "penalty_detail")]:
        b = blocks[key].rename(columns={"c1": "class_code", "c2": "class_description"})
        b["lookup_block"] = kind
        cls.append(b[["lookup_block", "class_code", "class_description"]])
    cls = pd.concat(cls)
    cls["class_code_norm"] = cls["class_code"].str.upper().str.replace(r"\s+", " ", regex=True).str.strip()
    cls["class_description"] = cls["class_description"].str.replace(r"\s+", " ", regex=True)

    dist = blocks["DISTRICT_OFFICE"].rename(columns={"c1": "office_name", "c2": "counties_served"})
    dist["counties_served"] = dist["counties_served"].str.replace(r"^Counties Served:\s*", "", regex=True)
    dist = dist[["office_name", "counties_served"]]

    cat = blocks["PENALTY_CATEGORY"].rename(columns={"c2": "category_name"})[["category_name"]]
    cat["category_name"] = cat["category_name"].str.replace(r"\s+", " ", regex=True).str.strip()
    return {"stg_lookup_fac_type": ft, "stg_lookup_fac_fdr": fdr, "stg_lookup_class": cls,
            "stg_lookup_district_office": dist, "stg_lookup_penalty_category": cat}


def stage_dictionary():
    d = pd.read_excel(RAW / "data-dictionary-health-facilities-state-enforcement-actions.xlsx", dtype=str)
    d.columns = [c.strip().lower() for c in d.columns]
    return d.dropna(subset=["variable"]).apply(lambda s: s.str.strip())


def run(log=print):
    ensure_dirs()
    tables = {
        "stg_enforcement_actions": stage_enforcement(),
        "stg_ltc_narratives": stage_narratives(),
        "stg_data_dictionary": stage_dictionary(),
        "stg_district_crosswalk": pd.read_csv(CONFIG / "district_crosswalk.csv", dtype=str),
        **stage_lookup(),
    }
    con = duckdb.connect(str(DB_PATH))
    for name, df in tables.items():
        con.register("_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
        con.unregister("_df")
        log(f"  + {name:<32} {len(df):>7,} rows")
    con.close()
    return {k: len(v) for k, v in tables.items()}
