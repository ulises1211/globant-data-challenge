-- Departments whose 2021 hires exceed the average across ALL departments.
-- Departments with no hires count as 0 (LEFT JOIN), so they lower the average.
WITH hires AS (
    SELECT d.id,
           d.department,
           COUNT(e.id) AS hired
    FROM departments d
    LEFT JOIN hired_employees e
           ON e.department_id = d.id
          AND e.datetime >= TIMESTAMPTZ '2021-01-01 00:00:00+00'
          AND e.datetime <  TIMESTAMPTZ '2022-01-01 00:00:00+00'
    GROUP BY d.id, d.department
)
SELECT id, department, hired
FROM hires
WHERE hired > (SELECT AVG(hired) FROM hires)
ORDER BY hired DESC, department;
