-- 090_views.sql — read-only views over one bank's tables. Every view has a COMMENT so the MCP server can describe it.
-- Created after OPEN SCHEMA <bank schema>, so unqualified names resolve in that bank's schema.

-- The bank's transactions as they really happened once holds were in place: TXN without the debits the bank refused.
-- TXN itself is what would happen if nobody acted, which the Compare screen replays. Every step that runs after a
-- hold could exist (a recalculation, the watch, the hold record) reads this view.
CREATE OR REPLACE VIEW TXN_LIVE AS
SELECT t.*
FROM TXN t
WHERE NOT EXISTS (SELECT 1 FROM DECLINED_ATTEMPT d WHERE d.txn_ref = t.txn_id)
COMMENT IS 'Transactions that went through: TXN without the debits the bank refused because of a hold (DECLINED_ATTEMPT).';

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