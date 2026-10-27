-- 010_features.sql — the training table for the learned signal (pipeline/learned.py; DECISIONS D-51).
-- Reads PH_AML (make data) and writes only PH_LEARN, so the banks' schemas are never touched.
-- Parameters: as_of, max_txns, max_positives, max_negatives (numbers), low, medium, high (expected monthly credits).
--
-- Which accounts: every AMLworld account that received money. Label 1 = it sent at least one payment AMLworld marks
-- as laundering (the same rule as pipeline/lab.py), else 0. All labelled accounts up to max_positives, and a fixed
-- pseudo-random set of the others (ordered by a hash of the ID, so a rerun picks the same ones). Accounts with more
-- than max_txns payments in or out are left out: busy hubs make the self-joins slow, and a ticket's accounts have
-- far fewer.
--
-- The features are the case-independent signals of sql/steps/060_score.sql and five connection inputs, computed the
-- same way over the same 90-day (30 for the profile) window before as_of, so a weight learned here means the same
-- thing at a bank.

CREATE SCHEMA IF NOT EXISTS PH_LEARN;

CREATE OR REPLACE TABLE PH_LEARN.SAMPLE AS
SELECT x.account_id, x.label
FROM (
    SELECT r.account_id,
           CASE WHEN b.account_id IS NULL THEN 0 ELSE 1 END AS label,
           ROW_NUMBER() OVER (PARTITION BY CASE WHEN b.account_id IS NULL THEN 0 ELSE 1 END
                              ORDER BY HASH_MD5(r.account_id)) AS rn
    FROM (SELECT to_account AS account_id FROM PH_AML.TXN
          GROUP BY to_account HAVING COUNT(*) <= {max_txns!d}) r
    LEFT JOIN (SELECT from_account, COUNT(*) AS n FROM PH_AML.TXN GROUP BY from_account) o
           ON o.from_account = r.account_id
    LEFT JOIN (SELECT DISTINCT from_account AS account_id FROM PH_AML.TXN WHERE is_laundering) b
           ON b.account_id = r.account_id
    WHERE COALESCE(o.n, 0) <= {max_txns!d}
) x
WHERE x.rn <= CASE WHEN x.label = 1 THEN {max_positives!d} ELSE {max_negatives!d} END;

CREATE OR REPLACE TABLE PH_LEARN.T AS
SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit
FROM PH_AML.TXN t
WHERE t.to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
   OR t.from_account IN (SELECT account_id FROM PH_LEARN.SAMPLE);

CREATE OR REPLACE TABLE PH_LEARN.FEATURES AS
SELECT s.account_id, s.label,
       dw.v AS median_dwell_minutes,
       pt.v AS pass_through_ratio,
       lp.v AS large_payer_share,
       ex.v AS cash_or_crypto_exits,
       cp.v AS credit_vs_profile,
       cx.distinct_payers, cx.distinct_payees, cx.out_in_ratio, cx.payment_count, cx.reciprocal_share
