-- v84 mechanism-2 variant: EXCEPT of two bounded transfer projections.
-- Default expected: HashSetOp; candidate Sorted SetOp under seven-cost only.
SELECT sourceid
FROM transfer
WHERE sourceid BETWEEN 1 AND 1500000
  AND type IN ('transfer', 'invest')
  AND amount > 0
EXCEPT
SELECT sourceid
FROM transfer
WHERE sourceid BETWEEN 1 AND 1500000
  AND type = 'donate'
  AND amount > 0;
