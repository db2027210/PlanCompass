-- v86 M04: two reused aggregate CTEs plus InitPlans keep both materialized
-- result sets live. Eligibility requires an actual CTE/InitPlan collapse.
WITH source_rollup AS (
    SELECT t.sourceid AS accountid,
           count(*) AS transfer_count,
           sum(t.amount) AS total_amount
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 1800000
      AND t.amount > 0
    GROUP BY t.sourceid
),
target_rollup AS (
    SELECT t.targetid AS accountid,
           count(*) AS transfer_count,
           sum(t.amount) AS total_amount
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 1800000
      AND t.amount > 0
    GROUP BY t.targetid
)
SELECT count(*), sum((s.transfer_count + d.transfer_count)::numeric)
FROM source_rollup s
JOIN target_rollup d USING (accountid)
WHERE s.total_amount >= (SELECT avg(total_amount) FROM source_rollup)
  AND d.total_amount >= (SELECT avg(total_amount) FROM target_rollup);
