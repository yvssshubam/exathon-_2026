-- 070_recommend.sql — Step 4: one recommended action per account the money reached (FR-09, requirements s9 step 4).
-- Runs inside Exasol, once per case and viewing bank, after 060_score.sql. pipeline/steps.py fills the parameters:
--   case_id, own_bank_id, rule, lane_a_basis, lane_b_basis
--
-- Rules, in order (first match wins):
--   exit                                         -> NONE, record the exit (money left banking; never held)
--   other bank, paid straight from our account   -> REFER_OUT the victim's share (FR-04); not hop 1, the
--                                                  victim's own payment, which the complaint ticket covers
--   other bank, further down the trail           -> NONE (the bank that paid it refers it; DECISIONS D-01)
--   nothing left to hold, first-layer account    -> HOLD_MARKER, catch later credits (SOP 9.1(i)); never nodal/pool/escrow
--   nothing left to hold                         -> NONE
--   Lane B and MULE, not nodal/pool/escrow       -> ACCOUNT_HOLD, flagged last resort (RBI draft)
--   otherwise                                    -> AMOUNT_HOLD
-- Amount held = the amount the trail names for the account (DECISIONS D-07), or the attributed amount if the notice
-- names none, never more than the balance (SOP 9.1(i)). Lane A: a narrowing request is drafted when less than that is
-- traceable to the victim, and a seizure request when the account looks like a mule (S.106 BNSS needs an FIR).
-- Nothing here holds anything: PH.DECISION records what an officer approves (FR-10).

DELETE FROM PH.RECOMMENDATION WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id};

INSERT INTO PH.RECOMMENDATION (case_id, viewing_bank_id, account_id, bank_id, hop, action_code, amount_inr,
                               notice_amount_inr, victim_amount_inr, other_victims_inr, balance_now_inr, band, score,
                               lane, trace_rule, legal_basis, reason_code, draft_narrowing, draft_seizure, last_resort,
                               needs_comment, stmt_id)
SELECT {case_id},
       {own_bank_id},
       r.account_id,
       r.bank_id,
       r.hop,
       r.action_code,
       CASE r.action_code
            WHEN 'AMOUNT_HOLD'  THEN r.hold_amount
            WHEN 'ACCOUNT_HOLD' THEN r.balance_now
            WHEN 'REFER_OUT'    THEN r.referred_share
            ELSE NULL END,
       r.notice_amount,
       r.victim_amount,
       r.other_victims,
       r.balance_now,
       r.band,
       r.score,
       r.lane,
       {rule},
       CASE WHEN r.action_code IN ('AMOUNT_HOLD', 'ACCOUNT_HOLD', 'HOLD_MARKER')
            THEN CASE WHEN r.lane = 'A' THEN {lane_a_basis} ELSE {lane_b_basis} END
            WHEN r.action_code = 'REFER_OUT' THEN 'Onward trail update to CFCFRMS (SOP 6.1(k))'
            ELSE NULL END,
       r.reason_code,
       CASE WHEN r.action_code = 'AMOUNT_HOLD' AND r.lane = 'A' AND r.victim_amount < r.hold_amount
            THEN TRUE ELSE FALSE END,
       CASE WHEN r.action_code = 'AMOUNT_HOLD' AND r.lane = 'A' AND r.band = 'MULE'
            THEN TRUE ELSE FALSE END,
       CASE WHEN r.action_code = 'ACCOUNT_HOLD' THEN TRUE ELSE FALSE END,
       CASE WHEN r.band = 'REVIEW' AND r.action_code IN ('AMOUNT_HOLD', 'ACCOUNT_HOLD') THEN TRUE ELSE FALSE END,
       CURRENT_STATEMENT
