-- 030_intake.sql — ticket intake (Module 1): is the ticket's transaction really in this bank's data, and is it new?
-- Runs inside the bank's schema the moment a ticket lands, before anything else. Run by the maker (tool read_ticket,
-- check_notice and find_related_cases); it changes nothing outside INTAKE_CHECK. Parameters: case_id, checked_at.
-- Only payments made by checked_at can match: the simulated world holds the whole day, a bank only what has happened.
--
--   EXACT       UTR, amount, time (within 15 minutes; the day, for a notice that gives no time) and the receiving
--               account all match one payment. An onward
--               ticket reports the victim's share, which may be less than the payment it names.
--   NEAR_MATCH  no exact match, but a payment of the same amount reached the same account within two hours (likely
--               a typo in the UTR). Nothing is held: the ticket is sent back and the near match goes to the officer.
--               Only the account the ticket names is searched: never another customer's transactions.
--   NO_MATCH    nothing close: sent back.
--   INCOMPLETE  a required field is missing (amount, UTR, legal basis, acknowledgement number): sent back.
--   ACCEPTED    the officer decided a near match is the ticket's payment (DECISION UNMATCHED_NOTICE approved).
--
--   related: DUPLICATE   the same payment is already in an earlier case here: link it, no second hold
--            NEW_VICTIM  another case's trail already reached the account this ticket names: split the money
--            CORRECTED   a corrected ticket replacing one this bank sent back
--            NEW         none of these

DELETE FROM INTAKE_CHECK WHERE case_id = {case_id};

INSERT INTO INTAKE_CHECK (case_id, match_status, matched_txn_id, mismatched, problems, near_txn_id, near_utr, near_at,
                          near_amount_inr, related, related_case_id, checked_at, stmt_id)
SELECT {case_id},
       CASE WHEN m.problems IS NOT NULL                          THEN 'INCOMPLETE'
            WHEN m.exact_txn IS NOT NULL                   THEN 'EXACT'
            WHEN m.accepted > 0 AND m.near_txn IS NOT NULL THEN 'ACCEPTED'
            WHEN m.near_txn IS NOT NULL                    THEN 'NEAR_MATCH'
            ELSE 'NO_MATCH' END,
       CASE WHEN m.problems IS NOT NULL THEN CAST(NULL AS VARCHAR(40))
            WHEN m.exact_txn IS NOT NULL THEN m.exact_txn
            WHEN m.accepted > 0 THEN m.near_txn
            ELSE CAST(NULL AS VARCHAR(40)) END,
       CASE WHEN m.exact_txn IS NOT NULL OR m.problems IS NOT NULL THEN CAST(NULL AS VARCHAR(400))
            ELSE RTRIM(
                CASE WHEN m.utr_account IS NULL OR m.utr_account <> m.beneficiary_account
                     THEN 'UTR: ' || m.utr || ' is not a payment into account ' || m.beneficiary_account || CHR(10)
                     ELSE '' END ||
                CASE WHEN m.utr_account = m.beneficiary_account AND m.utr_amount <> m.amount_inr
                     THEN 'Amount: the ticket says Rs ' || CAST(m.amount_inr AS VARCHAR(30))
                          || '; the payment with that UTR is Rs ' || CAST(m.utr_amount AS VARCHAR(30)) || CHR(10)
                     ELSE '' END ||
                CASE WHEN m.utr_account = m.beneficiary_account AND m.txn_at IS NOT NULL
                          AND COALESCE(m.txn_time_known, TRUE) AND ABS(MINUTES_BETWEEN(m.utr_ts, m.txn_at)) > 15
                     THEN 'Time: the ticket says ' || TO_CHAR(m.txn_at, 'YYYY-MM-DD HH24:MI')
                          || '; the payment with that UTR was at ' || TO_CHAR(m.utr_ts, 'YYYY-MM-DD HH24:MI') || CHR(10)
                     WHEN m.utr_account = m.beneficiary_account AND m.txn_at IS NOT NULL
                          AND m.txn_time_known = FALSE AND CAST(m.utr_ts AS DATE) <> CAST(m.txn_at AS DATE)
                     THEN 'Date: the ticket says ' || TO_CHAR(m.txn_at, 'YYYY-MM-DD')
                          || '; the payment with that UTR was on ' || TO_CHAR(m.utr_ts, 'YYYY-MM-DD') || CHR(10)
                     ELSE '' END, CHR(10)) END,
       m.problems,
       m.near_txn,
       m.near_utr,
       m.near_ts,
       m.near_amount,
       CASE WHEN m.dup_case IS NOT NULL      THEN 'DUPLICATE'
            WHEN m.corrects IS NOT NULL      THEN 'CORRECTED'
            WHEN m.shared_case IS NOT NULL   THEN 'NEW_VICTIM'
            ELSE 'NEW' END,
       COALESCE(m.dup_case, m.corrects, m.shared_case),
       TO_TIMESTAMP({checked_at}, 'YYYY-MM-DD HH24:MI:SS'),
       CURRENT_STATEMENT
