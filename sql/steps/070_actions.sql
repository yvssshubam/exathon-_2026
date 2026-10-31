-- 070_actions.sql — the rules engine: what the facts allow for each account the money reached, and whose call it is.
-- Runs inside the bank's schema, once per case, after 060_score.sql. pipeline/steps.py fills the parameters from
-- config/rules.yaml:
--   case_id, own_bank_id, basis_hold, basis_narrow, basis_larger, basis_freeze, basis_trail
--
-- Allowing is not doing: this step writes what is allowed; the maker proposes it and the checker (another database
-- user) carries out the automatic part. Nothing here touches a hold.
--
-- 1. CASE_ACCOUNT: every fact the rules read, one row per account or exit on the trail (inspectable on its own).
-- 2. CASE_ACTION, automatic rows (first match wins):
--      exit paid by an own account               -> RECORD_EXIT  (file the exit; money that left banking is never held)
--      exit paid by another bank's account       -> NONE         (that bank records it)
--      other bank, paid by an own account        -> ONWARD_ENTRY (file it with the victim's share; not hop 1, the
--                                                                 ticket's own payment, which the ticket covers)
--      other bank, none of the victim's money    -> NONE         (nothing to file: it would be a Rs 0 ticket there)
--      other bank, further down the trail        -> NONE         (the bank that paid it files it)
--      nothing left, first layer, not nodal etc. -> HOLD_MARKER  (catch later credits, SOP 9.1(i))
--      nothing left                              -> NONE
--      otherwise                                 -> MANDATORY_HOLD of the trail amount, never above the balance
--                                                   (less what other tickets already hold there) or the amount the
--                                                   ticket reports
-- 3. CASE_ACTION, officer rows (only proposed; nothing changes until an officer approves). What each account type
--    may trigger (config/rules.yaml account_types; D-57, D-59). One account type, one meaning: the same account can
--    never get both a shrink suggestion and a freeze suggestion.
--      PROPOSE_NARROW      held more than the victims' money, and the account is an Ordinary customer (or nodal,
--                          pool or escrow): shrink the lien to the victims' money. Exposed business and Grey zone:
--                          no automatic suggestion, the officer reviews.
--      PROPOSE_LARGER_HOLD the freeze ladder at Medium (fraud money leaves fast, 2 to under 3 fraud cases), on a Pipe
--                          for fraud or an Exposed business: hold up to the fraud-linked money of 90 days, never
--                          above the balance
--      PROPOSE_FULL_FREEZE the ladder at Strong (3 or more cases), on a Pipe for fraud or an Exposed business, never
--                          nodal, pool or escrow. The watch proposes one later when a Pipe for fraud or Fast mover
--                          tries to cash out (a refused withdrawal goes straight to Strong).
--    The ladder at Weak (one case) is shown as information: each fraud-linked payment is held as it arrives.
-- There is deliberately no automatic full freeze: every step is a suggestion, and the officer approves or rejects it.

DELETE FROM CASE_ACCOUNT WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO CASE_ACCOUNT (case_id, viewing_bank_id, account_id, bank_id, hop, is_exit, is_own_bank,
                          paid_by_own_bank, never_full, trail_amount_inr, reported_inr, victim_amount_inr,
                          other_victims_inr, balance_now_inr, hold_amount_inr, onward_share_inr, account_pattern,
                          pass_through_score, exposure_score, ladder_step, fraud_linked_inr, snapshot_id, stmt_id)
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
       cp.amount_inr,
       COALESCE(att.victim_amount_inr, 0),
       COALESCE(att.other_victims_inr, 0),
       COALESCE(att.balance_now_inr, 0),
       CASE WHEN e.exit_flag = 1 OR ta.bank_id IS NULL OR ta.bank_id <> {own_bank_id} THEN 0
            ELSE LEAST(CASE WHEN cp.notice_has_amount THEN e.trail_amount
                            ELSE COALESCE(att.victim_amount_inr, 0) + COALESCE(att.other_victims_inr, 0) END,
                       GREATEST(COALESCE(att.balance_now_inr, 0) - COALESCE(oh.held_inr, 0), 0),
                       COALESCE(cp.amount_inr, e.trail_amount)) END,
       COALESCE(rf.victim_share, 0),
       sc.account_pattern,
       sc.pass_through,
       sc.fraud_exposure,
       sc.ladder_step,
       sc.fraud_linked_inr,
       sc.snapshot_id,
       CURRENT_STATEMENT
FROM (
    SELECT te.to_account                                                              AS account_id,
           MIN(te.hop)                                                                AS hop,
           MAX(CASE WHEN te.is_exit THEN 1 ELSE 0 END)                                AS exit_flag,
           MAX(CASE WHEN fa.bank_id = {own_bank_id} AND te.hop > 1 THEN 1 ELSE 0 END) AS paid_by_own_flag,
           SUM(te.amount_inr)                                                         AS trail_amount
    FROM TRACE_EDGE te
    LEFT JOIN ACCOUNT fa ON fa.account_id = te.from_account
    WHERE te.case_id = {case_id}
    GROUP BY te.to_account
) e
CROSS JOIN (SELECT notice_has_amount, amount_inr FROM COMPLAINT WHERE case_id = {case_id}) cp
LEFT JOIN ACCOUNT ta           ON ta.account_id = e.account_id
LEFT JOIN ATTRIBUTION att      ON att.case_id = {case_id} AND att.trace_rule = 'LIBR'
                              AND att.account_id = e.account_id
