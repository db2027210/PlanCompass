SELECT co.category, count(*) AS cnt, sum(t.amount) AS total_amount
FROM company co
JOIN transfer t ON t.targetid = co.companyid
WHERE co.category IN (
  'software_IT',
  'internet_service',
  'telecommunication',
  'agriculture'
)
GROUP BY co.category
ORDER BY total_amount DESC;
