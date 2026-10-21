-- 083_onward.sql — tool draft_onward_entry: file one onward entry per transfer from an own-bank account to another
-- bank, carrying the victim's share so the next bank can size its hold (CFCFRMS opens one new ticket per entry).
-- Filing is automatic: the bank must report onward transfers, and every minute counts for the next bank.
-- Parameters: case_id, own_bank_id, placed_at. Same guards as 081.

INSERT INTO PH.PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, bank_id, txn_id, utr,
                             channel, amount_inr, victim_amount_inr, moved_at, detail, entry_status, filed_at,
                             filed_by, stmt_id)
SELECT r.case_id,
       r.from_bank_id,
       'ONWARD',
       r.from_account,
       r.account_id,
       r.bank_id,
       r.txn_id,
       r.utr,
       t.channel,
       r.amount_inr,
       r.victim_amount_inr,
       r.ts,
       'Money transferred to ' || COALESCE(b.bank_name, r.bank_id) || ', account ' || r.account_id,
       'FILED',
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       CURRENT_STATEMENT
FROM PH.REFERRAL_OUT r
JOIN PH.TXN t ON t.txn_id = r.txn_id
LEFT JOIN PH.BANK b ON b.bank_id = r.bank_id
WHERE r.case_id = {case_id} AND r.from_bank_id = {own_bank_id}
  AND (SELECT COUNT(*) FROM PH.CHECK_RESULT k
       WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id}) > 0
  AND NOT EXISTS (SELECT 1 FROM PH.CHECK_RESULT k
                  WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id} AND NOT k.check_passed)
  AND NOT EXISTS (SELECT 1 FROM PH.PORTAL_ENTRY p
                  WHERE p.case_id = r.case_id AND p.viewing_bank_id = r.from_bank_id
                    AND p.entry_kind = 'ONWARD' AND p.txn_id = r.txn_id);

UPDATE PH.CASE_ACTION AS a
SET action_status = 'DONE'
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'ONWARD_ENTRY' AND a.action_status = 'TO_DO'
  AND EXISTS (SELECT 1 FROM PH.PORTAL_ENTRY p
              WHERE p.case_id = a.case_id AND p.viewing_bank_id = a.viewing_bank_id
                AND p.entry_kind = 'ONWARD' AND p.account_id = a.account_id);