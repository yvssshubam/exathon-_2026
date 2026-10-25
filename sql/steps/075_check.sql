-- 075_check.sql — the checker's case-level checks (maker-checker). Deterministic, run under the checker's own
-- database user (pipeline/checker.py) before it carries out anything for the case. Parameters: case_id, own_bank_id.
-- Any failed check stops every change for the case; the case goes to the officer instead.
--
--   conservation          the victim's money, wherever it sits in this bank's view (on the trail, or back in one
--                         of its accounts through another bank), adds up to the reported payment, to the paisa
--   holds_within_balance  no automatic hold is larger than the account's balance
--   holds_within_trail    no automatic hold is larger than the amount the trail names for the account
--   holds_within_report   no automatic hold is larger than the amount the ticket reports
--   automatic_allowed     only the four automatic hold and filing actions are marked automatic (a freeze never is)
--   own_accounts_only     holds and markers only on this bank's own accounts
--   never_full_respected  no full-freeze proposal on a nodal, pool or escrow account

DELETE FROM CHECK_RESULT WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO CHECK_RESULT (case_id, viewing_bank_id, check_name, check_passed, detail, stmt_id)
SELECT {case_id}, {own_bank_id}, 'conservation',
       CASE WHEN COALESCE(t.on_trail, -1) = p.paid THEN TRUE ELSE FALSE END,
       'victim payment ' || CAST(p.paid AS VARCHAR(30)) || ', found in this bank''s view '
           || COALESCE(CAST(t.on_trail AS VARCHAR(30)), 'nothing'),
       CURRENT_STATEMENT
FROM (SELECT SUM(LEAST(x.amount_inr, COALESCE(c.amount_inr, x.amount_inr))) AS paid
      FROM INTAKE_CHECK k
      JOIN TXN x ON x.txn_id = k.matched_txn_id
      JOIN COMPLAINT c ON c.case_id = k.case_id
      WHERE k.case_id = {case_id}) p
CROSS JOIN (SELECT CAST(SUM(r.paise) / 100 AS DECIMAL(18,2)) AS on_trail
            FROM ATTRIBUTION_RAW r
            JOIN COMPLAINT c ON c.case_id = r.case_id AND c.victim_tag = r.component
            WHERE r.case_id = {case_id} AND r.trace_rule = 'LIBR' AND r.row_kind IN ('HOLD', 'EXIT')) t
UNION ALL
SELECT {case_id}, {own_bank_id}, 'holds_within_balance',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic holds above the balance',
       CURRENT_STATEMENT
FROM CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'MANDATORY_HOLD' AND a.amount_inr > a.balance_now_inr
UNION ALL
SELECT {case_id}, {own_bank_id}, 'holds_within_trail',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic holds above the amount the trail names',
       CURRENT_STATEMENT
FROM CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'MANDATORY_HOLD' AND a.amount_inr > a.trail_amount_inr
UNION ALL
SELECT {case_id}, {own_bank_id}, 'holds_within_report',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic holds above the amount the ticket reports',
       CURRENT_STATEMENT
FROM CASE_ACTION a
JOIN COMPLAINT c ON c.case_id = a.case_id
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER') AND a.amount_inr > COALESCE(c.amount_inr, a.amount_inr)
UNION ALL
SELECT {case_id}, {own_bank_id}, 'automatic_allowed',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic actions outside the allowed list',
       CURRENT_STATEMENT
FROM CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.act_mode = 'AUTOMATIC'
  AND a.action_code NOT IN ('MANDATORY_HOLD', 'HOLD_MARKER', 'RECORD_EXIT', 'ONWARD_ENTRY')
UNION ALL
SELECT {case_id}, {own_bank_id}, 'own_accounts_only',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' holds or markers on another bank''s account',
       CURRENT_STATEMENT
FROM CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND (a.bank_id IS NULL OR a.bank_id <> {own_bank_id})
UNION ALL
SELECT {case_id}, {own_bank_id}, 'never_full_respected',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' full-freeze proposals on nodal, pool or escrow accounts',
       CURRENT_STATEMENT
FROM CASE_ACTION a
JOIN ACCOUNT acc ON acc.account_id = a.account_id
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'PROPOSE_FULL_FREEZE'
  AND acc.account_type IN ('nodal', 'pool', 'escrow');