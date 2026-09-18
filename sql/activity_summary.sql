-- activity_summary - Delayed activity code count per well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T04:37:33+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  184
-- Contract:   well_id, delayed_activity_codes
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH task_history AS
(
    SELECT
        td.id AS record_id,
        td.ActionOn AS action_on,
        LTRIM(RTRIM(td.task_code)) AS task_code,
        td.target_start,
        td.target_end,
        td.actual_start,
        td.actual_end,
        td.progress,
        td.well_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY td.well_id, LTRIM(RTRIM(td.task_code))
            ORDER BY td.ActionOn DESC, td.id DESC
        ) AS row_number_value
    FROM well.task_daily AS td
),
latest_tasks AS
(
    SELECT
        th.record_id,
        th.action_on,
        th.task_code,
        th.target_start,
        th.target_end,
        th.actual_start,
        th.actual_end,
        th.progress,
        th.well_id
    FROM task_history AS th
    WHERE th.row_number_value = 1
),
mapping_unique AS
(
    SELECT
        CAST(mm.Activity_ID AS nvarchar(50)) AS activity_id,
        MAX(mm.New_Activity_Code) AS activity_code,
        MAX(mm.New_Crew_code) AS mapping_crew_code
    FROM dbo.mapping_master AS mm
    GROUP BY CAST(mm.Activity_ID AS nvarchar(50))
    HAVING COUNT(DISTINCT mm.New_Activity_Code) = 1
),
description_unique AS
(
    SELECT
        amc.activity_code,
        MAX(amc.activity_group_description) AS wbs,
        MAX(amc.crew_code) AS description_crew_code
    FROM dbo.activity_master_csv AS amc
    GROUP BY amc.activity_code
    HAVING COUNT(*) = COUNT(amc.activity_group_description)
       AND COUNT(DISTINCT amc.activity_group_description) = 1
       AND COUNT(*) = COUNT(amc.crew_code)
       AND COUNT(DISTINCT amc.crew_code) = 1
),
task_activity AS
(
    SELECT
        lt.record_id,
        lt.action_on,
        lt.task_code,
        lt.target_start,
        lt.target_end,
        lt.actual_start,
        lt.actual_end,
        lt.progress,
        lt.well_id,
        CASE
            WHEN CHARINDEX('-', lt.task_code) > 0
            THEN LEFT(lt.task_code, CHARINDEX('-', lt.task_code) - 1)
        END AS activity_id
    FROM latest_tasks AS lt
),
task_end_status AS
(
    SELECT
        ta.record_id,
        ta.well_id,
        mu.activity_code,
        du.wbs,
        CASE
            WHEN ta.target_end IS NULL THEN 'DATA_QUALITY_ISSUE'
            WHEN ta.actual_end IS NOT NULL
                 AND ta.actual_end < ta.target_end THEN 'COMPLETED_EARLY'
            WHEN ta.actual_end IS NOT NULL
                 AND ta.actual_end = ta.target_end THEN 'COMPLETED_ON_TIME'
            WHEN ta.actual_end IS NOT NULL
                 AND ta.actual_end > ta.target_end THEN 'COMPLETED_LATE'
            WHEN CAST(GETDATE() AS date) < ta.target_end THEN 'IN_PROGRESS_NOT_LATE'
            WHEN CAST(GETDATE() AS date) = ta.target_end THEN 'DUE_TODAY'
            ELSE 'OVERDUE'
        END AS end_status
    FROM task_activity AS ta
    LEFT JOIN mapping_unique AS mu
        ON mu.activity_id = ta.activity_id
    LEFT JOIN description_unique AS du
        ON du.activity_code = mu.activity_code
),
delayed_activity_counts AS
(
    SELECT
        wm.well_id,
        COUNT(DISTINCT tes.activity_code) AS delayed_activity_codes
    FROM task_end_status AS tes
    INNER JOIN well.well_master AS wm
        ON CAST(wm.well_id AS varchar(10)) = CAST(tes.well_id AS varchar(10))
       AND wm.eng_completion_date IS NULL
    WHERE tes.activity_code IS NOT NULL
      AND tes.end_status IN ('COMPLETED_LATE', 'OVERDUE')
    GROUP BY wm.well_id
)
SELECT
    dac.well_id AS well_id,
    dac.delayed_activity_codes AS delayed_activity_codes
FROM delayed_activity_counts AS dac
ORDER BY
    dac.delayed_activity_codes DESC,
    dac.well_id ASC
