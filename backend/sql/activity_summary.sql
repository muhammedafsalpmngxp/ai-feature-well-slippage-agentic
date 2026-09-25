-- activity_summary - Delayed activity code count per well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-25T05:44:59+00:00
-- Schema:     5c24c5428a67dbce9e06d931f7ad130f8e8c32d5386ef380f15141eb16c6a458
-- Rows then:  189
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
        td.well_id AS task_well_id,
        td.target_end,
        td.actual_end,
        ROW_NUMBER() OVER
        (
            PARTITION BY
                td.well_id,
                LTRIM(RTRIM(td.task_code))
            ORDER BY
                td.ActionOn DESC,
                td.id DESC
        ) AS row_num
    FROM well.task_daily AS td
),
task_latest AS
(
    SELECT
        th.task_well_id,
        th.task_code,
        th.target_end,
        th.actual_end
    FROM task_history AS th
    WHERE th.row_num = 1
),
mapping_unambiguous AS
(
    SELECT
        CAST(mm.Activity_ID AS nvarchar(50)) AS activity_id,
        MIN(mm.New_Activity_Code) AS activity_code
    FROM dbo.mapping_master AS mm
    GROUP BY
        CAST(mm.Activity_ID AS nvarchar(50))
    HAVING
        COUNT(DISTINCT mm.New_Activity_Code) = 1
        AND SUM(CASE WHEN mm.New_Activity_Code IS NULL THEN 1 ELSE 0 END) = 0
),
task_activity AS
(
    SELECT
        tl.task_well_id,
        tl.target_end,
        tl.actual_end,
        mu.activity_code
    FROM task_latest AS tl
    INNER JOIN mapping_unambiguous AS mu
        ON mu.activity_id =
           LEFT
           (
               tl.task_code,
               NULLIF(CHARINDEX('-', tl.task_code), 0) - 1
           )
    WHERE NULLIF(CHARINDEX('-', tl.task_code), 0) IS NOT NULL
),
delayed_activity_summary AS
(
    SELECT
        wm.well_id,
        COUNT(DISTINCT ta.activity_code) AS delayed_activity_codes
    FROM well.well_master AS wm
    INNER JOIN task_activity AS ta
        ON CAST(ta.task_well_id AS int) = CAST(wm.well_id AS int)
    WHERE wm.eng_completion_date IS NULL
      AND ta.activity_code IS NOT NULL
      AND ta.target_end IS NOT NULL
      AND
      (
          ta.actual_end > ta.target_end
          OR
          (
              ta.actual_end IS NULL
              AND CAST(GETDATE() AS date) > ta.target_end
          )
      )
    GROUP BY
        wm.well_id
)
SELECT
    das.well_id AS well_id,
    das.delayed_activity_codes AS delayed_activity_codes
FROM delayed_activity_summary AS das
WHERE das.delayed_activity_codes > 0
ORDER BY
    das.delayed_activity_codes DESC,
    das.well_id ASC
