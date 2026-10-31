-- 020_weight_rows.sql — training rows for the learned weights of scoring v2 (pipeline/weights.py; DECISIONS D-58).
-- Reads PH_AML (make data) and writes only PH_LEARN, so the banks' schemas are never touched.
-- Parameters: as_of (end of the signal window: the first 60% of AMLworld's period), label_end (end of the period),
--             max_txns, max_positives, max_negatives (numbers)
--
-- Time split: the signals are measured over the 90 days before as_of, the label over the rest of the period. Without
-- it, receiving laundered money would partly be the label itself. Label 1 = the account sends a payment AMLworld marks
-- as laundering after as_of.
-- Exposure stand-ins (AMLworld has no complaints or notices; the README says so):
--   flagged account   one that sent a laundering-marked payment in the signal window
--   a complaint       a laundering-marked payment's origin: E1 counts origins that paid the account directly in the
--                     last 7 days, E2 origins whose laundering money reached it directly or through one account
--   victim money (P4) the laundering-marked money that came in
-- Everything else is computed as sql/steps/060_score.sql computes it, with the same per-credit matching UDF.

CREATE SCHEMA IF NOT EXISTS PH_LEARN;

CREATE OR REPLACE PYTHON3 SET SCRIPT PH_LEARN.MATCH_CREDITS(
    account_id   VARCHAR(40),
    direction    CHAR(1),
    ts           TIMESTAMP,
    txn_id       VARCHAR(40),
    amount_paise DECIMAL(18,0),
    exit_kind    VARCHAR(4),
    fraud_linked BOOLEAN
) EMITS (
    account_id   VARCHAR(40),
    txn_id       VARCHAR(40),
    amount_paise DECIMAL(18,0),
    fraud_linked BOOLEAN,
    out_paise    DECIMAL(18,0),
    cash_paise   DECIMAL(18,0)
) AS
--@include sql/udf/match_credits.py
/

CREATE OR REPLACE TABLE PH_LEARN.W_SAMPLE AS
SELECT x.account_id, x.label
FROM (
    SELECT r.account_id,
           CASE WHEN b.account_id IS NULL THEN 0 ELSE 1 END AS label,
           ROW_NUMBER() OVER (PARTITION BY CASE WHEN b.account_id IS NULL THEN 0 ELSE 1 END
                              ORDER BY HASH_MD5(r.account_id)) AS rn
    FROM (SELECT to_account AS account_id FROM PH_AML.TXN
          WHERE ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
            AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
          GROUP BY to_account HAVING COUNT(*) <= {max_txns!d}) r
    LEFT JOIN (SELECT from_account, COUNT(*) AS n FROM PH_AML.TXN
               WHERE ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                 AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
               GROUP BY from_account) o ON o.from_account = r.account_id
    LEFT JOIN (SELECT DISTINCT from_account AS account_id FROM PH_AML.TXN
               WHERE is_laundering
                 AND ts >  TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                 AND ts <= TO_TIMESTAMP({label_end}, 'YYYY-MM-DD HH24:MI:SS')) b ON b.account_id = r.account_id
    WHERE COALESCE(o.n, 0) <= {max_txns!d}
) x
WHERE x.rn <= CASE WHEN x.label = 1 THEN {max_positives!d} ELSE {max_negatives!d} END;

CREATE OR REPLACE TABLE PH_LEARN.W_FLAGGED AS
SELECT DISTINCT from_account AS account_id
FROM PH_AML.TXN
WHERE is_laundering
  AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
  AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS');

CREATE OR REPLACE TABLE PH_LEARN.WT AS
SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit, t.is_laundering
FROM PH_AML.TXN t
WHERE t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
  AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
  AND t.to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
UNION
SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit, t.is_laundering
FROM PH_AML.TXN t
WHERE t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
  AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
  AND t.from_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE);

CREATE OR REPLACE TABLE PH_LEARN.W_MATCH AS
SELECT u.account_id, u.txn_id, u.amount_paise / 100 AS amount_inr, u.out_paise / 100 AS out_inr,
       u.cash_paise / 100 AS cash_inr
FROM (
    SELECT PH_LEARN.MATCH_CREDITS(e.account_id, e.direction, e.ts, e.txn_id, e.amount_paise, e.exit_kind,
                                  e.fraud_linked)
    FROM (
        SELECT c.to_account AS account_id, 'C' AS direction, c.ts AS ts, c.txn_id AS txn_id,
               CAST(c.amount_inr * 100 AS DECIMAL(18,0)) AS amount_paise, 'NONE' AS exit_kind,
               FALSE AS fraud_linked
        FROM PH_LEARN.WT c WHERE c.to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
        UNION ALL
        SELECT d.from_account, 'D', d.ts, d.txn_id, CAST(d.amount_inr * 100 AS DECIMAL(18,0)),
               CASE WHEN d.channel IN ('NACH', 'AUTOPAY') THEN 'AUTO'
                    WHEN d.channel IN ('ATM', 'CASH', 'CRYPTO') THEN 'CASH' ELSE 'MOVE' END,
               FALSE
        FROM PH_LEARN.WT d WHERE d.from_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
    ) e
    GROUP BY e.account_id
) u;

