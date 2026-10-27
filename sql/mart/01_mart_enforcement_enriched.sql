-- mart_enforcement_enriched: analysis-ready, one row per enforcement action.
-- fact + 6 dimensions + optional narrative. Every join is a many-to-one LEFT JOIN, so row count == fact row count (tested).
CREATE OR REPLACE TABLE mart_enforcement_enriched AS
SELECT
    f.penalty_number,
    f.issue_date, d.cal_year, d.cal_quarter, d.sfy, d.sfy_start_year,
    -- facility
    f.facid, fac.facility_name, fac.is_ltc,
    fac.fac_type_code, ft.fac_type_name, fac.fac_fdr,
    fac.district_office, dist.region, dist.match_type AS district_match_type, dist.counties_served,
    -- what was cited
    f.class_code, pc.class_description, pc.program, pc.hsc_section, pc.severity_rank, pc.is_serious,
    cat.category_name, cat.category_group, cat.ae_code,
    f.penalty_category_other,
    f.is_death_related, f.is_complaint_driven, f.n_complaint_intakes, f.top_intake_priority,
    f.violation_from_date, f.violation_to_date, f.violation_days, f.days_violation_to_issue,
    -- money & legal outcome
    f.amount_initial, f.amount_due_final, f.amount_offset, f.amount_collected, f.amount_balance_due,
    f.was_appealed, f.class_assessed_final, f.final_outcome, f.disposition,
    -- survey linkage
    f.eventid,
    -- narrative (2012-2017 LTC only)
    n.penalty_number IS NOT NULL AS has_narrative,
    n.narrative_length_chars,
    regs.f_tags, regs.title22_sections
FROM fact_enforcement_action f
LEFT JOIN dim_date             d    ON d.date_key = f.issue_date
LEFT JOIN dim_facility         fac  ON fac.facid = f.facid
LEFT JOIN dim_facility_type    ft   ON ft.fac_type_code = fac.fac_type_code
LEFT JOIN dim_district_office  dist ON dist.district_office = fac.district_office
LEFT JOIN dim_penalty_class    pc   ON pc.class_code = f.class_code
LEFT JOIN dim_penalty_category cat  ON cat.category_key = f.category_key
LEFT JOIN fact_citation_narrative n ON n.penalty_number = f.penalty_number
LEFT JOIN (
    SELECT penalty_number,
           string_agg(reg_code, ', ' ORDER BY reg_code) FILTER (WHERE reg_type = 'F-tag')    AS f_tags,
           string_agg(reg_code, ', ' ORDER BY reg_code) FILTER (WHERE reg_type = 'Title 22') AS title22_sections
    FROM bridge_narrative_regulation GROUP BY 1
) regs ON regs.penalty_number = f.penalty_number;
