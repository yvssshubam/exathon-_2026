-- 040_trace.sql — Step 1: follow the ticket's payment forward through this bank's accounts (FR-03, FR-05).
-- Runs inside the bank's schema, once per case. pipeline/steps.py fills the parameters:
--   case_id, as_of, max_hops (number), window_hours (number), own_bank_id
--
-- The root is the payment ticket intake matched (INTAKE_CHECK.matched_txn_id): an exact match on UTR, amount, time
-- and account, or a near match the officer accepted. No match, no trace, no hold.
--
-- Two parts, because Exasol's CONNECT BY accepts only equality links to PRIOR (DECISIONS D-21):
--   1a. One set-based join writes TRACE_LINK: which transaction can follow which. A child transaction must leave
--       an own account the parent paid into, not before the parent, within window_hours of it, and not after
--       as_of. Another bank's account ends the path: the money went on there, and that bank gets an onward ticket.
--   1b. CONNECT BY NOCYCLE walks TRACE_LINK from the root, at most max_hops deep, and writes TRACE_EDGE.
--
-- Notes
--   * Transactions come from TXN_LIVE: a debit the bank refused because of a hold never happened.
--   * NOCYCLE is the hang guard. Money coming back to an account already on the path arrives on a new
--     transaction, so CONNECT_BY_ISCYCLE alone would miss it; the path check catches it (DECISIONS D-08).
--   * Exits (EXIT:...) have no onward transactions, so their branch ends there.
--   * The trace over-reaches on purpose; how much victim money each edge really carried is step 2's job.
--   * One row per transaction: shortest hop, first path, loop if any path revisits an account (DECISIONS D-20).
--   * The root is found by joining on the transaction ID, which Exasol answers from its index (D-37).

DELETE FROM TRACE_LINK WHERE case_id = {case_id};

INSERT INTO TRACE_LINK (case_id, parent_txn, child_txn, from_account, to_account, amount_inr, ts, is_exit)
SELECT {case_id}, NULL, r.txn_id, r.from_account, r.to_account, r.amount_inr, r.ts, r.is_exit
FROM TXN_LIVE r
JOIN INTAKE_CHECK k ON k.matched_txn_id = r.txn_id AND k.case_id = {case_id}
WHERE r.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
UNION ALL
SELECT {case_id}, p.txn_id, c.txn_id, c.from_account, c.to_account, c.amount_inr, c.ts, c.is_exit
FROM TXN_LIVE p
JOIN ACCOUNT own
  ON  own.account_id = p.to_account
  AND own.bank_id = {own_bank_id}
JOIN TXN_LIVE c
  ON  c.from_account = p.to_account
  AND c.txn_id <> p.txn_id
  AND c.ts >= p.ts
  AND c.ts <= ADD_HOURS(p.ts, {window_hours!d})
WHERE p.ts >= (SELECT MIN(v.ts) FROM TXN_LIVE v JOIN INTAKE_CHECK kv ON kv.matched_txn_id = v.txn_id
               AND kv.case_id = {case_id})
  AND p.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS')
  -- c.ts >= p.ts already implies this; stating it lets Exasol cut both sides of the join to the case window
  -- before joining, instead of joining the whole table (D-37)
  AND c.ts >= (SELECT MIN(w.ts) FROM TXN_LIVE w JOIN INTAKE_CHECK kw ON kw.matched_txn_id = w.txn_id
               AND kw.case_id = {case_id})
  AND c.ts <= TO_TIMESTAMP({as_of}, 'YYYY-MM-DD HH24:MI:SS');

DELETE FROM TRACE_EDGE WHERE case_id = {case_id};

INSERT INTO TRACE_EDGE (case_id, hop, from_account, to_account, txn_id, amount_inr, ts,
                        is_cycle, is_exit, is_own_bank, trail_path, session_id, stmt_id)
SELECT {case_id},
       p.hop,
       p.from_account,
       p.to_account,
       p.txn_id,
       p.amount_inr,
       p.ts,
       p.is_cycle,
       p.is_exit,
       CASE WHEN a.bank_id = {own_bank_id} THEN TRUE ELSE FALSE END,
       p.trail_path,
       CURRENT_SESSION,
       CURRENT_STATEMENT
FROM (
    SELECT txn_id,
           MIN(from_account)                                          AS from_account,
           MIN(to_account)                                            AS to_account,
           MIN(amount_inr)                                            AS amount_inr,
           MIN(ts)                                                    AS ts,
           CASE WHEN MIN(is_exit_flag) = 1 THEN TRUE ELSE FALSE END    AS is_exit,
           CASE WHEN MAX(cycle_flag) = 1 THEN TRUE ELSE FALSE END      AS is_cycle,
           MIN(lvl)                                                   AS hop,
           MIN(path_text)                                             AS trail_path
    FROM (
        SELECT l.child_txn                                            AS txn_id,
               l.from_account,
               l.to_account,
               l.amount_inr,
               l.ts,
               CASE WHEN l.is_exit THEN 1 ELSE 0 END                 AS is_exit_flag,
               LEVEL                                                  AS lvl,
               CASE WHEN CONNECT_BY_ISCYCLE = 1
                      OR INSTR(SYS_CONNECT_BY_PATH(l.from_account, '>') || '>', '>' || l.to_account || '>') > 0
                    THEN 1 ELSE 0 END                                 AS cycle_flag,
               SYS_CONNECT_BY_PATH(l.from_account, '>') || '>' || l.to_account AS path_text
        FROM TRACE_LINK l
        START WITH l.case_id = {case_id}
               AND l.parent_txn IS NULL
        CONNECT BY NOCYCLE PRIOR l.child_txn = l.parent_txn
               AND PRIOR l.case_id = l.case_id
               AND LEVEL <= {max_hops!d}
    ) paths
    GROUP BY txn_id
) p
LEFT JOIN ACCOUNT a ON a.account_id = p.to_account;