\set ON_ERROR_STOP on
\pset pager off
\pset format unaligned
\pset tuples_only on

\echo 'Writing plan outputs to /tmp/plan_validate_*.txt'

DEALLOCATE ALL;

PREPARE q_invest(text, numeric, numeric, text, text, text, text, text) AS
select co.companyid,
       cu.custid,
       sum(t.amount) as invest_amount,
       count(t.amount) as count,
       avg(sa.balance) as saving_balance,
       avg(ca.balance) as checking_balance,
       avg(la.amount) as avg_loan_amount
from company co,
     transfer t,
     customer cu,
     savingaccount sa,
     loanapps la,
     checkingaccount ca
where t.targetid = co.companyid
  and t.sourceid = cu.custid
  and cu.custid = sa.userid
  and cu.custid = ca.userid
  and la.applicantid = cu.custid
  and t.type = $1
  and sa.balance > $2
  and ca.balance > $3
  and co.category in ($4, $5, $6, $7, $8)
group by co.companyid, cu.custid
order by invest_amount DESC,
         count DESC,
         saving_balance DESC,
         checking_balance DESC,
         avg_loan_amount DESC;

PREPARE q_abnormal(date, integer) AS
With abnormal_accounts as (
  select distinct applicantid as id, amount
  from loantrans lt1
  where contract_timestamp is not null
    and date(contract_timestamp) + duration < $1
  order by amount DESC
  limit $2
)
select aa.id,
       lt2.amount,
       lt2.timestamp,
       lt2.status,
       la.timestamp
from loantrans lt2,
     loanapps la,
     abnormal_accounts aa
where aa.id = lt2.applicantid
  and la.id = lt2.appid;

\echo '=== q_invest baseline ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 1.0;
SET LOCAL random_page_cost = 4.0;
SET LOCAL cpu_tuple_cost = 0.01;
SET LOCAL cpu_index_tuple_cost = 0.005;
SET LOCAL cpu_operator_cost = 0.0025;
SET LOCAL parallel_setup_cost = 1000.0;
SET LOCAL parallel_tuple_cost = 0.1;
\o /tmp/plan_validate_q_invest_baseline.txt
EXPLAIN (FORMAT JSON, COSTS true) EXECUTE q_invest('transfer', 1000, 1000, 'A', 'B', 'C', 'D', 'E');
\o
ROLLBACK;

\echo '=== q_invest aggressive ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 20.0;
SET LOCAL random_page_cost = 0.05;
SET LOCAL cpu_tuple_cost = 0.0001;
SET LOCAL cpu_index_tuple_cost = 0.0001;
SET LOCAL cpu_operator_cost = 0.0001;
SET LOCAL parallel_setup_cost = 0.0;
SET LOCAL parallel_tuple_cost = 0.0;
SET LOCAL min_parallel_table_scan_size = 0;
SET LOCAL min_parallel_index_scan_size = 0;
SET LOCAL max_parallel_workers_per_gather = 8;
SET LOCAL enable_nestloop = off;
\o /tmp/plan_validate_q_invest_aggressive.txt
EXPLAIN (FORMAT JSON, COSTS true) EXECUTE q_invest('transfer', 1000, 1000, 'A', 'B', 'C', 'D', 'E');
\o
ROLLBACK;

\echo '=== q_abnormal baseline ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 1.0;
SET LOCAL random_page_cost = 4.0;
SET LOCAL cpu_tuple_cost = 0.01;
SET LOCAL cpu_index_tuple_cost = 0.005;
SET LOCAL cpu_operator_cost = 0.0025;
SET LOCAL parallel_setup_cost = 1000.0;
SET LOCAL parallel_tuple_cost = 0.1;
\o /tmp/plan_validate_q_abnormal_baseline.txt
EXPLAIN (FORMAT JSON, COSTS true) EXECUTE q_abnormal(DATE '2026-03-01', 200);
\o
ROLLBACK;

\echo '=== q_abnormal aggressive ==='
BEGIN;
SET LOCAL plan_cache_mode = force_generic_plan;
SET LOCAL seq_page_cost = 20.0;
SET LOCAL random_page_cost = 0.05;
SET LOCAL cpu_tuple_cost = 0.0001;
SET LOCAL cpu_index_tuple_cost = 0.0001;
SET LOCAL cpu_operator_cost = 0.0001;
SET LOCAL parallel_setup_cost = 0.0;
SET LOCAL parallel_tuple_cost = 0.0;
SET LOCAL min_parallel_table_scan_size = 0;
SET LOCAL min_parallel_index_scan_size = 0;
SET LOCAL max_parallel_workers_per_gather = 8;
SET LOCAL enable_nestloop = off;
\o /tmp/plan_validate_q_abnormal_aggressive.txt
EXPLAIN (FORMAT JSON, COSTS true) EXECUTE q_abnormal(DATE '2026-03-01', 200);
\o
ROLLBACK;

\echo 'Done.'
\echo 'Files:'
\echo '  /tmp/plan_validate_q_invest_baseline.txt'
\echo '  /tmp/plan_validate_q_invest_aggressive.txt'
\echo '  /tmp/plan_validate_q_abnormal_baseline.txt'
\echo '  /tmp/plan_validate_q_abnormal_aggressive.txt'
