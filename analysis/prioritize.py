"""Client question: "We can only review 25 facilities next quarter. Which should we prioritize, and why?"

Approach (all built on mart_enforcement_enriched):
1. Point-in-time facility snapshots every 6 months. Features use ONLY actions issued before the
   snapshot date T; the label is whether the facility receives a *serious* state action
   (Class AA, Class A, or hospital Immediate-Jeopardy AP) in the 12 months after T.
2. Strict temporal backtest: train on snapshots whose label window closes before the test period
   starts; test on SFY2022-23 / SFY2023-24 snapshots. Compare with the rules a reviewer would use
   without a model (most actions last year, most serious history, most dollars).
3. Score every currently-active facility as of the day after the data ends and return the top 25,
   each with plain-language reasons (largest positive contributions in the logistic model)
   and a statutory flag: one Class AA in the last 24 months means a second AA triggers
   mandatory license suspension/revocation proceedings (HSC 1424.5(a)(1)).
"""
import json

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from pipeline.paths import ANALYSIS, DB_PATH, ensure_dirs

K = 25
HORIZON_MONTHS = 12
ACTIVE_YEARS = 5          # facility counts as "active" if it had any action in the 5 years before T
TEST_START = pd.Timestamp("2021-07-01")
REGION_CAP = 12          # capped variant: at most 12 of 25 from any one region

FEATURE_LABELS = {
    "n_12m": "enforcement actions in the last 12 months",
    "n_36m": "enforcement actions in the last 3 years",
    "n_all": "total enforcement actions on record",
    "serious_12m": "serious actions (AA/A/IJ) in the last 12 months",
    "serious_36m": "serious actions (AA/A/IJ) in the last 3 years",
    "serious_all": "serious actions on record",
    "aa_all": "Class AA (death-related) citations on record",
    "death_36m": "death-related investigations in the last 3 years",
    "abuse_36m": "unreported-abuse citations in the last 3 years",
    "care_36m": "patient-care / medication citations in the last 3 years",
    "staffing_36m": "nurse-staffing penalties in the last 3 years",
    "complaint_36m": "complaint-driven actions in the last 3 years",
    "log_amount_36m": "penalty dollars assessed in the last 3 years",
    "trend": "acceleration (last 12m vs prior 2-yr average)",
    "recency": "recent enforcement activity",
    "recency_serious": "a recent serious action",
    "appeal_rate": "share of actions appealed",
    "tenure_years": "years since first action",
    "type_snf": "skilled nursing facility",
    "type_gach": "general acute care hospital",
    "type_icfdd": "ICF for developmentally disabled",
}


def load_actions():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute("""
        SELECT facid, facility_name, fac_type_code, fac_fdr, district_office, region, issue_date,
               is_serious, class_code, is_death_related, is_complaint_driven, category_group,
               amount_due_final, was_appealed, program
        FROM mart_enforcement_enriched""").df()
    con.close()
    df["issue_date"] = pd.to_datetime(df["issue_date"])
    return df


