-- well_slippage - Well slippage listing
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T04:37:33+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  216
-- Contract:   well_id, flaf_deadline, flaf_status, flaf_variance_days, pegging_deadline, 
--             pegging_status, pegging_variance_days, construction_deadline, 
--             construction_status, construction_variance_days, rig_on_deadline, rig_on_status, 
--             rig_on_variance_days, rig_off_deadline, rig_off_status, rig_off_variance_days, 
--             hookup_deadline, hookup_status, hookup_variance_days, well_slippage_status
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH deadline_data AS
(
    SELECT
        wm.well_id,
        wm.ex_rig_on_date,
        wm.flaf_issue_date,
        wm.pegged_date,
        wm.rig_on_date,
        wm.rig_off_date,
        DATEADD(day, -90, wm.ex_rig_on_date) AS flaf_deadline,
        DATEADD(day, -60, wm.ex_rig_on_date) AS pegging_deadline,
        DATEADD(day, -1, wm.ex_rig_on_date) AS construction_deadline,
        wm.ex_rig_on_date AS rig_on_deadline,
        wm.ex_rig_off_date AS rig_off_deadline,
        CASE
            WHEN wm.rig_off_date IS NOT NULL
                THEN DATEADD(day, 2, wm.rig_off_date)
            ELSE DATEADD(day, 2, wm.ex_rig_off_date)
        END AS hookup_deadline
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
),
status_data AS
(
    SELECT
        dd.well_id,
        dd.ex_rig_on_date,
        dd.flaf_issue_date,
        dd.pegged_date,
        dd.rig_on_date,
        dd.rig_off_date,
        dd.flaf_deadline,
        dd.pegging_deadline,
        dd.construction_deadline,
        dd.rig_on_deadline,
        dd.rig_off_deadline,
        dd.hookup_deadline,
        CASE
            WHEN dd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.flaf_issue_date IS NULL
                 AND CAST(GETDATE() AS date) > dd.flaf_deadline THEN 'MISSED'
            WHEN dd.flaf_issue_date IS NULL THEN 'PENDING'
            WHEN dd.flaf_issue_date < dd.flaf_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.flaf_issue_date = dd.flaf_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS flaf_status,
        CASE
            WHEN dd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.pegged_date IS NULL
                 AND CAST(GETDATE() AS date) > dd.pegging_deadline THEN 'MISSED'
            WHEN dd.pegged_date IS NULL THEN 'PENDING'
            WHEN dd.pegged_date < dd.pegging_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.pegged_date = dd.pegging_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS pegging_status,
        CASE
            WHEN dd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.rig_on_date IS NULL
                 AND CAST(GETDATE() AS date) > dd.construction_deadline THEN 'MISSED'
            WHEN dd.rig_on_date IS NULL THEN 'PENDING'
            ELSE 'RIG_ARRIVED'
        END AS construction_status,
        CASE
            WHEN dd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.rig_on_date IS NULL
                 AND CAST(GETDATE() AS date) > dd.rig_on_deadline THEN 'MISSED'
            WHEN dd.rig_on_date IS NULL THEN 'PENDING'
            WHEN dd.rig_on_date < dd.rig_on_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.rig_on_date = dd.rig_on_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_on_status,
        CASE
            WHEN dd.rig_off_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN dd.rig_off_date IS NULL
                 AND CAST(GETDATE() AS date) > dd.rig_off_deadline THEN 'MISSED'
            WHEN dd.rig_off_date IS NULL THEN 'PENDING'
            WHEN dd.rig_off_date < dd.rig_off_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN dd.rig_off_date = dd.rig_off_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_off_status,
        CASE
            WHEN dd.rig_off_deadline IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN CAST(GETDATE() AS date) > dd.hookup_deadline THEN 'MISSED'
            ELSE 'PENDING'
        END AS hookup_status
    FROM deadline_data AS dd
),
headline_data AS
(
    SELECT
        sd.*,
        CASE
            WHEN sd.rig_on_status IN ('MISSED', 'DELAYED')
                THEN 'SLIPPED - RIG ON'
            WHEN sd.flaf_status IN ('MISSED', 'DELAYED')
                THEN 'SLIPPED - FLAF'
            WHEN sd.pegging_status IN ('MISSED', 'DELAYED')
                THEN 'SLIPPED - PEGGING'
            WHEN sd.construction_status = 'MISSED'
                THEN 'SLIPPED - CONSTRUCTION'
            WHEN sd.rig_off_status IN ('MISSED', 'DELAYED')
                THEN 'SLIPPED - RIG OFF'
            WHEN sd.hookup_status IN ('MISSED', 'DELAYED')
                THEN 'SLIPPED - HOOK-UP'
            ELSE NULL
        END AS well_slippage_status
    FROM status_data AS sd
)
SELECT
    hd.well_id AS well_id,
    hd.flaf_deadline AS flaf_deadline,
    hd.flaf_status AS flaf_status,
    CASE
        WHEN hd.flaf_status = 'MISSED'
            THEN DATEDIFF(day, hd.flaf_deadline, CAST(GETDATE() AS date))
        WHEN hd.flaf_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
            THEN DATEDIFF(day, hd.flaf_deadline, hd.flaf_issue_date)
        ELSE NULL
    END AS flaf_variance_days,
    hd.pegging_deadline AS pegging_deadline,
    hd.pegging_status AS pegging_status,
    CASE
        WHEN hd.pegging_status = 'MISSED'
            THEN DATEDIFF(day, hd.pegging_deadline, CAST(GETDATE() AS date))
        WHEN hd.pegging_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
            THEN DATEDIFF(day, hd.pegging_deadline, hd.pegged_date)
        ELSE NULL
    END AS pegging_variance_days,
    hd.construction_deadline AS construction_deadline,
    hd.construction_status AS construction_status,
    CASE
        WHEN hd.construction_status = 'MISSED'
            THEN DATEDIFF(day, hd.construction_deadline, CAST(GETDATE() AS date))
        ELSE NULL
    END AS construction_variance_days,
    hd.rig_on_deadline AS rig_on_deadline,
    hd.rig_on_status AS rig_on_status,
    CASE
        WHEN hd.rig_on_status = 'MISSED'
            THEN DATEDIFF(day, hd.rig_on_deadline, CAST(GETDATE() AS date))
        WHEN hd.rig_on_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
            THEN DATEDIFF(day, hd.rig_on_deadline, hd.rig_on_date)
        ELSE NULL
    END AS rig_on_variance_days,
    hd.rig_off_deadline AS rig_off_deadline,
    hd.rig_off_status AS rig_off_status,
    CASE
        WHEN hd.rig_off_status = 'MISSED'
            THEN DATEDIFF(day, hd.rig_off_deadline, CAST(GETDATE() AS date))
        WHEN hd.rig_off_status IN ('AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
            THEN DATEDIFF(day, hd.rig_off_deadline, hd.rig_off_date)
        ELSE NULL
    END AS rig_off_variance_days,
    hd.hookup_deadline AS hookup_deadline,
    hd.hookup_status AS hookup_status,
    CASE
        WHEN hd.hookup_status = 'MISSED'
            THEN DATEDIFF(day, hd.hookup_deadline, CAST(GETDATE() AS date))
        ELSE NULL
    END AS hookup_variance_days,
    hd.well_slippage_status AS well_slippage_status
FROM headline_data AS hd
WHERE hd.well_slippage_status IS NOT NULL
ORDER BY hd.ex_rig_on_date ASC
