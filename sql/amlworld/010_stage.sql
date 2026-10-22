-- 010_stage.sql — schema PH_AML: AMLworld HI-Small as downloaded, then mapped (run by `make data` only).
-- PH_AML is never touched by `make db` or `make demo-reset`, so the 5 million rows are imported once.
-- `make seed` copies PH_AML.BANK, ACCOUNT and TXN into PH next to the cases (pipeline/load_cases.py).
-- Raw columns are text: bank codes keep their leading zeros and a Windows line ending cannot break a number.

CREATE SCHEMA IF NOT EXISTS PH_AML;

CREATE OR REPLACE TABLE PH_AML.TXN_RAW (
    ts_raw             VARCHAR(20)  COMMENT IS 'Timestamp as in the file, YYYY/MM/DD HH24:MI',
    from_bank          VARCHAR(20)  COMMENT IS 'From Bank',
    from_acc           VARCHAR(40)  COMMENT IS 'Account (sender)',
    to_bank            VARCHAR(20)  COMMENT IS 'To Bank',
    to_acc             VARCHAR(40)  COMMENT IS 'Account (receiver)',
    amount_received    VARCHAR(40)  COMMENT IS 'Amount Received',
    receiving_currency VARCHAR(40)  COMMENT IS 'Receiving Currency',
    amount_paid        VARCHAR(40)  COMMENT IS 'Amount Paid',
    payment_currency   VARCHAR(40)  COMMENT IS 'Payment Currency',
    payment_format     VARCHAR(40)  COMMENT IS 'Payment Format: ACH, Wire, Cheque, Credit Card, Cash, Bitcoin, Reinvestment',
    is_laundering      VARCHAR(10)  COMMENT IS 'Is Laundering: 1 or 0'
) COMMENT IS 'HI-Small_Trans.csv exactly as downloaded (native IMPORT, header skipped).';

CREATE OR REPLACE TABLE PH_AML.ACCOUNT_RAW (
    bank_name   VARCHAR(100) COMMENT IS 'Bank Name, e.g. Portugal Bank #4507',
    bank_code   VARCHAR(20)  COMMENT IS 'Bank ID',
    account_no  VARCHAR(40)  COMMENT IS 'Account Number',
    entity_id   VARCHAR(40)  COMMENT IS 'Entity ID: the customer; one entity can hold several accounts',
    entity_name VARCHAR(100) COMMENT IS 'Entity Name, e.g. Corporation #33520'
) COMMENT IS 'HI-Small_accounts.csv exactly as downloaded (native IMPORT, header skipped).';

CREATE OR REPLACE TABLE PH_AML.PATTERN_RAW (
    pattern_id     DECIMAL(9,0) COMMENT IS 'Laundering attempt number, in file order',
    pattern_type   VARCHAR(40)  COMMENT IS 'FAN-OUT, FAN-IN, CYCLE, GATHER-SCATTER, SCATTER-GATHER, BIPARTITE, STACK or RANDOM',
    pattern_detail VARCHAR(200) COMMENT IS 'Rest of the BEGIN line, e.g. Max 12-degree Fan-Out',
    ts_raw         VARCHAR(20)  COMMENT IS 'Timestamp as in the file',
    from_bank      VARCHAR(20)  COMMENT IS 'From Bank',
    from_acc       VARCHAR(40)  COMMENT IS 'Account (sender)',
    to_bank        VARCHAR(20)  COMMENT IS 'To Bank',
    to_acc         VARCHAR(40)  COMMENT IS 'Account (receiver)',
    amount_paid    VARCHAR(40)  COMMENT IS 'Amount Paid',
    payment_format VARCHAR(40)  COMMENT IS 'Payment Format'
) COMMENT IS 'HI-Small_Patterns.txt: one row per transaction of each labelled laundering attempt.';

CREATE OR REPLACE TABLE PH_AML.CHANNEL_MAP (
    payment_format VARCHAR(40) NOT NULL COMMENT IS 'AMLworld Payment Format',
    channel        VARCHAR(10)          COMMENT IS 'Channel in PH.TXN; NULL = the row is not loaded'
) COMMENT IS 'config/amlworld.yaml channels.';