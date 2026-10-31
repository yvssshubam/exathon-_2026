-- 090_views.sql — read-only views over one bank's tables. Every view has a COMMENT so the MCP server can describe it.
-- Created after OPEN SCHEMA <bank schema>, so unqualified names resolve in that bank's schema.

-- The bank's transactions as they really happened once holds were in place: TXN without the debits the bank refused,
-- and without the credits another bank refused at its end (they never arrived).
-- TXN itself is what would happen if nobody acted, which the Compare screen replays. Every step that runs after a
-- hold could exist (a recalculation, the watch, the hold record) reads this view.
CREATE OR REPLACE VIEW TXN_LIVE AS
SELECT t.*
FROM TXN t
WHERE NOT EXISTS (SELECT 1 FROM DECLINED_ATTEMPT d WHERE d.txn_ref = t.txn_id)
  AND NOT EXISTS (SELECT 1 FROM UNDELIVERED_CREDIT u WHERE u.txn_ref = t.txn_id)
COMMENT IS 'Transactions that went through: TXN without the debits the bank refused because of a hold (DECLINED_ATTEMPT) and the credits another bank refused at its end (UNDELIVERED_CREDIT).';

-- One row per reported payment: the payment a ticket names (the one intake matched it to, which for a near match the
-- officer accepted is not the UTR typed on the ticket), whose victim it was, and how much of it the ticket reports.
-- The attribution tags these payments as victims' money. A duplicate ticket for the same payment never adds a second
-- victim (same UTR: one row), and a ticket only counts from the moment it reached the bank (reported_at), so a
-- recalculation never uses a ticket that had not arrived yet.
CREATE OR REPLACE VIEW ROOT_PAYMENT (
    utr          COMMENT IS 'UTR of the reported payment',
    case_id      COMMENT IS 'First ticket that reported it',
    victim_tag   COMMENT IS 'Victim whose money it was',
    tagged_inr   COMMENT IS 'How much of the payment the ticket reports as the victim''s (an onward ticket: the share it carried)',
    reported_at  COMMENT IS 'When that ticket reached the bank'
) AS
SELECT COALESCE(t.utr, c.utr),
       MIN(c.case_id),
       MIN(c.victim_tag),
       MIN(COALESCE(c.amount_inr, 999999999999.99)),
       MIN(c.reported_at)
FROM COMPLAINT c
LEFT JOIN INTAKE_CHECK i ON i.case_id = c.case_id
LEFT JOIN TXN t ON t.txn_id = i.matched_txn_id
WHERE c.ticket_kind <> 'IO_NOTICE'
  AND NOT EXISTS (SELECT 1 FROM CASE_LINK l WHERE l.case_id = c.case_id AND l.link_kind = 'DUPLICATE')
GROUP BY COALESCE(t.utr, c.utr)
COMMENT IS 'Reported payments: what the attribution treats as victims'' money, one row per UTR.';

-- What is held on each account right now, split into the reported victims' holds and money caught later.
CREATE OR REPLACE VIEW ACTIVE_HOLD (
    account_id     COMMENT IS 'Account',
    liens_inr      COMMENT IS 'Total under active liens (amount only)',
    not_linked_inr COMMENT IS 'Part of it caught after the first pass and not linked to a reported victim yet',
    frozen         COMMENT IS 'TRUE when an officer froze the whole account',
    has_marker     COMMENT IS 'TRUE when a first-layer marker catches later credits'
) AS
SELECT h.account_id,
       SUM(CASE WHEN h.hold_kind = 'LIEN' THEN h.amount_inr ELSE 0 END),
       SUM(CASE WHEN h.hold_kind = 'LIEN' AND h.hold_tag = 'NOT_LINKED' THEN h.amount_inr ELSE 0 END),
       CASE WHEN MAX(CASE WHEN h.hold_kind = 'ACCOUNT_FREEZE' THEN 1 ELSE 0 END) = 1 THEN TRUE ELSE FALSE END,
       CASE WHEN MAX(CASE WHEN h.hold_kind = 'MARKER' THEN 1 ELSE 0 END) = 1 THEN TRUE ELSE FALSE END
FROM HOLD_LEDGER h
WHERE h.hold_status = 'ACTIVE'
GROUP BY h.account_id
COMMENT IS 'Active holds per account, as the simulated core banking applies them.';
-- Flagged accounts (D-57, D-60), shared by the scoring (E3, E4) and the payment checks: accounts under a police notice
-- at this bank (named by a ticket that matched a transaction, or held under one), plus the simulated I4C Suspect
-- Registry, each with the moment it became flagged. A bank sees only its own notices: another bank's accounts are
-- flagged here only through the registry.
CREATE OR REPLACE VIEW FLAGGED_ACCOUNT (
    account_id     COMMENT IS 'Flagged account',
    flagged_since  COMMENT IS 'When it first became flagged',
    reason         COMMENT IS 'police_notice or suspect_registry (the earlier of the two when both)'
) AS
SELECT f.account_id,
       MIN(f.since),
       MIN(f.reason)