FROM (
    SELECT c.utr, c.amount_inr, c.txn_at, c.txn_time_known, c.beneficiary_account, c.corrects_case_id AS corrects,
           RTRIM(
               CASE WHEN c.amount_inr IS NULL OR NOT c.notice_has_amount THEN 'no amount' || CHR(10) ELSE '' END ||
               CASE WHEN c.lane = 'A' AND c.notice_legal_basis IS NULL THEN 'no legal basis' || CHR(10) ELSE '' END ||
               CASE WHEN c.utr IS NULL OR c.utr = '-' THEN 'no UTR' || CHR(10) ELSE '' END ||
               CASE WHEN c.beneficiary_account IS NULL THEN 'no receiving account' || CHR(10) ELSE '' END ||
               CASE WHEN LENGTH(c.ack_no) <> 14 THEN 'acknowledgement number is not 14 digits' || CHR(10)
                    ELSE '' END ||
               CASE WHEN (c.complaint_source = '1930' AND SUBSTR(c.ack_no, 1, 1) <> '3')
                      OR (c.complaint_source = 'NCRP' AND SUBSTR(c.ack_no, 1, 1) <> '2')
                    THEN 'acknowledgement number does not match its source' || CHR(10) ELSE '' END,
               CHR(10)) AS problems,
           ex.exact_txn, u.utr_account, u.utr_amount, u.utr_ts,
           n.txn_id AS near_txn, n.utr AS near_utr, n.ts AS near_ts, n.amount_inr AS near_amount,
           a.accepted, dp.dup_case, sh.shared_case
    FROM COMPLAINT c
    -- Exasol allows only equality in a correlated subquery, so each lookup below is a one-row aggregate over this
    -- ticket (case_id is a parameter) joined on with CROSS JOIN, never a subquery that refers to c.
    CROSS JOIN (SELECT MIN(t.txn_id) AS exact_txn
                FROM TXN t JOIN COMPLAINT k ON k.case_id = {case_id}
                                           AND t.utr = k.utr AND t.to_account = k.beneficiary_account
                WHERE t.ts <= TO_TIMESTAMP({checked_at}, 'YYYY-MM-DD HH24:MI:SS')
                  AND (t.amount_inr = k.amount_inr OR (k.ticket_kind = 'ONWARD' AND k.amount_inr <= t.amount_inr))
                  AND (k.txn_at IS NULL
                       OR (k.txn_time_known = FALSE AND CAST(t.ts AS DATE) = CAST(k.txn_at AS DATE))
                       OR (COALESCE(k.txn_time_known, TRUE) AND ABS(MINUTES_BETWEEN(t.ts, k.txn_at)) <= 15))) ex
    CROSS JOIN (SELECT MIN(t.to_account) AS utr_account, MIN(t.amount_inr) AS utr_amount, MIN(t.ts) AS utr_ts
                FROM TXN t JOIN COMPLAINT k ON k.case_id = {case_id} AND t.utr = k.utr
                WHERE t.ts <= TO_TIMESTAMP({checked_at}, 'YYYY-MM-DD HH24:MI:SS')) u
    CROSS JOIN (SELECT COUNT(*) AS accepted FROM DECISION d
                WHERE d.case_id = {case_id} AND d.action_code = 'UNMATCHED_NOTICE'
                  AND d.decision_outcome IN ('APPROVED', 'EDITED')) a
    CROSS JOIN (SELECT MIN(k2.case_id) AS dup_case
                FROM INTAKE_CHECK k2
                JOIN TXN t ON t.txn_id = k2.matched_txn_id
                JOIN COMPLAINT k ON k.case_id = {case_id} AND t.utr = k.utr AND t.to_account = k.beneficiary_account
                WHERE k2.case_id <> {case_id} AND k2.related <> 'DUPLICATE') dp
    CROSS JOIN (SELECT MIN(e.case_id) AS shared_case
                FROM TRACE_EDGE e JOIN COMPLAINT k ON k.case_id = {case_id} AND e.to_account = k.beneficiary_account
                WHERE e.case_id <> {case_id}) sh
    LEFT JOIN (
        SELECT x.txn_id, x.utr, x.ts, x.amount_inr
        FROM (
            SELECT t.txn_id, t.utr, t.ts, t.amount_inr,
                   ROW_NUMBER() OVER (ORDER BY EDIT_DISTANCE(t.utr, k.utr),
                                               ABS(SECONDS_BETWEEN(t.ts, COALESCE(k.txn_at, k.reported_at))),
                                               t.txn_id) AS rn
            FROM TXN t
            JOIN COMPLAINT k ON k.case_id = {case_id}
                            AND t.to_account = k.beneficiary_account
                            AND t.amount_inr = k.amount_inr
            WHERE t.utr <> k.utr
              AND t.ts <= TO_TIMESTAMP({checked_at}, 'YYYY-MM-DD HH24:MI:SS')
              AND ((COALESCE(k.txn_time_known, TRUE)
                    AND t.ts BETWEEN ADD_MINUTES(COALESCE(k.txn_at, k.reported_at), -120)
                                 AND ADD_MINUTES(COALESCE(k.txn_at, k.reported_at), 120))
                   OR (k.txn_time_known = FALSE AND CAST(t.ts AS DATE) = CAST(k.txn_at AS DATE)))
        ) x
        WHERE x.rn = 1
    ) n ON 1 = 1
    WHERE c.case_id = {case_id}
) m;