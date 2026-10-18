WITH source_groups AS MATERIALIZED (
    SELECT t.sourceid
    FROM transfer t
    WHERE t.sourceid BETWEEN 1 AND 2900000
    GROUP BY t.sourceid
),
target_groups AS MATERIALIZED (
    SELECT t.targetid
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 2900000
    GROUP BY t.targetid
)
SELECT
    (SELECT count(*) FROM source_groups)
    + (SELECT count(*) FROM target_groups);
