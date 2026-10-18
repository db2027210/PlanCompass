\set ON_ERROR_STOP on
\pset pager off
\pset format unaligned
\pset tuples_only on

\echo 'Writing plan outputs to /tmp/plan_validate_v2_*.txt'

\echo '=== q_random_all baseline ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 1.0;
SET LOCAL random_page_cost = 4.0;
SET LOCAL cpu_tuple_cost = 0.01;
SET LOCAL cpu_index_tuple_cost = 0.005;
SET LOCAL cpu_operator_cost = 0.0025;
SET LOCAL parallel_setup_cost = 1000.0;
SET LOCAL parallel_tuple_cost = 0.1;
-- SET LOCAL max_parallel_workers_per_gather = 0;
\o /tmp/plan_validate_v2_q_random_all_baseline.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT applicantid
FROM loantrans
ORDER BY RANDOM()
LIMIT 100;
\o
ROLLBACK;

\echo '=== q_random_all aggressive ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 30.0;
SET LOCAL random_page_cost = 0.01;
SET LOCAL cpu_tuple_cost = 0.0001;
SET LOCAL cpu_index_tuple_cost = 0.0001;
SET LOCAL cpu_operator_cost = 0.0001;
SET LOCAL parallel_setup_cost = 0.0;
SET LOCAL parallel_tuple_cost = 0.0;
-- SET LOCAL min_parallel_table_scan_size = 0;
-- SET LOCAL min_parallel_index_scan_size = 0;
-- SET LOCAL max_parallel_workers_per_gather = 8;
\o /tmp/plan_validate_v2_q_random_all_aggressive.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT applicantid
FROM loantrans
ORDER BY RANDOM()
LIMIT 100;
\o
ROLLBACK;

\echo '=== q_transfer_group baseline ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 1.0;
SET LOCAL random_page_cost = 4.0;
SET LOCAL cpu_tuple_cost = 0.01;
SET LOCAL cpu_index_tuple_cost = 0.005;
SET LOCAL cpu_operator_cost = 0.0025;
SET LOCAL parallel_setup_cost = 1000.0;
SET LOCAL parallel_tuple_cost = 0.1;
-- SET LOCAL max_parallel_workers_per_gather = 0;
\o /tmp/plan_validate_v2_q_transfer_group_baseline.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT t.targetid, sum(t.amount) AS total_amount
FROM transfer t
GROUP BY t.targetid
ORDER BY total_amount DESC
LIMIT 100;
\o
ROLLBACK;

\echo '=== q_transfer_group aggressive ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 30.0;
SET LOCAL random_page_cost = 0.01;
SET LOCAL cpu_tuple_cost = 0.0001;
SET LOCAL cpu_index_tuple_cost = 0.0001;
SET LOCAL cpu_operator_cost = 0.0001;
SET LOCAL parallel_setup_cost = 0.0;
SET LOCAL parallel_tuple_cost = 0.0;
-- SET LOCAL min_parallel_table_scan_size = 0;
-- SET LOCAL min_parallel_index_scan_size = 0;
-- SET LOCAL max_parallel_workers_per_gather = 8;
\o /tmp/plan_validate_v2_q_transfer_group_aggressive.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT t.targetid, sum(t.amount) AS total_amount
FROM transfer t
GROUP BY t.targetid
ORDER BY total_amount DESC
LIMIT 100;
\o
ROLLBACK;

\echo '=== q_join_company baseline ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 1.0;
SET LOCAL random_page_cost = 4.0;
SET LOCAL cpu_tuple_cost = 0.01;
SET LOCAL cpu_index_tuple_cost = 0.005;
SET LOCAL cpu_operator_cost = 0.0025;
SET LOCAL parallel_setup_cost = 1000.0;
SET LOCAL parallel_tuple_cost = 0.1;
-- SET LOCAL max_parallel_workers_per_gather = 0;
\o /tmp/plan_validate_v2_q_join_company_baseline.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT cu.companyid, count(*) AS transfer_cnt, sum(t.amount) AS total_amount
FROM customer cu
JOIN transfer t ON t.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;
\o
ROLLBACK;

\echo '=== q_join_company aggressive ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 30.0;
SET LOCAL random_page_cost = 0.01;
SET LOCAL cpu_tuple_cost = 0.0001;
SET LOCAL cpu_index_tuple_cost = 0.0001;
SET LOCAL cpu_operator_cost = 0.0001;
SET LOCAL parallel_setup_cost = 0.0;
SET LOCAL parallel_tuple_cost = 0.0;
--SET LOCAL min_parallel_table_scan_size = 0;
-- SET LOCAL min_parallel_index_scan_size = 0;
-- SET LOCAL max_parallel_workers_per_gather = 8;
SET LOCAL enable_nestloop = off;
\o /tmp/plan_validate_v2_q_join_company_aggressive.txt
EXPLAIN (FORMAT JSON, COSTS true)
SELECT cu.companyid, count(*) AS transfer_cnt, sum(t.amount) AS total_amount
FROM customer cu
JOIN transfer t ON t.sourceid = cu.custid
GROUP BY cu.companyid
ORDER BY total_amount DESC
LIMIT 100;
\o
ROLLBACK;

\echo 'Done.'
\echo 'Files:'
\echo '  /tmp/plan_validate_v2_q_random_all_baseline.txt'
\echo '  /tmp/plan_validate_v2_q_random_all_aggressive.txt'
\echo '  /tmp/plan_validate_v2_q_transfer_group_baseline.txt'
\echo '  /tmp/plan_validate_v2_q_transfer_group_aggressive.txt'
\echo '  /tmp/plan_validate_v2_q_join_company_baseline.txt'
\echo '  /tmp/plan_validate_v2_q_join_company_aggressive.txt'
