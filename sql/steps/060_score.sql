-- 060_score.sql — Step 3: account scoring v2 (DECISIONS D-57 to D-59). Two questions per own account:
--   pass-through    does money run straight through the account?       P1 dwell, P2 passed on, P3 cashed out,
--                                                                       P4 balance kept, P5 few large payers
--   fraud exposure  does the account keep getting fraud money?         E1 direct complaints, E2 trail touches,
--                                                                       E3 money from flagged accounts, E5 pays back
-- and one check that is not part of either score: E4, fraud money leaves fast, which decides the freeze ladder.
-- The two scores place the account in one of four types, or the grey zone. The type names a pattern of money, never
-- a verdict on the holder. The score can stop a hold, never start one (the rules in 070 and the watch apply it).
--
-- Runs inside the bank's schema. pipeline/steps.py fills the parameters:
--   case_id, as_of, high_at, low_below (numbers: the cut-offs), fast_share, fast_gap (E4), medium_cases,
--   strong_cases, weak_share (the ladder), config_version (number)
-- Which accounts: SCORE_TARGET for this case_id (the trail's own accounts at this bank, or one account the watch or
-- the daily rescoring looks at: WATCH:<account>). Every signal is over the 90 days before as_of, from this bank's own
-- transactions (TXN_LIVE: refused payments never happened). A signal that cannot be measured counts as m = 0.
-- A flagged account is one under a police notice at this bank, or in the Suspect Registry, as of as_of
-- (FLAGGED_ACCOUNT). Weights: the live version in SCORING_WEIGHTS (version 0 = config/scoring.yaml start_weight).

-- 1. Per-credit matching (P2, P3, E4): each debit goes to the earliest credit of the 60 minutes before it that still
--    has room (MATCH_CREDITS), so one debit is never counted twice. Automatic payments (NACH, UPI AutoPay) are never
--    matched (D-54). A credit is fraud-linked when it came from a flagged account or a complaint names it.
DELETE FROM CREDIT_MATCH WHERE case_id = {case_id};

INSERT INTO CREDIT_MATCH (case_id, account_id, txn_id, amount_inr, fraud_linked, out_inr, cash_inr, stmt_id)
SELECT {case_id}, u.account_id, u.txn_id, u.amount_paise / 100, u.fraud_linked, u.out_paise / 100,
       u.cash_paise / 100, CURRENT_STATEMENT
FROM (
    SELECT MATCH_CREDITS(e.account_id, e.direction, e.ts, e.txn_id, e.amount_paise, e.exit_kind, e.fraud_linked)
    FROM (
        SELECT c.to_account                                      AS account_id,
               'C'                                               AS direction,
               c.ts                                              AS ts,
               c.txn_id                                          AS txn_id,
               CAST(c.amount_inr * 100 AS DECIMAL(18,0))         AS amount_paise,
               'NONE'                                            AS exit_kind,
               CASE WHEN fa.account_id IS NOT NULL OR rp.utr IS NOT NULL THEN TRUE ELSE FALSE END AS fraud_linked
        FROM TXN_LIVE c
        LEFT JOIN FLAGGED_ACCOUNT fa
               ON fa.account_id = c.from_account
              AND fa.flagged_since <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        LEFT JOIN ROOT_PAYMENT rp
               ON rp.utr = c.utr
              AND rp.reported_at <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE c.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
          AND c.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        UNION ALL
        SELECT d.from_account,
               'D',
               d.ts,
               d.txn_id,
               CAST(d.amount_inr * 100 AS DECIMAL(18,0)),
               CASE WHEN d.channel IN ('NACH', 'AUTOPAY')       THEN 'AUTO'
                    WHEN d.channel IN ('ATM', 'CASH', 'CRYPTO') THEN 'CASH'
                    ELSE 'MOVE' END,
               FALSE
        FROM TXN_LIVE d
        WHERE d.from_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
          AND d.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND d.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
    ) e
    GROUP BY e.account_id
) u;

