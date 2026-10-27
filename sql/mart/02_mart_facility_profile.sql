-- mart_facility_profile: one row per facility, history summarised as of the last date in the data.
CREATE OR REPLACE TABLE mart_facility_profile AS
WITH snap AS (SELECT max(issue_date) AS as_of FROM mart_enforcement_enriched)
SELECT
    m.facid,
    any_value(m.facility_name)  AS facility_name,
    any_value(m.fac_type_code)  AS fac_type_code,
    any_value(m.fac_fdr)        AS fac_fdr,
    any_value(m.is_ltc)         AS is_ltc,
    any_value(m.district_office) AS district_office,
    any_value(m.region)         AS region,
    any_value(a.as_of)          AS as_of_date,
    count(*)                                                            AS actions_total,
    count(*) FILTER (WHERE m.issue_date > a.as_of - INTERVAL 12 MONTH)  AS actions_12m,
    count(*) FILTER (WHERE m.issue_date > a.as_of - INTERVAL 36 MONTH)  AS actions_36m,
    count(*) FILTER (WHERE m.is_serious)                                AS serious_total,
    count(*) FILTER (WHERE m.is_serious AND m.issue_date > a.as_of - INTERVAL 36 MONTH) AS serious_36m,
    count(*) FILTER (WHERE m.class_code = 'AA')                         AS class_aa_total,
    count(*) FILTER (WHERE m.is_death_related)                          AS death_related_total,
    count(*) FILTER (WHERE m.category_group = 'Abuse (not self-reported)') AS abuse_total,
    sum(m.amount_due_final)                                             AS amount_due_final_total,
    sum(m.amount_due_final) FILTER (WHERE m.issue_date > a.as_of - INTERVAL 36 MONTH) AS amount_due_final_36m,
    sum(m.amount_balance_due)                                           AS balance_due_total,
    avg(m.was_appealed::INT)                                            AS appeal_rate,
    min(m.issue_date)                                                   AS first_action_date,
    max(m.issue_date)                                                   AS last_action_date,
    datediff('day', max(m.issue_date), any_value(a.as_of))              AS days_since_last_action
FROM mart_enforcement_enriched m, snap a
GROUP BY m.facid;
