SELECT count(*)
FROM (
    SELECT t.targetid
    FROM transfer t
    WHERE t.targetid BETWEEN 1 AND 2900000
      AND (?::integer IS NOT NULL)
    GROUP BY t.targetid
) bonect_io_target_groupagg_rotation;
