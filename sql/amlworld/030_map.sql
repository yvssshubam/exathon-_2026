-- 030_map.sql — AMLworld in the shape of PH.BANK, PH.ACCOUNT and PH.TXN (run by `make data` only).
-- Parameters (pipeline/load_amlworld.py, from config/amlworld.yaml): last_day, usd_to_inr (number)
--
--   * IDs: accounts AML_<bank>_<account>, banks AML_<bank>, transactions AML<rn>, UTRs AMLW<rn>. No case ID starts
--     with AML, so nothing collides with the cases.
--   * Time: every row moves by the same number of days, so the last AMLworld day is last_day (DECISIONS D-37).
--   * Money: Amount Paid x dollars per unit (PH_AML.FX_RATE) x usd_to_inr, in rupees.
--   * Channels: PH_AML.CHANNEL_MAP. Reinvestment (an account paying itself) and any self-transfer are not loaded.
--   * Accounts: everything in the accounts file plus any account the transactions name that the file lacks.
--     AMLworld has no opening dates or declared income, so opened_on is spread over 1 to 10 years before last_day
--     (fixed per account) and profile_band is medium for all: the signals built on them stay neutral (D-37).

CREATE OR REPLACE TABLE PH_AML.BANK (
    bank_id      VARCHAR(20)  NOT NULL COMMENT IS 'AML_<Bank ID>',
    bank_name    VARCHAR(100) NOT NULL COMMENT IS 'Bank Name from the accounts file',
    is_own_bank  BOOLEAN      NOT NULL COMMENT IS 'Always FALSE: AMLworld banks are other banks'
) COMMENT IS 'AMLworld banks in the shape of PH.BANK.';

CREATE OR REPLACE TABLE PH_AML.ACCOUNT (
    account_id      VARCHAR(40)  NOT NULL COMMENT IS 'AML_<bank>_<account>',
    bank_id         VARCHAR(20)  NOT NULL COMMENT IS 'AML_<bank>',
    holder_name     VARCHAR(100)          COMMENT IS 'Entity Name, e.g. Corporation #33520',
    opened_on       DATE         NOT NULL COMMENT IS 'Synthetic: 1 to 10 years before last_day (AMLworld has none)',
    account_type    VARCHAR(10)  NOT NULL COMMENT IS 'current for companies and sole proprietors, savings otherwise',
    holder_type     VARCHAR(20)  NOT NULL COMMENT IS 'merchant (sole proprietorship), business (corporation, partnership) or individual',
    profile_band    VARCHAR(10)  NOT NULL COMMENT IS 'medium for all (AMLworld has no declared income)',
    history_profile VARCHAR(30)           COMMENT IS 'amlworld',
    customer_id     VARCHAR(20)           COMMENT IS 'Entity ID: accounts of one entity share it'
) COMMENT IS 'AMLworld accounts in the shape of PH.ACCOUNT.';

CREATE OR REPLACE TABLE PH_AML.TXN (
    txn_id          VARCHAR(40)   NOT NULL COMMENT IS 'AML<rn>',
    utr             VARCHAR(40)   NOT NULL COMMENT IS 'AMLW<rn>',
    ts              TIMESTAMP     NOT NULL COMMENT IS 'File time moved forward so the last day is last_day',
    from_account    VARCHAR(40)   NOT NULL COMMENT IS 'AML_<From Bank>_<Account>',
    to_account      VARCHAR(80)   NOT NULL COMMENT IS 'AML_<To Bank>_<Account>',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Amount Paid converted to rupees',
    channel         VARCHAR(10)   NOT NULL COMMENT IS 'From Payment Format via PH_AML.CHANNEL_MAP',
    is_exit         BOOLEAN       NOT NULL COMMENT IS 'Always FALSE: every AMLworld row names a receiving account',
    exit_identifier VARCHAR(200)           COMMENT IS 'Always NULL',
    data_source     VARCHAR(20)   NOT NULL COMMENT IS 'amlworld',
    payment_format  VARCHAR(40)   NOT NULL COMMENT IS 'Payment Format as in the file',
    is_laundering   BOOLEAN       NOT NULL COMMENT IS 'AMLworld label: TRUE when the row is part of laundering',
    DISTRIBUTE BY from_account
) COMMENT IS 'AMLworld transactions in the shape of PH.TXN, plus the file''s payment format and laundering label.';

INSERT INTO PH_AML.TXN (txn_id, utr, ts, from_account, to_account, amount_inr, channel, is_exit, exit_identifier,
                        data_source, payment_format, is_laundering)
SELECT 'AML'  || LPAD(CAST(k.rn AS VARCHAR(12)), 9, '0'),
       'AMLW' || LPAD(CAST(k.rn AS VARCHAR(12)), 10, '0'),
       ADD_DAYS(TO_TIMESTAMP(k.ts_raw, 'YYYY/MM/DD HH24:MI'), s.shift_days),
       'AML_' || k.from_bank || '_' || k.from_acc,
       'AML_' || k.to_bank || '_' || k.to_acc,
       CAST(ROUND(CAST(k.amount_paid AS DOUBLE) * f.usd_per_unit * {usd_to_inr!d}, 2) AS DECIMAL(18,2)),
       m.channel,
       FALSE,
       CAST(NULL AS VARCHAR(200)),
       'amlworld',
       k.payment_format,
       CASE WHEN k.is_laundering = '1' THEN TRUE ELSE FALSE END
