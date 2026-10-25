-- 083_onward.sql — checker action file_onward_entry: file one onward entry per transfer from an own account to
-- another bank's account, carrying everything the next bank needs to act at once: UTR, amount, time, receiving
-- account and the victim's share (the portal turns each entry into that bank's ticket). Filed at once, never a draft:
-- a draft sitting until 9 a.m. means the next bank's ticket does not exist all night.
-- Run only by the checker for one validated proposal. Parameters: case_id, own_bank_id, placed_at, account_id (the
-- other bank's account), proposal_id. Same guards as 081.

INSERT INTO PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, bank_id, txn_id, utr,
                          channel, amount_inr, victim_amount_inr, moved_at, detail, entry_status, filed_at,
                          filed_by, proposal_id, stmt_id)
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
       'Money transferred to ' || COALESCE(b.bank_name, r.bank_id) || ', account ' || r.account_id
           || '; victim''s share ' || CAST(r.victim_amount_inr AS VARCHAR(30)) || ' of '
           || CAST(r.amount_inr AS VARCHAR(30)),
       'FILED',
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       {proposal_id!d},
       CURRENT_STATEMENT
FROM REFERRAL_OUT r
JOIN TXN t ON t.txn_id = r.txn_id
LEFT JOIN BANK b ON b.bank_id = r.bank_id
WHERE r.case_id = {case_id} AND r.from_bank_id = {own_bank_id} AND r.account_id = {account_id}
  AND (SELECT COUNT(*) FROM CHECK_RESULT k
       WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id}) > 0
  AND NOT EXISTS (SELECT 1 FROM CHECK_RESULT k
                  WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id} AND NOT k.check_passed)
  AND NOT EXISTS (SELECT 1 FROM PORTAL_ENTRY p
                  WHERE p.case_id = r.case_id AND p.viewing_bank_id = r.from_bank_id
                    AND p.entry_kind = 'ONWARD' AND p.txn_id = r.txn_id);

UPDATE CASE_ACTION AS a
SET action_status = 'DONE'
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id} AND a.account_id = {account_id}
  AND a.action_code = 'ONWARD_ENTRY' AND a.action_status = 'TO_DO'
  AND EXISTS (SELECT 1 FROM PORTAL_ENTRY p
              WHERE p.case_id = a.case_id AND p.viewing_bank_id = a.viewing_bank_id
                AND p.entry_kind = 'ONWARD' AND p.account_id = a.account_id);