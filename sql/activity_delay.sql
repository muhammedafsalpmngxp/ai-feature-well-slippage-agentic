-- activity_delay - Activity delay detail for one well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T10:17:38+00:00
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
        w.well_id,
        w.eng_completion_date
    FROM well.well_master AS w
    WHERE CONVERT(varchar(50), w.well_id) = CONVERT(varchar(50), ?)
      AND w.eng_completion_date IS NULL
),
task_ranked AS
(
    SELECT
        t.id,
        t.ActionOn,
        t.task_code,
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
        t.id,
        t.ActionOn,
        t.task_code,
        t.target_start,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.progress,
        t.well_id
    FROM task_ranked AS t
    WHERE t.row_num = 1
),
mapping_one AS
(
    SELECT
        CAST(m.Activity_ID AS nvarchar(50)) AS activity_id,
        MAX(m.New_Activity_Code) AS activity_code
    FROM dbo.mapping_master AS m
    GROUP BY CAST(m.Activity_ID AS nvarchar(50))
    HAVING COUNT(DISTINCT m.New_Activity_Code) = 1
),
description_one AS
(
    SELECT
        a.activity_code,
        MAX(a.activity_group_description) AS wbs,
        MAX(a.crew_code) AS crew_code
    FROM dbo.activity_master_csv AS a
    GROUP BY a.activity_code
    HAVING COUNT(DISTINCT a.activity_group_description) <= 1
       AND COUNT(DISTINCT a.crew_code) <= 1
),
task_base AS
(
    SELECT
        t.well_id,
        LTRIM(RTRIM(t.task_code)) AS task_code,
        t.ActionOn,
        CASE
            WHEN CHARINDEX('-', LTRIM(RTRIM(t.task_code))) > 0
            THEN LEFT
            (
                LTRIM(RTRIM(t.task_code)),
                NULLIF(CHARINDEX('-', LTRIM(RTRIM(t.task_code))), 0) - 1
            )
        END AS activity_id,
        t.target_start,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.progress
    FROM task_latest AS t
    INNER JOIN well_data AS w
        ON CONVERT(varchar(50), t.well_id) = CONVERT(varchar(50), w.well_id)
),
task_calc AS
(
    SELECT
        b.well_id,
        b.task_code,
        b.ActionOn,
        b.activity_id,
        m.activity_code,
        a.wbs,
        a.crew_code,
        b.target_start,
        b.target_end,
        b.actual_start,
        b.actual_end,
        CASE
            WHEN b.target_start IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN b.actual_start IS NULL AND b.target_start < CAST(GETDATE() AS date)
                THEN 'NOT_STARTED_SLIPPING'
            WHEN b.actual_start IS NULL THEN 'NOT_STARTED_ON_SCHEDULE'
            WHEN b.actual_start < b.target_start THEN 'STARTED_EARLY'
            WHEN b.actual_start = b.target_start THEN 'STARTED_ON_TIME'
            ELSE 'START_DELAYED'
        END AS start_status,
        CASE
            WHEN b.target_start IS NULL THEN NULL
            WHEN b.actual_start IS NOT NULL
                THEN DATEDIFF(day, b.target_start, b.actual_start)
            WHEN b.target_start < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, b.target_start, CAST(GETDATE() AS date))
            ELSE NULL
        END AS start_variance_days,
        CASE
            WHEN b.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN b.actual_end IS NOT NULL AND b.actual_end < b.target_end
                THEN 'COMPLETED_EARLY'
            WHEN b.actual_end IS NOT NULL AND b.actual_end = b.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN b.actual_end IS NOT NULL
                THEN 'COMPLETED_LATE'
            WHEN b.target_end > CAST(GETDATE() AS date)
                THEN 'IN_PROGRESS_NOT_LATE'
            WHEN b.target_end = CAST(GETDATE() AS date)
                THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status,
        CASE
            WHEN b.target_end IS NULL THEN NULL
            WHEN b.actual_end IS NOT NULL
                THEN DATEDIFF(day, b.target_end, b.actual_end)
            WHEN b.target_end < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, b.target_end, CAST(GETDATE() AS date))
            ELSE NULL
        END AS end_variance_days,
        CASE
            WHEN b.actual_end IS NOT NULL THEN 'COMPLETED'
            WHEN b.actual_start IS NULL
                 AND b.target_start IS NOT NULL
                 AND b.target_start < CAST(GETDATE() AS date)
                THEN 'NOT_STARTED_LATE'
            WHEN b.actual_start IS NULL THEN 'NOT_STARTED'
            ELSE 'IN_PROGRESS'
        END AS execution_status,
        CASE
            WHEN b.target_end IS NOT NULL
             AND
             (
                 (b.actual_end IS NULL AND b.target_end < CAST(GETDATE() AS date))
                 OR b.actual_end > b.target_end
             )
                THEN 'RED'
            WHEN b.actual_start IS NULL
             AND b.target_start IS NOT NULL
             AND b.target_start < CAST(GETDATE() AS date)
                THEN 'AMBER_START_SLIPPING'
            WHEN b.actual_start IS NOT NULL
             AND b.target_start IS NOT NULL
             AND b.actual_start > b.target_start
                THEN 'AMBER_START_DELAYED'
            ELSE 'GREEN'
        END AS schedule_risk,
        CASE
            WHEN b.target_end IS NOT NULL
             AND
             (
                 (b.actual_end IS NULL AND b.target_end < CAST(GETDATE() AS date))
                 OR b.actual_end > b.target_end
             )
                THEN 1
            WHEN b.actual_start IS NULL
             AND b.target_start IS NOT NULL
             AND b.target_start < CAST(GETDATE() AS date)
                THEN 2
            WHEN b.actual_start IS NOT NULL
             AND b.target_start IS NOT NULL
             AND b.actual_start > b.target_start
                THEN 2
            ELSE 3
        END AS risk_sort,
        b.progress
    FROM task_base AS b
    LEFT JOIN mapping_one AS m
        ON CAST(m.activity_id AS nvarchar(50)) = b.activity_id
    LEFT JOIN description_one AS a
        ON a.activity_code = m.activity_code
)
SELECT
    d.well_id AS well_id,
    d.task_code AS task_code,
    d.ActionOn AS action_on,
    d.activity_id AS activity_id,
    d.activity_code AS activity_code,
    d.wbs AS wbs,
    d.crew_code AS crew_code,
    d.target_start AS target_start,
    d.target_end AS target_end,
    d.actual_start AS actual_start,
    d.actual_end AS actual_end,
    d.start_status AS start_status,
    d.start_variance_days AS start_variance_days,
    d.end_status AS end_status,
    d.end_variance_days AS end_variance_days,
    d.execution_status AS execution_status,
    d.schedule_risk AS schedule_risk,
    CASE
        WHEN d.progress IS NULL THEN NULL
        ELSE d.progress * 100.0
    END AS progress_percent
FROM task_calc AS d
ORDER BY
    d.risk_sort ASC,
    d.end_variance_days DESC,
    d.start_variance_days DESC,
    d.target_end ASC