FROM PH_AML.TXN_KEYED k
JOIN PH_AML.CHANNEL_MAP m ON m.payment_format = k.payment_format AND m.channel IS NOT NULL
JOIN PH_AML.FX_RATE f ON f.currency_name = k.payment_currency
CROSS JOIN (
    SELECT DAYS_BETWEEN(TO_DATE({last_day}, 'YYYY-MM-DD'),
                        MAX(TO_DATE(SUBSTR(ts_raw, 1, 10), 'YYYY/MM/DD'))) AS shift_days
    FROM PH_AML.TXN_KEYED
) s
WHERE NOT (k.from_bank = k.to_bank AND k.from_acc = k.to_acc);

INSERT INTO PH_AML.ACCOUNT (account_id, bank_id, holder_name, opened_on, account_type, holder_type, profile_band,
                            history_profile, customer_id)
SELECT a.account_id,
       a.bank_id,
       a.holder_name,
       ADD_DAYS(TO_DATE({last_day}, 'YYYY-MM-DD'), -(365 + MOD(a.n * 7919, 3286))),
       CASE WHEN a.holder_type IN ('merchant', 'business') THEN 'current' ELSE 'savings' END,
       a.holder_type,
       'medium',
       'amlworld',
       a.entity_id
FROM (
    SELECT u.account_id,
           u.bank_id,
           u.holder_name,
           u.entity_id,
           CASE WHEN u.holder_name LIKE 'Sole Proprietorship%' THEN 'merchant'
                WHEN u.holder_name LIKE 'Corporation%' OR u.holder_name LIKE 'Partnership%' THEN 'business'
                ELSE 'individual' END                              AS holder_type,
           ROW_NUMBER() OVER (ORDER BY u.account_id)               AS n
    FROM (
        SELECT x.account_id, MAX(x.bank_id) AS bank_id, MAX(x.holder_name) AS holder_name,
               MAX(x.entity_id) AS entity_id
        FROM (
            SELECT 'AML_' || b.bank || '_' || b.account_no AS account_id, 'AML_' || b.bank AS bank_id,
                   b.entity_name AS holder_name, b.entity_id
            FROM (
                SELECT COALESCE(LTRIM(TRIM(bank_code), '0'), '0') AS bank,
                       TRIM(account_no)                              AS account_no,
                       TRIM(REPLACE(entity_name, CHR(13)))       AS entity_name,
                       TRIM(entity_id)                               AS entity_id
                FROM PH_AML.ACCOUNT_RAW
            ) b
            UNION ALL
            SELECT from_account, 'AML_' || SUBSTR(from_account, 5, INSTR(from_account, '_', 5) - 5),
                   CAST(NULL AS VARCHAR(100)), CAST(NULL AS VARCHAR(40))
            FROM PH_AML.TXN
            UNION ALL
            SELECT to_account, 'AML_' || SUBSTR(to_account, 5, INSTR(to_account, '_', 5) - 5),
                   CAST(NULL AS VARCHAR(100)), CAST(NULL AS VARCHAR(40))
            FROM PH_AML.TXN
        ) x
        GROUP BY x.account_id
    ) u
) a;

INSERT INTO PH_AML.BANK (bank_id, bank_name, is_own_bank)
SELECT x.bank_id, COALESCE(MAX(x.bank_name), 'AMLworld bank ' || SUBSTR(x.bank_id, 5)), FALSE
FROM (
    SELECT 'AML_' || COALESCE(LTRIM(TRIM(bank_code), '0'), '0') AS bank_id,
           TRIM(bank_name) AS bank_name
    FROM PH_AML.ACCOUNT_RAW
    UNION ALL
    SELECT DISTINCT bank_id, CAST(NULL AS VARCHAR(100)) FROM PH_AML.ACCOUNT
) x
GROUP BY x.bank_id;

-- Which loaded transactions belong to which labelled laundering attempt (empty when no patterns file).
CREATE OR REPLACE TABLE PH_AML.PATTERN_TXN AS
SELECT DISTINCT p.pattern_id,
       p.pattern_type,
       p.pattern_detail,
       'AML' || LPAD(CAST(k.rn AS VARCHAR(12)), 9, '0') AS txn_id
FROM (
    SELECT pattern_id, pattern_type, pattern_detail, TRIM(ts_raw) AS ts_raw,
           COALESCE(LTRIM(TRIM(from_bank), '0'), '0') AS from_bank,
           TRIM(from_acc) AS from_acc,
           COALESCE(LTRIM(TRIM(to_bank), '0'), '0')     AS to_bank,
           TRIM(to_acc) AS to_acc, TRIM(amount_paid) AS amount_paid, TRIM(payment_format) AS payment_format
    FROM PH_AML.PATTERN_RAW
) p
JOIN PH_AML.TXN_KEYED k
  ON  k.ts_raw = p.ts_raw AND k.from_bank = p.from_bank AND k.from_acc = p.from_acc
  AND k.to_bank = p.to_bank AND k.to_acc = p.to_acc AND k.amount_paid = p.amount_paid
  AND k.payment_format = p.payment_format;

COMMENT ON TABLE PH_AML.PATTERN_TXN IS 'Transactions of each labelled laundering attempt in HI-Small_Patterns.txt (FAN-OUT, CYCLE, ...).';