FROM PH_LEARN.SAMPLE s
LEFT JOIN (
    -- median minutes from a credit to the next debit (or to as_of if nothing has left yet)
    SELECT x.account_id, CAST(MEDIAN(x.dwell) AS DOUBLE) AS v
    FROM (
        SELECT c.to_account AS account_id, c.txn_id,
               MINUTES_BETWEEN(COALESCE(MIN(d.ts), TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')), c.ts) AS dwell
        FROM PH_LEARN.T c
        LEFT JOIN PH_LEARN.T d
               ON d.from_account = c.to_account
              AND d.ts >= c.ts
              AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE c.to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
          AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY c.to_account, c.txn_id, c.ts
    ) x
    GROUP BY x.account_id
) dw ON dw.account_id = s.account_id
LEFT JOIN (
    -- share of each credit sent on within 60 minutes, over all credits
    SELECT y.account_id, CAST(SUM(y.passed) / NULLIF(SUM(y.amount), 0) AS DOUBLE) AS v
    FROM (
        SELECT c.to_account AS account_id, c.txn_id, c.amount_inr AS amount,
               LEAST(c.amount_inr, COALESCE(SUM(d.amount_inr), 0)) AS passed
        FROM PH_LEARN.T c
        LEFT JOIN PH_LEARN.T d
               ON d.from_account = c.to_account
              AND d.ts >= c.ts
              AND d.ts <= ADD_MINUTES(c.ts, 60)
              AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE c.to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
          AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY c.to_account, c.txn_id, c.amount_inr
    ) y
    GROUP BY y.account_id
) pt ON pt.account_id = s.account_id
LEFT JOIN (
    -- share of credit value from the top three payers
    SELECT z.account_id,
           CAST(SUM(CASE WHEN z.rk <= 3 THEN z.amt ELSE 0 END) / NULLIF(SUM(z.amt), 0) AS DOUBLE) AS v
    FROM (
        SELECT p.account_id, p.amt, ROW_NUMBER() OVER (PARTITION BY p.account_id ORDER BY p.amt DESC) AS rk
        FROM (
            SELECT t.to_account AS account_id, t.from_account, SUM(t.amount_inr) AS amt
            FROM PH_LEARN.T t
            WHERE t.to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY t.to_account, t.from_account
        ) p
    ) z
    GROUP BY z.account_id
) lp ON lp.account_id = s.account_id
LEFT JOIN (
    -- cash or crypto withdrawals
    SELECT a.account_id, CAST(COUNT(t.txn_id) AS DOUBLE) AS v
    FROM PH_LEARN.SAMPLE a
    LEFT JOIN PH_LEARN.T t
           ON t.from_account = a.account_id
          AND t.is_exit
          AND t.channel IN ('ATM', 'CASH', 'CRYPTO')
          AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
    GROUP BY a.account_id
) ex ON ex.account_id = s.account_id
LEFT JOIN (
    -- credits in the last 30 days / what the declared profile band expects in a month
    SELECT a.account_id,
           CAST(COALESCE(SUM(t.amount_inr), 0) /
                MAX(CASE acc.profile_band WHEN 'low' THEN {low!d} WHEN 'high' THEN {high!d} ELSE {medium!d} END)
                AS DOUBLE) AS v
    FROM PH_LEARN.SAMPLE a
    LEFT JOIN PH_AML.ACCOUNT acc ON acc.account_id = a.account_id
    LEFT JOIN PH_LEARN.T t
           ON t.to_account = a.account_id
          AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -30)
          AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
    GROUP BY a.account_id
) cp ON cp.account_id = s.account_id
LEFT JOIN (
    -- connection inputs (the model's only): distinct payers and payees, money out / in, number of payments, and the
    -- share of its payees that also paid it. The same SQL as sql/steps/060_score.sql, over the same 90 days.
    SELECT a.account_id,
           CAST(COALESCE(ci.payers, 0) AS DOUBLE) AS distinct_payers,
           CAST(COALESCE(co.payees, 0) AS DOUBLE) AS distinct_payees,
           CAST(COALESCE(co.amt, 0) / NULLIF(ci.amt, 0) AS DOUBLE) AS out_in_ratio,
           CAST(COALESCE(ci.n, 0) + COALESCE(co.n, 0) AS DOUBLE) AS payment_count,
           CAST(COALESCE(rc.n, 0) AS DOUBLE) / NULLIF(co.payees, 0) AS reciprocal_share
    FROM PH_LEARN.SAMPLE a
    LEFT JOIN (SELECT to_account AS account_id, COUNT(DISTINCT from_account) AS payers,
                      SUM(amount_inr) AS amt, COUNT(*) AS n
               FROM PH_LEARN.T
               WHERE to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
                 AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                 AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
               GROUP BY to_account) ci ON ci.account_id = a.account_id
    LEFT JOIN (SELECT from_account AS account_id, COUNT(DISTINCT to_account) AS payees,
                      SUM(amount_inr) AS amt, COUNT(*) AS n
               FROM PH_LEARN.T
               WHERE from_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
                 AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                 AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
               GROUP BY from_account) co ON co.account_id = a.account_id
    LEFT JOIN (SELECT o.account_id, COUNT(DISTINCT o.other) AS n
               FROM (SELECT DISTINCT from_account AS account_id, to_account AS other FROM PH_LEARN.T
                     WHERE from_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
                       AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                       AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) o
               JOIN (SELECT DISTINCT to_account AS account_id, from_account AS other FROM PH_LEARN.T
                     WHERE to_account IN (SELECT account_id FROM PH_LEARN.SAMPLE)
                       AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                       AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) i
                 ON i.account_id = o.account_id AND i.other = o.other
               GROUP BY o.account_id) rc ON rc.account_id = a.account_id
) cx ON cx.account_id = s.account_id;