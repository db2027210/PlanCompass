-- v82 C02: native AP13-class WindowAgg + Sort probe.
SELECT tr.sourceid,
       tr.targetid,
       cu.province,
       tr.amount,
       rank() OVER (PARTITION BY cu.province ORDER BY tr.amount DESC) AS ranking
FROM transfer tr
JOIN customer cu ON tr.targetid = cu.custid
WHERE tr.sourceid = 1001;
