-- Employees hired per department and job in 2021, by quarter.
-- Half-open UTC range on `datetime` keeps the predicate index-friendly.
SELECT d.department,
       j.job,
       COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM e.datetime AT TIME ZONE 'UTC') = 1) AS "Q1",
       COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM e.datetime AT TIME ZONE 'UTC') = 2) AS "Q2",
       COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM e.datetime AT TIME ZONE 'UTC') = 3) AS "Q3",
       COUNT(*) FILTER (WHERE EXTRACT(QUARTER FROM e.datetime AT TIME ZONE 'UTC') = 4) AS "Q4"
FROM hired_employees e
JOIN departments d ON d.id = e.department_id
JOIN jobs        j ON j.id = e.job_id
WHERE e.datetime >= TIMESTAMPTZ '2021-01-01 00:00:00+00'
  AND e.datetime <  TIMESTAMPTZ '2022-01-01 00:00:00+00'
GROUP BY d.department, j.job
ORDER BY d.department, j.job;
