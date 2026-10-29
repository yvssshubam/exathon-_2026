-- 060_score.sql — Step 3: the account evidence. Does each own account look like a mule or an honest receiver?
-- Runs inside the bank's schema. pipeline/steps.py fills the parameters:
--   case_id, as_of, mule_band (number), review_band (number)
-- Which accounts: SCORE_TARGET for this case_id. For a ticket that is the trail's own accounts at this bank (another
-- bank's accounts are not scored: their history is that bank's); the watch also scores one account a refused
-- transfer named (case_id WATCH:<account>).
--
-- Eight signals per account, each over the 90 days before as_of (definitions: DECISIONS D-22), and a ninth learned
-- one when a model is loaded (D-51). Weights and thresholds come from SCORING_CONFIG (loaded from
-- config/scoring.yaml). Each signal becomes a mule-likeness m in [0, 1]; the score is the weighted sum; every signal
-- keeps its value, contribution and a plain sentence, ranked as a reason both ways, so the reasons can cite the top
-- signals behind whichever way the account looks.
-- The score is an input to the rules (narrow or propose a freeze), never a verdict shown on its own.
-- Transactions come from TXN_LIVE; reported payments count only once their ticket reached the bank.

DELETE FROM ACCOUNT_SIGNALS WHERE case_id = {case_id};

INSERT INTO ACCOUNT_SIGNALS (case_id, account_id, as_of, signal_name, signal_value, weight, mule_likeness,
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
        FROM ACCOUNT a
        WHERE a.account_id IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})

        UNION ALL
        -- 2. median minutes from a credit to the next debit (or to as_of if nothing has left yet),
        --    automatic payments (NACH, UPI AutoPay) are not counted as money moving on (D-54)
        SELECT x.account_id, 'median_dwell_minutes', CAST(MEDIAN(x.dwell) AS DOUBLE)
        FROM (
            SELECT c.to_account AS account_id, c.txn_id,
                   MINUTES_BETWEEN(COALESCE(MIN(d.ts), TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')), c.ts) AS dwell
            FROM TXN_LIVE c
            LEFT JOIN TXN_LIVE d
                   ON d.from_account = c.to_account
                  AND d.ts >= c.ts
                  AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.channel NOT IN ('NACH', 'AUTOPAY')  -- automatic payments to registered companies (D-54)
            WHERE c.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
              AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY c.to_account, c.txn_id, c.ts
        ) x
        GROUP BY x.account_id

        UNION ALL
        -- 3. share of each credit sent on within 60 minutes, over all credits (automatic payments left out, D-54)
        SELECT y.account_id, 'pass_through_ratio', CAST(SUM(y.passed) / NULLIF(SUM(y.amount), 0) AS DOUBLE)
        FROM (
            SELECT c.to_account AS account_id, c.txn_id, c.amount_inr AS amount,
                   LEAST(c.amount_inr, COALESCE(SUM(d.amount_inr), 0)) AS passed
            FROM TXN_LIVE c
            LEFT JOIN TXN_LIVE d
                   ON d.from_account = c.to_account
                  AND d.ts >= c.ts
                  AND d.ts <= ADD_MINUTES(c.ts, 60)
                  AND d.channel NOT IN ('NACH', 'AUTOPAY')  -- automatic payments to registered companies (D-54)
                  AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            WHERE c.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
              AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY c.to_account, c.txn_id, c.amount_inr
        ) y
        GROUP BY y.account_id

        UNION ALL
        -- 4. distinct reported payments (tickets that had reached the bank) into this account in 7 days
        SELECT a.account_id, 'complaint_linked_credits_7d', CAST(COUNT(DISTINCT cp.utr) AS DOUBLE)
        FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a
        LEFT JOIN TXN_LIVE t
               ON t.to_account = a.account_id
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -7)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        LEFT JOIN ROOT_PAYMENT cp ON cp.utr = t.utr
                                 AND cp.reported_at <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY a.account_id

        UNION ALL
        -- 5. share of credit value from the top three payers
        SELECT z.account_id, 'large_payer_share',
               CAST(SUM(CASE WHEN z.rk <= 3 THEN z.amt ELSE 0 END) / NULLIF(SUM(z.amt), 0) AS DOUBLE)
        FROM (
            SELECT p.account_id, p.amt, ROW_NUMBER() OVER (PARTITION BY p.account_id ORDER BY p.amt DESC) AS rk
            FROM (
                SELECT t.to_account AS account_id, t.from_account, SUM(t.amount_inr) AS amt
                FROM TXN_LIVE t
                WHERE t.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
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
        FROM ATTRIBUTION att
        LEFT JOIN (
            SELECT t.to_account, SUM(f.victim_inr) AS tainted_in
            FROM ATTRIBUTION_FLOW f
            JOIN TXN_LIVE t ON t.txn_id = f.txn_id
            WHERE f.case_id = {case_id} AND f.trace_rule = 'LIBR'
            GROUP BY t.to_account
        ) fin ON fin.to_account = att.account_id
        WHERE att.case_id = {case_id} AND att.trace_rule = 'LIBR'
          AND att.account_id IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})

        UNION ALL
        -- 7. cash or crypto withdrawals
        SELECT a.account_id, 'cash_or_crypto_exits', CAST(COUNT(t.txn_id) AS DOUBLE)
        FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a
        LEFT JOIN TXN_LIVE t
               ON t.from_account = a.account_id
              AND t.is_exit
              AND t.channel IN ('ATM', 'CASH', 'CRYPTO')
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY a.account_id

        UNION ALL
        -- 8. credits in the last 30 days / what the declared profile band expects in a month, leaving out money paid
        --    in through NACH (salary, pension, dividends, refunds from registered companies, D-55)
        SELECT a.account_id, 'credit_vs_profile',
               CAST(COALESCE(SUM(t.amount_inr), 0) / NULLIF(MAX(pb.expected_monthly_inr), 0) AS DOUBLE)
        FROM ACCOUNT a
        JOIN PROFILE_BAND pb ON pb.profile_band = a.profile_band
        LEFT JOIN TXN_LIVE t
               ON t.to_account = a.account_id
              AND t.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -30)
              AND t.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
              AND t.channel <> 'NACH'  -- money paid in by registered companies through NACH (D-55)
        WHERE a.account_id IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
        GROUP BY a.account_id
    ) v
    JOIN SCORING_CONFIG c ON c.signal_name = v.signal_name
    CROSS JOIN (SELECT SUM(weight) AS total_weight FROM SCORING_CONFIG) tw
) m;

