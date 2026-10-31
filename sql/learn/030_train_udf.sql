-- 030_train_udf.sql — the learned weights' trainer as an Exasol Python 3 SET script (DECISIONS D-58). The Python lives
-- in sql/udf/train_weights.py so CI can test it without Exasol; pipeline/run_sql.py pastes it in at --@include.
-- pipeline/weights.py loads PH_LEARN.W_CONFIG (the signals' low and high points and starting weights), builds
-- PH_LEARN.W_ROWS (one row per account, score and signal, with its m value) and calls this once over all of it.

CREATE OR REPLACE PYTHON3 SET SCRIPT PH_LEARN.TRAIN_SCORING_WEIGHTS(
    account_id   VARCHAR(100),
    label        DECIMAL(1,0),
    score_name   VARCHAR(20),
    signal_name  VARCHAR(40),
    m_value      DOUBLE,
    start_weight DOUBLE,
    seed         DECIMAL(9,0),
    test_share   DOUBLE,
    l2           DOUBLE,
    weight_min   DOUBLE,
    weight_max   DOUBLE
) EMITS (
    score_name      VARCHAR(20),
    signal_name     VARCHAR(40),
    coefficient     DOUBLE,
    weight          DOUBLE,
    start_weight    DOUBLE,
    test_auc        DOUBLE,
    test_precision  DOUBLE,
    start_auc       DOUBLE,
    start_precision DOUBLE,
    train_accounts  DECIMAL(9,0),
    test_accounts   DECIMAL(9,0)
) AS
--@include sql/udf/train_weights.py
/