-- activity_delay - Activity delay detail for one well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T10:52:00+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  88
-- Contract:   well_id, task_code, action_on, activity_id, activity_code, wbs, crew_code, 
--             target_start, target_end, actual_start, actual_end, start_status, 
--             start_variance_days, end_status, end_variance_days, execution_status, 
--             schedule_risk, progress_percent
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH well_data AS
(
    SELECT
        w.well_id
    FROM well.well_master AS w
    WHERE w.eng_completion_date IS NULL
      AND CONVERT(varchar(50), w.well_id) = CONVERT(varchar(50), ?)
),
task_ranked AS
(
    SELECT
        t.id,
        t.ActionOn,
        LTRIM(RTRIM(t.task_code)) AS task_code,
        t.target_start,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.progress,
        t.well_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY t.well_id, LTRIM(RTRIM(t.task_code))
            ORDER BY t.ActionOn DESC, t.id DESC
        ) AS row_num
    FROM well.task_daily AS t
),
task_latest AS
(
    SELECT
        tr.id,
        tr.ActionOn,
        tr.task_code,
        tr.target_start,
        tr.target_end,
        tr.actual_start,
        tr.actual_end,
        tr.progress,
        tr.well_id
    FROM task_ranked AS tr
    WHERE tr.row_num = 1
),
mapping_collapsed AS
(
    SELECT
        CONVERT(nvarchar(50), m.Activity_ID) AS activity_id,
        MAX(m.New_Activity_Code) AS activity_code
    FROM dbo.mapping_master AS m
    GROUP BY CONVERT(nvarchar(50), m.Activity_ID)
    HAVING COUNT(DISTINCT m.New_Activity_Code) = 1
),
description_collapsed AS
(
    SELECT
        amc.activity_code,
        MAX(amc.activity_group_description) AS wbs,
        MAX(amc.crew_code) AS crew_code
    FROM dbo.activity_master_csv AS amc
    GROUP BY amc.activity_code
    HAVING COUNT(DISTINCT amc.activity_group_description) = 1
       AND COUNT(DISTINCT amc.crew_code) = 1
),
task_activity AS
(
    SELECT
        t.well_id,
        t.task_code,
        t.ActionOn,
        LEFT(t.task_code, NULLIF(CHARINDEX('-', t.task_code), 0) - 1) AS activity_id,
        m.activity_code,
        d.wbs,
        d.crew_code,
        t.target_start,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.progress
    FROM task_latest AS t
    LEFT JOIN mapping_collapsed AS m
        ON m.activity_id =
           LEFT(t.task_code, NULLIF(CHARINDEX('-', t.task_code), 0) - 1)
    LEFT JOIN description_collapsed AS d
        ON d.activity_code = m.activity_code
),
task_status AS
(
    SELECT
        ta.well_id,
        ta.task_code,
        ta.ActionOn,
        ta.activity_id,
        ta.activity_code,
        ta.wbs,
        ta.crew_code,
        ta.target_start,
        ta.target_end,
        ta.actual_start,
        ta.actual_end,
        CASE
            WHEN ta.target_start IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN ta.actual_start IS NULL AND ta.target_start < CAST(GETDATE() AS date)
                THEN 'NOT_STARTED_SLIPPING'
            WHEN ta.actual_start IS NULL THEN 'NOT_STARTED_ON_SCHEDULE'
            WHEN ta.actual_start < ta.target_start THEN 'STARTED_EARLY'
            WHEN ta.actual_start = ta.target_start THEN 'STARTED_ON_TIME'
            ELSE 'START_DELAYED'
        END AS start_status,
        CASE
            WHEN ta.target_start IS NULL THEN NULL
            WHEN ta.actual_start IS NOT NULL
                THEN DATEDIFF(day, ta.target_start, ta.actual_start)
            WHEN ta.target_start < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, ta.target_start, CAST(GETDATE() AS date))
            ELSE NULL
        END AS start_variance_days,
        CASE
            WHEN ta.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN ta.actual_end IS NOT NULL AND ta.actual_end < ta.target_end
                THEN 'COMPLETED_EARLY'
            WHEN ta.actual_end IS NOT NULL AND ta.actual_end = ta.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN ta.actual_end IS NOT NULL THEN 'COMPLETED_LATE'
            WHEN ta.target_end > CAST(GETDATE() AS date)
                THEN 'IN_PROGRESS_NOT_LATE'
            WHEN ta.target_end = CAST(GETDATE() AS date)
                THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status,
        CASE
            WHEN ta.target_end IS NULL THEN NULL
            WHEN ta.actual_end IS NOT NULL
                THEN DATEDIFF(day, ta.target_end, ta.actual_end)
            WHEN ta.target_end < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, ta.target_end, CAST(GETDATE() AS date))
            ELSE NULL
        END AS end_variance_days,
        CASE
            WHEN ta.actual_end IS NOT NULL THEN 'COMPLETED'
            WHEN ta.actual_start IS NULL
                 AND ta.target_start < CAST(GETDATE() AS date)
                THEN 'NOT_STARTED_LATE'
            WHEN ta.actual_start IS NULL THEN 'NOT_STARTED'
            ELSE 'IN_PROGRESS'
        END AS execution_status,
        CASE
            WHEN ta.target_end IS NOT NULL
                 AND
                 (
                     ta.actual_end > ta.target_end
                     OR
                     (
                         ta.actual_end IS NULL
                         AND ta.target_end < CAST(GETDATE() AS date)
                     )
                 )
                THEN 'RED'
            WHEN ta.actual_start IS NULL
                 AND ta.target_start IS NOT NULL
                 AND ta.target_start < CAST(GETDATE() AS date)
                THEN 'AMBER_START_SLIPPING'
            WHEN ta.actual_start IS NOT NULL
                 AND ta.target_start IS NOT NULL
                 AND ta.actual_start > ta.target_start
                THEN 'AMBER_START_DELAYED'
            ELSE 'GREEN'
        END AS schedule_risk,
        ta.progress
    FROM task_activity AS ta
)
SELECT
    ts.well_id AS well_id,
    ts.task_code AS task_code,
    ts.ActionOn AS action_on,
    ts.activity_id AS activity_id,
    ts.activity_code AS activity_code,
    ts.wbs AS wbs,
    ts.crew_code AS crew_code,
    ts.target_start AS target_start,
    ts.target_end AS target_end,
    ts.actual_start AS actual_start,
    ts.actual_end AS actual_end,
    ts.start_status AS start_status,
    ts.start_variance_days AS start_variance_days,
    ts.end_status AS end_status,
    ts.end_variance_days AS end_variance_days,
    ts.execution_status AS execution_status,
    ts.schedule_risk AS schedule_risk,
    CASE
        WHEN ts.progress IS NULL THEN NULL
        ELSE ts.progress * CAST(100 AS decimal(18, 6))
    END AS progress_percent
FROM task_status AS ts
INNER JOIN well_data AS wd
    ON CONVERT(varchar(50), ts.well_id) = CONVERT(varchar(50), wd.well_id)
ORDER BY
    CASE ts.schedule_risk
        WHEN 'RED' THEN 1
        WHEN 'AMBER_START_SLIPPING' THEN 2
        WHEN 'AMBER_START_DELAYED' THEN 3
        ELSE 4
    END,
    ts.end_variance_days DESC,
    ts.start_variance_days DESC,
    ts.target_end ASC
