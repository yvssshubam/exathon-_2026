-- 050_attribute.sql — Step 2: how much of each victim's money is in each account the trace reached (FR-06, FR-07).
-- Runs inside the bank's schema, once per case, after 040_trace.sql, and again whenever a recalculation touches
-- the case (pipeline/recalc.py). pipeline/steps.py fills the parameters:
--   case_id, victim_tag, as_of, replay_from, own_bank_id, rule (the rule onward shares use)
--
-- What it does
--   1. Feeds ATTRIBUTE_CASE (Python UDF) every transaction in or out of the traced accounts between
--      replay_from (the last known balances) and as_of, plus those opening balances, once per rule.
--      Every reported payment into these accounts (ROOT_PAYMENT: this ticket's and any other ticket that had
--      reached the bank by as_of) is tagged with its victim, so mixed victims split (DECISIONS D-14).
--      A ticket that reports only part of a payment (an onward ticket carries the victim's share) tags only that
--      part; the rest of the payment arrives as ordinary money.
--   2. Writes the UDF's raw output to ATTRIBUTION_RAW (kept for inspection).
--   3. Builds ATTRIBUTION (per account and rule: this victim, other victims, own money, balance)
--      and ATTRIBUTION_FLOW (victim money carried by each transaction).
--   4. Writes REFERRAL_OUT: money that left this bank for another bank, with the victim's share (the onward entry).
--      The ticket's own payment (hop 1) is not one: the ticket already reached the bank that received it.
-- Transactions come from TXN_LIVE: a debit the bank refused because of a hold never happened.

DELETE FROM ATTRIBUTION_RAW WHERE case_id = {case_id};

INSERT INTO ATTRIBUTION_RAW (case_id, trace_rule, row_kind, ref_id, component, paise, session_id, stmt_id)
SELECT {case_id}, u.trace_rule, u.row_kind, u.ref_id, u.component, u.paise, CURRENT_SESSION, CURRENT_STATEMENT
FROM (
    SELECT ATTRIBUTE_CASE(i.trace_rule, i.row_kind, i.ts, i.txn_id, i.from_account, i.to_account,
                          i.amount_paise, i.victim_tag)
    FROM (
        SELECT r.trace_rule, x.row_kind, x.ts, x.txn_id, x.from_account, x.to_account, x.amount_paise, x.victim_tag
        FROM (SELECT 'LIBR' AS trace_rule FROM DUAL
              UNION ALL SELECT 'FIFO' FROM DUAL
              UNION ALL SELECT 'PRO_RATA' FROM DUAL) r
        CROSS JOIN (
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
            -- answers each from its automatic index instead of scanning every transaction (D-37); UNION drops a
            -- transaction that is on both sides. Part 1 of each row is the tagged (reported) part, part 2 the
            -- untagged rest of a payment a ticket reports only in part.
            SELECT 'TXN', t.ts, t.txn_id, t.from_account, t.to_account,
                   CAST(CASE WHEN p.part = 2 THEN t.amount_inr - c.tagged_inr
                             WHEN c.utr IS NULL THEN t.amount_inr
                             ELSE LEAST(t.amount_inr, c.tagged_inr) END * 100 AS DECIMAL(18,0)),
                   CASE WHEN p.part = 1 THEN c.victim_tag ELSE CAST(NULL AS VARCHAR(20)) END
            FROM (
                SELECT x.ts, x.txn_id, x.from_account, x.to_account, x.amount_inr, x.utr
                FROM TXN_LIVE x
                JOIN (SELECT DISTINCT to_account AS acc FROM TRACE_EDGE WHERE case_id = {case_id}) e
                  ON e.acc = x.from_account
                WHERE x.ts > TO_TIMESTAMP({replay_from}, 'YYYY-MM-DD HH24:MI:SS')
                  AND x.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
                UNION
                SELECT x.ts, x.txn_id, x.from_account, x.to_account, x.amount_inr, x.utr
                FROM TXN_LIVE x
                JOIN (SELECT DISTINCT to_account AS acc FROM TRACE_EDGE WHERE case_id = {case_id}) e
                  ON e.acc = x.to_account
                WHERE x.ts > TO_TIMESTAMP({replay_from}, 'YYYY-MM-DD HH24:MI:SS')
                  AND x.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            ) t
            LEFT JOIN ROOT_PAYMENT c
                   ON c.utr = t.utr
                  AND c.reported_at <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
            CROSS JOIN (SELECT 1 AS part FROM DUAL UNION ALL SELECT 2 FROM DUAL) p
            WHERE p.part = 1 OR (c.utr IS NOT NULL AND t.amount_inr > c.tagged_inr)
        ) x
    ) i
    GROUP BY i.trace_rule
) u;

DELETE FROM ATTRIBUTION WHERE case_id = {case_id};

INSERT INTO ATTRIBUTION (case_id, trace_rule, account_id, victim_tag, victim_amount_inr, other_victims_inr,
                         own_funds_inr, balance_now_inr, as_of, row_source, session_id, stmt_id)
SELECT {case_id},
       r.trace_rule,
       r.ref_id,
       {victim_tag},
       CAST(SUM(CASE WHEN r.component = {victim_tag} THEN r.paise ELSE 0 END) / 100 AS DECIMAL(18,2)),
       CAST(SUM(CASE WHEN r.component NOT IN ({victim_tag}, 'clean') THEN r.paise ELSE 0 END) / 100 AS DECIMAL(18,2)),
       CAST(SUM(CASE WHEN r.component = 'clean' THEN r.paise ELSE 0 END) / 100 AS DECIMAL(18,2)),
       CAST(SUM(r.paise) / 100 AS DECIMAL(18,2)),
       TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS'),
       'UDF',
       CURRENT_SESSION,
       CURRENT_STATEMENT
FROM ATTRIBUTION_RAW r
WHERE r.case_id = {case_id}
  AND r.row_kind IN ('HOLD', 'EXIT')
  AND r.ref_id IN (SELECT to_account FROM TRACE_EDGE WHERE case_id = {case_id})
GROUP BY r.trace_rule, r.ref_id;

DELETE FROM ATTRIBUTION_FLOW WHERE case_id = {case_id};

INSERT INTO ATTRIBUTION_FLOW (case_id, trace_rule, txn_id, victim_tag, victim_inr, stmt_id)
SELECT {case_id}, r.trace_rule, r.ref_id, r.component, CAST(SUM(r.paise) / 100 AS DECIMAL(18,2)), CURRENT_STATEMENT
FROM ATTRIBUTION_RAW r
WHERE r.case_id = {case_id}
  AND r.row_kind = 'MOVE'
GROUP BY r.trace_rule, r.ref_id, r.component;

DELETE FROM REFERRAL_OUT WHERE case_id = {case_id} AND from_bank_id = {own_bank_id};

INSERT INTO REFERRAL_OUT (case_id, from_bank_id, from_account, bank_id, account_id, txn_id, utr, amount_inr,
                          victim_amount_inr, trace_rule, ts, stmt_id)
SELECT {case_id}, {own_bank_id}, e.from_account, ta.bank_id, e.to_account, e.txn_id, t.utr, e.amount_inr,
       COALESCE(f.victim_inr, 0), {rule}, e.ts, CURRENT_STATEMENT
FROM TRACE_EDGE e
JOIN TXN t      ON t.txn_id = e.txn_id
JOIN ACCOUNT fa ON fa.account_id = e.from_account
JOIN ACCOUNT ta ON ta.account_id = e.to_account
LEFT JOIN ATTRIBUTION_FLOW f
       ON f.case_id = e.case_id AND f.txn_id = e.txn_id AND f.trace_rule = {rule} AND f.victim_tag = {victim_tag}
WHERE e.case_id = {case_id}
  AND e.hop > 1                 -- hop 1 is the ticket's own payment: the ticket already went to that bank
  AND fa.bank_id = {own_bank_id}
  AND ta.bank_id <> {own_bank_id};