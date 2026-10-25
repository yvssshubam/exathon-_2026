-- 050_attribute_udf.sql — the attribution engine as an Exasol Python 3 SET script (requirements s9 step 2).
-- The Python lives in sql/udf/attribute_case.py so CI can test it without Exasol (DECISIONS D-10);
-- pipeline/run_sql.py pastes it in at the --@include line. Called once per tracing rule by sql/steps/050_attribute.sql.

CREATE OR REPLACE PYTHON3 SET SCRIPT ATTRIBUTE_CASE(
    trace_rule   VARCHAR(10),
    row_kind     VARCHAR(5),
    ts           TIMESTAMP,
    txn_id       VARCHAR(40),
    from_account VARCHAR(40),
    to_account   VARCHAR(80),
    amount_paise DECIMAL(18,0),
    victim_tag   VARCHAR(20)
) EMITS (
    trace_rule   VARCHAR(10),
    row_kind     VARCHAR(5),
    ref_id          VARCHAR(80),
    component    VARCHAR(40),
    paise        DECIMAL(18,0)
) AS
--@include sql/udf/attribute_case.py
/

COMMENT ON SCRIPT ATTRIBUTE_CASE IS 'Replays a case''s transactions under LIBR, FIFO or PRO_RATA and returns how much of each victim''s money is in each account, which exit it left by, and which transaction carried it. Integer paise; conserves money exactly.';