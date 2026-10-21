-- 082_exits.sql — tool record_exit: file every exit from an own-bank account with its identifiers (ATM ID, wallet,
-- device) and the victim money that left through it (SOP 9.1(i)). Parameters: case_id, own_bank_id, placed_at.
-- Same guards as 081: checks passed, nothing filed twice.

INSERT INTO PH.PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, bank_id, txn_id, utr,
                             channel, amount_inr, victim_amount_inr, moved_at, detail, entry_status, filed_at,
                             filed_by, stmt_id)
SELECT e.case_id,
       {own_bank_id},
       'EXIT',
       e.from_account,
       e.to_account,
       NULL,
       e.txn_id,
       t.utr,
       t.channel,
       e.amount_inr,
       COALESCE(f.victim_inr, 0),
       e.ts,
       t.exit_identifier,
       'FILED',
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       CURRENT_STATEMENT
FROM PH.TRACE_EDGE e
JOIN PH.TXN t      ON t.txn_id = e.txn_id
JOIN PH.ACCOUNT fa ON fa.account_id = e.from_account
JOIN PH.COMPLAINT c ON c.case_id = e.case_id
LEFT JOIN PH.ATTRIBUTION_FLOW f
       ON f.case_id = e.case_id AND f.txn_id = e.txn_id AND f.trace_rule = 'LIBR' AND f.victim_tag = c.victim_tag
WHERE e.case_id = {case_id}
  AND e.is_exit
  AND fa.bank_id = {own_bank_id}
  AND (SELECT COUNT(*) FROM PH.CHECK_RESULT k
       WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id}) > 0
  AND NOT EXISTS (SELECT 1 FROM PH.CHECK_RESULT k
                  WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id} AND NOT k.check_passed)
  AND NOT EXISTS (SELECT 1 FROM PH.PORTAL_ENTRY p
                  WHERE p.case_id = e.case_id AND p.viewing_bank_id = {own_bank_id}
                    AND p.entry_kind = 'EXIT' AND p.txn_id = e.txn_id);

UPDATE PH.CASE_ACTION AS a
SET action_status = 'DONE'
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'RECORD_EXIT' AND a.action_status = 'TO_DO'
  AND EXISTS (SELECT 1 FROM PH.PORTAL_ENTRY p
              WHERE p.case_id = a.case_id AND p.viewing_bank_id = a.viewing_bank_id
                AND p.entry_kind = 'EXIT' AND p.account_id = a.account_id);