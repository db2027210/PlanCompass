-- v84 mechanism-2: hashsetop_intersect_to_sorted_setop
-- INTERSECT of two bounded transfer projections (type=transfer vs type=donate).
-- Default expected: HashSetOp large work_mem-resident table.
-- Candidate: Sorted SetOp under seven-cost only (enable_hashagg fixed ON both sides).
-- High risk: Sorted SetOp may require enable_hashagg=off (not a candidate lever).
SELECT sourceid
FROM transfer
WHERE sourceid BETWEEN 1 AND 1800000
  AND type = 'transfer'
  AND amount > 0
INTERSECT
SELECT sourceid
FROM transfer
WHERE sourceid BETWEEN 1 AND 1800000
  AND type = 'donate'
  AND amount > 0;
