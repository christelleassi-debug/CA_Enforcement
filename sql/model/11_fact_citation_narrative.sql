-- fact_citation_narrative: 1:0..1 extension of the fact (LTC class AA/A/B citations issued 2012-2017).
-- Join key PENALTY_NUMBER; FACID agrees with the fact on 100% of matched rows (tested).
CREATE OR REPLACE TABLE fact_citation_narrative AS
SELECT
    n.penalty_number,
    n.facid,
    n.class_assessed_initial                         AS narrative_class_code,
    n.penalty_issue_date                             AS narrative_issue_date,
    n.eventid                                        AS narrative_eventid,
    n.narrative_length                               AS narrative_length_published,
    length(n.narrative)                              AS narrative_length_chars,
    n.narrative,
    f.penalty_number IS NOT NULL                     AS has_fact_match
FROM stg_ltc_narratives n
LEFT JOIN fact_enforcement_action f USING (penalty_number);

-- bridge_narrative_regulation: many-to-many narrative <-> cited regulation (text-to-metrics).
-- Federal F-tags (e.g. F323 accidents/supervision) and CA Title 22 sections (e.g. 72311 nursing service).
CREATE OR REPLACE TABLE bridge_narrative_regulation AS
WITH ftags AS (
    SELECT penalty_number, 'F-tag' AS reg_type,
           'F' || unnest(regexp_extract_all(narrative, '\bF\s?(\d{3})\b', 1)) AS reg_code
    FROM fact_citation_narrative
),
t22 AS (
    SELECT penalty_number, 'Title 22' AS reg_type,
           unnest(regexp_extract_all(narrative, '\b(7[0-9]{4})\b', 1)) AS reg_code
    FROM fact_citation_narrative
)
SELECT DISTINCT penalty_number, reg_type, reg_code FROM (SELECT * FROM ftags UNION ALL SELECT * FROM t22);
