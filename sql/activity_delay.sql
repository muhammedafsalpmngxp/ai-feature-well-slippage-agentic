-- activity_delay - Activity delay detail for one well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T11:40:09+00:00
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
WITH task_ranked AS
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
            PARTITION BY
                t.well_id,
                LTRIM(RTRIM(t.task_code))
            ORDER BY
                t.ActionOn DESC,
                t.id DESC
        ) AS rn
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
    WHERE tr.rn = 1
),
task_activity AS
(
    SELECT
        tl.id,
        tl.ActionOn,
        tl.task_code,
        tl.target_start,
        tl.target_end,
        tl.actual_start,
        tl.actual_end,
        tl.progress,
        tl.well_id,
        LEFT
        (
            tl.task_code,
            NULLIF(CHARINDEX('-', tl.task_code), 0) - 1
        ) AS activity_id
    FROM task_latest AS tl
),
mapping_collapsed AS
(
    SELECT
        CONVERT(nvarchar(50), mm.Activity_ID) AS activity_id,
        CASE
            WHEN COUNT(*) = COUNT(mm.New_Activity_Code)
                 AND MIN(mm.New_Activity_Code) = MAX(mm.New_Activity_Code)
            THEN MIN(mm.New_Activity_Code)
            ELSE NULL
        END AS activity_code
    FROM dbo.mapping_master AS mm
    GROUP BY CONVERT(nvarchar(50), mm.Activity_ID)
),
description_collapsed AS
(
    SELECT
        amc.activity_code,
        CASE
            WHEN COUNT(*) = COUNT(amc.activity_group_description)
                 AND MIN(amc.activity_group_description) = MAX(amc.activity_group_description)
            THEN MIN(amc.activity_group_description)
            ELSE NULL
        END AS wbs,
        CASE
            WHEN COUNT(*) = COUNT(amc.crew_code)
                 AND MIN(amc.crew_code) = MAX(amc.crew_code)
            THEN MIN(amc.crew_code)
            ELSE NULL
        END AS crew_code
    FROM dbo.activity_master_csv AS amc
    GROUP BY amc.activity_code
),
joined_data AS
(
    SELECT
        w.well_id,
        ta.task_code,
        ta.ActionOn,
        ta.activity_id,
        mc.activity_code,
        dc.wbs,
        dc.crew_code,
        ta.target_start,
        ta.target_end,
        ta.actual_start,
        ta.actual_end,
        ta.progress
    FROM task_activity AS ta
    INNER JOIN well.well_master AS w
        ON CONVERT(varchar(50), ta.well_id) = CONVERT(varchar(50), w.well_id)
    LEFT JOIN mapping_collapsed AS mc
        ON mc.activity_id = CONVERT(nvarchar(50), ta.activity_id)
    LEFT JOIN description_collapsed AS dc
        ON dc.activity_code = mc.activity_code
    WHERE w.eng_completion_date IS NULL
      AND CONVERT(varchar(50), w.well_id) = CONVERT(varchar(50), ?)
),
calculated_data AS
(
    SELECT
        jd.well_id,
        jd.task_code,
        jd.ActionOn,
        jd.activity_id,
        jd.activity_code,
        jd.wbs,
        jd.crew_code,
        jd.target_start,
        jd.target_end,
        jd.actual_start,
        jd.actual_end,
        jd.progress,
        CASE
            WHEN jd.target_start IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN jd.actual_start IS NULL
                 AND jd.target_start < CAST(GETDATE() AS date)
                THEN 'NOT_STARTED_SLIPPING'
            WHEN jd.actual_start IS NULL
                THEN 'NOT_STARTED_ON_SCHEDULE'
            WHEN jd.actual_start < jd.target_start
                THEN 'STARTED_EARLY'
            WHEN jd.actual_start = jd.target_start
                THEN 'STARTED_ON_TIME'
            ELSE 'START_DELAYED'
        END AS start_status,
        CASE
            WHEN jd.target_start IS NULL THEN NULL
            WHEN jd.actual_start IS NOT NULL
                THEN DATEDIFF(day, jd.target_start, jd.actual_start)
            WHEN jd.target_start < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, jd.target_start, CAST(GETDATE() AS date))
            ELSE NULL
        END AS start_variance_days,
        CASE
            WHEN jd.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN jd.actual_end IS NOT NULL
                 AND jd.actual_end < jd.target_end
                THEN 'COMPLETED_EARLY'
            WHEN jd.actual_end IS NOT NULL
                 AND jd.actual_end = jd.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN jd.actual_end IS NOT NULL
                THEN 'COMPLETED_LATE'
            WHEN jd.target_end > CAST(GETDATE() AS date)
                THEN 'IN_PROGRESS_NOT_LATE'
            WHEN jd.target_end = CAST(GETDATE() AS date)
                THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status,
        CASE
            WHEN jd.target_end IS NULL THEN NULL
            WHEN jd.actual_end IS NOT NULL
                THEN DATEDIFF(day, jd.target_end, jd.actual_end)
            WHEN jd.target_end < CAST(GETDATE() AS date)
                THEN DATEDIFF(day, jd.target_end, CAST(GETDATE() AS date))
            WHEN jd.target_end = CAST(GETDATE() AS date)
                THEN 0
            ELSE NULL
        END AS end_variance_days,
        CASE
            WHEN jd.actual_end IS NOT NULL THEN 'COMPLETED'
            WHEN jd.actual_start IS NULL
                 AND (jd.target_start IS NULL
                      OR jd.target_start >= CAST(GETDATE() AS date))
                THEN 'NOT_STARTED'
            WHEN jd.actual_start IS NULL THEN 'NOT_STARTED_LATE'
            ELSE 'IN_PROGRESS'
        END AS execution_status,
        CASE
            WHEN jd.actual_end IS NOT NULL
                 AND jd.target_end IS NOT NULL
                 AND jd.actual_end > jd.target_end
                THEN 'RED'
            WHEN jd.actual_end IS NULL
                 AND jd.target_end IS NOT NULL
                 AND jd.target_end < CAST(GETDATE() AS date)
                THEN 'RED'
            WHEN jd.actual_start IS NULL
                 AND jd.target_start IS NOT NULL
                 AND jd.target_start < CAST(GETDATE() AS date)
                THEN 'AMBER_START_SLIPPING'
            WHEN jd.actual_start IS NOT NULL
                 AND jd.target_start IS NOT NULL
                 AND jd.actual_start > jd.target_start
                THEN 'AMBER_START_DELAYED'
            ELSE 'GREEN'
        END AS schedule_risk
    FROM joined_data AS jd
)
SELECT
    cd.well_id AS well_id,
    cd.task_code AS task_code,
    cd.ActionOn AS action_on,
    cd.activity_id AS activity_id,
    cd.activity_code AS activity_code,
    cd.wbs AS wbs,
    cd.crew_code AS crew_code,
    cd.target_start AS target_start,
    cd.target_end AS target_end,
    cd.actual_start AS actual_start,
    cd.actual_end AS actual_end,
    cd.start_status AS start_status,
    cd.start_variance_days AS start_variance_days,
    cd.end_status AS end_status,
    cd.end_variance_days AS end_variance_days,
    cd.execution_status AS execution_status,
    cd.schedule_risk AS schedule_risk,
    CASE
        WHEN cd.progress IS NULL THEN NULL
        ELSE cd.progress * 100
    END AS progress_percent
FROM calculated_data AS cd
ORDER BY
    CASE cd.schedule_risk
        WHEN 'RED' THEN 1
        WHEN 'AMBER_START_SLIPPING' THEN 2
        WHEN 'AMBER_START_DELAYED' THEN 3
        ELSE 4
    END,
    cd.end_variance_days DESC,
    cd.start_variance_days DESC,
    CASE
        WHEN cd.target_end IS NULL THEN 1
        ELSE 0
    END,
    cd.target_end ASC
