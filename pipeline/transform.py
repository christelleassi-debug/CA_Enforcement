"""Stages 3 & 4 - model and mart.

Runs sql/model/*.sql then sql/mart/*.sql (lexical order) inside DuckDB and
exports every modeled table to Parquet + CSV under output/tables/.
"""
import duckdb

from .paths import DB_PATH, SQL, TABLES, ensure_dirs

MODEL_TABLES = ["dim_facility", "dim_facility_type", "dim_district_office", "dim_penalty_class",
                "dim_penalty_category", "dim_date", "fact_enforcement_action",
                "fact_citation_narrative", "bridge_narrative_regulation"]
MART_TABLES = ["mart_enforcement_enriched", "mart_facility_profile"]


def _run_dir(con, sub, log):
    for f in sorted((SQL / sub).glob("*.sql")):
        con.execute(f.read_text())
        log(f"  + ran {sub}/{f.name}")


def export(con, tables, log):
    for t in tables:
        con.execute(f"COPY {t} TO '{TABLES / (t + '.parquet')}' (FORMAT PARQUET)")
        # CSV for the client-friendly tables; the narrative table is too wide for spreadsheets.
        if t != "fact_citation_narrative":
            con.execute(f"COPY {t} TO '{TABLES / (t + '.csv')}' (HEADER)")
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log(f"  > exported {t:<30} {n:>7,} rows")


def run_model(log=print):
    ensure_dirs()
    con = duckdb.connect(str(DB_PATH))
    _run_dir(con, "model", log)
    export(con, MODEL_TABLES, log)
    con.close()


def run_mart(log=print):
    con = duckdb.connect(str(DB_PATH))
    _run_dir(con, "mart", log)
    export(con, MART_TABLES, log)
    con.close()
