-- 010_schema.sql — schema PH (Proportionate Holds). Requirements section 8.
-- Idempotent: `make db` rebuilds everything from scratch (CREATE OR REPLACE). `make seed` reloads the cases.
-- Every table and column has a COMMENT so the Exasol MCP server can describe them (requirements s7).
-- Money is DECIMAL(18,2) rupees (NFR-03). Times are IST, stored as TIMESTAMP without zone.
-- Column names avoid SQL keywords on purpose (e.g. trace_rule not rule, action_code not action).

CREATE SCHEMA IF NOT EXISTS PH;
CREATE SCHEMA IF NOT EXISTS PH_STAGE;
OPEN SCHEMA PH;

CREATE OR REPLACE TABLE PH.BANK (
    bank_id      VARCHAR(20)  NOT NULL COMMENT IS 'Bank identifier, e.g. BANK_X',
    bank_name    VARCHAR(100) NOT NULL COMMENT IS 'Display name',
    is_own_bank  BOOLEAN      NOT NULL COMMENT IS 'TRUE for the bank running the tool; its accounts can be held directly, others get an outbound referral',
    CONSTRAINT pk_bank PRIMARY KEY (bank_id) DISABLE
) COMMENT IS 'Banks present in the synthetic data. Exactly one has is_own_bank = TRUE.';

CREATE OR REPLACE TABLE PH.ACCOUNT (
    account_id      VARCHAR(40)  NOT NULL COMMENT IS 'Account identifier (demo: ACC_A, ACC_B, ACC_C, ACC_D)',
    bank_id         VARCHAR(20)  NOT NULL COMMENT IS 'Bank holding the account, see PH.BANK',
    holder_name     VARCHAR(100)          COMMENT IS 'Synthetic holder name',
    opened_on       DATE         NOT NULL COMMENT IS 'Account opening date; feeds the account-age signal',
    account_type    VARCHAR(10)  NOT NULL COMMENT IS 'savings, current, nodal, pool or escrow. Nodal, pool and escrow accounts are never fully held (SOP 9.1(i), 9.1(iii))',
    holder_type     VARCHAR(20)  NOT NULL COMMENT IS 'individual, merchant or business',
    profile_band    VARCHAR(10)  NOT NULL COMMENT IS 'Declared income/turnover band: low, medium, high; feeds the credit-vs-profile signal',
    history_profile VARCHAR(30)           COMMENT IS 'Which synthetic history the generator gives this account (fast_pass_through, merchant_300_per_day, none)',
    CONSTRAINT pk_account PRIMARY KEY (account_id) DISABLE
) COMMENT IS 'Accounts with synthetic KYC and profile. Exit pseudo-accounts (EXIT:...) are never in this table.';

CREATE OR REPLACE TABLE PH.TXN (
    txn_id          VARCHAR(40)   NOT NULL COMMENT IS 'Transaction identifier',
    utr             VARCHAR(40)   NOT NULL COMMENT IS 'Unique transaction reference as printed on statements; complaints point at a UTR',
    ts              TIMESTAMP     NOT NULL COMMENT IS 'When the money moved (IST)',
    from_account    VARCHAR(40)   NOT NULL COMMENT IS 'Debited account',
    to_account      VARCHAR(80)   NOT NULL COMMENT IS 'Credited account, or an exit pseudo-account EXIT:<channel>:<id> when money left banking',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Amount in rupees',
    channel         VARCHAR(10)   NOT NULL COMMENT IS 'IMPS, UPI, NEFT, RTGS, ATM, CARD, CRYPTO or CASH',
    is_exit         BOOLEAN       NOT NULL COMMENT IS 'TRUE when the money left the banking system (ATM, cash, crypto, card). Exits are recorded, never held',
    exit_identifier VARCHAR(200)           COMMENT IS 'ATM ID, device, wallet or merchant terminal for an exit (SOP 9.1(i))',
    data_source     VARCHAR(20)   NOT NULL COMMENT IS 'demo_case, extra_cases, loop_case, history or amlworld',
    CONSTRAINT pk_txn PRIMARY KEY (txn_id) DISABLE,
    DISTRIBUTE BY from_account
) COMMENT IS 'Every money movement. Distributed by from_account so the forward trace joins locally.';

