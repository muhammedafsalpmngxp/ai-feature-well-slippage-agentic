-- activity_summary - Delayed activity code count per well
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-18T11:40:09+00:00
-- Schema:     c744bd114e7d5585374563a34e105f61d024b7ca85ee781f4b8e7b8ccd4a605f
-- Rows then:  186
-- Contract:   well_id, delayed_activity_codes
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH task_history_ranked AS
(
    SELECT
        t.well_id AS task_well_id,
        LTRIM(RTRIM(t.task_code)) AS task_code,
        t.target_start AS target_start,
        t.target_end AS target_end,
        t.actual_start AS actual_start,
        t.actual_end AS actual_end,
        ROW_NUMBER() OVER
        (
            PARTITION BY
                LTRIM(RTRIM(t.well_id)),
                LTRIM(RTRIM(t.task_code))
            ORDER BY
                t.ActionOn DESC,
                t.id DESC
        ) AS history_rank
    FROM well.task_daily AS t
),
current_tasks AS
(
    SELECT
        r.task_well_id AS task_well_id,
        r.task_code AS task_code,
        r.target_start AS target_start,
        r.target_end AS target_end,
        r.actual_start AS actual_start,
        r.actual_end AS actual_end,
        LEFT
        (
            r.task_code,
            NULLIF(CHARINDEX('-', r.task_code), 0) - 1
        ) AS activity_id
    FROM task_history_ranked AS r
    WHERE r.history_rank = 1
),
task_schedule_risk AS
(
    SELECT
        c.task_well_id AS task_well_id,
        c.activity_id AS activity_id,
        CASE
            WHEN c.target_end IS NOT NULL
                 AND
                 (
                     c.actual_end > c.target_end
                     OR
                     (
                         c.actual_end IS NULL
                         AND c.target_end < CAST(GETDATE() AS date)
                     )
                 )
                THEN 'RED'
            WHEN c.target_start IS NOT NULL
                 AND c.actual_start IS NULL
                 AND c.target_start < CAST(GETDATE() AS date)
                THEN 'AMBER, START SLIPPING'
            WHEN c.target_start IS NOT NULL
                 AND c.actual_start IS NOT NULL
                 AND c.actual_start > c.target_start
                THEN 'AMBER, START DELAYED'
            ELSE 'GREEN'
        END AS schedule_risk
    FROM current_tasks AS c
),
unique_activity_mappings AS
(
    SELECT
        CAST(m.Activity_ID AS nvarchar(50)) AS activity_id,
        MAX(m.New_Activity_Code) AS activity_code
    FROM dbo.mapping_master AS m
    GROUP BY
        CAST(m.Activity_ID AS nvarchar(50))
    HAVING COUNT(DISTINCT m.New_Activity_Code) = 1
),
delayed_activity_set AS
(
    SELECT DISTINCT
        TRY_CONVERT(int, LTRIM(RTRIM(r.task_well_id))) AS well_id,
        m.activity_code AS activity_code
    FROM task_schedule_risk AS r
    INNER JOIN unique_activity_mappings AS m
        ON m.activity_id = r.activity_id
    INNER JOIN well.well_master AS w
        ON TRY_CONVERT(int, LTRIM(RTRIM(r.task_well_id))) =
           TRY_CONVERT(int, w.well_id)
       AND w.eng_completion_date IS NULL
    WHERE r.schedule_risk IN
    (
        'RED',
        'AMBER, START SLIPPING',
        'AMBER, START DELAYED'
    )
      AND m.activity_code IS NOT NULL
)
SELECT
    d.well_id AS well_id,
    COUNT(DISTINCT d.activity_code) AS delayed_activity_codes
FROM delayed_activity_set AS d
GROUP BY
    d.well_id
ORDER BY
    COUNT(DISTINCT d.activity_code) DESC,
    d.well_id ASC
