-- well_slippage - Well slippage listing
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T10:17:38+00:00
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
        w.well_id,
        w.ex_rig_on_date,
        w.flaf_issue_date,
        w.pegged_date,
        w.rig_on_date,
        w.ex_rig_off_date,
        w.rig_off_date
    FROM well.well_master AS w
    WHERE w.eng_completion_date IS NULL
),
deadlines_cte AS
(
    SELECT
        d.well_id,
        d.ex_rig_on_date,
        d.flaf_issue_date,
        DATEADD(day, -90, d.ex_rig_on_date) AS flaf_deadline,
        d.pegged_date,
        DATEADD(day, -60, d.ex_rig_on_date) AS pegging_deadline,
        d.rig_on_date,
        d.ex_rig_on_date AS rig_on_deadline,
        d.ex_rig_off_date,
        d.rig_off_date
    FROM well_data AS d
),
status_cte AS
(
    SELECT
        s.well_id,
        s.ex_rig_on_date,
        s.flaf_issue_date,
        s.flaf_deadline,
        s.pegged_date,
        s.pegging_deadline,
        s.rig_on_date,
        s.rig_on_deadline,
        s.ex_rig_off_date,
        s.rig_off_date,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN s.flaf_issue_date IS NULL
                 AND CAST(GETDATE() AS date) > s.flaf_deadline THEN 'MISSED'
            WHEN s.flaf_issue_date IS NULL THEN 'PENDING'
            WHEN s.flaf_issue_date < s.flaf_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN s.flaf_issue_date = s.flaf_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS flaf_status,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN NULL
            WHEN s.flaf_issue_date IS NOT NULL
                THEN DATEDIFF(day, s.flaf_deadline, s.flaf_issue_date)
            WHEN CAST(GETDATE() AS date) > s.flaf_deadline
                THEN DATEDIFF(day, s.flaf_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS flaf_variance_days,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN s.pegged_date IS NULL
                 AND CAST(GETDATE() AS date) > s.pegging_deadline THEN 'MISSED'
            WHEN s.pegged_date IS NULL THEN 'PENDING'
            WHEN s.pegged_date < s.pegging_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN s.pegged_date = s.pegging_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS pegging_status,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN NULL
            WHEN s.pegged_date IS NOT NULL
                THEN DATEDIFF(day, s.pegging_deadline, s.pegged_date)
            WHEN CAST(GETDATE() AS date) > s.pegging_deadline
                THEN DATEDIFF(day, s.pegging_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS pegging_variance_days,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN s.rig_on_date IS NULL
                 AND CAST(GETDATE() AS date) > s.rig_on_deadline THEN 'MISSED'
            WHEN s.rig_on_date IS NULL THEN 'PENDING'
            WHEN s.rig_on_date < s.rig_on_deadline THEN 'AHEAD_OF_SCHEDULE'
            WHEN s.rig_on_date = s.rig_on_deadline THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_on_status,

        CASE
            WHEN s.ex_rig_on_date IS NULL THEN NULL
            WHEN s.rig_on_date IS NOT NULL
                THEN DATEDIFF(day, s.rig_on_deadline, s.rig_on_date)
            WHEN CAST(GETDATE() AS date) > s.rig_on_deadline
                THEN DATEDIFF(day, s.rig_on_deadline, CAST(GETDATE() AS date))
            ELSE NULL
        END AS rig_on_variance_days,

        CASE
            WHEN s.ex_rig_off_date IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN s.rig_off_date IS NULL
                 AND CAST(GETDATE() AS date) > s.ex_rig_off_date THEN 'MISSED'
            WHEN s.rig_off_date IS NULL THEN 'PENDING'
            WHEN s.rig_off_date < s.ex_rig_off_date THEN 'AHEAD_OF_SCHEDULE'
            WHEN s.rig_off_date = s.ex_rig_off_date THEN 'ON_SCHEDULE'
            ELSE 'DELAYED'
        END AS rig_off_status,

        CASE
            WHEN s.ex_rig_off_date IS NULL THEN NULL
            WHEN s.rig_off_date IS NOT NULL
                THEN DATEDIFF(day, s.ex_rig_off_date, s.rig_off_date)
            WHEN CAST(GETDATE() AS date) > s.ex_rig_off_date
                THEN DATEDIFF(day, s.ex_rig_off_date, CAST(GETDATE() AS date))
            ELSE NULL
        END AS rig_off_variance_days
    FROM deadlines_cte AS s
),
headline_cte AS
(
    SELECT
        r.well_id,
        r.ex_rig_on_date,
        r.flaf_deadline,
        r.flaf_issue_date,
        r.flaf_status,
        r.flaf_variance_days,
        r.pegging_deadline,
        r.pegged_date,
        r.pegging_status,
        r.pegging_variance_days,
        r.rig_on_deadline,
        r.rig_on_date,
        r.rig_on_status,
        r.rig_on_variance_days,
        r.ex_rig_off_date AS rig_off_deadline,
        r.rig_off_date,
        r.rig_off_status,
        r.rig_off_variance_days,
        CASE
            WHEN r.rig_on_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG ON'
            WHEN r.flaf_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - FLAF'
            WHEN r.pegging_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - PEGGING'
            WHEN r.rig_off_status IN ('MISSED', 'DELAYED') THEN 'SLIPPED - RIG OFF'
            ELSE NULL
        END AS well_slippage_status
    FROM status_cte AS r
)
SELECT
    h.well_id AS well_id,
    h.flaf_deadline AS flaf_deadline,
    h.flaf_issue_date AS flaf_actual,
    h.flaf_status AS flaf_status,
    h.flaf_variance_days AS flaf_variance_days,
    h.pegging_deadline AS pegging_deadline,
    h.pegged_date AS pegging_actual,
    h.pegging_status AS pegging_status,
    h.pegging_variance_days AS pegging_variance_days,
    h.rig_on_deadline AS rig_on_deadline,
    h.rig_on_date AS rig_on_actual,
    h.rig_on_status AS rig_on_status,
    h.rig_on_variance_days AS rig_on_variance_days,
    h.rig_off_deadline AS rig_off_deadline,
    h.rig_off_date AS rig_off_actual,
    h.rig_off_status AS rig_off_status,
    h.rig_off_variance_days AS rig_off_variance_days,
    h.well_slippage_status AS well_slippage_status
FROM headline_cte AS h
WHERE h.well_slippage_status IS NOT NULL
ORDER BY h.ex_rig_on_date ASC