CREATE OR REPLACE TABLE PH.BALANCE_SNAPSHOT (
    account_id  VARCHAR(40)   NOT NULL COMMENT IS 'Account',
    ts          TIMESTAMP     NOT NULL COMMENT IS 'Balance is as of this moment',
    balance_inr DECIMAL(18,2) NOT NULL COMMENT IS 'Balance in rupees; the opening balance for the attribution replay (LIBR)'
) COMMENT IS 'Known balances. The attribution replay starts from the latest snapshot before the window.';

CREATE OR REPLACE TABLE PH.COMPLAINT (
    case_id             VARCHAR(20)   NOT NULL COMMENT IS 'Case identifier (demo: CASE_DEMO)',
    ack_no              VARCHAR(14)   NOT NULL COMMENT IS 'NCRP acknowledgement number, 14 digits; starts with 2 (NCRP) or 3 (1930)',
    complaint_source    VARCHAR(10)   NOT NULL COMMENT IS '1930, NCRP or branch',
    victim_account      VARCHAR(40)   NOT NULL COMMENT IS 'Victim''s debited account. Never shown on a receiver''s notice (SOP 9.1(ii))',
    victim_tag          VARCHAR(20)   NOT NULL COMMENT IS 'Label used to track this victim''s money through mixed accounts',
    utr                 VARCHAR(40)   NOT NULL COMMENT IS 'UTR of the victim''s payment; the trace starts here',
    amount_inr          DECIMAL(18,2)          COMMENT IS 'Amount reported; NULL makes the notice incomplete (FR-02)',
    txn_at              TIMESTAMP              COMMENT IS 'When the victim paid',
    reported_at         TIMESTAMP     NOT NULL COMMENT IS 'When the ticket reached this bank',
    beneficiary_account VARCHAR(40)            COMMENT IS 'First account that received the victim''s money',
    lane                CHAR(1)       NOT NULL COMMENT IS 'A = police-instructed hold (MHA SOP, in force); B = bank''s own suspicion (RBI draft, not in force)',
    notice_legal_basis  VARCHAR(100)           COMMENT IS 'Legal provision cited by the notice; NULL makes the notice incomplete (FR-02)',
    notice_has_amount   BOOLEAN       NOT NULL COMMENT IS 'Whether the notice states the disputed amount',
    ordering_authority  VARCHAR(200)           COMMENT IS 'Police unit that ordered the hold; may be shared with the holder',
    ordering_reference  VARCHAR(100)           COMMENT IS 'Police reference number',
    CONSTRAINT pk_complaint PRIMARY KEY (case_id) DISABLE
) COMMENT IS 'One row per fraud ticket.';

CREATE OR REPLACE TABLE PH.TRACE_EDGE (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case traced',
    hop          DECIMAL(3,0)  NOT NULL COMMENT IS 'Hop number from the victim''s payment (1 = the payment itself)',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Sender on this edge',
    to_account   VARCHAR(80)   NOT NULL COMMENT IS 'Receiver on this edge (or an exit)',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Transaction on this edge',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Full transaction amount; how much of it was the victim''s is in PH.ATTRIBUTION',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'Transaction time',
    is_cycle     BOOLEAN       NOT NULL COMMENT IS 'TRUE when the money came back to an account already on this path',
    is_exit      BOOLEAN       NOT NULL COMMENT IS 'TRUE when the edge leaves the banking system',
    is_own_bank  BOOLEAN       NOT NULL COMMENT IS 'TRUE when the receiving account is at the bank running the tool',
    trail_path   VARCHAR(2000)          COMMENT IS 'Printable path from the victim, accounts separated by >',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row (traceability, NFR-06)'
) COMMENT IS 'Step 1 output: candidate money paths from the complaint''s UTR (CONNECT BY NOCYCLE).';

