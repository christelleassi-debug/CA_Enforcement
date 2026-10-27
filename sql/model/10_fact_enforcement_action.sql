-- fact_enforcement_action: grain = one state enforcement action (PENALTY_NUMBER, verified unique).
CREATE OR REPLACE TABLE fact_enforcement_action AS
SELECT
    e.penalty_number,
    e.facid,                                                  -- FK dim_facility
    e.class_assessed_initial                 AS class_code,   -- FK dim_penalty_class
    c.category_key,                                           -- FK dim_penalty_category (nullable)
    e.penalty_issue_date                     AS issue_date,   -- FK dim_date
    e.sfy,
    e.disposition,
    e.penalty_category_other,
    e.violation_from_date,
    e.violation_to_date,
    -- 4 legacy rows have from/to swapped; their duration is unknowable, so NULL rather than negative
    CASE WHEN e.violation_to_date >= e.violation_from_date
         THEN datediff('day', e.violation_from_date, e.violation_to_date) + 1 END AS violation_days,
    e.violation_to_date < e.violation_from_date                            AS violation_dates_inverted,
    datediff('day', e.violation_to_date, e.penalty_issue_date)             AS days_violation_to_issue,
    e.appealed = 'Yes'                                                     AS was_appealed,
    e.appeal_due_date,
    e.appeal_received_date,
    e.class_assessed_final_raw,
    e.class_assessed_final,
    CASE
        WHEN e.class_assessed_final IS NULL                                     THEN 'Unchanged'
        WHEN e.class_assessed_final IN ('UPHELD') OR e.class_assessed_final = e.class_assessed_initial THEN 'Upheld'
        WHEN e.class_assessed_final LIKE '%TREBLED%'                            THEN 'Trebled'
        WHEN e.class_assessed_final IN ('DROP>DEFICIENCY','DISMISSED BY COURT','REVERSED')
          OR e.class_assessed_final LIKE 'DEPT WITHDREW%'                       THEN 'Dropped / dismissed'
        WHEN e.class_assessed_final LIKE 'APPEAL WITHDRAWN%'                    THEN 'Appeal withdrawn'
        WHEN e.class_assessed_final IN ('UNCOLLECTABLE') OR e.class_assessed_final LIKE 'CHOW%' THEN 'Settled / uncollectable'
        WHEN e.class_assessed_final IN ('AA','A','B')                           THEN
             CASE WHEN (CASE e.class_assessed_final WHEN 'AA' THEN 3 WHEN 'A' THEN 2 ELSE 1 END)
                     < (CASE e.class_assessed_initial WHEN 'AA' THEN 3 WHEN 'A' THEN 2 WHEN 'B' THEN 1 ELSE 0 END)
                  THEN 'Downgraded' ELSE 'Reclassified' END
        ELSE 'Reclassified' END                                             AS final_outcome,
    e.total_amount_initial                   AS amount_initial,
    e.total_amount_due_final                 AS amount_due_final,
    e.total_penalty_offset_amount            AS amount_offset,
    e.total_collected_amount                 AS amount_collected,
    e.total_balance_due                      AS amount_balance_due,
    e.death_related = 'Y'                    AS is_death_related,
    e.eventid,                                                -- survey id (placeholder A99999 nulled in staging)
    e.aclaims_visit_number,
    e.intakeid_all,
    CASE WHEN e.intakeid_all IS NULL THEN 0
         ELSE len(list_distinct(list_filter(string_split(replace(e.intakeid_all, ' ', ''), ','), lambda x: x <> ''))) END AS n_complaint_intakes,
    e.intakeid_all IS NOT NULL               AS is_complaint_driven,
    list_sort(list_distinct(list_filter(string_split(replace(e.priority_all, ' ', ''), ','), lambda x: x <> '')))[1] AS top_intake_priority
FROM stg_enforcement_actions e
LEFT JOIN dim_penalty_category c ON c.category_name = e.penalty_category;