def snapshot(df, T, with_label=True):
    """Feature row per facility active as of T (uses only issue_date < T)."""
    past = df[df.issue_date < T]
    d = (T - past.issue_date).dt.days
    is_aa = past.class_code.eq("AA")
    in36 = d <= 3 * 365
    p = past.assign(
        n12=d <= 365, n36=in36, n12_36=(d > 365) & in36,
        ser=past.is_serious,
        ser12=past.is_serious & (d <= 365), ser36=past.is_serious & in36,
        aa=is_aa, aa24=is_aa & (d <= 730),
        death36=past.is_death_related & in36,
        abuse36=past.category_group.eq("Abuse (not self-reported)") & in36,
        care36=past.category_group.eq("Patient care") & in36,
        staff36=(past.class_code.eq("AP NHPPD") | past.category_group.eq("Staffing")) & in36,
        compl36=past.is_complaint_driven & in36,
        amt36=past.amount_due_final.where(in36, 0.0),
        ser_date=past.issue_date.where(past.is_serious),
        appealed=past.was_appealed.astype(float),
    )
    f = p.groupby("facid").agg(
        n_12m=("n12", "sum"), n_36m=("n36", "sum"), n_all=("n12", "size"), n_12_36m=("n12_36", "sum"),
        serious_12m=("ser12", "sum"), serious_36m=("ser36", "sum"), serious_all=("ser", "sum"),
        aa_all=("aa", "sum"), aa_24m=("aa24", "sum"), death_36m=("death36", "sum"),
        abuse_36m=("abuse36", "sum"), care_36m=("care36", "sum"), staffing_36m=("staff36", "sum"),
        complaint_36m=("compl36", "sum"), amount_36m=("amt36", "sum"),
        last=("issue_date", "max"), first=("issue_date", "min"), last_serious=("ser_date", "max"),
        appeal_rate=("appealed", "mean"),
        fac_type_code=("fac_type_code", "first"), region=("region", "first"),
        facility_name=("facility_name", "first"), district_office=("district_office", "first"),
        fac_fdr=("fac_fdr", "first"),
    )
    f = f[(T - f["last"]).dt.days <= ACTIVE_YEARS * 365].copy()
    f["log_amount_36m"] = np.log1p(f.amount_36m)
    f["trend"] = f.n_12m - f.n_12_36m / 2
    f["days_since_last"] = (T - f["last"]).dt.days
    f["recency"] = np.exp(-f.days_since_last / 365)               # 1 = yesterday, ~0.37 = a year ago
    f["recency_serious"] = np.exp(-(T - f.last_serious).dt.days.fillna(1e5) / 365)
    f["tenure_years"] = (T - f["first"]).dt.days / 365
    f["type_snf"] = f.fac_type_code.eq("SNF").astype(int)
    f["type_gach"] = f.fac_type_code.isin(["GACH", "SPHOSP"]).astype(int)
    f["type_icfdd"] = f.fac_type_code.str.startswith("ICFDD").astype(int)
    f["region_la"] = f.region.eq("Los Angeles").astype(int)
    f["snapshot"] = T
    if with_label:
        fut = df[(df.issue_date >= T) & (df.issue_date < T + pd.DateOffset(months=HORIZON_MONTHS))]
        ser = fut[fut.is_serious].groupby("facid").size()
        f["y"] = (pd.Series(f.index.map(ser), index=f.index).fillna(0) > 0).astype(int)
    return f.reset_index()


FEATURES = list(FEATURE_LABELS)
BASELINES = {
    "Rule: most actions, last 12m": "n_12m",
    "Rule: most serious actions, last 3y": "serious_36m",
    "Rule: most penalty $, last 3y": "amount_36m",
}


def select_top(score, region=None, k=K, cap=None):
    """Indices of the k highest scores; optionally at most `cap` per region (greedy by score)."""
    order = np.argsort(-np.asarray(score), kind="stable")
    if cap is None:
        return order[:k]
    taken, counts = [], {}
    reg = np.asarray(region)
    for i in order:
        if counts.get(reg[i], 0) < cap:
            taken.append(i); counts[reg[i]] = counts.get(reg[i], 0) + 1
        if len(taken) == k:
            break
    return np.array(taken)


def precision_at_k(y, score, k=K, region=None, cap=None):
    return np.asarray(y)[select_top(score, region, k, cap)].mean()


def build_panel(df):
    end = df.issue_date.max()
    snaps = pd.date_range("2005-01-01", end - pd.DateOffset(months=HORIZON_MONTHS) + pd.Timedelta(days=1), freq="6MS")
    snaps = [s for s in snaps if s.month in (1, 7)]
    return pd.concat([snapshot(df, T) for T in snaps], ignore_index=True)


def models():
    return {
        "Logistic regression": make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000)),
        "Gradient boosting": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                                            l2_regularization=1.0, random_state=7),
    }


