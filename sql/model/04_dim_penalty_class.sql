-- dim_penalty_class: one row per initial class code.
-- PENALTY_DETAIL <-> CLASS_ASSESSED_INITIAL is exactly 1:1 in the data (verified by crosstab),
-- so the class code is the key and the detail text is an attribute.
-- severity_rank / program are business rules taken from the CDPH "About ... State Enforcement Actions" paper.
CREATE OR REPLACE TABLE dim_penalty_class AS
WITH f AS (
    SELECT class_assessed_initial AS class_code,
           any_value(penalty_detail) AS penalty_detail,
           any_value(penalty_type)   AS penalty_type
    FROM stg_enforcement_actions GROUP BY 1
),
lk AS (
    SELECT class_code_norm, any_value(class_description) AS lookup_description
    FROM stg_lookup_class WHERE lookup_block IN ('initial', 'penalty_detail') GROUP BY 1
)
SELECT
    f.class_code,
    coalesce(f.penalty_detail, lk.lookup_description,
             'Hospital AP - unit category (no penalty detail recorded)')       AS class_description,
    f.penalty_type,
    regexp_extract(coalesce(f.penalty_detail, lk.lookup_description), 'HSC\s*([0-9.]+(\([^)]*\))*)', 1) AS hsc_section,
    lk.class_code_norm IS NOT NULL                                             AS in_lookup,
    CASE WHEN f.class_code IN ('AA','A','B','WF','WO','RD') THEN 'LTC citation (HSC 1424/1432)'
         WHEN f.class_code = 'AP NHPPD'                     THEN 'SNF staffing AP (HSC 1276.5)'
         WHEN f.class_code IN ('AP BR','FTR BR','FTR RES')  THEN 'Medical-information breach (HSC 1280.15)'
         ELSE 'Hospital AP (HSC 1280.3 / 1280.4)' END                          AS program,
    CASE f.class_code WHEN 'AA' THEN 5 WHEN 'A' THEN 4 WHEN 'AP IJ' THEN 4
                      WHEN 'WF' THEN 3 WHEN 'WO' THEN 3
                      WHEN 'FTR AE' THEN 1 WHEN 'FTR BR' THEN 1 WHEN 'FTR RES' THEN 1
                      ELSE 2 END                                               AS severity_rank,
    f.class_code IN ('AA','A','AP IJ')                                         AS is_serious
FROM f LEFT JOIN lk ON lk.class_code_norm = f.class_code;
