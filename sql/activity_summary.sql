-- activity_summary - Delayed activity code count per well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T10:52:00+00:00
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
        td.*,
        ROW_NUMBER() OVER
        (
            PARTITION BY
                CAST(td.well_id AS varchar(10)),
                TRIM(td.task_code)
            ORDER BY
                td.ActionOn DESC,
                td.id DESC
        ) AS row_num
    FROM well.task_daily AS td
),
mapping_unique AS
(
    SELECT
        CAST(mm.Activity_ID AS nvarchar(50)) AS activity_id,
        MIN(mm.New_Activity_Code) AS activity_code
    FROM dbo.mapping_master AS mm
    GROUP BY CAST(mm.Activity_ID AS nvarchar(50))
    HAVING COUNT(DISTINCT mm.New_Activity_Code) = 1
),
description_unique AS
(
    SELECT
        am.activity_code,
        MIN(am.activity_group_description) AS wbs
    FROM dbo.activity_master_csv AS am
    GROUP BY am.activity_code
    HAVING COUNT(DISTINCT am.activity_group_description) = 1
),
task_resolved AS
(
    SELECT
        CAST(lt.well_id AS int) AS well_id,
        map.activity_code,
        desc_map.wbs,
        lt.target_end,
        lt.actual_end
    FROM latest_tasks AS lt
    INNER JOIN well.well_master AS wm
        ON CAST(lt.well_id AS varchar(10)) = CAST(wm.well_id AS varchar(10))
       AND wm.eng_completion_date IS NULL
    LEFT JOIN mapping_unique AS map
        ON map.activity_id =
           LEFT
           (
               TRIM(lt.task_code),
               NULLIF(CHARINDEX('-', TRIM(lt.task_code)), 0) - 1
           )
    LEFT JOIN description_unique AS desc_map
        ON desc_map.activity_code = map.activity_code
    WHERE lt.row_num = 1
),
delayed_tasks AS
(
    SELECT
        tr.well_id,
        tr.activity_code
    FROM task_resolved AS tr
    WHERE tr.activity_code IS NOT NULL
      AND
      (
          tr.target_end IS NOT NULL
          AND
          (
              tr.actual_end > tr.target_end
              OR
              (
                  tr.actual_end IS NULL
                  AND tr.target_end < CAST(GETDATE() AS date)
              )
          )
      )
)
SELECT
    dt.well_id AS well_id,
    COUNT(DISTINCT dt.activity_code) AS delayed_activity_codes
FROM delayed_tasks AS dt
GROUP BY dt.well_id
ORDER BY
    COUNT(DISTINCT dt.activity_code) DESC,
    dt.well_id ASC
