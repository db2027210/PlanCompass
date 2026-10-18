-- Build an SF1-equivalent TPC-H workload by making ten referentially
-- consistent copies of the existing SF0.1 database.  The original `tpch`
-- database is read-only; all writes go to the new `tpch_sf1` database.

CREATE DATABASE tpch_sf1 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE tpch_sf1;

CREATE TABLE tpch_sf1.region LIKE tpch.region;
CREATE TABLE tpch_sf1.nation LIKE tpch.nation;
CREATE TABLE tpch_sf1.part LIKE tpch.part;
CREATE TABLE tpch_sf1.supplier LIKE tpch.supplier;
CREATE TABLE tpch_sf1.customer LIKE tpch.customer;
CREATE TABLE tpch_sf1.partsupp LIKE tpch.partsupp;
CREATE TABLE tpch_sf1.orders LIKE tpch.orders;
CREATE TABLE tpch_sf1.lineitem LIKE tpch.lineitem;

CREATE TEMPORARY TABLE tpch_copies (copy_id INT PRIMARY KEY);
INSERT INTO tpch_copies VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9);

SET SESSION foreign_key_checks = 0;
SET SESSION unique_checks = 0;

INSERT INTO tpch_sf1.region SELECT * FROM tpch.region;
INSERT INTO tpch_sf1.nation SELECT * FROM tpch.nation;

INSERT INTO tpch_sf1.part
SELECT p_partkey + copy_id * 20000, p_name, p_mfgr, p_brand, p_type,
       p_size, p_container, p_retailprice, p_comment
FROM tpch.part CROSS JOIN tpch_copies;

INSERT INTO tpch_sf1.supplier
SELECT s_suppkey + copy_id * 1000, s_name, s_address, s_nationkey,
       s_phone, s_acctbal, s_comment
FROM tpch.supplier CROSS JOIN tpch_copies;

INSERT INTO tpch_sf1.customer
SELECT c_custkey + copy_id * 15000, c_name, c_address, c_nationkey,
       c_phone, c_acctbal, c_mktsegment, c_comment
FROM tpch.customer CROSS JOIN tpch_copies;

INSERT INTO tpch_sf1.partsupp
SELECT ps_partkey + copy_id * 20000, ps_suppkey + copy_id * 1000,
       ps_availqty, ps_supplycost, ps_comment
FROM tpch.partsupp CROSS JOIN tpch_copies;

INSERT INTO tpch_sf1.orders
SELECT o_orderkey + copy_id * 600000, o_custkey + copy_id * 15000,
       o_orderstatus, o_totalprice, o_orderdate, o_orderpriority,
       o_clerk, o_shippriority, o_comment
FROM tpch.orders CROSS JOIN tpch_copies;

INSERT INTO tpch_sf1.lineitem
SELECT l_orderkey + copy_id * 600000, l_partkey + copy_id * 20000,
       l_suppkey + copy_id * 1000, l_linenumber, l_quantity,
       l_extendedprice, l_discount, l_tax, l_returnflag, l_linestatus,
       l_shipdate, l_commitdate, l_receiptdate, l_shipinstruct,
       l_shipmode, l_comment
FROM tpch.lineitem CROSS JOIN tpch_copies;

SET SESSION unique_checks = 1;
SET SESSION foreign_key_checks = 1;

ANALYZE TABLE tpch_sf1.region, tpch_sf1.nation, tpch_sf1.part,
              tpch_sf1.supplier, tpch_sf1.customer, tpch_sf1.partsupp,
              tpch_sf1.orders, tpch_sf1.lineitem;

SELECT table_name, table_rows,
       ROUND((data_length + index_length) / 1024 / 1024, 1) AS mb
FROM information_schema.tables
WHERE table_schema = 'tpch_sf1'
ORDER BY table_name;
