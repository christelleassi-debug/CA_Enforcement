-- dim_facility_type: one row per ELMS facility-type code.
-- Sources: lookup FAC_TYPE_CODE block (names/definitions) + FAC_FDR block (roll-up),
-- plus any code seen on facts but absent from the lookup (e.g. SPHOSP only appears in the FDR roll-up list).
CREATE OR REPLACE TABLE dim_facility_type AS
WITH codes AS (
    SELECT fac_type_code FROM stg_lookup_fac_type
    UNION SELECT fac_type_code FROM stg_lookup_fac_fdr
    UNION SELECT DISTINCT fac_type_code FROM stg_enforcement_actions
),
names AS (
    SELECT fac_type_code, any_value(fac_type_name) AS fac_type_name, any_value(definition) AS fac_type_definition
    FROM stg_lookup_fac_type GROUP BY 1
),
fdr AS (
    SELECT fac_type_code, string_agg(DISTINCT fdr_name, ' / ') AS fdr_rollup_lookup
    FROM stg_lookup_fac_fdr GROUP BY 1
)
SELECT
    c.fac_type_code,
    coalesce(n.fac_type_name, f.fdr_rollup_lookup, c.fac_type_code)      AS fac_type_name,
    n.fac_type_definition,
    f.fdr_rollup_lookup,
    n.fac_type_code IS NOT NULL                                          AS in_lookup_type_block,
    f.fac_type_code IS NOT NULL                                          AS in_lookup_fdr_block,
    c.fac_type_code IN (SELECT fac_type_code FROM stg_enforcement_actions) AS seen_in_facts
FROM codes c
LEFT JOIN names n USING (fac_type_code)
LEFT JOIN fdr f USING (fac_type_code)
WHERE c.fac_type_code IS NOT NULL;
