#!/usr/bin/env python3
"""Single entry point: raw download -> staging -> modeled tables -> enriched mart -> tests -> analysis.

    python run_pipeline.py              # full run, downloads from data.chhs.ca.gov (falls back to vendored snapshot)
    python run_pipeline.py --offline    # skip the network, use the committed snapshot (same md5s)
    python run_pipeline.py --skip-analysis
"""
import argparse
import time

from pipeline import extract, quality, stage, transform


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use data/vendor snapshot instead of downloading")
    ap.add_argument("--skip-analysis", action="store_true", help="stop after the mart + quality checks")
    args = ap.parse_args()

    steps = [
        ("1/6 extract  (raw download + md5 verify)", lambda: extract.run(offline=args.offline)),
        ("2/6 stage    (raw -> stg_* in DuckDB)", stage.run),
        ("3/6 model    (dims, facts, bridge)", transform.run_model),
        ("4/6 mart     (enriched joins)", transform.run_mart),
        ("5/6 quality  (keys, joins, reconciliation)", quality.run),
    ]
    if not args.skip_analysis:
        from analysis import prioritize
        steps.append(("6/6 analysis (facility prioritization model)", prioritize.run))

    t0 = time.time()
    for label, fn in steps:
        print(f"\n== {label}")
        t = time.time()
        fn()
        print(f"   done in {time.time() - t:.1f}s")
    print(f"\nPipeline finished in {time.time() - t0:.1f}s. Outputs in ./output, warehouse in ./data/warehouse")


if __name__ == "__main__":
    main()
