CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DROP TABLE IF EXISTS bpt_orders;
DROP TABLE IF EXISTS bpt_accounts;
DROP TABLE IF EXISTS bpt_touch;

CREATE TABLE bpt_accounts AS
SELECT
    i AS id,
    (i % 20) AS region,
    (i % 5) AS status,
    repeat(md5(i::text), 2) AS padding
FROM generate_series(1, 100000) AS s(i);

ALTER TABLE bpt_accounts ADD PRIMARY KEY (id);
CREATE INDEX bpt_accounts_region_idx ON bpt_accounts(region);
CREATE INDEX bpt_accounts_status_idx ON bpt_accounts(status);

CREATE TABLE bpt_orders AS
SELECT
    i AS id,
    (((i::bigint * 7919) % 100000) + 1)::integer AS account_id,
    round((10 + (i % 1000) + random() * 100)::numeric, 2) AS amount,
    DATE '2025-01-01' + ((i * 13) % 365) AS created_at,
    (i % 6) AS status,
    repeat(md5((i * 17)::text), 2) AS payload
FROM generate_series(1, 500000) AS s(i);

ALTER TABLE bpt_orders ADD PRIMARY KEY (id);
CREATE INDEX bpt_orders_account_idx ON bpt_orders(account_id);
CREATE INDEX bpt_orders_status_created_idx ON bpt_orders(status, created_at);
CREATE INDEX bpt_orders_created_idx ON bpt_orders(created_at);
CREATE INDEX bpt_orders_amount_idx ON bpt_orders(amount);

ALTER TABLE bpt_orders
  ADD CONSTRAINT bpt_orders_account_fk
  FOREIGN KEY (account_id) REFERENCES bpt_accounts(id);

VACUUM ANALYZE bpt_accounts;
VACUUM ANALYZE bpt_orders;

CREATE TABLE bpt_touch AS
SELECT
    i AS id,
    0 AS v,
    repeat(md5(('touch-' || i)::text), 4) AS padding
FROM generate_series(1, 100000) AS s(i);

ALTER TABLE bpt_touch ADD PRIMARY KEY (id);
VACUUM ANALYZE bpt_touch;

SELECT pg_stat_statements_reset();
