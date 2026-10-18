-- Representative HyBench XP PostgreSQL workload for endpoint screening.
-- Values replace JDBC placeholders so EXPLAIN can run directly.

select * from customer where custid = 1001;

select * from company where companyID = 2001;

select * from savingAccount where accountid = 3001;

select * from checkingAccount where accountid = 4001;

select * from transfer
where sourceid = 1001 or targetid = 1001
order by timestamp desc
limit 1;

select * from checking
where sourceid = 1001 or targetid = 1001
order by timestamp desc
limit 1;

select sourceid, targetid,
case when sourceid = 1001 then 'outbound' when targetid = 1001 then 'inbound' end as direction,
sum(amount) as total_amount
from transfer
where sourceid = 1001 or targetid = 1001
group by sourceid, targetid
order by total_amount desc;

select cu.custid, la.timestamp, la.amount, lt.timestamp, lt.amount, lt.status
from customer cu, loanapps la, loantrans lt
where cu.custid = la.applicantid
and la.id = lt.appid
and cu.custid = 1001;

select custid, name, phone, sum(amount)
from (
  select cu.custid, cu.name, cu.phone, t.amount
  from customer cu join transfer t on cu.custid = t.targetid
  where custid = 1001
  union all
  select cu.custid, cu.name, cu.phone, ch.amount
  from customer cu join checking ch on cu.custid = ch.targetid
  where cu.custid = 1001
) income
group by custid, name, phone;

select cid, total_expense, sum(ch.amount) as checking_amount
from (
  select co.companyid as cid, sum(tr.amount) as total_expense
  from company co, transfer tr
  where co.companyid = tr.sourceid
  and co.name like 'software%'
  group by co.companyid
  order by total_expense desc
) big_company, checking ch
where big_company.cid = ch.sourceid
group by cid, total_expense
order by total_expense desc, checking_amount desc;

with blocked_account as (
  select c.name as name, s.userID as id
  from customer c, savingaccount s
  where c.custid = s.userID
  and (c.isblocked = 1 or s.isblocked = 1)
  limit 100
)
select name, t.sourceid, t.targetid, sum(t.amount) as amount
from blocked_account b, transfer t
where b.id = t.sourceid
group by name, t.sourceid, t.targetid
union all
select name, t.sourceid, t.targetid, sum(t.amount) as amount
from blocked_account b, transfer t
where b.id = t.targetid
group by name, t.sourceid, t.targetid
union all
select name, c.sourceid, c.targetid, sum(c.amount) as amount
from blocked_account b, checking c
where b.id = c.sourceid
group by name, c.sourceid, c.targetid
union all
select name, c.sourceid, c.targetid, sum(c.amount) as amount
from blocked_account b, checking c
where b.id = c.targetid
group by name, c.sourceid, c.targetid
order by amount desc;

select c.name, sum(t.amount) as total_revenue
from company c, transfer t
where c.companyid = t.targetid
and exists (
  select *
  from loanapps la
  where la.applicantid = c.companyid
  and date(timestamp::timestamp) between '2023-11-01' and (cast('2023-11-01' as date) + 60)
)
and not exists (
  select *
  from loantrans lt
  where lt.applicantid = c.companyid
  and date(timestamp::timestamp) between '2023-11-01' and (cast('2023-11-01' as date) + 60)
)
group by c.name
order by total_revenue desc;

with big_account_inbound as (
  select t.targetid as id, c.name as name, sum(t.amount) as amount
  from customer c, transfer t
  where date(t.timestamp::timestamp) between '2023-11-01' and (cast('2023-11-01' as date) + 60)
  and c.custid = t.targetid
  group by name, id
  order by amount desc
  limit 10
),
big_account_outbound as (
  select b.id, b.name as name, sum(t.amount) as amount
  from big_account_inbound b, transfer t
  where date(t.timestamp::timestamp) between '2023-11-01' and (cast('2023-11-01' as date) + 60)
  and b.id = t.sourceid
  group by b.id, b.name
)
select bin.id, bin.name, (sum(bin.amount) - sum(bout.amount)) as revenue
from big_account_inbound bin, big_account_outbound bout
where bin.id = bout.id
group by bin.id, bin.name
order by revenue desc;