CREATE OR REPLACE TABLE PH_LEARN.W_VALUES AS
SELECT v.account_id, v.signal_name, v.signal_value
FROM (
    SELECT x1.account_id, 'median_dwell_minutes' AS signal_name, CAST(MEDIAN(x1.dwell) AS DOUBLE) AS signal_value
    FROM (
        SELECT c1.to_account AS account_id, c1.txn_id,
               MINUTES_BETWEEN(COALESCE(MIN(d1.ts), TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')), c1.ts) AS dwell
        FROM PH_LEARN.WT c1
        LEFT JOIN PH_LEARN.WT d1 ON d1.from_account = c1.to_account AND d1.ts >= c1.ts
                                AND d1.channel NOT IN ('NACH', 'AUTOPAY')
        WHERE c1.to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
        GROUP BY c1.to_account, c1.txn_id, c1.ts
    ) x1
    GROUP BY x1.account_id
    UNION ALL
    SELECT account_id, 'passed_on_within_hour', CAST(SUM(out_inr) / NULLIF(SUM(amount_inr), 0) AS DOUBLE)
    FROM PH_LEARN.W_MATCH GROUP BY account_id
    UNION ALL
    SELECT account_id, 'cashed_out_within_hour', CAST(SUM(cash_inr) / NULLIF(SUM(amount_inr), 0) AS DOUBLE)
    FROM PH_LEARN.W_MATCH GROUP BY account_id
    UNION ALL
    SELECT s.account_id, 'balance_kept_ratio',
           CAST(GREATEST(COALESCE(i.amt, 0) - COALESCE(o.amt, 0), 0) / NULLIF(i.laundering, 0) AS DOUBLE)
    FROM PH_LEARN.W_SAMPLE s
    LEFT JOIN (SELECT to_account, SUM(amount_inr) AS amt,
                      SUM(CASE WHEN is_laundering THEN amount_inr ELSE 0 END) AS laundering
               FROM PH_LEARN.WT GROUP BY to_account) i ON i.to_account = s.account_id
    LEFT JOIN (SELECT from_account, SUM(amount_inr) AS amt FROM PH_LEARN.WT GROUP BY from_account) o
           ON o.from_account = s.account_id
    UNION ALL
    SELECT z.account_id, 'top3_payer_share',
           CAST(SUM(CASE WHEN z.rk <= 3 THEN z.amt ELSE 0 END) / NULLIF(SUM(z.amt), 0) AS DOUBLE)
    FROM (
        SELECT p.account_id, p.amt, ROW_NUMBER() OVER (PARTITION BY p.account_id ORDER BY p.amt DESC) AS rk
        FROM (SELECT to_account AS account_id, from_account, SUM(amount_inr) AS amt FROM PH_LEARN.WT
              WHERE to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE) AND channel <> 'NACH'
              GROUP BY to_account, from_account) p
    ) z
    GROUP BY z.account_id
    UNION ALL
    SELECT to_account, 'direct_complaints_7d', CAST(COUNT(DISTINCT from_account) AS DOUBLE)
    FROM PH_LEARN.WT
    WHERE is_laundering AND to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
      AND ts > ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -7)
    GROUP BY to_account
    UNION ALL
    SELECT g.account_id, 'trail_touches_90d', CAST(SUM(g.w) AS DOUBLE)
    FROM (
        SELECT r.account_id, r.origin,
               CASE WHEN MAX(r.ts) > ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -30) THEN 1 ELSE 0.5 END
                   AS w
        FROM (
            SELECT to_account AS account_id, from_account AS origin, ts FROM PH_LEARN.WT
            WHERE is_laundering AND to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
            UNION ALL
            SELECT t.to_account, l.from_account, l.ts
            FROM PH_AML.TXN l
            JOIN PH_LEARN.WT t ON t.from_account = l.to_account AND t.ts >= l.ts
            WHERE l.is_laundering
              AND l.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND l.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
              AND t.to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)
        ) r
        GROUP BY r.account_id, r.origin
    ) g
    GROUP BY g.account_id
    UNION ALL
    SELECT t.to_account, 'money_from_flagged_share',
           CAST(SUM(CASE WHEN f.account_id IS NOT NULL THEN t.amount_inr ELSE 0 END) / NULLIF(SUM(t.amount_inr), 0)
                AS DOUBLE)
    FROM PH_LEARN.WT t LEFT JOIN PH_LEARN.W_FLAGGED f ON f.account_id = t.from_account
    WHERE t.to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE) AND t.channel <> 'NACH'
    GROUP BY t.to_account
    UNION ALL
    SELECT o.account_id, 'pays_back_its_payers', CAST(COUNT(i.other) AS DOUBLE) / NULLIF(COUNT(o.other), 0)
    FROM (SELECT DISTINCT from_account AS account_id, to_account AS other FROM PH_LEARN.WT
          WHERE from_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE) AND NOT is_exit) o
    LEFT JOIN (SELECT DISTINCT to_account AS account_id, from_account AS other FROM PH_LEARN.WT
               WHERE to_account IN (SELECT account_id FROM PH_LEARN.W_SAMPLE)) i
           ON i.account_id = o.account_id AND i.other = o.other
    GROUP BY o.account_id
) v;