-- activity_delay - Activity delay detail for one well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-25T05:44:59+00:00
-- Schema:     5c24c5428a67dbce9e06d931f7ad130f8e8c32d5386ef380f15141eb16c6a458
-- Rows then:  88
-- Contract:   well_id, task_code, action_on, activity_id, activity_code, wbs, crew_code, 
--             crew_id, crew_type_id, target_start, target_end, actual_start, actual_end, 
--             start_status, start_variance_days, end_status, end_variance_days, 
--             execution_status, schedule_risk, progress_percent
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH ranked_tasks AS
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
        td.crew_id,
        td.crew_type_id,
        td.well_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY td.well_id, LTRIM(RTRIM(td.task_code))
            ORDER BY td.ActionOn DESC, td.id DESC
        ) AS row_num
    FROM well.task_daily AS td
    WHERE CONVERT(varchar(50), td.well_id) = CONVERT(varchar(50), ?)
),
latest_tasks AS
(
    SELECT
        rt.id,
        rt.ActionOn,
        LTRIM(RTRIM(rt.task_code)) AS task_code,
        rt.target_start,
        rt.target_end,
        rt.actual_start,
        rt.actual_end,
        rt.progress,
        rt.crew_id,
        rt.crew_type_id,
        rt.well_id
    FROM ranked_tasks AS rt
    WHERE rt.row_num = 1
),
mapping_distinct AS
(
    SELECT DISTINCT
        CAST(mm.Activity_ID AS nvarchar(50)) AS activity_id,
        mm.New_Activity_Code AS activity_code
    FROM dbo.mapping_master AS mm
),
mapping_unique AS
(
    SELECT
        md.activity_id,
        MAX(md.activity_code) AS activity_code
    FROM mapping_distinct AS md
    GROUP BY md.activity_id
    HAVING COUNT(*) = 1
),
description_distinct AS
(
    SELECT DISTINCT
        am.activity_code,
        am.activity_group_description AS wbs,
        am.crew_code
    FROM dbo.activity_master_csv AS am
),
description_unique AS
(
    SELECT
        dd.activity_code,
        MAX(dd.wbs) AS wbs,
        MAX(dd.crew_code) AS crew_code
    FROM description_distinct AS dd
    GROUP BY dd.activity_code
    HAVING COUNT(*) = 1
),
resolved_tasks AS
(
    SELECT
        lt.id,
        lt.ActionOn,
        lt.task_code,
        lt.target_start,
        lt.target_end,
        lt.actual_start,
        lt.actual_end,
        lt.progress,
        lt.crew_id,
        lt.crew_type_id,
        lt.well_id,
        LEFT
        (
            lt.task_code,
            NULLIF(CHARINDEX('-', lt.task_code), 0) - 1
        ) AS activity_id,
        mu.activity_code,
        du.wbs,
        du.crew_code
    FROM latest_tasks AS lt
    LEFT JOIN mapping_unique AS mu
        ON mu.activity_id =
           LEFT
           (
               lt.task_code,
               NULLIF(CHARINDEX('-', lt.task_code), 0) - 1
           )
    LEFT JOIN description_unique AS du
        ON du.activity_code = mu.activity_code
),
calculated_tasks AS
(
    SELECT
        rt.*,
        CASE
            WHEN rt.target_start IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN rt.actual_start IS NULL
                 AND CAST(GETDATE() AS date) > rt.target_start
                THEN 'NOT_STARTED_SLIPPING'
            WHEN rt.actual_start IS NULL
                THEN 'NOT_STARTED_ON_SCHEDULE'
            WHEN rt.actual_start < rt.target_start
                THEN 'STARTED_EARLY'
            WHEN rt.actual_start = rt.target_start
                THEN 'STARTED_ON_TIME'
            ELSE 'START_DELAYED'
        END AS start_status,
        CASE
            WHEN rt.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN rt.actual_end IS NOT NULL
                 AND rt.actual_end < rt.target_end
                THEN 'COMPLETED_EARLY'
            WHEN rt.actual_end IS NOT NULL
                 AND rt.actual_end = rt.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN rt.actual_end IS NOT NULL
                THEN 'COMPLETED_LATE'
            WHEN rt.target_end > CAST(GETDATE() AS date)
                THEN 'IN_PROGRESS_NOT_LATE'
            WHEN rt.target_end = CAST(GETDATE() AS date)
                THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status,
        CASE
            WHEN rt.actual_end IS NOT NULL THEN 'COMPLETED'
            WHEN rt.actual_start IS NULL
                 AND
                 (
                     rt.target_start IS NULL
                     OR rt.target_start >= CAST(GETDATE() AS date)
                 )
                THEN 'NOT_STARTED'
            WHEN rt.actual_start IS NULL
                THEN 'NOT_STARTED_LATE'
            ELSE 'IN_PROGRESS'
        END AS execution_status,
        CASE
            WHEN
                (
                    rt.actual_end IS NULL
                    AND rt.target_end IS NOT NULL
                    AND rt.target_end < CAST(GETDATE() AS date)
                )
                OR
                (
                    rt.actual_end IS NOT NULL
                    AND rt.target_end IS NOT NULL
                    AND rt.actual_end > rt.target_end
                )
                THEN 'RED'
            WHEN rt.actual_start IS NULL
                 AND rt.target_start IS NOT NULL
                 AND rt.target_start < CAST(GETDATE() AS date)
                THEN 'AMBER_START_SLIPPING'
            WHEN rt.actual_start IS NOT NULL
                 AND rt.target_start IS NOT NULL
                 AND rt.actual_start > rt.target_start
                THEN 'AMBER_START_DELAYED'
            ELSE 'GREEN'
        END AS schedule_risk,
        CASE
            WHEN rt.target_start IS NULL THEN NULL
            WHEN rt.actual_start IS NOT NULL
                THEN DATEDIFF(day, rt.target_start, rt.actual_start)
            WHEN rt.target_start < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, rt.target_start, CAST(GETDATE() AS date))
            ELSE NULL
        END AS start_variance_days,
        CASE
            WHEN rt.target_end IS NULL THEN NULL
            WHEN rt.actual_end IS NOT NULL
                THEN DATEDIFF(day, rt.target_end, rt.actual_end)
            WHEN rt.target_end < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, rt.target_end, CAST(GETDATE() AS date))
            ELSE NULL
        END AS end_variance_days
    FROM resolved_tasks AS rt
)
SELECT
    ct.well_id AS well_id,
    ct.task_code AS task_code,
    ct.ActionOn AS action_on,
    ct.activity_id AS activity_id,
    ct.activity_code AS activity_code,
    ct.wbs AS wbs,
    ct.crew_code AS crew_code,
    ct.crew_id AS crew_id,
    ct.crew_type_id AS crew_type_id,
    ct.target_start AS target_start,
    ct.target_end AS target_end,
    ct.actual_start AS actual_start,
    ct.actual_end AS actual_end,
    ct.start_status AS start_status,
    ct.start_variance_days AS start_variance_days,
    ct.end_status AS end_status,
    ct.end_variance_days AS end_variance_days,
    ct.execution_status AS execution_status,
    ct.schedule_risk AS schedule_risk,
    CASE
        WHEN ct.progress IS NULL THEN NULL
        ELSE ct.progress * 100
    END AS progress_percent
FROM calculated_tasks AS ct
INNER JOIN well.well_master AS wm
    ON CONVERT(varchar(50), wm.well_id) = CONVERT(varchar(50), ct.well_id)
WHERE wm.eng_completion_date IS NULL
ORDER BY
    CASE
        WHEN ct.schedule_risk = 'RED' THEN 1
        WHEN ct.schedule_risk IN
             ('AMBER_START_SLIPPING', 'AMBER_START_DELAYED') THEN 2
        ELSE 3
    END,
    ct.end_variance_days DESC,
    ct.start_variance_days DESC,
    CASE
        WHEN ct.target_end IS NULL THEN 1
        ELSE 0
    END,
    ct.target_end ASC
