-- 020_rates.sql — number the raw rows and read the exchange rates out of the file (run by `make data` only).
-- Parameters: none.
--
-- TXN_KEYED: raw rows cleaned (no stray carriage return, bank codes without leading zeros: the transactions file
-- writes 010 where the accounts file writes 10) and numbered in a fixed order, so transaction IDs are the same on
-- every reload of the same file.
-- FX_RATE: US dollars per unit of each currency, the median over the rows that convert to or from dollars.
-- pipeline/load_amlworld.py stops if a currency in the file has no rate here.

CREATE OR REPLACE TABLE PH_AML.TXN_KEYED AS
SELECT ROW_NUMBER() OVER (ORDER BY k.ts_raw, k.from_bank, k.from_acc, k.to_bank, k.to_acc, k.amount_paid,
                                   k.payment_currency, k.amount_received, k.receiving_currency, k.payment_format,
                                   k.is_laundering) AS rn,
       k.*
FROM (
    SELECT TRIM(REPLACE(ts_raw, CHR(13)))                                          AS ts_raw,
           COALESCE(LTRIM(TRIM(from_bank), '0'), '0') AS from_bank,
           TRIM(from_acc)                                                               AS from_acc,
           COALESCE(LTRIM(TRIM(to_bank), '0'), '0')     AS to_bank,
           TRIM(to_acc)                                                                 AS to_acc,
           TRIM(amount_received)                                                        AS amount_received,
           TRIM(receiving_currency)                                                     AS receiving_currency,
           TRIM(amount_paid)                                                            AS amount_paid,
           TRIM(payment_currency)                                                       AS payment_currency,
           TRIM(payment_format)                                                         AS payment_format,
           TRIM(REPLACE(is_laundering, CHR(13)))                                    AS is_laundering
    FROM PH_AML.TXN_RAW
) k;

COMMENT ON TABLE PH_AML.TXN_KEYED IS 'AMLworld rows cleaned and numbered (rn) in a fixed order; transaction IDs come from rn.';

CREATE OR REPLACE TABLE PH_AML.FX_RATE AS
SELECT r.currency_name,
       CAST(MEDIAN(r.usd_per_unit) AS DOUBLE) AS usd_per_unit,
       COUNT(*)                               AS sample_rows
FROM (
    SELECT payment_currency AS currency_name,
           CAST(amount_received AS DOUBLE) / CAST(amount_paid AS DOUBLE) AS usd_per_unit
    FROM PH_AML.TXN_KEYED
    WHERE receiving_currency = 'US Dollar' AND CAST(amount_paid AS DOUBLE) > 0
    UNION ALL
    SELECT receiving_currency,
           CAST(amount_paid AS DOUBLE) / CAST(amount_received AS DOUBLE)
    FROM PH_AML.TXN_KEYED
    WHERE payment_currency = 'US Dollar' AND CAST(amount_received AS DOUBLE) > 0
) r
GROUP BY r.currency_name;

COMMENT ON TABLE PH_AML.FX_RATE IS 'US dollars per unit of each AMLworld currency, read from the file (median over dollar conversions).';