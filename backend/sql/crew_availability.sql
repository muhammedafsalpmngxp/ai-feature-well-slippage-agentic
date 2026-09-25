-- crew_availability - Crew availability
--
-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated
-- only when the database's structural fingerprint changes; a data load does not
-- invalidate it. To force a rewrite: python main.py --regenerate
--
-- Frozen at:  2026-09-25T09:27:26+00:00
-- Schema:     5c24c5428a67dbce9e06d931f7ad130f8e8c32d5386ef380f15141eb16c6a458
-- Rows then:  388
-- Contract:   crew_id, crew_type_id, open_tasks, in_progress_tasks, overdue_tasks, 
--             wells_with_open_tasks, wells_in_progress, latest_action_on, availability_status
--
-- Editing this by hand is allowed, but it voids the verification: the hash in
-- manifest.json stops matching and `python main.py --frozen` reports the file as
-- hand-edited rather than as approved.
-- ---- frozen SQL below ------------------------------------------------------
WITH filtered_tasks AS
(
    SELECT
        t.id,
        t.ActionOn,
        t.task_code,
        t.well_id,
        t.actual_start,
        t.actual_end,
        t.target_end,
        t.crew_id,
        t.crew_type_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY
                TRIM(t.well_id),
                TRIM(t.task_code)
            ORDER BY
                t.ActionOn DESC,
                t.id DESC
        ) AS rn
    FROM well.task_daily AS t
    WHERE t.crew_id IS NOT NULL
),
reduced_tasks AS
(
    SELECT
        ft.id,
        ft.ActionOn,
        ft.task_code,
        ft.well_id,
        ft.actual_start,
        ft.actual_end,
        ft.target_end,
        ft.crew_id,
        ft.crew_type_id
    FROM filtered_tasks AS ft
    WHERE ft.rn = 1
),
open_work AS
(
    SELECT
        rt.id,
        rt.ActionOn,
        rt.well_id,
        rt.actual_start,
        rt.target_end,
        rt.crew_id,
        rt.crew_type_id
    FROM reduced_tasks AS rt
    INNER JOIN well.well_master AS wm
        ON CAST(TRIM(rt.well_id) AS varchar(10))
         = CAST(wm.well_id AS varchar(10))
    WHERE rt.actual_end IS NULL
      AND wm.eng_completion_date IS NULL
),
crew_latest_ranked AS
(
    SELECT
        rt.crew_id,
        rt.crew_type_id,
        rt.ActionOn,
        rt.id,
        ROW_NUMBER() OVER
        (
            PARTITION BY rt.crew_id
            ORDER BY rt.ActionOn DESC, rt.id DESC
        ) AS rn
    FROM reduced_tasks AS rt
),
crew_latest AS
(
    SELECT
        clr.crew_id,
        clr.crew_type_id
    FROM crew_latest_ranked AS clr
    WHERE clr.rn = 1
),
crew_counts AS
(
    SELECT
        ow.crew_id,
        COUNT(*) AS open_tasks,
        SUM(CASE WHEN ow.actual_start IS NOT NULL THEN 1 ELSE 0 END) AS in_progress_tasks,
        SUM
        (
            CASE
                WHEN ow.target_end IS NOT NULL
                 AND ow.target_end < CAST(GETDATE() AS date)
                THEN 1
                ELSE 0
            END
        ) AS overdue_tasks,
        COUNT(DISTINCT ow.well_id) AS wells_with_open_tasks,
        COUNT
        (
            DISTINCT CASE
                WHEN ow.actual_start IS NOT NULL THEN ow.well_id
            END
        ) AS wells_in_progress,
        MAX(ow.ActionOn) AS latest_action_on
    FROM open_work AS ow
    GROUP BY ow.crew_id
),
crew_result AS
(
    SELECT
        cc.crew_id,
        cl.crew_type_id,
        cc.open_tasks,
        cc.in_progress_tasks,
        cc.overdue_tasks,
        cc.wells_with_open_tasks,
        cc.wells_in_progress,
        cc.latest_action_on,
        CASE
            WHEN cc.in_progress_tasks = 0 THEN 'AVAILABLE'
            ELSE 'BUSY'
        END AS availability_status
    FROM crew_counts AS cc
    INNER JOIN crew_latest AS cl
        ON cl.crew_id = cc.crew_id
)
SELECT
    cr.crew_id AS crew_id,
    cr.crew_type_id AS crew_type_id,
    cr.open_tasks AS open_tasks,
    cr.in_progress_tasks AS in_progress_tasks,
    cr.overdue_tasks AS overdue_tasks,
    cr.wells_with_open_tasks AS wells_with_open_tasks,
    cr.wells_in_progress AS wells_in_progress,
    cr.latest_action_on AS latest_action_on,
    cr.availability_status AS availability_status
FROM crew_result AS cr
ORDER BY
    CASE WHEN cr.availability_status = 'AVAILABLE' THEN 0 ELSE 1 END,
    cr.crew_type_id,
    cr.wells_in_progress,
    cr.in_progress_tasks,
    cr.overdue_tasks,
    cr.crew_id