-- 2. The nine signals, one row per account and signal (every pair, so a signal that cannot be measured still shows,
--    with m = 0), with the live weights, m, the contribution and a plain sentence.
DELETE FROM ACCOUNT_SIGNALS WHERE case_id = {case_id};

INSERT INTO ACCOUNT_SIGNALS (case_id, account_id, as_of, score_name, signal_name, signal_value, weight, m_value,
                             contribution, sentence, high_rank, low_rank, stmt_id)
SELECT {case_id},
       x.account_id,
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       x.score_name,
       x.signal_name,
       x.signal_value,
       x.weight,
       x.m_value,
       x.weight * x.m_value / x.total_weight,
       CASE WHEN x.signal_value IS NULL THEN NULL
            ELSE REPLACE(REPLACE(REPLACE(
                     CASE WHEN x.m_value >= 0.5 THEN x.high_sentence ELSE x.low_sentence END,
                     '{{v}}',   CAST(CAST(ROUND(x.signal_value) AS DECIMAL(18,0)) AS VARCHAR(30))),
                     '{{v1}}',  CAST(CAST(ROUND(x.signal_value, 1) AS DECIMAL(18,1)) AS VARCHAR(30))),
                     '{{pct}}', CAST(CAST(ROUND(x.signal_value * 100) AS DECIMAL(18,0)) AS VARCHAR(30)))
            END,
       ROW_NUMBER() OVER (PARTITION BY x.account_id, x.score_name ORDER BY x.weight * x.m_value DESC, x.signal_name),
       ROW_NUMBER() OVER (PARTITION BY x.account_id, x.score_name ORDER BY x.weight * (1 - x.m_value) DESC, x.signal_name),
       CURRENT_STATEMENT
