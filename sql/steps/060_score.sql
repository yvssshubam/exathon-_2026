-- 060_score.sql — Step 3: does each account the money reached look like a mule or an honest receiver? (FR-08)
-- Runs inside Exasol, once per case, after 050_attribute.sql. pipeline/steps.py fills the parameters:
--   case_id, as_of, mule_band (number), review_band (number)
--
-- Eight signals per account, each over the 90 days before as_of (definitions: DECISIONS D-22). Weights and
-- thresholds come from PH.SCORING_CONFIG (loaded from config/scoring.yaml). Each signal becomes a mule-likeness
-- m in [0, 1]; the score is the weighted sum; every signal keeps its value, contribution and a plain sentence,
-- ranked as a reason both ways, so the console can show the top three behind whichever band the account gets.

DELETE FROM PH.ACCOUNT_SIGNALS WHERE case_id = {case_id};

INSERT INTO PH.ACCOUNT_SIGNALS (case_id, account_id, as_of, signal_name, signal_value, weight, mule_likeness,
                                contribution, sentence, mule_rank, honest_rank, stmt_id)
SELECT {case_id},
       m.account_id,
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       m.signal_name,
       m.signal_value,
       m.weight,
       m.ml,
       m.weight * m.ml / m.total_weight,
       COALESCE(
         REPLACE(REPLACE(REPLACE(REPLACE(
           CASE WHEN m.ml >= 0.5 THEN m.mule_sentence ELSE m.honest_sentence END,
           '{{v}}',     CAST(CAST(ROUND(m.signal_value) AS DECIMAL(18,0)) AS VARCHAR(30))),
           '{{v1}}',    CAST(CAST(ROUND(m.signal_value, 1) AS DECIMAL(18,1)) AS VARCHAR(30))),
           '{{pct}}',   CAST(CAST(ROUND(m.signal_value * 100) AS DECIMAL(18,0)) AS VARCHAR(30))),
           '{{years}}', CAST(CAST(ROUND(m.signal_value / 365, 1) AS DECIMAL(18,1)) AS VARCHAR(30))),
         CASE WHEN m.ml >= 0.5 THEN m.mule_sentence ELSE m.honest_sentence END),
       ROW_NUMBER() OVER (PARTITION BY m.account_id ORDER BY m.weight * m.ml DESC, m.signal_name),
       ROW_NUMBER() OVER (PARTITION BY m.account_id ORDER BY m.weight * (1 - m.ml) DESC, m.signal_name),
       CURRENT_STATEMENT
