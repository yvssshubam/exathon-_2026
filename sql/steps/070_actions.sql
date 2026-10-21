-- 070_actions.sql — the rules engine: what is allowed for each account the money reached, and who does it.
-- Runs inside Exasol, once per case and viewing bank, after 060_score.sql. pipeline/steps.py fills the parameters
-- from config/rules.yaml:
--   case_id, own_bank_id, basis_hold, basis_narrow, basis_freeze, basis_trail,
--   freeze_links_min, freeze_pass_min, freeze_cash_min (numbers)
--
-- 1. PH.CASE_ACCOUNT: every fact the rules read, one row per account or exit on the trail (inspectable on its own).
-- 2. PH.CASE_ACTION, automatic rows (first match wins):
--      exit paid by an own account               -> RECORD_EXIT  (file the exit; money that left banking is never held)
--      exit paid by another bank's account       -> NONE         (that bank records it)
--      other bank, paid by an own account        -> ONWARD_ENTRY (file it with the victim's share; not hop 1, the
--                                                                 victim's own payment, which the ticket covers)
--      other bank, further down the trail        -> NONE         (the bank that paid it files it; DECISIONS D-01)
--      nothing left, first layer, not nodal etc. -> HOLD_MARKER  (catch later credits, SOP 9.1(i))
--      nothing left                              -> NONE
--      otherwise                                 -> MANDATORY_HOLD of the trail amount, capped at the balance
-- 3. PH.CASE_ACTION, officer rows (proposals; nothing changes until an officer approves):
--      PROPOSE_NARROW      held more than the victims' money, and the evidence reads honest or borderline
--                          (or the account is nodal, pool or escrow): narrow the lien to the victims' money
--      PROPOSE_FULL_FREEZE MULE evidence with the exceptional pattern (config/rules.yaml full_freeze_evidence)
--      CONFIRM_HOLD        borderline evidence (REVIEW) and nothing to narrow
-- There is deliberately no automatic full freeze: the agent can only propose one.

DELETE FROM PH.CASE_ACCOUNT WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO PH.CASE_ACCOUNT (case_id, viewing_bank_id, account_id, bank_id, hop, is_exit, is_own_bank,
                             paid_by_own_bank, never_full, trail_amount_inr, victim_amount_inr, other_victims_inr,
                             balance_now_inr, hold_amount_inr, onward_share_inr, band, score, pass_through,
                             cash_exits, complaint_links, stmt_id)
SELECT {case_id},
       {own_bank_id},
       e.account_id,
       ta.bank_id,
       e.hop,
       CASE WHEN e.exit_flag = 1 THEN TRUE ELSE FALSE END,
       CASE WHEN ta.bank_id = {own_bank_id} THEN TRUE ELSE FALSE END,
       CASE WHEN e.paid_by_own_flag = 1 THEN TRUE ELSE FALSE END,
       CASE WHEN ta.account_type IN ('nodal', 'pool', 'escrow') THEN TRUE ELSE FALSE END,
       e.trail_amount,
       COALESCE(att.victim_amount_inr, 0),
       COALESCE(att.other_victims_inr, 0),
       COALESCE(att.balance_now_inr, 0),
       CASE WHEN e.exit_flag = 1 OR ta.bank_id IS NULL OR ta.bank_id <> {own_bank_id} THEN 0
            ELSE LEAST(CASE WHEN cp.notice_has_amount THEN e.trail_amount
                            ELSE COALESCE(att.victim_amount_inr, 0) + COALESCE(att.other_victims_inr, 0) END,
                       COALESCE(att.balance_now_inr, 0)) END,
       COALESCE(rf.victim_share, 0),
       sc.band,
       sc.score,
       sg.pass_through,
       sg.cash_exits,
       sg.complaint_links,
       CURRENT_STATEMENT
FROM (
    SELECT te.to_account                                                              AS account_id,
           MIN(te.hop)                                                                AS hop,
           MAX(CASE WHEN te.is_exit THEN 1 ELSE 0 END)                                AS exit_flag,
           MAX(CASE WHEN fa.bank_id = {own_bank_id} AND te.hop > 1 THEN 1 ELSE 0 END) AS paid_by_own_flag,
           SUM(te.amount_inr)                                                         AS trail_amount
    FROM PH.TRACE_EDGE te
    LEFT JOIN PH.ACCOUNT fa ON fa.account_id = te.from_account
    WHERE te.case_id = {case_id}
    GROUP BY te.to_account
) e
CROSS JOIN (SELECT notice_has_amount FROM PH.COMPLAINT WHERE case_id = {case_id}) cp
LEFT JOIN PH.ACCOUNT ta        ON ta.account_id = e.account_id
LEFT JOIN PH.ATTRIBUTION att   ON att.case_id = {case_id} AND att.trace_rule = 'LIBR'
                              AND att.account_id = e.account_id
LEFT JOIN PH.ACCOUNT_SCORE sc  ON sc.case_id = {case_id} AND sc.account_id = e.account_id
LEFT JOIN (
    SELECT account_id, SUM(victim_amount_inr) AS victim_share
    FROM PH.REFERRAL_OUT
    WHERE case_id = {case_id} AND from_bank_id = {own_bank_id}
    GROUP BY account_id
) rf ON rf.account_id = e.account_id
LEFT JOIN (
    SELECT account_id,
           MAX(CASE WHEN signal_name = 'pass_through_ratio'          THEN signal_value END) AS pass_through,
           MAX(CASE WHEN signal_name = 'cash_or_crypto_exits'        THEN signal_value END) AS cash_exits,
           MAX(CASE WHEN signal_name = 'complaint_linked_credits_7d' THEN signal_value END) AS complaint_links
    FROM PH.ACCOUNT_SIGNALS
    WHERE case_id = {case_id}
    GROUP BY account_id
) sg ON sg.account_id = e.account_id;

DELETE FROM PH.CASE_ACTION WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO PH.CASE_ACTION (case_id, viewing_bank_id, account_id, bank_id, hop, action_code, act_mode, amount_inr,
                            current_hold_inr, victim_amount_inr, other_victims_inr, balance_now_inr, trail_amount_inr,
                            band, score, reason_code, legal_basis, action_status, stmt_id)
SELECT {case_id},
       {own_bank_id},
       r.account_id,
       r.bank_id,
       r.hop,
       r.action_code,
       CASE WHEN r.action_code = 'NONE' THEN 'INFO' ELSE 'AUTOMATIC' END,
       CASE r.action_code
            WHEN 'MANDATORY_HOLD' THEN r.hold_amount_inr
            WHEN 'RECORD_EXIT'    THEN r.victim_amount_inr
            WHEN 'ONWARD_ENTRY'   THEN r.onward_share_inr
            ELSE NULL END,
       NULL,
       r.victim_amount_inr,
       r.other_victims_inr,
       r.balance_now_inr,
       r.trail_amount_inr,
       r.band,
       r.score,
       r.reason_code,
       CASE WHEN r.action_code IN ('MANDATORY_HOLD', 'HOLD_MARKER') THEN {basis_hold}
            WHEN r.action_code IN ('RECORD_EXIT', 'ONWARD_ENTRY') THEN {basis_trail}
            ELSE NULL END,
       CASE WHEN r.action_code = 'NONE' THEN 'INFO' ELSE 'TO_DO' END,
       CURRENT_STATEMENT
FROM (
    SELECT ca.*,
           CASE
               WHEN ca.is_exit AND ca.paid_by_own_bank                          THEN 'RECORD_EXIT'
               WHEN ca.is_exit                                                  THEN 'NONE'
               WHEN NOT ca.is_own_bank AND ca.paid_by_own_bank                  THEN 'ONWARD_ENTRY'
               WHEN NOT ca.is_own_bank                                          THEN 'NONE'
               WHEN ca.hold_amount_inr <= 0 AND ca.hop = 1 AND NOT ca.never_full THEN 'HOLD_MARKER'
               WHEN ca.hold_amount_inr <= 0                                     THEN 'NONE'
               ELSE 'MANDATORY_HOLD'
           END AS action_code,
           CASE
               WHEN ca.is_exit AND ca.paid_by_own_bank                          THEN 'EXIT_FROM_OWN_ACCOUNT'
               WHEN ca.is_exit                                                  THEN 'EXIT_AT_OTHER_BANK'
               WHEN NOT ca.is_own_bank AND ca.paid_by_own_bank                  THEN 'MOVED_TO_OTHER_BANK'
               WHEN NOT ca.is_own_bank                                          THEN 'OTHER_BANK_DOWNSTREAM'
               WHEN ca.hold_amount_inr <= 0 AND ca.hop = 1 AND NOT ca.never_full THEN 'EMPTY_FIRST_LAYER'
               WHEN ca.hold_amount_inr <= 0                                     THEN 'NOTHING_LEFT'
               WHEN ca.never_full                                               THEN 'NODAL_POOL_ESCROW'
               ELSE 'MONEY_STILL_THERE'
           END AS reason_code
    FROM PH.CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
) r;

INSERT INTO PH.CASE_ACTION (case_id, viewing_bank_id, account_id, bank_id, hop, action_code, act_mode, amount_inr,
                            current_hold_inr, victim_amount_inr, other_victims_inr, balance_now_inr, trail_amount_inr,
                            band, score, reason_code, legal_basis, action_status, stmt_id)
SELECT {case_id}, {own_bank_id}, p.account_id, p.bank_id, p.hop, p.action_code, 'OFFICER', p.amount_inr,
       p.hold_amount_inr, p.victim_amount_inr, p.other_victims_inr, p.balance_now_inr, p.trail_amount_inr,
       p.band, p.score, p.reason_code, p.legal_basis, 'PROPOSED', CURRENT_STATEMENT
FROM (
    SELECT ca.*, 'PROPOSE_NARROW' AS action_code,
           ca.victim_amount_inr + ca.other_victims_inr AS amount_inr,
           CASE WHEN ca.never_full THEN 'NODAL_POOL_ESCROW' ELSE 'LIKELY_HONEST_RECEIVER' END AS reason_code,
           {basis_narrow} AS legal_basis
    FROM PH.CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit
      AND ca.hold_amount_inr > 0
      AND ca.victim_amount_inr + ca.other_victims_inr < ca.hold_amount_inr
      AND (ca.band IN ('HONEST', 'REVIEW') OR ca.never_full)

    UNION ALL
    SELECT ca.*, 'PROPOSE_FULL_FREEZE',
           ca.balance_now_inr,
           CASE WHEN ca.complaint_links >= {freeze_links_min!d} THEN 'REPEATED_COMPLAINTS'
                ELSE 'PASS_THROUGH_AND_CASH_OUT' END,
           {basis_freeze}
    FROM PH.CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit AND NOT ca.never_full
      AND ca.band = 'MULE'
      AND (   ca.complaint_links >= {freeze_links_min!d}
           OR (ca.pass_through >= {freeze_pass_min!d} AND ca.cash_exits >= {freeze_cash_min!d}))

    UNION ALL
    SELECT ca.*, 'CONFIRM_HOLD',
           ca.hold_amount_inr,
           'BORDERLINE_EVIDENCE',
           {basis_hold}
    FROM PH.CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit AND NOT ca.never_full
      AND ca.hold_amount_inr > 0
      AND ca.band = 'REVIEW'
      AND ca.victim_amount_inr + ca.other_victims_inr >= ca.hold_amount_inr
) p;