def backtest(panel):
    train = panel[panel.snapshot + pd.DateOffset(months=HORIZON_MONTHS) <= TEST_START]
    test = panel[panel.snapshot >= TEST_START]
    rows, fitted = [], {}
    for name, m in models().items():
        m.fit(train[FEATURES], train.y)
        fitted[name] = m
        test = test.assign(**{name: m.predict_proba(test[FEATURES])[:, 1]})
    test = test.assign(**{"Ensemble (LR + GBM)": (test["Logistic regression"] + test["Gradient boosting"]) / 2})
    methods = ["Ensemble (LR + GBM)"] + list(fitted) + list(BASELINES)
    for T, t in test.groupby("snapshot"):
        t = t.reset_index(drop=True)
        base = t.y.mean()
        variants = [(m, None) for m in methods] + [("Ensemble (LR + GBM)", REGION_CAP)]
        for name, cap in variants:
            s = t[name] if name not in BASELINES else t[BASELINES[name]] + 1e-6 * t["n_all"]   # tie-break by history
            pk = precision_at_k(t.y, s, region=t.region, cap=cap)
            label = name if cap is None else f"{name}, max {cap} per region"
            top_idx = select_top(s, t.region, K, cap)
            rows.append({"snapshot": T.date(), "method": label, "facilities_scored": len(t),
                         "base_rate": round(base, 4), "precision_at_25": round(pk, 4),
                         "hits_in_top_25": int(round(pk * K)), "lift_vs_random": round(pk / base, 2),
                         "la_share_of_top_25": round(float((t.region.iloc[top_idx] == "Los Angeles").mean()), 2),
                         "pr_auc": round(average_precision_score(t.y, s), 4),
                         "roc_auc": round(roc_auc_score(t.y, s), 4)})
    res = pd.DataFrame(rows)
    summary = (res.groupby("method")[["precision_at_25", "hits_in_top_25", "lift_vs_random", "la_share_of_top_25", "pr_auc", "roc_auc"]]
               .mean().round(3).sort_values("precision_at_25", ascending=False).reset_index())
    return res, summary, len(train), train.snapshot.nunique(), test.snapshot.nunique()


def reasons(model, X, top=3):
    """Per-facility reason codes from the logistic model: standardized value x coefficient."""
    scaler, lr = model.named_steps["standardscaler"], model.named_steps["logisticregression"]
    contrib = scaler.transform(X[FEATURES]) * lr.coef_[0]
    out = []
    behavioural = np.array([not f.startswith("type_") for f in FEATURES])   # type is context, not a reason
    for row in contrib:
        row = np.where(behavioural, row, -np.inf)
        order = np.argsort(-row)[:top]
        out.append("; ".join(FEATURE_LABELS[FEATURES[i]] for i in order if row[i] > 0))
    return out


