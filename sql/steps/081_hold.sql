-- 081_hold.sql — checker action place_mandatory_hold: write one automatic lien or first-layer marker to the simulated
-- core banking hold ledger. Run only by the checker (pipeline/checker.py), under its own database user, for one
-- proposal it has validated. Parameters: case_id, own_bank_id, placed_at, account_id, proposal_id.
-- It acts only on what the rules engine marked AUTOMATIC for this account, only when every check in CHECK_RESULT
-- passed for the case (no check rows = no holds), and re-running never doubles a hold.

INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, placed_at, placed_by,
                         hold_status, action_code, reason_text, proposal_id, stmt_id)
SELECT a.case_id,
       a.viewing_bank_id,
       a.account_id,
       CASE WHEN a.action_code = 'HOLD_MARKER' THEN 'MARKER' ELSE 'LIEN' END,
       a.amount_inr,
       c.victim_tag,
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       'ACTIVE',
       a.action_code,
       a.reason_code,
       {proposal_id!d},
       CURRENT_STATEMENT
FROM CASE_ACTION a
JOIN COMPLAINT c ON c.case_id = a.case_id
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id} AND a.account_id = {account_id}
  AND a.act_mode = 'AUTOMATIC'
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND a.action_status = 'TO_DO'
  AND (SELECT COUNT(*) FROM CHECK_RESULT k
       WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id}) > 0
  AND NOT EXISTS (SELECT 1 FROM CHECK_RESULT k
                  WHERE k.case_id = {case_id} AND k.viewing_bank_id = {own_bank_id} AND NOT k.check_passed)
  AND NOT EXISTS (SELECT 1 FROM HOLD_LEDGER h
                  WHERE h.case_id = a.case_id AND h.viewing_bank_id = a.viewing_bank_id
                    AND h.account_id = a.account_id AND h.placed_by = 'agent'
                    AND h.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER', 'LINKED_HOLD'));

UPDATE CASE_ACTION AS a
SET action_status = 'DONE'
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id} AND a.account_id = {account_id}
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND a.action_status = 'TO_DO'
  AND EXISTS (SELECT 1 FROM HOLD_LEDGER h
              WHERE h.case_id = a.case_id AND h.viewing_bank_id = a.viewing_bank_id
                AND h.account_id = a.account_id AND h.placed_by = 'agent');