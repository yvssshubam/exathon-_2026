-- 090_today.sql — today's process on the same ticket, for the Compare screen (FR-14).
-- Today nobody touches the ticket until the desk opens; then each account on the trail is reached in turn, and the
-- desk freezes whatever it finds (the over-freeze incentive: action under S.201 BNS for holding too little, nothing for
-- holding too much). pipeline/today.py runs this once per own-bank account, cut at the moment the desk reaches it.
-- Parameters: case_id, own_bank_id, probe_id, hop, action_at, replay_from, victim_tag, trail_amount, freeze_whole (1/0)
--
-- Same Python UDF as step 2 (ATTRIBUTE_CASE, LIBR only), over the accounts the trail reached, replayed up to
-- action_at over TXN (what happens if nobody acts): so later cash-outs (C at 04:30, A's transfer at 05:10 in the
-- demo) are in, and the money is gone.

DELETE FROM TODAY_RAW WHERE case_id = {case_id} AND probe_id = {probe_id};

INSERT INTO TODAY_RAW (case_id, probe_id, row_kind, ref_id, component, paise, stmt_id)
SELECT {case_id}, {probe_id}, u.row_kind, u.ref_id, u.component, u.paise, CURRENT_STATEMENT
FROM (
    SELECT ATTRIBUTE_CASE(i.trace_rule, i.row_kind, i.ts, i.txn_id, i.from_account, i.to_account,
                             i.amount_paise, i.victim_tag)
    FROM (
        SELECT 'LIBR' AS trace_rule, x.row_kind, x.ts, x.txn_id, x.from_account, x.to_account, x.amount_paise,
               x.victim_tag
        FROM (
            SELECT 'OPEN'                                    AS row_kind,
                   CAST(NULL AS TIMESTAMP)                   AS ts,
                   CAST(NULL AS VARCHAR(40))                 AS txn_id,
                   s.account_id                              AS from_account,
                   CAST(NULL AS VARCHAR(80))                 AS to_account,
                   CAST(s.balance_inr * 100 AS DECIMAL(18,0)) AS amount_paise,
                   CAST(NULL AS VARCHAR(20))                 AS victim_tag
            FROM (
                SELECT account_id, balance_inr,
                       ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY ts DESC) AS rn
                FROM BALANCE_SNAPSHOT
                WHERE ts <= TO_TIMESTAMP({replay_from}, 'YYYY-MM-DD HH24:MI:SS')
                  AND account_id IN (SELECT to_account FROM TRACE_EDGE WHERE case_id = {case_id})
            ) s
            WHERE s.rn = 1
            UNION ALL
            -- Transactions out of or into the accounts the trail reached. Two joins rather than one OR, so Exasol
            -- answers each from its automatic index instead of scanning every transaction (4.6 million with
            -- AMLworld, D-37); UNION drops a transaction that is on both sides.
            SELECT 'TXN', t.ts, t.txn_id, t.from_account, t.to_account,
                   CAST(t.amount_inr * 100 AS DECIMAL(18,0)), c.victim_tag
            FROM (
                SELECT x.ts, x.txn_id, x.from_account, x.to_account, x.amount_inr, x.utr
                FROM TXN x
                JOIN (SELECT DISTINCT to_account AS acc FROM TRACE_EDGE WHERE case_id = {case_id}) e
                  ON e.acc = x.from_account
                WHERE x.ts > TO_TIMESTAMP({replay_from}, 'YYYY-MM-DD HH24:MI:SS')
                  AND x.ts <= TO_TIMESTAMP({action_at}, 'YYYY-MM-DD HH24:MI:SS')
                UNION
                SELECT x.ts, x.txn_id, x.from_account, x.to_account, x.amount_inr, x.utr
                FROM TXN x
                JOIN (SELECT DISTINCT to_account AS acc FROM TRACE_EDGE WHERE case_id = {case_id}) e
                  ON e.acc = x.to_account
                WHERE x.ts > TO_TIMESTAMP({replay_from}, 'YYYY-MM-DD HH24:MI:SS')
                  AND x.ts <= TO_TIMESTAMP({action_at}, 'YYYY-MM-DD HH24:MI:SS')
            ) t
            LEFT JOIN ROOT_PAYMENT c ON c.utr = t.utr
                                    AND c.reported_at <= TO_TIMESTAMP({action_at}, 'YYYY-MM-DD HH24:MI:SS')
        ) x
    ) i
    GROUP BY i.trace_rule
) u;

DELETE FROM TODAY_OUTCOME
WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id} AND account_id = {probe_id};

INSERT INTO TODAY_OUTCOME (case_id, viewing_bank_id, account_id, hop, action_at, balance_then_inr,
                              victim_then_inr, today_action, locked_inr, honest_locked_inr, stmt_id)
SELECT {case_id},
       {own_bank_id},
       {probe_id},
       {hop!d},
       TO_TIMESTAMP({action_at}, 'YYYY-MM-DD HH24:MI:SS'),
       x.balance,
       x.victim,
       CASE WHEN x.balance <= 0 THEN 'NOTHING_LEFT'
            WHEN {freeze_whole!d} = 1 THEN 'WHOLE_FREEZE'
            ELSE 'LIEN' END,
       CASE WHEN x.balance <= 0 THEN 0
            WHEN {freeze_whole!d} = 1 THEN x.balance
            ELSE LEAST(x.balance, {trail_amount!d}) END,
       CASE WHEN x.balance <= 0 THEN 0
            WHEN {freeze_whole!d} = 1 THEN x.balance - LEAST(x.balance, x.tainted)
            ELSE LEAST(x.balance, {trail_amount!d}) - LEAST(x.balance, {trail_amount!d}, x.tainted) END,
       CURRENT_STATEMENT
FROM (
    SELECT CAST(COALESCE(SUM(r.paise), 0) / 100 AS DECIMAL(18,2)) AS balance,
           CAST(COALESCE(SUM(CASE WHEN r.component = {victim_tag} THEN r.paise ELSE 0 END), 0) / 100
                AS DECIMAL(18,2)) AS victim,
           CAST(COALESCE(SUM(CASE WHEN r.component <> 'clean' THEN r.paise ELSE 0 END), 0) / 100
                AS DECIMAL(18,2)) AS tainted
    FROM TODAY_RAW r
    WHERE r.case_id = {case_id} AND r.probe_id = {probe_id}
      AND r.row_kind = 'HOLD' AND r.ref_id = {probe_id}
) x;