CREATE OR REPLACE TABLE PH.TRACE_LINK (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case being traced',
    parent_txn   VARCHAR(40)            COMMENT IS 'Transaction the money arrived by; NULL for the victim''s own payment (the root)',
    child_txn    VARCHAR(40)   NOT NULL COMMENT IS 'Later transaction out of the same account that may carry that money on',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Sender of child_txn',
    to_account   VARCHAR(80)   NOT NULL COMMENT IS 'Receiver of child_txn, or an exit pseudo-account',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Amount of child_txn',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'Time of child_txn',
    is_exit      BOOLEAN       NOT NULL COMMENT IS 'TRUE when child_txn leaves the banking system'
) COMMENT IS 'Step 1a: which transaction can follow which (same account, not earlier, within the hop window, before the hold time). CONNECT BY walks this.';

CREATE OR REPLACE TABLE PH.REFERRAL_OUT (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    from_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank sending the referral (the bank using the tool)',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Own-bank account the money left from',
    bank_id      VARCHAR(20)   NOT NULL COMMENT IS 'Other bank that received the money',
    account_id   VARCHAR(40)   NOT NULL COMMENT IS 'Account at the other bank',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Onward transfer',
    utr          VARCHAR(40)   NOT NULL COMMENT IS 'UTR of the onward transfer',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Amount transferred',
    victim_amount_inr DECIMAL(18,2)     COMMENT IS 'How much of it was the victim''s money under trace_rule; NULL = share unknown',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'Rule the victim share was computed with',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'When it moved',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'FR-04: outbound next-hop records (the simulated CFCFRMS trail update), carrying the victim''s share of each amount.';

CREATE OR REPLACE TABLE PH.ATTRIBUTION (
    case_id           VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    trace_rule        VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit pseudo-account',
    victim_tag        VARCHAR(20)   NOT NULL COMMENT IS 'Victim whose money this is',
    victim_amount_inr DECIMAL(18,2) NOT NULL COMMENT IS 'This victim''s money in the account as of the hold time',
    other_victims_inr DECIMAL(18,2) NOT NULL COMMENT IS 'Other victims'' money in the same account',
    own_funds_inr     DECIMAL(18,2) NOT NULL COMMENT IS 'The holder''s own (clean) money in the account',
    balance_now_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Total balance as of the hold time',
    as_of             TIMESTAMP     NOT NULL COMMENT IS 'Replay cut-off: the moment holds are placed (DECISIONS D-06)',
    row_source        VARCHAR(12)   NOT NULL COMMENT IS 'UDF (computed in Exasol by PH.ATTRIBUTE_CASE)',
    session_id        DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2 output: the victim''s traceable money per account under each tracing rule.';

CREATE OR REPLACE TABLE PH.ATTRIBUTION_RAW (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    row_kind     VARCHAR(5)    NOT NULL COMMENT IS 'HOLD (money left in an account), EXIT (left banking), MOVE (carried by a transaction) or SHORT (paid out beyond known balance)',
    ref_id          VARCHAR(80)   NOT NULL COMMENT IS 'Account, exit or transaction the row is about',
    component    VARCHAR(40)   NOT NULL COMMENT IS 'clean (the holder''s own money) or a victim tag',
    paise        DECIMAL(18,0) NOT NULL COMMENT IS 'Amount in paise (integer, exact)',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2 raw output of the PH.ATTRIBUTE_CASE UDF, kept for inspection; ATTRIBUTION and ATTRIBUTION_FLOW are built from it.';

CREATE OR REPLACE TABLE PH.ATTRIBUTION_FLOW (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Transaction',
    victim_tag   VARCHAR(20)   NOT NULL COMMENT IS 'Victim whose money it carried',
    victim_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'How much of that victim''s money this transaction carried',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2: victim money carried by each transaction. Feeds referral shares and the trail labels.';

CREATE OR REPLACE TABLE PH.ACCOUNT_SIGNALS (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case the account was scored for',
    account_id   VARCHAR(40)   NOT NULL COMMENT IS 'Account scored',
    as_of        TIMESTAMP     NOT NULL COMMENT IS 'Signals use the 90 days before this moment',
    signal_name  VARCHAR(40)   NOT NULL COMMENT IS 'Signal from config/scoring.yaml',
    signal_value DOUBLE                 COMMENT IS 'Raw value of the signal',
    weight       DOUBLE        NOT NULL COMMENT IS 'Hand-set weight from config/scoring.yaml',
    mule_likeness DOUBLE                COMMENT IS '0 = honest-like, 1 = mule-like',
    contribution DOUBLE                 COMMENT IS 'weight * mule_likeness / total weight; contributions sum to the score',
    sentence     VARCHAR(300)           COMMENT IS 'The signal written as a plain sentence for the reasons',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 3 output: one row per account per signal, so every reason is explainable (FR-08).';

CREATE OR REPLACE TABLE PH.RECOMMENDATION (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    account_id   VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit',
    action_code  VARCHAR(15)   NOT NULL COMMENT IS 'AMOUNT_HOLD, ACCOUNT_HOLD, REFER_OUT, HOLD_MARKER or NONE',
    amount_inr   DECIMAL(18,2)          COMMENT IS 'Amount to hold, when the action is an amount hold',
    score        DOUBLE                 COMMENT IS 'Mule score 0..1',
    band         VARCHAR(6)             COMMENT IS 'MULE, REVIEW or HONEST',
    reasons_json VARCHAR(4000)          COMMENT IS 'Rule used, attributed amount, band, top signals, legal basis, drafted requests',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 4 output: what the tool recommends. Never a hold by itself; an officer must approve (FR-10).';

CREATE OR REPLACE TABLE PH.DECISION (
    decision_id  DECIMAL(18,0) IDENTITY COMMENT IS 'Decision number; also the hold id for approved holds',
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    account_id   VARCHAR(80)   NOT NULL COMMENT IS 'Account decided on',
    action_code  VARCHAR(15)   NOT NULL COMMENT IS 'Action approved (or REJECTED)',
    amount_inr   DECIMAL(18,2)          COMMENT IS 'Amount approved',
    lane         CHAR(1)       NOT NULL COMMENT IS 'Legal lane of the hold',
    decided_by   VARCHAR(60)   NOT NULL COMMENT IS 'Officer who decided; never empty (AT-09)',
    decided_at   TIMESTAMP     NOT NULL COMMENT IS 'When',
    reason_text  VARCHAR(2000)          COMMENT IS 'Officer''s reason; required for rejections and REVIEW-band approvals',
    statement_ids VARCHAR(200)          COMMENT IS 'Exasol statement IDs behind the numbers the officer saw'
) COMMENT IS 'Officer decisions. Append-only. Nothing is held unless it is here.';

CREATE OR REPLACE TABLE PH.DEADLINE (
    decision_id  DECIMAL(18,0) NOT NULL COMMENT IS 'Hold this deadline belongs to (PH.DECISION)',
    lane         CHAR(1)       NOT NULL COMMENT IS 'Lane whose clock this is',
    rule_name    VARCHAR(50)   NOT NULL COMMENT IS 'Deadline name from config/lanes.yaml',
    due_at       TIMESTAMP     NOT NULL COMMENT IS 'When it is due',
    deadline_status VARCHAR(10) NOT NULL COMMENT IS 'OPEN, DONE or MISSED'
) COMMENT IS 'Legal clocks per hold (FR-12), from config/lanes.yaml.';

CREATE OR REPLACE TABLE PH.NOTICE (
    decision_id  DECIMAL(18,0) NOT NULL COMMENT IS 'Hold the notice is about',
    lang         VARCHAR(2)    NOT NULL COMMENT IS 'en, hi or ta',
    notice_body  VARCHAR(8000) NOT NULL COMMENT IS 'Generated notice text, stored for audit; never contains the victim''s details',
    sent_at      TIMESTAMP              COMMENT IS 'When it was (simulated) sent'
) COMMENT IS 'Customer notices (FR-11).';

CREATE OR REPLACE TABLE PH.AUDIT_LOG (
    event_id     DECIMAL(18,0) IDENTITY COMMENT IS 'Event number',
    event_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT IS 'When',
    actor        VARCHAR(60)   NOT NULL COMMENT IS 'pipeline, api, or the officer''s user name',
    event_name   VARCHAR(60)   NOT NULL COMMENT IS 'What happened',
    case_id      VARCHAR(20)            COMMENT IS 'Case, if any',
    detail       VARCHAR(2000)          COMMENT IS 'Free text or JSON',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement ID of the step logged (NFR-06)'
) COMMENT IS 'Every pipeline step and officer action, linked to the Exasol statement that produced it.';