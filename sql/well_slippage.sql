-- well_slippage - Well slippage listing
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T09:32:56+00:00
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
        w.well_id AS well_id,
        w.ex_rig_on_date AS ex_rig_on_date,
        w.flaf_issue_date AS flaf_actual,
        w.pegged_date AS pegging_actual,
        w.rig_on_date AS rig_on_actual,
        w.ex_rig_off_date AS ex_rig_off_date,
        w.rig_off_date AS rig_off_actual
    FROM well.well_master AS w
    WHERE w.eng_completion_date IS NULL
),
deadline_data AS
(
    SELECT
        wd.well_id AS well_id,
        DATEADD(day, -90, wd.ex_rig_on_date) AS flaf_deadline,
        wd.flaf_actual AS flaf_actual,
        DATEADD(day, -60, wd.ex_rig_on_date) AS pegging_deadline,
        wd.pegging_actual AS pegging_actual,
        wd.ex_rig_on_date AS rig_on_deadline,
        wd.rig_on_actual AS rig_on_actual,
        wd.ex_rig_off_date AS rig_off_deadline,
        wd.rig_off_actual AS rig_off_actual
    FROM well_data AS wd
),
status_data AS
(
    SELECT
        dd.well_id AS well_id,
        dd.flaf_deadline AS flaf_deadline,
        dd.flaf_actual AS flaf_actual,
        CASE
            WHEN dd.flaf_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.flaf_actual IS NULL AND CAST(GETDATE() AS date) > dd.flaf_deadline THEN 'MISSED'
            WHEN dd.flaf_actual IS NULL THEN 'PENDING'
            WHEN dd.flaf_actual < dd.flaf_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.flaf_actual = dd.flaf_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS flaf_status,
        CASE
            WHEN dd.flaf_deadline IS NULL THEN NULL
            WHEN dd.flaf_actual IS NOT NULL THEN DATEDIFF(day, dd.flaf_deadline, dd.flaf_actual)
            WHEN CAST(GETDATE() AS date) > dd.flaf_deadline THEN DATEDIFF(day, dd.flaf_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS flaf_variance_days,
        dd.pegging_deadline AS pegging_deadline,
        dd.pegging_actual AS pegging_actual,
        CASE
            WHEN dd.pegging_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.pegging_actual IS NULL AND CAST(GETDATE() AS date) > dd.pegging_deadline THEN 'MISSED'
            WHEN dd.pegging_actual IS NULL THEN 'PENDING'
            WHEN dd.pegging_actual < dd.pegging_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.pegging_actual = dd.pegging_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS pegging_status,
        CASE
            WHEN dd.pegging_deadline IS NULL THEN NULL
            WHEN dd.pegging_actual IS NOT NULL THEN DATEDIFF(day, dd.pegging_deadline, dd.pegging_actual)
            WHEN CAST(GETDATE() AS date) > dd.pegging_deadline THEN DATEDIFF(day, dd.pegging_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS pegging_variance_days,
        dd.rig_on_deadline AS rig_on_deadline,
        dd.rig_on_actual AS rig_on_actual,
        CASE
            WHEN dd.rig_on_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.rig_on_actual IS NULL AND CAST(GETDATE() AS date) > dd.rig_on_deadline THEN 'MISSED'
            WHEN dd.rig_on_actual IS NULL THEN 'PENDING'
            WHEN dd.rig_on_actual < dd.rig_on_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.rig_on_actual = dd.rig_on_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_on_status,
        CASE
            WHEN dd.rig_on_deadline IS NULL THEN NULL
            WHEN dd.rig_on_actual IS NOT NULL THEN DATEDIFF(day, dd.rig_on_deadline, dd.rig_on_actual)
            WHEN CAST(GETDATE() AS date) > dd.rig_on_deadline THEN DATEDIFF(day, dd.rig_on_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS rig_on_variance_days,
        dd.rig_off_deadline AS rig_off_deadline,
        dd.rig_off_actual AS rig_off_actual,
        CASE
            WHEN dd.rig_off_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.rig_off_actual IS NULL AND CAST(GETDATE() AS date) > dd.rig_off_deadline THEN 'MISSED'
            WHEN dd.rig_off_actual IS NULL THEN 'PENDING'
            WHEN dd.rig_off_actual < dd.rig_off_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.rig_off_actual = dd.rig_off_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_off_status,
        CASE
            WHEN dd.rig_off_deadline IS NULL THEN NULL
            WHEN dd.rig_off_actual IS NOT NULL THEN DATEDIFF(day, dd.rig_off_deadline, dd.rig_off_actual)
            WHEN CAST(GETDATE() AS date) > dd.rig_off_deadline THEN DATEDIFF(day, dd.rig_off_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS rig_off_variance_days
    FROM deadline_data AS dd
),
headline_data AS
(
    SELECT
        sd.well_id AS well_id,
        sd.flaf_deadline AS flaf_deadline,
        sd.flaf_actual AS flaf_actual,
        sd.flaf_status AS flaf_status,
        sd.flaf_variance_days AS flaf_variance_days,
        sd.pegging_deadline AS pegging_deadline,
        sd.pegging_actual AS pegging_actual,
        sd.pegging_status AS pegging_status,
        sd.pegging_variance_days AS pegging_variance_days,
        sd.rig_on_deadline AS rig_on_deadline,
        sd.rig_on_actual AS rig_on_actual,
        sd.rig_on_status AS rig_on_status,
        sd.rig_on_variance_days AS rig_on_variance_days,
        sd.rig_off_deadline AS rig_off_deadline,
        sd.rig_off_actual AS rig_off_actual,
        sd.rig_off_status AS rig_off_status,
        sd.rig_off_variance_days AS rig_off_variance_days,
        CASE
            WHEN sd.rig_on_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG ON'
            WHEN sd.flaf_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - FLAF'
            WHEN sd.pegging_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - PEGGING'
            WHEN sd.rig_off_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG OFF'
            ELSE NULL
        END AS well_slippage_status,
        sd.rig_on_deadline AS sort_rig_on_date
    FROM status_data AS sd
)
SELECT
    hd.well_id AS well_id,
    hd.flaf_deadline AS flaf_deadline,
    hd.flaf_actual AS flaf_actual,
    hd.flaf_status AS flaf_status,
    hd.flaf_variance_days AS flaf_variance_days,
    hd.pegging_deadline AS pegging_deadline,
    hd.pegging_actual AS pegging_actual,
    hd.pegging_status AS pegging_status,
    hd.pegging_variance_days AS pegging_variance_days,
    hd.rig_on_deadline AS rig_on_deadline,
    hd.rig_on_actual AS rig_on_actual,
    hd.rig_on_status AS rig_on_status,
    hd.rig_on_variance_days AS rig_on_variance_days,
    hd.rig_off_deadline AS rig_off_deadline,
    hd.rig_off_actual AS rig_off_actual,
    hd.rig_off_status AS rig_off_status,
    hd.rig_off_variance_days AS rig_off_variance_days,
    hd.well_slippage_status AS well_slippage_status
FROM headline_data AS hd
WHERE hd.well_slippage_status IS NOT NULL
ORDER BY hd.sort_rig_on_date ASC
