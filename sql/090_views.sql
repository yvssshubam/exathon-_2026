-- 090_views.sql — read-only views over the case tables. Every view has a COMMENT so the MCP server can describe it.

-- FR-02: is the incoming notice complete enough to act on? (Rajasthan HC: clarify unclear notices, don't block
-- mechanically.) One row per complaint; notice_status INCOMPLETE lists what is missing.
CREATE OR REPLACE VIEW PH.COMPLAINT_CHECK (
    case_id          COMMENT IS 'Case',
    ack_no           COMMENT IS 'NCRP acknowledgement number',
    complaint_source COMMENT IS '1930, NCRP or branch',
    lane             COMMENT IS 'A = police-instructed hold (the only lane this build works)',
    utr              COMMENT IS 'UTR of the victim''s payment',
    amount_inr       COMMENT IS 'Amount reported',
    notice_status    COMMENT IS 'VALID or INCOMPLETE',
    problems         COMMENT IS 'What is missing or wrong, in plain words; empty when VALID'
) AS
SELECT c.case_id,
       c.ack_no,
       c.complaint_source,
       c.lane,
       c.utr,
       c.amount_inr,
       CASE WHEN c.amount_inr IS NULL OR NOT c.notice_has_amount
              OR (c.lane = 'A' AND c.notice_legal_basis IS NULL)
              OR c.utr IS NULL
              OR LENGTH(c.ack_no) <> 14
              OR (c.complaint_source = '1930' AND SUBSTR(c.ack_no, 1, 1) <> '3')
              OR (c.complaint_source = 'NCRP' AND SUBSTR(c.ack_no, 1, 1) <> '2')
              OR t.txn_id IS NULL
            THEN 'INCOMPLETE' ELSE 'VALID' END AS notice_status,
       TRIM(
           CASE WHEN c.amount_inr IS NULL OR NOT c.notice_has_amount THEN 'no amount; ' ELSE '' END ||
           CASE WHEN c.lane = 'A' AND c.notice_legal_basis IS NULL THEN 'no legal basis; ' ELSE '' END ||
           CASE WHEN c.utr IS NULL THEN 'no UTR; ' ELSE '' END ||
           CASE WHEN LENGTH(c.ack_no) <> 14 THEN 'acknowledgement number is not 14 digits; ' ELSE '' END ||
           CASE WHEN (c.complaint_source = '1930' AND SUBSTR(c.ack_no, 1, 1) <> '3')
                  OR (c.complaint_source = 'NCRP' AND SUBSTR(c.ack_no, 1, 1) <> '2')
                THEN 'acknowledgement number does not match its source; ' ELSE '' END ||
           CASE WHEN t.txn_id IS NULL THEN 'UTR not found in transactions; ' ELSE '' END
       ) AS problems
FROM PH.COMPLAINT c
LEFT JOIN (SELECT utr, MIN(txn_id) AS txn_id FROM PH.TXN GROUP BY utr) t ON t.utr = c.utr
COMMENT IS 'FR-02: whether each complaint notice is complete (amount, legal basis, UTR, acknowledgement number) and what is missing.';