-- dim_penalty_category: one row per distinct PENALTY_CATEGORY value in the facts.
-- The lookup spells adverse events as 'AE: <text>' while the data uses 'AE17 - <text>', and has small
-- spelling drift ('edevice', 'Fac' vs 'Facility'), so matching is: exact -> normalised key -> fuzzy (Jaro-Winkler >= 0.93; 0.90 produced a false match: "...by health care worker" -> "...by person other than a h/c worker").
CREATE OR REPLACE TABLE dim_penalty_category AS
WITH f AS (
    SELECT DISTINCT penalty_category AS category_name FROM stg_enforcement_actions WHERE penalty_category IS NOT NULL
),
norm AS (
    SELECT category_name,
           regexp_extract(category_name, '^AE\s*(\d+)', 1)                                   AS ae_code,
           lower(regexp_replace(regexp_replace(category_name, '^(AE\s*\d*\s*[-:]\s*)', ''), '[^A-Za-z0-9]', '', 'g')) AS k
    FROM f
),
lk AS (
    SELECT category_name AS lookup_name,
           lower(regexp_replace(regexp_replace(category_name, '^(AE\s*\d*\s*[-:]\s*)', ''), '[^A-Za-z0-9]', '', 'g')) AS k
    FROM stg_lookup_penalty_category
),
best AS (
    SELECT n.category_name, lk.lookup_name, jaro_winkler_similarity(n.k, lk.k) AS sim,
           row_number() OVER (PARTITION BY n.category_name ORDER BY jaro_winkler_similarity(n.k, lk.k) DESC) AS rn
    FROM norm n CROSS JOIN lk
)
SELECT
    md5(n.category_name)[:12]                                           AS category_key,
    n.category_name,
    nullif(n.ae_code, '')                                               AS ae_code,
    CASE WHEN b.sim >= 0.93 THEN b.lookup_name END                      AS lookup_category_name,
    CASE WHEN b.sim = 1 AND n.category_name = b.lookup_name THEN 'exact'
         WHEN b.sim = 1 THEN 'normalised'
         WHEN b.sim >= 0.93 THEN 'fuzzy'
         ELSE 'not_in_lookup' END                                       AS lookup_match,
    round(b.sim, 3)                                                     AS match_similarity,
    CASE
        WHEN n.category_name ~ '^AE'                                          THEN 'Adverse event (hospital)'
        WHEN n.category_name ~* 'breach'                                      THEN 'Privacy breach'
        WHEN n.category_name ~* '^(DHPPD|Staffing)|3\.2 hours'                THEN 'Staffing'
        WHEN n.category_name ~ '^A\d+ - '                                     THEN 'Hospital unit (2023-24 AP)'
        WHEN n.category_name ~* 'immediate jeopardy'                          THEN 'Hospital non-AE jeopardy'
        WHEN n.category_name ~* '^Abuse'                                      THEN 'Abuse (not self-reported)'
        WHEN n.category_name IN ('Patient Care','Medication','Dietary')       THEN 'Patient care'
        WHEN n.category_name = 'Patient Rights'                               THEN 'Patient rights'
        WHEN n.category_name IN ('Problem Transfer','Refusal to Readmit')     THEN 'Transfer / readmission'
        WHEN n.category_name = 'Physical Environment'                         THEN 'Physical environment'
        WHEN n.category_name ~* 'willful|record|administration|financial|council|notification|remedy|ombudsman'
                                                                              THEN 'Administration & records'
        ELSE 'Other' END                                                AS category_group
FROM norm n LEFT JOIN best b ON b.category_name = n.category_name AND b.rn = 1;
