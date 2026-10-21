-- 075_check.sql — the checker (maker-checker): deterministic checks that must all pass before the agent may change
-- anything. Runs inside Exasol after 070_actions.sql. Parameters: case_id, own_bank_id.
-- Any failed check stops every change-making tool (holds, exits, onward entries); the case goes to the officer.
--
--   conservation          the victim's money on the trail adds up to the payment, to the paisa
--   holds_within_balance  no automatic hold is larger than the account's balance
--   holds_within_trail    no automatic hold is larger than the amount the trail names for the account
--   automatic_allowed     only the four automatic actions are marked automatic (a full freeze never is)
--   own_accounts_only     holds and markers only on the viewing bank's own accounts
--   never_full_respected  no full-freeze proposal on a nodal, pool or escrow account

DELETE FROM PH.CHECK_RESULT WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO PH.CHECK_RESULT (case_id, viewing_bank_id, check_name, check_passed, detail, stmt_id)
SELECT {case_id}, {own_bank_id}, 'conservation',
       CASE WHEN COALESCE(t.on_trail, -1) = p.paid THEN TRUE ELSE FALSE END,
       'victim payment ' || CAST(p.paid AS VARCHAR(30)) || ', found on the trail '
           || COALESCE(CAST(t.on_trail AS VARCHAR(30)), 'nothing'),
       CURRENT_STATEMENT
FROM (SELECT SUM(x.amount_inr) AS paid
      FROM PH.TXN x JOIN PH.COMPLAINT c ON c.utr = x.utr AND c.case_id = {case_id}) p
CROSS JOIN (SELECT SUM(a.victim_amount_inr) AS on_trail
            FROM PH.ATTRIBUTION a
            WHERE a.case_id = {case_id} AND a.trace_rule = 'LIBR') t
UNION ALL
SELECT {case_id}, {own_bank_id}, 'holds_within_balance',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic holds above the balance',
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'MANDATORY_HOLD' AND a.amount_inr > a.balance_now_inr
UNION ALL
SELECT {case_id}, {own_bank_id}, 'holds_within_trail',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic holds above the amount the trail names',
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'MANDATORY_HOLD' AND a.amount_inr > a.trail_amount_inr
UNION ALL
SELECT {case_id}, {own_bank_id}, 'automatic_allowed',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' automatic actions outside the allowed list',
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.act_mode = 'AUTOMATIC'
  AND a.action_code NOT IN ('MANDATORY_HOLD', 'HOLD_MARKER', 'RECORD_EXIT', 'ONWARD_ENTRY')
UNION ALL
SELECT {case_id}, {own_bank_id}, 'own_accounts_only',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' holds or markers on another bank''s account',
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER')
  AND (a.bank_id IS NULL OR a.bank_id <> {own_bank_id})
UNION ALL
SELECT {case_id}, {own_bank_id}, 'never_full_respected',
       CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END,
       CAST(COUNT(*) AS VARCHAR(10)) || ' full-freeze proposals on nodal, pool or escrow accounts',
       CURRENT_STATEMENT
FROM PH.CASE_ACTION a
JOIN PH.ACCOUNT acc ON acc.account_id = a.account_id
WHERE a.case_id = {case_id} AND a.viewing_bank_id = {own_bank_id}
  AND a.action_code = 'PROPOSE_FULL_FREEZE'
  AND acc.account_type IN ('nodal', 'pool', 'escrow');