FROM (
    SELECT v.account_id, v.signal_name, v.signal_value, c.weight, c.mule_sentence, c.honest_sentence,
           tw.total_weight,
           LEAST(1, GREATEST(0, COALESCE((v.signal_value - c.honest_at) / (c.mule_at - c.honest_at), 0))) AS ml
    FROM (
        -- 1. account age in days
        SELECT a.account_id, 'account_age_days' AS signal_name,
               CAST(DAYS_BETWEEN(CAST(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS') AS DATE), a.opened_on)
                    AS DOUBLE) AS signal_value
        FROM PH.ACCOUNT a
        WHERE a.account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)

        UNION ALL
        -- 2. median minutes from a credit to the next debit (or to as_of if nothing has left yet)
        SELECT x.account_id, 'median_dwell_minutes', CAST(MEDIAN(x.dwell) AS DOUBLE)
        FROM (
            SELECT c.to_account AS account_id, c.txn_id,
                   MINUTES_BETWEEN(COALESCE(MIN(d.ts), TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')), c.ts) AS dwell
            FROM PH.TXN c
            LEFT JOIN PH.TXN d
                   ON d.from_account = c.to_account
                  AND d.ts >= c.ts
                  AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            WHERE c.to_account IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)
              AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY c.to_account, c.txn_id, c.ts
        ) x
        GROUP BY x.account_id

        UNION ALL
        -- 3. share of each credit sent on within 60 minutes, over all credits
        SELECT y.account_id, 'pass_through_ratio', CAST(SUM(y.passed) / NULLIF(SUM(y.amount), 0) AS DOUBLE)
        FROM (
            SELECT c.to_account AS account_id, c.txn_id, c.amount_inr AS amount,
                   LEAST(c.amount_inr, COALESCE(SUM(d.amount_inr), 0)) AS passed
            FROM PH.TXN c
            LEFT JOIN PH.TXN d
                   ON d.from_account = c.to_account
                  AND d.ts >= c.ts
                  AND d.ts <= ADD_MINUTES(c.ts, 60)
                  AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            WHERE c.to_account IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)
              AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY c.to_account, c.txn_id, c.amount_inr
        ) y
        GROUP BY y.account_id

        UNION ALL
        -- 4. distinct fraud complaints whose victim paid straight into this account in the last 7 days
        SELECT a.account_id, 'complaint_linked_credits_7d', CAST(COUNT(DISTINCT cp.case_id) AS DOUBLE)
        FROM (SELECT DISTINCT to_account AS account_id FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit) a
        LEFT JOIN PH.TXN t
               ON t.to_account = a.account_id
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -7)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        LEFT JOIN PH.COMPLAINT cp ON cp.utr = t.utr
        GROUP BY a.account_id

        UNION ALL
        -- 5. share of credit value from the top three payers
        SELECT z.account_id, 'large_payer_share',
               CAST(SUM(CASE WHEN z.rk <= 3 THEN z.amt ELSE 0 END) / NULLIF(SUM(z.amt), 0) AS DOUBLE)
        FROM (
            SELECT p.account_id, p.amt, ROW_NUMBER() OVER (PARTITION BY p.account_id ORDER BY p.amt DESC) AS rk
            FROM (
                SELECT t.to_account AS account_id, t.from_account, SUM(t.amount_inr) AS amt
                FROM PH.TXN t
                WHERE t.to_account IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)
                  AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                  AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                GROUP BY t.to_account, t.from_account
            ) p
        ) z
        GROUP BY z.account_id

        UNION ALL
        -- 6. balance at the hold time / victim money that came in (LIBR)
        SELECT att.account_id, 'balance_retained_ratio',
               CAST(att.balance_now_inr / NULLIF(fin.tainted_in, 0) AS DOUBLE)
        FROM PH.ATTRIBUTION att
        LEFT JOIN (
            SELECT t.to_account, SUM(f.victim_inr) AS tainted_in
            FROM PH.ATTRIBUTION_FLOW f
            JOIN PH.TXN t ON t.txn_id = f.txn_id
            WHERE f.case_id = {case_id} AND f.trace_rule = 'LIBR'
            GROUP BY t.to_account
        ) fin ON fin.to_account = att.account_id
        WHERE att.case_id = {case_id} AND att.trace_rule = 'LIBR'
          AND att.account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)

        UNION ALL
        -- 7. cash or crypto withdrawals
        SELECT a.account_id, 'cash_or_crypto_exits', CAST(COUNT(t.txn_id) AS DOUBLE)
        FROM (SELECT DISTINCT to_account AS account_id FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit) a
        LEFT JOIN PH.TXN t
               ON t.from_account = a.account_id
              AND t.is_exit
              AND t.channel IN ('ATM', 'CASH', 'CRYPTO')
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY a.account_id

        UNION ALL
        -- 8. credits in the last 30 days / what the declared profile band expects in a month
        SELECT a.account_id, 'credit_vs_profile',
               CAST(COALESCE(SUM(t.amount_inr), 0) / NULLIF(MAX(pb.expected_monthly_inr), 0) AS DOUBLE)
        FROM PH.ACCOUNT a
        JOIN PH.PROFILE_BAND pb ON pb.profile_band = a.profile_band
        LEFT JOIN PH.TXN t
               ON t.to_account = a.account_id
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -30)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE a.account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {case_id} AND NOT is_exit)
        GROUP BY a.account_id
    ) v
    JOIN PH.SCORING_CONFIG c ON c.signal_name = v.signal_name
    CROSS JOIN (SELECT SUM(weight) AS total_weight FROM PH.SCORING_CONFIG) tw
) m;

DELETE FROM PH.ACCOUNT_SCORE WHERE case_id = {case_id};

INSERT INTO PH.ACCOUNT_SCORE (case_id, account_id, as_of, score, band, account_type, never_account_hold, stmt_id)
SELECT {case_id},
       s.account_id,
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       SUM(s.contribution),
       CASE WHEN SUM(s.contribution) >= {mule_band!d}   THEN 'MULE'
            WHEN SUM(s.contribution) >= {review_band!d} THEN 'REVIEW'
            ELSE 'HONEST' END,
       MAX(a.account_type),
       CASE WHEN MAX(a.account_type) IN ('nodal', 'pool', 'escrow') THEN TRUE ELSE FALSE END,
       CURRENT_STATEMENT
FROM PH.ACCOUNT_SIGNALS s
JOIN PH.ACCOUNT a ON a.account_id = s.account_id
WHERE s.case_id = {case_id}
GROUP BY s.account_id;