-- 055_score_target.sql — which accounts the evidence step (060) reads for a ticket: the trail's own accounts at
-- this bank. Another bank's accounts are never scored here: their history is that bank's, and this bank only sees
-- the payments its own customers made to them. Parameters: case_id, own_bank_id.

DELETE FROM SCORE_TARGET WHERE case_id = {case_id};

INSERT INTO SCORE_TARGET (case_id, account_id)
SELECT DISTINCT {case_id}, e.to_account
FROM TRACE_EDGE e
JOIN ACCOUNT a ON a.account_id = e.to_account AND a.bank_id = {own_bank_id}
WHERE e.case_id = {case_id}
  AND NOT e.is_exit;