-- 9. The learned signal (DECISIONS D-51), only when a model is loaded: LEARNED_COEF holds the inputs' scaling (and
-- the logistic regression's coefficients), LEARNED_TREE the boosted trees when those are deployed, and SCORING_CONFIG
-- has a learned_pattern row. Its inputs are the signal values above and five connection inputs that only the model
-- uses (distinct payers and payees, money out / in, number of payments, share of payees that also paid it). Each
-- input is taken as log(1 + x) where marked, standardised and clipped to +-5 (a missing value counts as average),
-- and kept in LEARNED_INPUT so any rating can be checked. rating = 1 / (1 + e^-(intercept + sum(coef * z) +
-- sum over trees of rate * leaf)): the logistic regression has no trees, the trees have no coefficients. One more
-- signal, with the smallest weight; it never replaces the eight, and the inputs never score on their own.
-- No model: nothing is added and the score is the eight signals' alone.
DELETE FROM LEARNED_INPUT WHERE case_id = {case_id};

INSERT INTO LEARNED_INPUT (case_id, account_id, feature_name, raw_value, z_value, stmt_id)
SELECT {case_id},
       x.account_id,
       x.feature_name,
       x.v,
       CASE WHEN x.v IS NULL THEN 0
            ELSE LEAST(5, GREATEST(-5,
                 ((CASE WHEN f.use_log THEN LN(1 + GREATEST(x.v, 0)) ELSE x.v END) - f.mean_value) / f.std_value))
            END,
       CURRENT_STATEMENT
FROM (
    SELECT t.account_id, f.feature_name,
           CASE f.feature_name WHEN 'distinct_payers'  THEN cx.distinct_payers
                               WHEN 'distinct_payees'  THEN cx.distinct_payees
                               WHEN 'out_in_ratio'     THEN cx.out_in_ratio
                               WHEN 'payment_count'    THEN cx.payment_count
                               WHEN 'reciprocal_share' THEN cx.reciprocal_share
                               ELSE s.signal_value END AS v
    FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) t
    CROSS JOIN LEARNED_COEF f
    LEFT JOIN ACCOUNT_SIGNALS s
           ON s.case_id = {case_id} AND s.account_id = t.account_id AND s.signal_name = f.feature_name
    LEFT JOIN (
        -- the model's connection inputs, as sql/learn/010_features.sql computes them for training
        SELECT a.account_id,
               CAST(COALESCE(ci.payers, 0) AS DOUBLE) AS distinct_payers,
               CAST(COALESCE(co.payees, 0) AS DOUBLE) AS distinct_payees,
               CAST(COALESCE(co.amt, 0) / NULLIF(ci.amt, 0) AS DOUBLE) AS out_in_ratio,
               CAST(COALESCE(ci.n, 0) + COALESCE(co.n, 0) AS DOUBLE) AS payment_count,
               CAST(COALESCE(rc.n, 0) AS DOUBLE) / NULLIF(co.payees, 0) AS reciprocal_share
        FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a
        LEFT JOIN (SELECT to_account AS account_id, COUNT(DISTINCT from_account) AS payers,
                          SUM(amount_inr) AS amt, COUNT(*) AS n
                   FROM TXN_LIVE
                   WHERE to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                     AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                     AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                   GROUP BY to_account) ci ON ci.account_id = a.account_id
        LEFT JOIN (SELECT from_account AS account_id, COUNT(DISTINCT to_account) AS payees,
                          SUM(amount_inr) AS amt, COUNT(*) AS n
                   FROM TXN_LIVE
                   WHERE from_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                     AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                     AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                   GROUP BY from_account) co ON co.account_id = a.account_id
        LEFT JOIN (SELECT o.account_id, COUNT(DISTINCT o.other) AS n
                   FROM (SELECT DISTINCT from_account AS account_id, to_account AS other FROM TXN_LIVE
                         WHERE from_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                           AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                           AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) o
                   JOIN (SELECT DISTINCT to_account AS account_id, from_account AS other FROM TXN_LIVE
                         WHERE to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                           AND ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                           AND ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) i
                     ON i.account_id = o.account_id AND i.other = o.other
                   GROUP BY o.account_id) rc ON rc.account_id = a.account_id
    ) cx ON cx.account_id = t.account_id
) x
JOIN LEARNED_COEF f ON f.feature_name = x.feature_name
WHERE f.feature_name <> '(intercept)';

