-- 084_kyc.sql — tool draft_kyc_packet: draft the KYC packet (name, address, PAN, customer ID, IFSC, mobile, e-mail,
-- how many other accounts the customer has here) for every own-bank account the agent held or marked. Due on CFCFRMS
-- within a week (SOP 6.1(k)); the officer sends it, so it stays a DRAFT. All KYC data is synthetic.
-- Parameters: case_id, own_bank_id, placed_at.
-- Kept to plain SQL on purpose (typed NULLs, CAST, a joined count): the first version used GROUP_CONCAT and TO_CHAR.

INSERT INTO PH.PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, bank_id, txn_id, utr,
                             channel, amount_inr, victim_amount_inr, moved_at, detail, entry_status, filed_at,
                             filed_by, stmt_id)
SELECT h.case_id,
       h.viewing_bank_id,
       'KYC',
       CAST(NULL AS VARCHAR(80)),
       acc.account_id,
       acc.bank_id,
       CAST(NULL AS VARCHAR(40)),
       CAST(NULL AS VARCHAR(40)),
       CAST(NULL AS VARCHAR(10)),
       CAST(NULL AS DECIMAL(18,2)),
       CAST(NULL AS DECIMAL(18,2)),
       CAST(NULL AS TIMESTAMP),
       'Name: ' || COALESCE(acc.holder_name, '-')
           || ' | Customer ID: ' || COALESCE(acc.customer_id, '-')
           || ' | Address: ' || COALESCE(acc.address, '-')
           || ' | PAN: ' || COALESCE(acc.pan, '-')
           || ' | IFSC: ' || COALESCE(acc.ifsc, '-')
           || ' | Mobile: ' || COALESCE(acc.mobile, '-')
           || ' | E-mail: ' || COALESCE(acc.email, '-')
           || ' | Account opened: ' || CAST(acc.opened_on AS VARCHAR(10))
           || ' | Other accounts of this customer here: ' || CAST(COALESCE(o.n_other, 0) AS VARCHAR(5)),
       'DRAFT',
       TO_TIMESTAMP({placed_at}, 'YYYY-MM-DD HH24:MI:SS'),
       'agent',
       CURRENT_STATEMENT
FROM (SELECT DISTINCT case_id, viewing_bank_id, account_id
      FROM PH.HOLD_LEDGER
      WHERE case_id = {case_id} AND viewing_bank_id = {own_bank_id} AND placed_by = 'agent') h
JOIN PH.ACCOUNT acc ON acc.account_id = h.account_id
LEFT JOIN (SELECT a1.account_id, COUNT(a2.account_id) AS n_other
           FROM PH.ACCOUNT a1
           LEFT JOIN PH.ACCOUNT a2 ON a2.customer_id = a1.customer_id AND a2.account_id <> a1.account_id
                                  AND a2.bank_id = a1.bank_id
           -- only the held accounts: PH.ACCOUNT also holds the 5 lakh AMLworld background accounts (D-37)
           WHERE a1.account_id IN (SELECT account_id FROM PH.HOLD_LEDGER
                                   WHERE case_id = {case_id} AND placed_by = 'agent')
           GROUP BY a1.account_id) o ON o.account_id = acc.account_id
WHERE NOT EXISTS (SELECT 1 FROM PH.PORTAL_ENTRY p
                  WHERE p.case_id = h.case_id AND p.viewing_bank_id = h.viewing_bank_id
                    AND p.entry_kind = 'KYC' AND p.account_id = h.account_id);