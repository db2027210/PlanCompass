-- v75 floor-only: loantrans applicant HashAgg (stable plan at spc=2/3 per v74 EXPLAIN).
-- Plan: Aggregate:Hashed > Gather:W4 > Aggregate:Hashed > Seq Scan:loantrans
-- first_flip_spc=5; same_as_default at spc=2 and spc=3.
SELECT count(*)
FROM (
    SELECT lt.applicantid, count(*) AS cnt, sum(lt.amount) AS amt
    FROM loantrans lt
    GROUP BY lt.applicantid
) bonect_memory_v75_floor_loan_applicant_hash;
