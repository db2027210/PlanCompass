-- v82 C03: company/category aggregation with a Gather Merge + PartialHA default.
-- Any serial candidate containing Merge Join is rejected as a closed family.
SELECT co.category,
       count(*) AS cnt,
       round(sum(t.amount)::numeric, 2) AS total_amount
FROM company co
JOIN transfer t ON t.targetid = co.companyid
WHERE co.category IN (
  'software_IT',
  'internet_service',
  'telecommunication',
  'agriculture'
)
GROUP BY co.category
ORDER BY total_amount DESC, co.category;
