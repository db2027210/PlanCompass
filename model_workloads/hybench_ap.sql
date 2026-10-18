SELECT cid, total_expense, SUM(ch.amount) AS checking_amount
FROM (
  SELECT co.companyid AS cid, SUM(tr.amount) AS total_expense
  FROM company co
  JOIN transfer tr ON co.companyid = tr.sourceid
  WHERE co.name LIKE 'software%'
  GROUP BY co.companyid
  ORDER BY total_expense DESC
  LIMIT 10
) big_company
JOIN checking ch ON big_company.cid = ch.sourceid
GROUP BY cid, total_expense
ORDER BY total_expense DESC, checking_amount DESC
