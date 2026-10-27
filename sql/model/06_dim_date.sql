-- dim_date: calendar spine covering every date in the facts. CA state fiscal year runs Jul 1 - Jun 30.
CREATE OR REPLACE TABLE dim_date AS
WITH bounds AS (
    SELECT least(min(penalty_issue_date), min(violation_from_date)) AS d0,
           greatest(max(penalty_issue_date), max(appeal_due_date))  AS d1
    FROM stg_enforcement_actions
)
SELECT
    d::DATE                                         AS date_key,
    year(d)                                         AS cal_year,
    quarter(d)                                      AS cal_quarter,
    month(d)                                        AS cal_month,
    CASE WHEN month(d) >= 7 THEN year(d) ELSE year(d) - 1 END AS sfy_start_year,
    'SFY' || (CASE WHEN month(d) >= 7 THEN year(d) ELSE year(d) - 1 END)::VARCHAR || '-' ||
      right(((CASE WHEN month(d) >= 7 THEN year(d) ELSE year(d) - 1 END) + 1)::VARCHAR, 2) AS sfy
FROM bounds, generate_series(bounds.d0, bounds.d1, INTERVAL 1 DAY) t(d);