LEFT JOIN ACCOUNT_SCORE sc     ON sc.case_id = {case_id} AND sc.account_id = e.account_id
-- Money other tickets already hold on the account (not a watch hold waiting to be linked, which this ticket may be
-- the one to claim): two tickets for one victim must not hold the same rupees twice, or more than the balance.
LEFT JOIN (
    SELECT account_id, SUM(amount_inr) AS held_inr
    FROM HOLD_LEDGER
    WHERE viewing_bank_id = {own_bank_id} AND case_id <> {case_id} AND hold_status = 'ACTIVE'
      AND hold_kind <> 'MARKER' AND COALESCE(hold_tag, '-') <> 'NOT_LINKED'
    GROUP BY account_id
) oh ON oh.account_id = e.account_id
LEFT JOIN (
    SELECT account_id, SUM(victim_amount_inr) AS victim_share
    FROM REFERRAL_OUT
    WHERE case_id = {case_id} AND from_bank_id = {own_bank_id}
    GROUP BY account_id
) rf ON rf.account_id = e.account_id;

DELETE FROM CASE_ACTION WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO CASE_ACTION (case_id, viewing_bank_id, account_id, bank_id, hop, action_code, act_mode, amount_inr,
                         current_hold_inr, victim_amount_inr, other_victims_inr, balance_now_inr, trail_amount_inr,
                         account_pattern, snapshot_id, reason_code, legal_basis, action_status, stmt_id)
SELECT {case_id},
       {own_bank_id},
       r.account_id,
       r.bank_id,
       r.hop,
       r.action_code,
       CASE WHEN r.action_code = 'NONE' THEN 'INFO' ELSE 'AUTOMATIC' END,
       CASE r.action_code
            WHEN 'MANDATORY_HOLD' THEN r.hold_amount_inr
            WHEN 'HOLD_MARKER'    THEN LEAST(r.trail_amount_inr, COALESCE(r.reported_inr, r.trail_amount_inr))
            WHEN 'RECORD_EXIT'    THEN r.victim_amount_inr
            WHEN 'ONWARD_ENTRY'   THEN r.onward_share_inr
            ELSE NULL END,
       NULL,
       r.victim_amount_inr,
       r.other_victims_inr,
       r.balance_now_inr,
       r.trail_amount_inr,
       r.account_pattern,
       r.snapshot_id,
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
               WHEN NOT ca.is_own_bank AND ca.paid_by_own_bank AND ca.onward_share_inr > 0 THEN 'ONWARD_ENTRY'
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
    FROM CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
) r;

INSERT INTO CASE_ACTION (case_id, viewing_bank_id, account_id, bank_id, hop, action_code, act_mode, amount_inr,
                         current_hold_inr, victim_amount_inr, other_victims_inr, balance_now_inr, trail_amount_inr,
                         account_pattern, snapshot_id, reason_code, legal_basis, action_status, stmt_id)
SELECT {case_id}, {own_bank_id}, p.account_id, p.bank_id, p.hop, p.action_code, 'OFFICER', p.amount_inr,
       p.hold_amount_inr, p.victim_amount_inr, p.other_victims_inr, p.balance_now_inr, p.trail_amount_inr,
       p.account_pattern, p.snapshot_id, p.reason_code, p.legal_basis, 'PROPOSED', CURRENT_STATEMENT
FROM (
    SELECT ca.*, 'PROPOSE_NARROW' AS action_code,
           ca.victim_amount_inr + ca.other_victims_inr AS amount_inr,
           CASE WHEN ca.never_full THEN 'NODAL_POOL_ESCROW' ELSE 'ORDINARY_CUSTOMER' END AS reason_code,
           {basis_narrow} AS legal_basis
    FROM CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit
      AND ca.hold_amount_inr > 0
      AND ca.victim_amount_inr + ca.other_victims_inr < ca.hold_amount_inr
      AND (ca.account_pattern = 'ORDINARY_CUSTOMER' OR ca.never_full)

    UNION ALL
    SELECT ca.*, 'PROPOSE_LARGER_HOLD',
           LEAST(ca.fraud_linked_inr, ca.balance_now_inr),
           'LADDER_MEDIUM',
           {basis_larger}
    FROM CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit
      AND ca.account_pattern IN ('PIPE_FOR_FRAUD', 'EXPOSED_BUSINESS')
      AND ca.ladder_step = 'MEDIUM'
      AND LEAST(ca.fraud_linked_inr, ca.balance_now_inr) > ca.hold_amount_inr
      AND ca.victim_amount_inr + ca.other_victims_inr >= ca.hold_amount_inr

    UNION ALL
    SELECT ca.*, 'PROPOSE_FULL_FREEZE',
           ca.balance_now_inr,
           'LADDER_STRONG',
           {basis_freeze}
    FROM CASE_ACCOUNT ca
    WHERE ca.case_id = {case_id} AND ca.viewing_bank_id = {own_bank_id}
      AND ca.is_own_bank AND NOT ca.is_exit AND NOT ca.never_full
      AND ca.account_pattern IN ('PIPE_FOR_FRAUD', 'EXPOSED_BUSINESS')
      AND ca.ladder_step = 'STRONG'
) p;