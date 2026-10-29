-- 010_portal.sql — the simulated CFCFRMS portal (schema PH_PORTAL). pipeline/run_sql.py runs it after
-- OPEN SCHEMA PH_PORTAL. The portal is the only link between banks: police tickets come in here, banks' filings
-- (onward entries, exits, KYC packets, send-backs) are collected here, and an onward entry becomes the next bank's
-- ticket here. Only the portal service's database user (PH_PORTAL_SVC) can read it; no bank can.

CREATE OR REPLACE TABLE TICKET (
    ticket_id           DECIMAL(18,0) IDENTITY COMMENT IS 'Portal ticket number',
    case_id             VARCHAR(40)   NOT NULL COMMENT IS 'Case ID at the receiving bank',
    to_bank_id          VARCHAR(20)   NOT NULL COMMENT IS 'Bank the ticket is for',
    origin              VARCHAR(20)   NOT NULL COMMENT IS 'POLICE, or the bank whose onward entry made it',
    parent_case_id      VARCHAR(40)            COMMENT IS 'For an onward ticket: the case at the bank that filed the onward entry',
    source_entry_id     DECIMAL(18,0)          COMMENT IS 'For an onward ticket: the portal entry it came from (ENTRY)',
    ack_no              VARCHAR(14)   NOT NULL COMMENT IS 'NCRP acknowledgement number',
    complaint_source    VARCHAR(10)   NOT NULL COMMENT IS '1930, NCRP, branch, IO or BANK',
    ticket_kind         VARCHAR(10)   NOT NULL COMMENT IS 'POLICE, ONWARD or IO_NOTICE',
    victim_account      VARCHAR(40)            COMMENT IS 'Victim''s account (police tickets only)',
    victim_tag          VARCHAR(20)   NOT NULL COMMENT IS 'Victim label carried along the trail',
    utr                 VARCHAR(40)   NOT NULL COMMENT IS 'UTR named',
    amount_inr          DECIMAL(18,2)          COMMENT IS 'Reported amount (onward: the victim''s share)',
    txn_at              TIMESTAMP              COMMENT IS 'When the payment was made',
    created_at          TIMESTAMP     NOT NULL COMMENT IS 'When the portal created the ticket (simulated clock)',
    beneficiary_account VARCHAR(40)            COMMENT IS 'Account named as having received the money',
    lane                CHAR(1)       NOT NULL COMMENT IS 'A',
    notice_legal_basis  VARCHAR(100)           COMMENT IS 'Legal provision of the notice',
    notice_has_amount   BOOLEAN       NOT NULL COMMENT IS 'Whether the notice states the amount',
    ordering_authority  VARCHAR(200)           COMMENT IS 'Police unit',
    ordering_reference  VARCHAR(100)           COMMENT IS 'Police reference',
    fir_reference       VARCHAR(100)           COMMENT IS 'FIR number, for a notice after an FIR',
    remarks             VARCHAR(2000)          COMMENT IS 'Free text typed on the ticket',
    corrects_case_id    VARCHAR(40)            COMMENT IS 'A corrected ticket: the case it replaces',
    delivered_at        TIMESTAMP              COMMENT IS 'When the portal put it in the bank''s inbox',
    share_known         BOOLEAN       DEFAULT TRUE COMMENT IS 'FALSE: the onward entry gave no victim share; the amount is the whole transfer (D-17)',
    prev_hash           VARCHAR(64)            COMMENT IS 'Hash of the ticket this one came from (NULL for a police ticket)',
    chain_hash          VARCHAR(64)            COMMENT IS 'SHA-256 over prev_hash and the referral fields: signed by the filing bank, or by the portal (a police ticket, or an entry without a share)'
) COMMENT IS 'Every ticket the portal routed: police tickets, and onward tickets it made from banks'' onward entries.';

CREATE OR REPLACE TABLE ENTRY (
    entry_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Portal entry number',
    from_bank_id      VARCHAR(20)   NOT NULL COMMENT IS 'Bank that filed it',
    bank_entry_id     DECIMAL(18,0) NOT NULL COMMENT IS 'Entry number in that bank''s outbox (PORTAL_ENTRY)',
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case at the filing bank',
    entry_kind        VARCHAR(10)   NOT NULL COMMENT IS 'ONWARD, EXIT, KYC, SEND_BACK or PRE_ALERT',
    from_account      VARCHAR(80)            COMMENT IS 'Account the money left from',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Receiving account, exit, KYC account, or the account a sent-back ticket names',
    bank_id           VARCHAR(20)            COMMENT IS 'Receiving bank (ONWARD)',
    txn_id            VARCHAR(40)            COMMENT IS 'Transaction reported',
    utr               VARCHAR(40)            COMMENT IS 'UTR reported',
    channel           VARCHAR(10)            COMMENT IS 'Channel',
    amount_inr        DECIMAL(18,2)          COMMENT IS 'Amount moved',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'Victim''s share (the next bank''s reported amount)',
    moved_at          TIMESTAMP              COMMENT IS 'When the money moved',
    detail            VARCHAR(4000)          COMMENT IS 'Exit identifiers, KYC packet or send-back letter',
    filed_at          TIMESTAMP              COMMENT IS 'When the bank filed it (simulated clock)',
    received_at       TIMESTAMP              COMMENT IS 'When the portal collected it',
    routed_ticket_id  DECIMAL(18,0)          COMMENT IS 'For an onward entry: the ticket it became',
    prev_hash         VARCHAR(64)            COMMENT IS 'ONWARD: the hash the filing bank chained it to',
    chain_hash        VARCHAR(64)            COMMENT IS 'ONWARD: the hash the filing bank signed (NULL: the bank gave none)'
) COMMENT IS 'Everything banks filed, as the portal received it. The police see the whole trail here; no bank does.';

CREATE OR REPLACE TABLE ROUTE_LOG (
    event_id     DECIMAL(18,0) IDENTITY COMMENT IS 'Event number',
    event_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT IS 'Real time',
    sim_at       TIMESTAMP              COMMENT IS 'Simulated clock',
    event_name   VARCHAR(60)   NOT NULL COMMENT IS 'collected, routed, delivered, not_routed',
    detail       VARCHAR(2000)          COMMENT IS 'In plain words'
) COMMENT IS 'What the portal did, for the narrator''s view.';

CREATE OR REPLACE TABLE SIM_CLOCK (
    clock_at     TIMESTAMP     NOT NULL COMMENT IS 'How far the simulated night has run',
    started_at   TIMESTAMP     NOT NULL COMMENT IS 'Where it started',
    updated_at   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT IS 'Real time of the last run'
) COMMENT IS 'The simulation''s clock (pipeline/night.py): one row. Not part of CFCFRMS; kept here because every bank shares it.';