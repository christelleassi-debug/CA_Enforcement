-- dim_facility: one row per FACID.
-- Profiling showed FACID functionally determines name, LTC flag, type, FDR roll-up and district
-- (0 FACIDs with >1 distinct value), so facility attributes are lifted out of the fact.
-- any_value() is safe because tests/ asserts that functional dependency on every run.
CREATE OR REPLACE TABLE dim_facility AS
SELECT
    facid,
    any_value(facility_name)    AS facility_name,
    any_value(ltc) = 'LTC'      AS is_ltc,
    any_value(fac_type_code)    AS fac_type_code,
    any_value(fac_fdr)          AS fac_fdr,
    any_value(district_office)  AS district_office,
    min(penalty_issue_date)     AS first_action_date,
    max(penalty_issue_date)     AS last_action_date
FROM stg_enforcement_actions
GROUP BY facid;
