-- activity_delay - Activity delay detail for one well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T04:37:33+00:00
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
        wm.well_id
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
      AND CONVERT(varchar(50), wm.well_id) = CONVERT(varchar(50), ?)
),
task_ranked AS
(
    SELECT
        td.id,
        td.ActionOn,
        td.task_code,
        td.target_start,
        td.target_end,
        td.actual_start,
        td.actual_end,
        td.progress,
        td.well_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY RTRIM(td.well_id), RTRIM(td.task_code)
            ORDER BY td.ActionOn DESC, td.id DESC
        ) AS row_num
    FROM well.task_daily AS td
),
latest_tasks AS
(
    SELECT
        tr.id,
        tr.ActionOn,
        RTRIM(tr.task_code) AS task_code,
        tr.target_start,
        tr.target_end,
        tr.actual_start,
        tr.actual_end,
        tr.progress,
        wd.well_id
    FROM task_ranked AS tr
    INNER JOIN well_data AS wd
        ON CONVERT(varchar(50), tr.well_id) = CONVERT(varchar(50), wd.well_id)
    WHERE tr.row_num = 1
),
mapping_collapsed AS
(
    SELECT
        CAST(mm.Activity_ID AS nvarchar(50)) AS activity_id,
        CASE
            WHEN COUNT(DISTINCT mm.New_Activity_Code) = 1
            THEN MAX(mm.New_Activity_Code)
        END AS activity_code
    FROM dbo.mapping_master AS mm
    GROUP BY CAST(mm.Activity_ID AS nvarchar(50))
),
description_collapsed AS
(
    SELECT
        am.activity_code,
        CASE
            WHEN COUNT(DISTINCT am.activity_group_description) = 1
            THEN MAX(am.activity_group_description)
        END AS wbs,
        CASE
            WHEN COUNT(DISTINCT am.crew_code) = 1
            THEN MAX(am.crew_code)
        END AS crew_code
    FROM dbo.activity_master_csv AS am
    GROUP BY am.activity_code
),
task_activity AS
(
    SELECT
        lt.well_id,
        lt.task_code,
        lt.ActionOn,
        CASE
            WHEN CHARINDEX('-', lt.task_code) > 0
            THEN LEFT(lt.task_code, NULLIF(CHARINDEX('-', lt.task_code), 0) - 1)
        END AS activity_id,
        lt.target_start,
        lt.target_end,
        lt.actual_start,
        lt.actual_end,
        lt.progress,
        mc.activity_code,
        dc.wbs,
        dc.crew_code
    FROM latest_tasks AS lt
    LEFT JOIN mapping_collapsed AS mc
        ON mc.activity_id =
           CASE
               WHEN CHARINDEX('-', lt.task_code) > 0
               THEN LEFT(lt.task_code, NULLIF(CHARINDEX('-', lt.task_code), 0) - 1)
           END
    LEFT JOIN description_collapsed AS dc
        ON dc.activity_code = mc.activity_code
),
task_calculated AS
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
            WHEN ta.actual_start IS NULL
                 AND CAST(GETDATE() AS date) > ta.target_start
                THEN 'NOT_STARTED_SLIPPING'
            WHEN ta.actual_start IS NULL
                THEN 'NOT_STARTED_ON_SCHEDULE'
            WHEN ta.actual_start < ta.target_start
                THEN 'STARTED_EARLY'
            WHEN ta.actual_start = ta.target_start
                THEN 'STARTED_ON_TIME'
            ELSE 'START_DELAYED'
        END AS start_status,
        CASE
            WHEN ta.target_start IS NULL THEN NULL
            ELSE DATEDIFF(day, ta.target_start, ta.actual_start)
        END AS start_variance_days,
        CASE
            WHEN ta.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN ta.actual_end IS NOT NULL
                 AND ta.actual_end < ta.target_end
                THEN 'COMPLETED_EARLY'
            WHEN ta.actual_end IS NOT NULL
                 AND ta.actual_end = ta.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN ta.actual_end IS NOT NULL
                THEN 'COMPLETED_LATE'
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
                 AND ta.target_start IS NOT NULL
                 AND ta.target_start >= CAST(GETDATE() AS date)
                THEN 'NOT_STARTED'
            WHEN ta.actual_start IS NULL THEN 'NOT_STARTED_LATE'
            ELSE 'IN_PROGRESS'
        END AS execution_status,
        CASE
            WHEN ta.target_end IS NOT NULL
                 AND
                 (
                     (ta.actual_end IS NULL
                      AND ta.target_end < CAST(GETDATE() AS date))
                     OR ta.actual_end > ta.target_end
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
        CASE
            WHEN ta.progress IS NULL THEN NULL
            ELSE ta.progress * 100
        END AS progress_percent
    FROM task_activity AS ta
)
SELECT
    tc.well_id AS well_id,
    tc.task_code AS task_code,
    tc.ActionOn AS action_on,
    tc.activity_id AS activity_id,
    tc.activity_code AS activity_code,
    tc.wbs AS wbs,
    tc.crew_code AS crew_code,
    tc.target_start AS target_start,
    tc.target_end AS target_end,
    tc.actual_start AS actual_start,
    tc.actual_end AS actual_end,
    tc.start_status AS start_status,
    tc.start_variance_days AS start_variance_days,
    tc.end_status AS end_status,
    tc.end_variance_days AS end_variance_days,
    tc.execution_status AS execution_status,
    tc.schedule_risk AS schedule_risk,
    tc.progress_percent AS progress_percent
FROM task_calculated AS tc
ORDER BY
    CASE tc.schedule_risk
        WHEN 'RED' THEN 1
        WHEN 'AMBER_START_SLIPPING' THEN 2
        WHEN 'AMBER_START_DELAYED' THEN 3
        ELSE 4
    END,
    tc.end_variance_days DESC,
    tc.start_variance_days DESC,
    tc.target_end ASC
