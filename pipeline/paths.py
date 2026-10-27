from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SQL = ROOT / "sql"
DATA = ROOT / "data"
VENDOR = DATA / "vendor"          # committed snapshot of the CHHS files (offline fallback)
RAW = DATA / "raw"                # where extract lands files for this run (gitignored)
WAREHOUSE = DATA / "warehouse"    # DuckDB database
OUTPUT = ROOT / "output"          # exported modeled tables, mart, analysis results
TABLES = OUTPUT / "tables"
ANALYSIS = OUTPUT / "analysis"
DB_PATH = WAREHOUSE / "enforcement.duckdb"


def ensure_dirs():
    for p in (RAW, WAREHOUSE, OUTPUT, TABLES, ANALYSIS):
        p.mkdir(parents=True, exist_ok=True)
