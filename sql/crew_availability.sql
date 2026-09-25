-- crew_availability - Crew availability
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-25T05:44:59+00:00
-- Schema:     5c24c5428a67dbce9e06d931f7ad130f8e8c32d5386ef380f15141eb16c6a458
-- Rows then:  1253
-- Contract:   crew_id, crew_type_id, open_tasks, in_progress_tasks, overdue_tasks, 
--             wells_active, latest_action_on, availability_status
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH reduced_tasks AS
(
    SELECT
        t.id,
        t.ActionOn,
        LTRIM(RTRIM(t.task_code)) AS task_code,
        LTRIM(RTRIM(t.well_id)) AS task_well_id,
        t.target_end,
        t.actual_start,
        t.actual_end,
        t.crew_id,
        t.crew_type_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY LTRIM(RTRIM(t.well_id)), LTRIM(RTRIM(t.task_code))
            ORDER BY t.ActionOn DESC, t.id DESC
        ) AS rn
    FROM well.task_daily AS t
),
current_tasks AS
(
    SELECT
        r.id,
        r.ActionOn,
        r.task_code,
        r.task_well_id,
        r.target_end,
        r.actual_start,
        r.actual_end,
        r.crew_id,
        r.crew_type_id
    FROM reduced_tasks AS r
    WHERE r.rn = 1
),
crew_records AS
(
    SELECT
        c.crew_id,
        c.crew_type_id,
        c.ActionOn,
        ROW_NUMBER() OVER
        (
            PARTITION BY c.crew_id
            ORDER BY c.ActionOn DESC, c.id DESC
        ) AS crew_rn
    FROM current_tasks AS c
    WHERE c.crew_id IS NOT NULL
),
crew_universe AS
(
    SELECT
        c.crew_id,
        c.crew_type_id,
        c.ActionOn
    FROM crew_records AS c
    WHERE c.crew_rn = 1
),
open_work AS
(
    SELECT
        c.crew_id,
        c.task_well_id,
        c.actual_start,
        c.target_end
    FROM current_tasks AS c
    INNER JOIN well.well_master AS w
        ON TRY_CONVERT(int, c.task_well_id) = TRY_CONVERT(int, w.well_id)
    WHERE c.crew_id IS NOT NULL
      AND w.eng_completion_date IS NULL
      AND c.actual_end IS NULL
),
workload AS
(
    SELECT
        o.crew_id,
        COUNT(*) AS open_tasks,
        COUNT(CASE WHEN o.actual_start IS NOT NULL THEN 1 END) AS in_progress_tasks,
        COUNT(CASE WHEN o.target_end < CAST(GETDATE() AS date) THEN 1 END) AS overdue_tasks,
        COUNT(DISTINCT TRY_CONVERT(int, o.task_well_id)) AS wells_active
    FROM open_work AS o
    GROUP BY o.crew_id
),
availability AS
(
    SELECT
        u.crew_id,
        u.crew_type_id,
        COALESCE(w.open_tasks, 0) AS open_tasks,
        COALESCE(w.in_progress_tasks, 0) AS in_progress_tasks,
        COALESCE(w.overdue_tasks, 0) AS overdue_tasks,
        COALESCE(w.wells_active, 0) AS wells_active,
        u.ActionOn AS latest_action_on,
        CASE
            WHEN COALESCE(w.in_progress_tasks, 0) = 0 THEN 'AVAILABLE'
            ELSE 'BUSY'
        END AS availability_status
    FROM crew_universe AS u
    LEFT JOIN workload AS w
        ON w.crew_id = u.crew_id
)
SELECT
    a.crew_id AS crew_id,
    a.crew_type_id AS crew_type_id,
    a.open_tasks AS open_tasks,
    a.in_progress_tasks AS in_progress_tasks,
    a.overdue_tasks AS overdue_tasks,
    a.wells_active AS wells_active,
    a.latest_action_on AS latest_action_on,
    a.availability_status AS availability_status
FROM availability AS a
ORDER BY
    a.crew_type_id,
    CASE WHEN a.availability_status = 'AVAILABLE' THEN 0 ELSE 1 END,
    a.in_progress_tasks,
    a.overdue_tasks,
    a.crew_id
