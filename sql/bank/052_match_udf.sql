-- 052_match_udf.sql — per-credit matching for the pass-through signals P2 and P3 and the E4 check (DECISIONS D-57),
-- as an Exasol Python 3 SET script. The Python lives in sql/udf/match_credits.py so CI can test it without Exasol;
-- pipeline/run_sql.py pastes it in at the --@include line. Called once per account by sql/steps/060_score.sql.

CREATE OR REPLACE PYTHON3 SET SCRIPT MATCH_CREDITS(
    account_id   VARCHAR(40),
    direction    CHAR(1),
    ts           TIMESTAMP,
    txn_id       VARCHAR(40),
    amount_paise DECIMAL(18,0),
    exit_kind    VARCHAR(4),
    fraud_linked BOOLEAN
) EMITS (
    account_id   VARCHAR(40),
    txn_id       VARCHAR(40),
    amount_paise DECIMAL(18,0),
    fraud_linked BOOLEAN,
    out_paise    DECIMAL(18,0),
    cash_paise   DECIMAL(18,0)
) AS
--@include sql/udf/match_credits.py
/

COMMENT ON SCRIPT MATCH_CREDITS IS 'Gives each debit to the earliest credit of the previous 60 minutes that still has room, so no debit is counted twice. Returns, per credit, how much left within the hour and how much of that was cash or crypto.';