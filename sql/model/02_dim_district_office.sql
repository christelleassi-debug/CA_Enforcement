-- dim_district_office: one row per district office name as it appears in the facts,
-- resolved to the lookup's offices/counties through a documented crosswalk (config/district_crosswalk.csv).
CREATE OR REPLACE TABLE dim_district_office AS
WITH xw AS (
    SELECT x.*, unnest(CASE WHEN lookup_office_names IS NULL THEN [NULL]
                            ELSE string_split(lookup_office_names, ';') END) AS lookup_office
    FROM stg_district_crosswalk x
),
resolved AS (
    SELECT xw.district_office,
           any_value(xw.match_type)  AS match_type,
           any_value(xw.region)      AS region,
           any_value(xw.note)        AS crosswalk_note,
           string_agg(DISTINCT xw.lookup_office, '; ') FILTER (WHERE l.office_name IS NOT NULL) AS lookup_offices,
           string_agg(DISTINCT l.counties_served, ' | ')                                         AS counties_served
    FROM xw LEFT JOIN stg_lookup_district_office l ON l.office_name = xw.lookup_office
    GROUP BY 1
)
SELECT f.district_office,
       coalesce(r.match_type, 'missing_from_crosswalk') AS match_type,
       r.region, r.lookup_offices, r.counties_served, r.crosswalk_note
FROM (SELECT DISTINCT district_office FROM stg_enforcement_actions WHERE district_office IS NOT NULL) f
LEFT JOIN resolved r USING (district_office);