def plot(summary, top25, base_rate):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid, accent, base = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#b9b8b2"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": grid, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted})
    s = summary.sort_values("precision_at_25")
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=150)
    colors = [accent if m == s.method.iloc[-1] else base for m in s.method]
    ax.barh(s.method, s.precision_at_25 * 100, color=colors, height=0.55)
    for y, v in enumerate(s.precision_at_25 * 100):
        ax.text(v + 1, y, f"{v:.0f}%", va="center", color=ink)
    ax.set_xlabel("Share of the top 25 that received a serious action in the next 12 months (backtest)")
    ax.axvline(base_rate * 100, color=muted, linestyle=(0, (3, 3)), linewidth=1)
    ax.text(base_rate * 100 + 1, -0.75, f"random pick ~{base_rate * 100:.0f}%", color=muted, fontsize=8.5)
    ax.set_xlim(0, 100); ax.grid(axis="x", color=grid, linewidth=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    fig.suptitle("25-facility review list: hit rate on held-out years (2021-2023)", x=0.02, ha="left", color=ink, fontsize=11); ax.set_title("")
    fig.tight_layout(); fig.savefig(ANALYSIS / "backtest_precision_at_25.png"); plt.close(fig)


def run(log=print):
    ensure_dirs()
    df = load_actions()
    panel = build_panel(df)
    log(f"  panel: {len(panel):,} facility-snapshots, {panel.snapshot.nunique()} snapshots, "
        f"base rate {panel.y.mean():.1%}")
    res, summary, n_train, k_train, k_test = backtest(panel)
    res.to_csv(ANALYSIS / "backtest_by_snapshot.csv", index=False)
    summary.to_csv(ANALYSIS / "backtest_summary.csv", index=False)
    log(f"  train rows {n_train:,} ({k_train} snapshots) | test snapshots {k_test}")
    log("  " + summary.to_string(index=False).replace("\n", "\n  "))

    # Final model: refit on every labelled snapshot, score as of the day after the data ends.
    T_now = (df.issue_date.max() + pd.Timedelta(days=1)).normalize()
    lr = models()["Logistic regression"].fit(panel[FEATURES], panel.y)
    gb = models()["Gradient boosting"].fit(panel[FEATURES], panel.y)
    cur = snapshot(df, T_now, with_label=False)
    # Rank-average of the two models: more stable than either alone (both evaluated in the backtest).
    cur["p_lr"] = lr.predict_proba(cur[FEATURES])[:, 1]
    cur["p_gb"] = gb.predict_proba(cur[FEATURES])[:, 1]
    cur["p_serious_12m"] = (cur.p_lr + cur.p_gb) / 2              # the ensemble validated in the backtest
    cur["why"] = reasons(lr, cur)
    cur["statutory_flag"] = np.where(cur.aa_24m > 0,
                                     "Class AA in last 24 months - a second AA triggers license action (HSC 1424.5)", "")
    cur = cur.sort_values("p_serious_12m", ascending=False).reset_index(drop=True)
    cur["rank"] = cur.index + 1
    capped = set(cur.facid.iloc[select_top(cur.p_serious_12m, cur.region, K, REGION_CAP)])
    cur["in_top25"] = cur["rank"] <= K
    cur["in_top25_region_capped"] = cur.facid.isin(capped)
    cols = ["rank", "facid", "facility_name", "fac_fdr", "fac_type_code", "district_office", "region",
            "p_serious_12m", "in_top25", "in_top25_region_capped", "n_12m", "n_36m", "serious_12m", "serious_36m", "aa_all", "aa_24m",
            "death_36m", "abuse_36m", "staffing_36m", "amount_36m", "days_since_last", "why", "statutory_flag"]
    cur[cols].to_csv(ANALYSIS / "facility_risk_scores_all.csv", index=False)
    top = cur[cols].head(K)
    top.to_csv(ANALYSIS / "top25_priority_facilities.csv", index=False)
    cur[cols][cur.in_top25_region_capped].to_csv(ANALYSIS / "top25_region_capped.csv", index=False)

    coef = pd.DataFrame({"feature": FEATURES, "meaning": [FEATURE_LABELS[f] for f in FEATURES],
                         "std_coefficient": lr.named_steps["logisticregression"].coef_[0]}).sort_values(
        "std_coefficient", ascending=False)
    coef.to_csv(ANALYSIS / "model_coefficients.csv", index=False)
    plot(summary, top, res.groupby("snapshot").base_rate.first().mean())
    meta = {"scored_as_of": str(T_now.date()), "facilities_scored": int(len(cur)),
            "horizon_months": HORIZON_MONTHS, "label": "any Class AA/A citation or hospital IJ AP issued in horizon",
            "expected_serious_in_top25": round(float(top.p_serious_12m.sum()), 1),
            "expected_serious_random25": round(float(cur.p_serious_12m.mean() * K), 1),
            "expected_serious_region_capped25": round(float(cur.p_serious_12m[cur.in_top25_region_capped].sum()), 1),
            "region_cap": REGION_CAP,
            "active_facilities_with_class_aa_last_24m": int((cur.aa_24m > 0).sum()),
            "top25_with_class_aa_last_24m": int((top.aa_24m > 0).sum())}
    (ANALYSIS / "scoring_metadata.json").write_text(json.dumps(meta, indent=2))
    log(f"  scored {len(cur):,} active facilities as of {T_now.date()}; top-25 written")
    log("  " + top[["rank", "facility_name", "fac_type_code", "district_office", "p_serious_12m", "serious_36m"]]
        .to_string(index=False).replace("\n", "\n  "))
