-- well_slippage - Well slippage listing
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-25T05:44:59+00:00
-- Schema:     5c24c5428a67dbce9e06d931f7ad130f8e8c32d5386ef380f15141eb16c6a458
-- Rows then:  222
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
        w.well_id,
        w.ex_rig_on_date,
        w.flaf_issue_date,
        w.pegged_date,
        w.rig_on_date,
        w.ex_rig_off_date,
        w.rig_off_date,
        DATEADD(day, -90, w.ex_rig_on_date) AS flaf_deadline,
        DATEADD(day, -60, w.ex_rig_on_date) AS pegging_deadline,
        w.ex_rig_on_date AS rig_on_deadline,
        w.ex_rig_off_date AS rig_off_deadline
    FROM well.well_master AS w
    WHERE w.eng_completion_date IS NULL
),
status_data AS
(
    SELECT
        wd.*,
        CASE
            WHEN wd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN wd.flaf_issue_date IS NULL
                 AND CAST(GETDATE() AS date) > wd.flaf_deadline THEN 'MISSED'
            WHEN wd.flaf_issue_date IS NULL THEN 'PENDING'
            WHEN wd.flaf_issue_date < wd.flaf_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN wd.flaf_issue_date = wd.flaf_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS flaf_status,
        CASE
            WHEN wd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN wd.pegged_date IS NULL
                 AND CAST(GETDATE() AS date) > wd.pegging_deadline THEN 'MISSED'
            WHEN wd.pegged_date IS NULL THEN 'PENDING'
            WHEN wd.pegged_date < wd.pegging_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN wd.pegged_date = wd.pegging_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS pegging_status,
        CASE
            WHEN wd.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN wd.rig_on_date IS NULL
                 AND CAST(GETDATE() AS date) > wd.rig_on_deadline THEN 'MISSED'
            WHEN wd.rig_on_date IS NULL THEN 'PENDING'
            WHEN wd.rig_on_date < wd.rig_on_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN wd.rig_on_date = wd.rig_on_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_on_status,
        CASE
            WHEN wd.ex_rig_off_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN wd.rig_off_date IS NULL
                 AND CAST(GETDATE() AS date) > wd.rig_off_deadline THEN 'MISSED'
            WHEN wd.rig_off_date IS NULL THEN 'PENDING'
            WHEN wd.rig_off_date < wd.rig_off_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN wd.rig_off_date = wd.rig_off_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_off_status,
        CASE
            WHEN wd.ex_rig_on_date IS NOT NULL
                 AND (
                     wd.flaf_issue_date > wd.flaf_deadline
                     OR (
                         wd.flaf_issue_date IS NULL
                         AND CAST(GETDATE() AS date) > wd.flaf_deadline
                     )
                 ) THEN 1 ELSE 0
        END AS flaf_failed,
        CASE
            WHEN wd.ex_rig_on_date IS NOT NULL
                 AND (
                     wd.pegged_date > wd.pegging_deadline
                     OR (
                         wd.pegged_date IS NULL
                         AND CAST(GETDATE() AS date) > wd.pegging_deadline
                     )
                 ) THEN 1 ELSE 0
        END AS pegging_failed,
        CASE
            WHEN wd.ex_rig_on_date IS NOT NULL
                 AND (
                     wd.rig_on_date > wd.rig_on_deadline
                     OR (
                         wd.rig_on_date IS NULL
                         AND CAST(GETDATE() AS date) > wd.rig_on_deadline
                     )
                 ) THEN 1 ELSE 0
        END AS rig_on_failed,
        CASE
            WHEN wd.ex_rig_off_date IS NOT NULL
                 AND (
                     wd.rig_off_date > wd.rig_off_deadline
                     OR (
                         wd.rig_off_date IS NULL
                         AND CAST(GETDATE() AS date) > wd.rig_off_deadline
                     )
                 ) THEN 1 ELSE 0
        END AS rig_off_failed
    FROM well_data AS wd
),
headline_data AS
(
    SELECT
        sd.*,
        CASE
            WHEN sd.rig_on_failed = 1 THEN 'SLIPPED - RIG ON'
            WHEN sd.flaf_failed = 1 THEN 'SLIPPED - FLAF'
            WHEN sd.pegging_failed = 1 THEN 'SLIPPED - PEGGING'
            WHEN sd.rig_off_failed = 1 THEN 'SLIPPED - RIG OFF'
            ELSE NULL
        END AS well_slippage_status
    FROM status_data AS sd
),
variance_data AS
(
    SELECT
        hd.*,
        CASE
            WHEN hd.flaf_status IN ('MISSED', 'AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
                THEN DATEDIFF(day, hd.flaf_deadline, COALESCE(hd.flaf_issue_date, CAST(GETDATE() AS date)))
            ELSE NULL
        END AS flaf_variance_days,
        CASE
            WHEN hd.pegging_status IN ('MISSED', 'AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
                THEN DATEDIFF(day, hd.pegging_deadline, COALESCE(hd.pegged_date, CAST(GETDATE() AS date)))
            ELSE NULL
        END AS pegging_variance_days,
        CASE
            WHEN hd.rig_on_status IN ('MISSED', 'AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
                THEN DATEDIFF(day, hd.rig_on_deadline, COALESCE(hd.rig_on_date, CAST(GETDATE() AS date)))
            ELSE NULL
        END AS rig_on_variance_days,
        CASE
            WHEN hd.rig_off_status IN ('MISSED', 'AHEAD_OF_SCHEDULE', 'ON_SCHEDULE', 'DELAYED')
                THEN DATEDIFF(day, hd.rig_off_deadline, COALESCE(hd.rig_off_date, CAST(GETDATE() AS date)))
            ELSE NULL
        END AS rig_off_variance_days
    FROM headline_data AS hd
)
SELECT
    vd.well_id AS well_id,
    vd.flaf_deadline AS flaf_deadline,
    vd.flaf_issue_date AS flaf_actual,
    vd.flaf_status AS flaf_status,
    vd.flaf_variance_days AS flaf_variance_days,
    vd.pegging_deadline AS pegging_deadline,
    vd.pegged_date AS pegging_actual,
    vd.pegging_status AS pegging_status,
    vd.pegging_variance_days AS pegging_variance_days,
    vd.rig_on_deadline AS rig_on_deadline,
    vd.rig_on_date AS rig_on_actual,
    vd.rig_on_status AS rig_on_status,
    vd.rig_on_variance_days AS rig_on_variance_days,
    vd.rig_off_deadline AS rig_off_deadline,
    vd.rig_off_date AS rig_off_actual,
    vd.rig_off_status AS rig_off_status,
    vd.rig_off_variance_days AS rig_off_variance_days,
    vd.well_slippage_status AS well_slippage_status
FROM variance_data AS vd
WHERE vd.well_slippage_status IS NOT NULL
ORDER BY vd.ex_rig_on_date
