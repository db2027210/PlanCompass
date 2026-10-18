CREATE EXTENSION IF NOT EXISTS pg_prewarm;

WITH target_tables AS (
    SELECT c.oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = ANY (ARRAY[
          'checking',
          'customer',
          'savingaccount',
          'checkingaccount',
          'transfer',
          'company'
      ])
), target_relations AS (
    SELECT oid AS relation_oid, 0 AS relation_order
    FROM target_tables
    UNION ALL
    SELECT i.indexrelid AS relation_oid, 1 AS relation_order
    FROM pg_index AS i
    JOIN target_tables AS t ON t.oid = i.indrelid
)
SELECT relation_oid::regclass AS relation,
       pg_prewarm(relation_oid::regclass, 'buffer') AS blocks_loaded
FROM target_relations
ORDER BY relation_order, relation_oid;
