-- 081_hold.sql — tool place_mandatory_hold: write the automatic liens and first-layer markers to the simulated core
-- banking hold ledger. Parameters: case_id, own_bank_id, placed_at.
-- Only rows the rules marked AUTOMATIC can get here, and only when every check in PH.CHECK_RESULT passed and the
-- check was run for this case (no check rows = no holds). Re-running never doubles a hold.

INSERT INTO PH.HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, placed_at, placed_by,
                            hold_status, action_code, reason_text, stmt_id)
SELECT a.case_id,
       a.viewing_bank_id,
       a.account_id,
       CASE WHEN a.action_code = 'HOLD_MARKER' THEN 'MARKER' ELSE 'LIEN' END,
       a.amount_inr,
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       'ACTIVE',
       a.action_code,
       a.reason_code,
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.act_mode = 'AUTOMATIC'
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND a.action_status = 'TO_DO'
  AND (SELECT COUNT(*) FROM PH.CHECK_RESULT k
       WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id}) > 0
  AND NOT EXISTS (SELECT 1 FROM PH.CHECK_RESULT k
                  WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id} AND NOT k.check_passed)
  AND NOT EXISTS (SELECT 1 FROM PH.HOLD_LEDGER h
                  WHERE h.case_id = a.case_id AND h.viewing_bank_id = a.viewing_bank_id
                    AND h.account_id = a.account_id AND h.placed_by = 'agent');

UPDATE PH.CASE_ACTION AS a
SET action_status = 'DONE'
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND a.action_status = 'TO_DO'
  AND EXISTS (SELECT 1 FROM PH.HOLD_LEDGER h
              WHERE h.case_id = a.case_id AND h.viewing_bank_id = a.viewing_bank_id
                AND h.account_id = a.account_id AND h.placed_by = 'agent');