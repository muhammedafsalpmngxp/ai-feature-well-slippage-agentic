-- well_slippage - Well slippage listing
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T11:40:09+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  212
-- Contract:   well_id, flaf_deadline, flaf_actual, flaf_status, flaf_variance_days, 
--             pegging_deadline, pegging_actual, pegging_status, pegging_variance_days, 
--             rig_on_deadline, rig_on_actual, rig_on_status, rig_on_variance_days, 
--             rig_off_deadline, rig_off_actual, rig_off_status, rig_off_variance_days, 
--             well_slippage_status
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH well_data AS
(
    SELECT
        wm.well_id,
        wm.ex_rig_on_date,
        wm.flaf_issue_date,
        wm.pegged_date,
        wm.rig_on_date,
        wm.ex_rig_off_date,
        wm.rig_off_date
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
),
milestone_dates AS
(
    SELECT
        wd.well_id,
        wd.ex_rig_on_date,
        DATEADD(day, -90, wd.ex_rig_on_date) AS flaf_deadline,
        wd.flaf_issue_date AS flaf_actual,
        DATEADD(day, -60, wd.ex_rig_on_date) AS pegging_deadline,
        wd.pegged_date AS pegging_actual,
        wd.ex_rig_on_date AS rig_on_deadline,
        wd.rig_on_date AS rig_on_actual,
        wd.ex_rig_off_date AS rig_off_deadline,
        wd.rig_off_date AS rig_off_actual
    FROM well_data AS wd
),
milestone_status AS
(
    SELECT
        md.well_id,
        md.ex_rig_on_date,
        md.flaf_deadline,
        md.flaf_actual,
        CASE
            WHEN md.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN md.flaf_actual IS NULL AND CAST(GETDATE() AS date) > md.flaf_deadline THEN 'MISSED'
            WHEN md.flaf_actual IS NULL THEN 'PENDING'
            WHEN md.flaf_actual < md.flaf_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN md.flaf_actual = md.flaf_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS flaf_status,
        md.pegging_deadline,
        md.pegging_actual,
        CASE
            WHEN md.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN md.pegging_actual IS NULL AND CAST(GETDATE() AS date) > md.pegging_deadline THEN 'MISSED'
            WHEN md.pegging_actual IS NULL THEN 'PENDING'
            WHEN md.pegging_actual < md.pegging_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN md.pegging_actual = md.pegging_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS pegging_status,
        md.rig_on_deadline,
        md.rig_on_actual,
        CASE
            WHEN md.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN md.rig_on_actual IS NULL AND CAST(GETDATE() AS date) > md.rig_on_deadline THEN 'MISSED'
            WHEN md.rig_on_actual IS NULL THEN 'PENDING'
            WHEN md.rig_on_actual < md.rig_on_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN md.rig_on_actual = md.rig_on_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_on_status,
        md.rig_off_deadline,
        md.rig_off_actual,
        CASE
            WHEN md.rig_off_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN md.rig_off_actual IS NULL AND CAST(GETDATE() AS date) > md.rig_off_deadline THEN 'MISSED'
            WHEN md.rig_off_actual IS NULL THEN 'PENDING'
            WHEN md.rig_off_actual < md.rig_off_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN md.rig_off_actual = md.rig_off_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_off_status
    FROM milestone_dates AS md
),
headline AS
(
    SELECT
        ms.*,
        CASE
            WHEN ms.rig_on_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG ON'
            WHEN ms.flaf_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - FLAF'
            WHEN ms.pegging_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - PEGGING'
            WHEN ms.rig_off_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG OFF'
        END AS well_slippage_status
    FROM milestone_status AS ms
),
result_set AS
(
    SELECT
        h.well_id,
        h.flaf_deadline,
        h.flaf_actual,
        h.flaf_status,
        CASE
            WHEN h.flaf_status = 'MISSED' THEN DATEDIFF(day, h.flaf_deadline, CAST(GETDATE() AS date))
            WHEN h.flaf_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED') THEN DATEDIFF(day, h.flaf_deadline, h.flaf_actual)
        END AS flaf_variance_days,
        h.pegging_deadline,
        h.pegging_actual,
        h.pegging_status,
        CASE
            WHEN h.pegging_status = 'MISSED' THEN DATEDIFF(day, h.pegging_deadline, CAST(GETDATE() AS date))
            WHEN h.pegging_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED') THEN DATEDIFF(day, h.pegging_deadline, h.pegging_actual)
        END AS pegging_variance_days,
        h.rig_on_deadline,
        h.rig_on_actual,
        h.rig_on_status,
        CASE
            WHEN h.rig_on_status = 'MISSED' THEN DATEDIFF(day, h.rig_on_deadline, CAST(GETDATE() AS date))
            WHEN h.rig_on_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED') THEN DATEDIFF(day, h.rig_on_deadline, h.rig_on_actual)
        END AS rig_on_variance_days,
        h.rig_off_deadline,
        h.rig_off_actual,
        h.rig_off_status,
        CASE
            WHEN h.rig_off_status = 'MISSED' THEN DATEDIFF(day, h.rig_off_deadline, CAST(GETDATE() AS date))
            WHEN h.rig_off_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED') THEN DATEDIFF(day, h.rig_off_deadline, h.rig_off_actual)
        END AS rig_off_variance_days,
        h.well_slippage_status
    FROM headline AS h
    WHERE h.well_slippage_status IS NOT NULL
)
SELECT
    rs.well_id AS well_id,
    rs.flaf_deadline AS flaf_deadline,
    rs.flaf_actual AS flaf_actual,
    rs.flaf_status AS flaf_status,
    rs.flaf_variance_days AS flaf_variance_days,
    rs.pegging_deadline AS pegging_deadline,
    rs.pegging_actual AS pegging_actual,
    rs.pegging_status AS pegging_status,
    rs.pegging_variance_days AS pegging_variance_days,
    rs.rig_on_deadline AS rig_on_deadline,
    rs.rig_on_actual AS rig_on_actual,
    rs.rig_on_status AS rig_on_status,
    rs.rig_on_variance_days AS rig_on_variance_days,
    rs.rig_off_deadline AS rig_off_deadline,
    rs.rig_off_actual AS rig_off_actual,
    rs.rig_off_status AS rig_off_status,
    rs.rig_off_variance_days AS rig_off_variance_days,
    rs.well_slippage_status AS well_slippage_status
FROM result_set AS rs
ORDER BY rs.rig_on_deadline ASC