FROM (
    SELECT a.account_id, c.score_name, c.signal_name, v.signal_value, w.weight, tw.total_weight,
           c.high_sentence, c.low_sentence,
           LEAST(1, GREATEST(0, COALESCE((v.signal_value - c.low_at) / (c.high_at - c.low_at), 0))) AS m_value
    FROM (SELECT DISTINCT account_id FROM SCORE_TARGET WHERE case_id = {case_id}) a
    CROSS JOIN SCORING_CONFIG c
    JOIN SCORING_WEIGHTS w ON w.is_live AND w.score_name = c.score_name AND w.signal_name = c.signal_name
    JOIN (SELECT score_name, SUM(weight) AS total_weight FROM SCORING_WEIGHTS WHERE is_live GROUP BY score_name) tw
      ON tw.score_name = c.score_name
    LEFT JOIN (
        -- P1. median minutes from a credit to the next debit (or to as_of if nothing has left yet); automatic
        --     payments (NACH, UPI AutoPay) are not money moving on (D-54)
        SELECT x1.account_id, 'median_dwell_minutes' AS signal_name, CAST(MEDIAN(x1.dwell) AS DOUBLE) AS signal_value
        FROM (
            SELECT c1.to_account AS account_id, c1.txn_id,
                   MINUTES_BETWEEN(COALESCE(MIN(d1.ts), TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')), c1.ts)
                       AS dwell
            FROM TXN_LIVE c1
            LEFT JOIN TXN_LIVE d1
                   ON d1.from_account = c1.to_account
                  AND d1.ts >= c1.ts
                  AND d1.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                  AND d1.channel NOT IN ('NACH', 'AUTOPAY')
            WHERE c1.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
              AND c1.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
              AND c1.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            GROUP BY c1.to_account, c1.txn_id, c1.ts
        ) x1
        GROUP BY x1.account_id

        UNION ALL
        -- P2. the part of each credit sent on within 60 minutes (each debit matched once), over all credits
        SELECT m2.account_id, 'passed_on_within_hour', CAST(SUM(m2.out_inr) / NULLIF(SUM(m2.amount_inr), 0) AS DOUBLE)
        FROM CREDIT_MATCH m2
        WHERE m2.case_id = {case_id}
        GROUP BY m2.account_id

        UNION ALL
        -- P3. the part of each credit taken out as ATM cash, cash or crypto within 60 minutes, over all credits
        SELECT m3.account_id, 'cashed_out_within_hour', CAST(SUM(m3.cash_inr) / NULLIF(SUM(m3.amount_inr), 0) AS DOUBLE)
        FROM CREDIT_MATCH m3
        WHERE m3.case_id = {case_id}
        GROUP BY m3.account_id

        UNION ALL
        -- P4. balance at the decision / the victim money that came in (LIBR); a watch rescoring has no ticket, so no value
        SELECT att.account_id, 'balance_kept_ratio', CAST(att.balance_now_inr / NULLIF(fin.tainted_in, 0) AS DOUBLE)
        FROM ATTRIBUTION att
        LEFT JOIN (
            SELECT t4.to_account, SUM(f4.victim_inr) AS tainted_in
            FROM ATTRIBUTION_FLOW f4
            JOIN TXN_LIVE t4 ON t4.txn_id = f4.txn_id
            WHERE f4.case_id = {case_id} AND f4.trace_rule = 'LIBR'
            GROUP BY t4.to_account
        ) fin ON fin.to_account = att.account_id
        WHERE att.case_id = {case_id} AND att.trace_rule = 'LIBR'
          AND att.account_id IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})

        UNION ALL
        -- P5. share of money received from the top three payers; NACH credits (an employer's salary) left out
        SELECT z5.account_id, 'top3_payer_share',
               CAST(SUM(CASE WHEN z5.rk <= 3 THEN z5.amt ELSE 0 END) / NULLIF(SUM(z5.amt), 0) AS DOUBLE)
        FROM (
            SELECT p5.account_id, p5.amt, ROW_NUMBER() OVER (PARTITION BY p5.account_id ORDER BY p5.amt DESC) AS rk
            FROM (
                SELECT t5.to_account AS account_id, t5.from_account, SUM(t5.amount_inr) AS amt
                FROM TXN_LIVE t5
                WHERE t5.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                  AND t5.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                  AND t5.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                  AND t5.channel <> 'NACH'
                GROUP BY t5.to_account, t5.from_account
            ) p5
        ) z5
        GROUP BY z5.account_id

        UNION ALL
        -- E1. distinct complaints (one per victim) whose victim paid straight into the account in the last 7 days.
        --     A complaint counts once it reached the bank
        SELECT t6.to_account, 'direct_complaints_7d', CAST(COUNT(DISTINCT rp6.victim_tag) AS DOUBLE)
        FROM TXN_LIVE t6
        JOIN ROOT_PAYMENT rp6
          ON rp6.utr = t6.utr
         AND rp6.reported_at <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE t6.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
          AND t6.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -7)
          AND t6.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY t6.to_account

        UNION ALL
        -- E2. distinct complaints whose traced trail passed through the account in the last 90 days: a complaint of the
        --     last 30 days counts 1, an older one 0.5 (the same count as the freeze ladder's fraud cases)
        SELECT tc.account_id, 'trail_touches_90d', CAST(SUM(tc.w) AS DOUBLE)
        FROM (
            SELECT e7.to_account AS account_id, c7.victim_tag,
                   CASE WHEN MAX(c7.reported_at) > ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -30)
                        THEN 1 ELSE 0.5 END AS w
            FROM TRACE_EDGE e7
            JOIN COMPLAINT c7 ON c7.case_id = e7.case_id
            WHERE e7.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
              AND c7.reported_at <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
              AND c7.reported_at >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
            GROUP BY e7.to_account, c7.victim_tag
        ) tc
        GROUP BY tc.account_id

        UNION ALL
        -- E3. money from flagged accounts / all money received, NACH credits left out (a share, so a big business with
        --     one stray fraud payment stays low)
        SELECT t8.to_account, 'money_from_flagged_share',
               CAST(SUM(CASE WHEN fa8.account_id IS NOT NULL THEN t8.amount_inr ELSE 0 END)
                    / NULLIF(SUM(t8.amount_inr), 0) AS DOUBLE)
        FROM TXN_LIVE t8
        LEFT JOIN FLAGGED_ACCOUNT fa8
               ON fa8.account_id = t8.from_account
              AND fa8.flagged_since <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
        WHERE t8.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
          AND t8.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
          AND t8.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
          AND t8.channel <> 'NACH'
        GROUP BY t8.to_account

        UNION ALL
        -- E5. share of the accounts it paid (not exits) that also paid it, last 90 days
        SELECT o9.account_id, 'pays_back_its_payers',
               CAST(COUNT(i9.other) AS DOUBLE) / NULLIF(COUNT(o9.other), 0)
        FROM (SELECT DISTINCT t9.from_account AS account_id, t9.to_account AS other
              FROM TXN_LIVE t9
              WHERE t9.from_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                AND NOT t9.is_exit
                AND t9.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                AND t9.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) o9
        LEFT JOIN (SELECT DISTINCT t10.to_account AS account_id, t10.from_account AS other
                   FROM TXN_LIVE t10
                   WHERE t10.to_account IN (SELECT account_id FROM SCORE_TARGET WHERE case_id = {case_id})
                     AND t10.ts >  ADD_DAYS(TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'), -90)
                     AND t10.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')) i9
               ON i9.account_id = o9.account_id AND i9.other = o9.other
        GROUP BY o9.account_id
    ) v ON v.account_id = a.account_id AND v.signal_name = c.signal_name
) x;

-- 3. The two scores, the account type, E4 and the freeze ladder.
DELETE FROM ACCOUNT_SCORE WHERE case_id = {case_id};

INSERT INTO ACCOUNT_SCORE (case_id, account_id, as_of, snapshot_id, weight_version, pass_through, fraud_exposure,
                           account_pattern, fraud_linked_credits, fraud_out_share, other_out_share,
                           fraud_money_leaves_fast, fraud_case_count, fraud_money_share, fraud_linked_inr, ladder_step,
                           account_type, never_account_hold, stmt_id)
SELECT {case_id},
       y.account_id,
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       NULL,
       (SELECT MAX(weight_version) FROM SCORING_WEIGHTS WHERE is_live),
       y.pt,
       y.fe,
       CASE WHEN y.pt >= {high_at!d}  AND y.fe >= {high_at!d}  THEN 'PIPE_FOR_FRAUD'
            WHEN y.pt >= {high_at!d}  AND y.fe <  {low_below!d} THEN 'FAST_MOVER'
            WHEN y.pt <  {low_below!d} AND y.fe >= {high_at!d}  THEN 'EXPOSED_BUSINESS'
            WHEN y.pt <  {low_below!d} AND y.fe <  {low_below!d} THEN 'ORDINARY_CUSTOMER'
            ELSE 'GREY_ZONE' END,
       y.fraud_credits,
       y.fraud_share,
       y.other_share,
       y.leaves_fast,
       y.cases,
       y.fraud_money_share,
       y.fraud_inr,
       CASE WHEN NOT y.leaves_fast                                       THEN 'NONE'
            WHEN COALESCE(y.fraud_money_share, 0) < {weak_share!d}       THEN 'WEAK'
            WHEN y.cases >= {strong_cases!d} AND NOT y.never_hold        THEN 'STRONG'
            WHEN y.cases >= {medium_cases!d}                             THEN 'MEDIUM'
            ELSE 'WEAK' END,
       y.account_type,
       y.never_hold,
       CURRENT_STATEMENT
FROM (
    SELECT sc.account_id, sc.pt, sc.fe, acc.account_type,
           CASE WHEN acc.account_type IN ('nodal', 'pool', 'escrow') THEN TRUE ELSE FALSE END AS never_hold,
           COALESCE(e4.fraud_credits, 0) AS fraud_credits,
           e4.fraud_share,
           e4.other_share,
           CASE WHEN e4.fraud_share >= {fast_share!d}
                 AND e4.fraud_share - COALESCE(e4.other_share, 0) >= {fast_gap!d} THEN TRUE ELSE FALSE END
               AS leaves_fast,
           COALESCE(sc.cases, 0) AS cases,
           e4.fraud_money_share,
           COALESCE(e4.fraud_inr, 0) AS fraud_inr
    FROM (
        SELECT account_id,
               COALESCE(SUM(CASE WHEN score_name = 'pass_through' THEN contribution END), 0)   AS pt,
               COALESCE(SUM(CASE WHEN score_name = 'fraud_exposure' THEN contribution END), 0) AS fe,
               MAX(CASE WHEN signal_name = 'trail_touches_90d' THEN signal_value END)            AS cases
        FROM ACCOUNT_SIGNALS
        WHERE case_id = {case_id}
        GROUP BY account_id
    ) sc
    JOIN ACCOUNT acc ON acc.account_id = sc.account_id
    LEFT JOIN (
        SELECT account_id,
               SUM(CASE WHEN fraud_linked THEN 1 ELSE 0 END) AS fraud_credits,
               CAST(SUM(CASE WHEN fraud_linked THEN out_inr END)
                    / NULLIF(SUM(CASE WHEN fraud_linked THEN amount_inr END), 0) AS DOUBLE) AS fraud_share,
               CAST(SUM(CASE WHEN NOT fraud_linked THEN out_inr END)
                    / NULLIF(SUM(CASE WHEN NOT fraud_linked THEN amount_inr END), 0) AS DOUBLE) AS other_share,
               CAST(SUM(CASE WHEN fraud_linked THEN amount_inr ELSE 0 END)
                    / NULLIF(SUM(amount_inr), 0) AS DOUBLE) AS fraud_money_share,
               SUM(CASE WHEN fraud_linked THEN amount_inr ELSE 0 END) AS fraud_inr
        FROM CREDIT_MATCH
        WHERE case_id = {case_id}
        GROUP BY account_id
    ) e4 ON e4.account_id = sc.account_id
) y;

-- 4. The snapshot every decision cites: append-only, so an old hold is always explained with the numbers of its day.
INSERT INTO SCORE_SNAPSHOT (case_id, account_id, computed_at, weight_version, pass_through, fraud_exposure,
                            account_pattern, fraud_money_leaves_fast, fraud_case_count, ladder_step, signals_text,
                            config_version, stmt_id)
SELECT s.case_id, s.account_id, s.as_of, s.weight_version, s.pass_through, s.fraud_exposure, s.account_pattern,
       s.fraud_money_leaves_fast, s.fraud_case_count, s.ladder_step, g.txt, {config_version!d}, CURRENT_STATEMENT
FROM ACCOUNT_SCORE s
LEFT JOIN (
    SELECT account_id,
           GROUP_CONCAT(signal_name || '=' ||
                        COALESCE(CAST(CAST(ROUND(signal_value, 4) AS DECIMAL(30,4)) AS VARCHAR(40)), 'none') ||
                        '/' || CAST(CAST(ROUND(m_value, 3) AS DECIMAL(10,3)) AS VARCHAR(20))
                        ORDER BY signal_name SEPARATOR '; ') AS txt
    FROM ACCOUNT_SIGNALS
    WHERE case_id = {case_id}
    GROUP BY account_id
) g ON g.account_id = s.account_id
WHERE s.case_id = {case_id};

MERGE INTO ACCOUNT_SCORE s
USING (
    SELECT account_id, MAX(snapshot_id) AS sid
    FROM SCORE_SNAPSHOT
    WHERE case_id = {case_id}
    GROUP BY account_id
) n
ON (s.case_id = {case_id} AND s.account_id = n.account_id)
WHEN MATCHED THEN UPDATE SET snapshot_id = n.sid;