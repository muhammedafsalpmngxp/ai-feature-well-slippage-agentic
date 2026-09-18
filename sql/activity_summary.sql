-- activity_summary - Delayed activity code count per well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T10:17:38+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  184
-- Contract:   well_id, delayed_activity_codes
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH latest_tasks AS
(
    SELECT
        t.id,
        t.ActionOn,
        LTRIM(RTRIM(t.task_code)) AS task_code,
        t.well_id,
        t.target_start,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.progress,
        ROW_NUMBER() OVER
        (
            PARTITION BY t.well_id, LTRIM(RTRIM(t.task_code))
            ORDER BY t.ActionOn DESC, t.id DESC
        ) AS row_num
    FROM well.task_daily AS t
),
mapping_grouped AS
(
    SELECT
        CAST(m.Activity_ID AS nvarchar(50)) AS activity_id,
        COUNT(DISTINCT m.New_Activity_Code) AS activity_code_count,
        MAX(m.New_Activity_Code) AS activity_code,
        MAX(m.New_Crew_code) AS crew_code
    FROM dbo.mapping_master AS m
    GROUP BY CAST(m.Activity_ID AS nvarchar(50))
),
mapping_unique AS
(
    SELECT
        mg.activity_id,
        mg.activity_code,
        mg.crew_code
    FROM mapping_grouped AS mg
    WHERE mg.activity_code_count = 1
),
description_grouped AS
(
    SELECT
        d.activity_code,
        COUNT(DISTINCT d.activity_group_description) AS wbs_count,
        MAX(d.activity_group_description) AS wbs,
        MAX(d.crew_code) AS description_crew_code
    FROM dbo.activity_master_csv AS d
    GROUP BY d.activity_code
),
description_unique AS
(
    SELECT
        dg.activity_code,
        dg.wbs,
        dg.description_crew_code
    FROM description_grouped AS dg
    WHERE dg.wbs_count = 1
),
task_activity AS
(
    SELECT
        lt.id,
        lt.ActionOn,
        lt.task_code,
        lt.well_id,
        lt.target_start,
        lt.target_end,
        lt.actual_start,
        lt.actual_end,
        lt.progress,
        LEFT
        (
            lt.task_code,
            NULLIF(CHARINDEX('-', lt.task_code), 0) - 1
        ) AS activity_id
    FROM latest_tasks AS lt
    WHERE lt.row_num = 1
),
resolved_tasks AS
(
    SELECT
        ta.id,
        ta.ActionOn,
        ta.task_code,
        ta.well_id,
        ta.target_start,
        ta.target_end,
        ta.actual_start,
        ta.actual_end,
        ta.progress,
        ta.activity_id,
        mu.activity_code,
        mu.crew_code,
        du.wbs
    FROM task_activity AS ta
    LEFT JOIN mapping_unique AS mu
        ON mu.activity_id = ta.activity_id
    LEFT JOIN description_unique AS du
        ON du.activity_code = mu.activity_code
),
task_end_status AS
(
    SELECT
        rt.well_id,
        rt.activity_code,
        CASE
            WHEN rt.target_end IS NULL
                THEN 'DATA_QUALITY_ISSUE'
            WHEN rt.actual_end IS NOT NULL
                 AND rt.actual_end < rt.target_end
                THEN 'COMPLETED_EARLY'
            WHEN rt.actual_end IS NOT NULL
                 AND rt.actual_end = rt.target_end
                THEN 'COMPLETED_ON_TIME'
            WHEN rt.actual_end IS NOT NULL
                 AND rt.actual_end > rt.target_end
                THEN 'COMPLETED_LATE'
            WHEN rt.actual_end IS NULL
                 AND rt.target_end > CAST(GETDATE() AS date)
                THEN 'IN_PROGRESS_NOT_LATE'
            WHEN rt.actual_end IS NULL
                 AND rt.target_end = CAST(GETDATE() AS date)
                THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status
    FROM resolved_tasks AS rt
    INNER JOIN well.well_master AS w
        ON CAST(rt.well_id AS int) = CAST(w.well_id AS int)
       AND w.eng_completion_date IS NULL
),
summary_rows AS
(
    SELECT
        tes.well_id,
        COUNT(DISTINCT tes.activity_code) AS delayed_activity_codes
    FROM task_end_status AS tes
    WHERE tes.activity_code IS NOT NULL
      AND tes.end_status IN ('COMPLETED_LATE', 'OVERDUE')
    GROUP BY tes.well_id
)
SELECT
    sr.well_id AS well_id,
    sr.delayed_activity_codes AS delayed_activity_codes
FROM summary_rows AS sr
ORDER BY
    sr.delayed_activity_codes DESC,
    sr.well_id ASC
