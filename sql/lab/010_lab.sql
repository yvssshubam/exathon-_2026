-- 010_lab.sql — the AMLworld test lab (Module 9; pipeline/lab.py). Schema PH_LAB is built like a bank's schema (the
-- same sql/bank/ files), then this file points its transactions and accounts at the AMLworld data in PH_AML, as one
-- bank that owns every account. Then the same trace and attribution SQL the agent runs (sql/steps/040, 050) runs
-- over every labelled laundering pattern. Read-only on PH_AML. Run after `make data`.

DROP TABLE IF EXISTS TXN;
DROP TABLE IF EXISTS ACCOUNT;
DROP TABLE IF EXISTS BANK;

CREATE OR REPLACE VIEW BANK AS
SELECT CAST('LAB' AS VARCHAR(20)) AS bank_id, CAST('AMLworld lab' AS VARCHAR(100)) AS bank_name,
       TRUE AS is_own_bank
FROM DUAL
COMMENT IS 'One bank owning every AMLworld account, so the trace follows every hop.';

CREATE OR REPLACE VIEW ACCOUNT AS
SELECT a.account_id, CAST('LAB' AS VARCHAR(20)) AS bank_id, a.holder_name, a.opened_on, a.account_type,
       a.holder_type, a.profile_band, a.history_profile, a.customer_id,
       CAST(NULL AS VARCHAR(11)) AS ifsc, CAST(NULL AS VARCHAR(20)) AS mobile, CAST(NULL AS VARCHAR(100)) AS email,
       CAST(NULL AS VARCHAR(200)) AS address, CAST(NULL AS VARCHAR(20)) AS pan
FROM PH_AML.ACCOUNT a
COMMENT IS 'AMLworld accounts, all at the lab bank.';

CREATE OR REPLACE VIEW TXN AS
SELECT t.txn_id, t.utr, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit, t.exit_identifier,
       t.data_source
FROM PH_AML.TXN t
COMMENT IS 'AMLworld transactions (the laundering labels stay in PH_AML.TXN).';

-- Views that read TXN were built over the table that is gone: build them again over the view.
CREATE OR REPLACE VIEW TXN_LIVE AS
SELECT t.* FROM TXN t
COMMENT IS 'In the lab nothing is ever refused: every AMLworld transaction went through.';

CREATE OR REPLACE VIEW ROOT_PAYMENT (utr, case_id, victim_tag, tagged_inr, reported_at) AS
SELECT COALESCE(t.utr, c.utr), MIN(c.case_id), MIN(c.victim_tag), MIN(COALESCE(c.amount_inr, 999999999999.99)),
       MIN(c.reported_at)
FROM COMPLAINT c
LEFT JOIN INTAKE_CHECK i ON i.case_id = c.case_id
LEFT JOIN TXN t ON t.txn_id = i.matched_txn_id
GROUP BY COALESCE(t.utr, c.utr)
COMMENT IS 'The root payment of each laundering pattern under test, tagged as one victim''s money.';

CREATE OR REPLACE TABLE LAB_RESULT (
    run_at        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT IS 'When the test ran',
    test_name     VARCHAR(40)   NOT NULL COMMENT IS 'trace, data_check, scoring or officer_share',
    pattern_id    DECIMAL(9,0)           COMMENT IS 'AMLworld laundering attempt',
    pattern_type  VARCHAR(40)            COMMENT IS 'FAN-OUT, FAN-IN, CYCLE, SCATTER-GATHER, GATHER-SCATTER, STACK, BIPARTITE, RANDOM',
    passed        BOOLEAN                COMMENT IS 'Pass or fail',
    detail        VARCHAR(2000)          COMMENT IS 'The numbers behind it',
    stmt_id       DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Module 9 results: one row per pattern per test, plus the summary rows.';