FROM (
    SELECT q.*,
           CASE
               WHEN q.exit_flag = 1                                         THEN 'NONE'
               WHEN q.own_flag = 0 AND q.from_own_flag = 1                  THEN 'REFER_OUT'
               WHEN q.own_flag = 0                                          THEN 'NONE'
               WHEN q.hold_amount <= 0 AND q.hop = 1 AND q.never_full = 0   THEN 'HOLD_MARKER'
               WHEN q.hold_amount <= 0                                      THEN 'NONE'
               WHEN q.lane = 'B' AND q.band = 'MULE' AND q.never_full = 0   THEN 'ACCOUNT_HOLD'
               ELSE 'AMOUNT_HOLD'
           END AS action_code,
           CASE
               WHEN q.exit_flag = 1                                         THEN 'EXIT'
               WHEN q.own_flag = 0 AND q.from_own_flag = 1                  THEN 'OTHER_BANK_REFER'
               WHEN q.own_flag = 0                                          THEN 'OTHER_BANK_DOWNSTREAM'
               WHEN q.hold_amount <= 0 AND q.hop = 1 AND q.never_full = 0   THEN 'ZERO_BALANCE_FIRST_LAYER'
               WHEN q.hold_amount <= 0                                      THEN 'NOTHING_TO_HOLD'
               WHEN q.never_full = 1                                        THEN 'NODAL_POOL_ESCROW'
               WHEN q.lane = 'B' AND q.band = 'MULE'                        THEN 'LANE_B_MULE'
               WHEN q.lane = 'B'                                            THEN 'LANE_B_FLAGGED_TXN'
               WHEN q.band = 'MULE'                                         THEN 'LANE_A_MULE'
               ELSE 'LANE_A_HONEST_OR_REVIEW'
           END AS reason_code
    FROM (
        SELECT e.account_id,
               ta.bank_id,
               e.hop,
               e.exit_flag,
               e.from_own_flag,
               CASE WHEN ta.bank_id = {own_bank_id} THEN 1 ELSE 0 END                          AS own_flag,
               CASE WHEN ta.account_type IN ('nodal', 'pool', 'escrow') THEN 1 ELSE 0 END      AS never_full,
               e.inflow_traced                                                                  AS notice_amount,
               COALESCE(att.victim_amount_inr, 0)                                               AS victim_amount,
               COALESCE(att.other_victims_inr, 0)                                               AS other_victims,
               COALESCE(att.balance_now_inr, 0)                                                 AS balance_now,
               sc.band,
               sc.score,
               cp.lane,
               COALESCE(rf.victim_share, 0)                                                           AS referred_share,
               LEAST(COALESCE(CASE WHEN cp.notice_has_amount THEN e.inflow_traced END,
                              att.victim_amount_inr, 0),
                     COALESCE(att.balance_now_inr, 0))                                          AS hold_amount
        FROM (
            SELECT te.to_account                                                   AS account_id,
                   MIN(te.hop)                                                     AS hop,
                   MAX(CASE WHEN te.is_exit THEN 1 ELSE 0 END)                     AS exit_flag,
                   MAX(CASE WHEN fa.bank_id = {own_bank_id} AND te.hop > 1 THEN 1 ELSE 0 END) AS from_own_flag,
                   SUM(te.amount_inr)                                              AS inflow_traced
            FROM PH.TRACE_EDGE te
            LEFT JOIN PH.ACCOUNT fa ON fa.account_id = te.from_account
            WHERE te.case_id = {case_id}
            GROUP BY te.to_account
        ) e
        CROSS JOIN (SELECT lane, notice_has_amount FROM PH.COMPLAINT WHERE case_id = {case_id}) cp
        LEFT JOIN PH.ACCOUNT ta        ON ta.account_id = e.account_id
        LEFT JOIN PH.ATTRIBUTION att   ON att.case_id = {case_id} AND att.trace_rule = {rule}
                                      AND att.account_id = e.account_id
        LEFT JOIN PH.ACCOUNT_SCORE sc  ON sc.case_id = {case_id} AND sc.account_id = e.account_id
        LEFT JOIN (
            SELECT account_id, SUM(victim_amount_inr) AS victim_share
            FROM PH.REFERRAL_OUT
            WHERE case_id = {case_id} AND from_bank_id = {own_bank_id}
            GROUP BY account_id
        ) rf ON rf.account_id = e.account_id
    ) q
) r;