FROM (
    SELECT c.beneficiary_account AS account_id, c.reported_at AS since, 'police_notice' AS reason
    FROM COMPLAINT c
    JOIN INTAKE_CHECK k ON k.case_id = c.case_id AND k.match_status IN ('EXACT', 'ACCEPTED')
    WHERE c.beneficiary_account IS NOT NULL
    UNION ALL
    SELECT h.account_id, h.placed_at, 'police_notice'
    FROM HOLD_LEDGER h
    WHERE COALESCE(h.legal_basis, 'police_notice') = 'police_notice' AND NOT COALESCE(h.proactive, FALSE)
    UNION ALL
    SELECT r.account_id, r.listed_on, 'suspect_registry'
    FROM SUSPECT_REGISTRY r
) f
GROUP BY f.account_id
COMMENT IS 'Accounts under a police notice at this bank or in the Suspect Registry, with the moment each became flagged.';

-- One row per proactive hold with its outcome (Module 7, D-65). The same outcomes are what the weight model would
-- retrain on with real data: a hold a victim's complaint later matched, against one released after an explanation.
CREATE OR REPLACE VIEW PROACTIVE_HOLD_OUTCOME (
    hold_id            COMMENT IS 'The proactive hold (its first row)',
    case_id            COMMENT IS 'The notice behind the watch',
    account_id         COMMENT IS 'Account',
    txn_id             COMMENT IS 'The held credit',
    amount_inr         COMMENT IS 'Amount held',
    placed_at          COMMENT IS 'When it was placed',
    hold_mode          COMMENT IS 'rbi_draft or current_sop',
    payer_type         COMMENT IS 'flagged, own_customer or other_bank',
    account_pattern    COMMENT IS 'Account type at the decision',
    snapshot_id        COMMENT IS 'Scoring snapshot behind it',
    outcome            COMMENT IS 'confirmed_by_victim, released_after_explanation, layering, released_at_limit or open',
    minutes_to_complaint COMMENT IS 'Minutes from the hold to the victim''s complaint, where there is one'
) AS
SELECT h.hold_id, h.case_id, h.account_id, h.source_txn_id, h.amount_inr, h.placed_at, h.hold_mode, h.payer_type,
       s.account_pattern, h.snapshot_id,
       CASE WHEN h.linked_case_id IS NOT NULL OR pa.alert_status IN ('answered', 'confirmed') THEN 'confirmed_by_victim'
            WHEN h.payer_type = 'flagged' OR pa.alert_status = 'not_needed'                     THEN 'layering'
            WHEN h.closed_why IN ('released at the limit, no decision recorded',
                                  'released: no police direction within 30 days')              THEN 'released_at_limit'
            WHEN h.hold_status = 'RELEASED'                                                    THEN 'released_after_explanation'
            ELSE 'open' END,
       CASE WHEN h.linked_at IS NOT NULL THEN MINUTES_BETWEEN(h.linked_at, h.placed_at) END
FROM HOLD_LEDGER h
LEFT JOIN SCORE_SNAPSHOT s ON s.snapshot_id = h.snapshot_id
LEFT JOIN PRE_ALERT pa ON pa.hold_id = h.hold_id
WHERE h.action_code = 'PROACTIVE_HOLD'
COMMENT IS 'Proactive holds and how each one ended: the outcome metrics on the Compare screen and the label export.';

-- The label export for the scoring model (D-65): 1 when a victim's complaint later matched the hold, 0 when it was
-- released after an explanation; every other outcome is left out. The demo never trains on it (synthetic data).
CREATE OR REPLACE VIEW PROACTIVE_HOLD_LABEL (
    account_id   COMMENT IS 'Account',
    snapshot_id  COMMENT IS 'Scoring snapshot behind the hold',
    label        COMMENT IS '1 confirmed by a victim, 0 released after an explanation'
) AS
SELECT account_id, snapshot_id,
       CASE outcome WHEN 'confirmed_by_victim' THEN 1 ELSE 0 END
FROM PROACTIVE_HOLD_OUTCOME
WHERE outcome IN ('confirmed_by_victim', 'released_after_explanation')
COMMENT IS 'Closed, labelled proactive holds, one row each, in the shape the weight learner reads.';