INSERT INTO ACCOUNT_SIGNALS (case_id, account_id, as_of, signal_name, signal_value, weight, mule_likeness,
                                contribution, sentence, mule_rank, honest_rank, stmt_id)
SELECT {case_id},
       r.account_id,
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       c.signal_name,
       r.rating,
       c.weight,
       LEAST(1, GREATEST(0, (r.rating - c.honest_at) / (c.mule_at - c.honest_at))),
       c.weight * LEAST(1, GREATEST(0, (r.rating - c.honest_at) / (c.mule_at - c.honest_at))) / tw.total_weight,
       REPLACE(CASE WHEN (r.rating - c.honest_at) / (c.mule_at - c.honest_at) >= 0.5 THEN c.mule_sentence
                    ELSE c.honest_sentence END,
               '{{pct}}', CAST(CAST(ROUND(r.rating * 100) AS DECIMAL(18,0)) AS VARCHAR(30))),
       NULL,
       NULL,
       CURRENT_STATEMENT
FROM (
    SELECT a.account_id,
           1 / (1 + EXP(-LEAST(30, GREATEST(-30, i.coef + COALESCE(lin.total, 0) + COALESCE(tr.total, 0)))))
               AS rating
    FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a
    CROSS JOIN (SELECT coef FROM LEARNED_COEF WHERE feature_name = '(intercept)') i
    LEFT JOIN (
        SELECT n.account_id, SUM(f.coef * n.z_value) AS total
        FROM LEARNED_INPUT n
        JOIN LEARNED_COEF f ON f.feature_name = n.feature_name
        WHERE n.case_id = {case_id}
        GROUP BY n.account_id
    ) lin ON lin.account_id = a.account_id
    LEFT JOIN (
        -- each tree: one split at the root, one under each side, a leaf at the end (the same as learned.tree_leaf)
        SELECT a2.account_id,
               SUM(t.rate * CASE WHEN COALESCE(zr.z_value, 0) <= t.root_t
                                 THEN CASE WHEN COALESCE(zl.z_value, 0) <= t.left_t THEN t.leaf_ll ELSE t.leaf_lr END
                                 ELSE CASE WHEN COALESCE(zz.z_value, 0) <= t.right_t THEN t.leaf_rl ELSE t.leaf_rr END
                            END) AS total
        FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a2
        CROSS JOIN LEARNED_TREE t
        LEFT JOIN LEARNED_INPUT zr
               ON zr.case_id = {case_id} AND zr.account_id = a2.account_id AND zr.feature_name = t.root_f
        LEFT JOIN LEARNED_INPUT zl
               ON zl.case_id = {case_id} AND zl.account_id = a2.account_id AND zl.feature_name = t.left_f
        LEFT JOIN LEARNED_INPUT zz
               ON zz.case_id = {case_id} AND zz.account_id = a2.account_id AND zz.feature_name = t.right_f
        GROUP BY a2.account_id
    ) tr ON tr.account_id = a.account_id
) r
JOIN SCORING_CONFIG c ON c.signal_name = 'learned_pattern'
CROSS JOIN (SELECT SUM(weight) AS total_weight FROM SCORING_CONFIG) tw;

-- Rank every signal again, the learned one included, so the reasons can cite whichever signals weigh most.
MERGE INTO ACCOUNT_SIGNALS s
USING (
    SELECT account_id, signal_name,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY weight * mule_likeness DESC, signal_name) AS mr,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY weight * (1 - mule_likeness) DESC, signal_name) AS hr
    FROM ACCOUNT_SIGNALS
    WHERE case_id = {case_id}
) r
ON (s.case_id = {case_id} AND s.account_id = r.account_id AND s.signal_name = r.signal_name)
WHEN MATCHED THEN UPDATE SET mule_rank = r.mr, honest_rank = r.hr;

DELETE FROM ACCOUNT_SCORE WHERE case_id = {case_id};

INSERT INTO ACCOUNT_SCORE (case_id, account_id, as_of, score, band, account_type, never_account_hold, stmt_id)
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
FROM ACCOUNT_SIGNALS s
JOIN ACCOUNT a ON a.account_id = s.account_id
WHERE s.case_id = {case_id}
GROUP BY s.account_id;