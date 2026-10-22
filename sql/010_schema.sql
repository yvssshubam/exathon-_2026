-- 010_schema.sql — schema PH (Proportionate Holds). docs/DESIGN.md has the picture; requirements section 8 the origin.
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
    is_own_bank  BOOLEAN      NOT NULL COMMENT IS 'TRUE for the bank running the tool; its accounts can be held directly, others get an onward entry',
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
    customer_id     VARCHAR(20)           COMMENT IS 'Synthetic customer ID; accounts of one customer share it (KYC packet)',
    ifsc            VARCHAR(11)           COMMENT IS 'Synthetic branch code (KYC packet)',
    mobile          VARCHAR(20)           COMMENT IS 'Synthetic, masked mobile number (KYC packet)',
    email           VARCHAR(100)          COMMENT IS 'Synthetic e-mail at example.com (KYC packet)',
    address         VARCHAR(200)          COMMENT IS 'Synthetic address (KYC packet)',
    pan             VARCHAR(20)           COMMENT IS 'Synthetic PAN placeholder, never a real PAN (KYC packet)',
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
    lane                CHAR(1)       NOT NULL COMMENT IS 'A = police-instructed hold (MHA SOP). The only lane this build works (docs/DESIGN.md)',
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
) COMMENT IS 'Money that left the viewing bank for another bank, with the victim''s share: the source of onward entries.';

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

CREATE OR REPLACE TABLE PH.SCORING_CONFIG (
    signal_name     VARCHAR(40)   NOT NULL COMMENT IS 'Signal, as named in config/scoring.yaml',
    weight          DOUBLE        NOT NULL COMMENT IS 'Hand-set weight (not validated on real data)',
    honest_at       DOUBLE        NOT NULL COMMENT IS 'Value at which the signal looks fully honest (mule-likeness 0)',
    mule_at         DOUBLE        NOT NULL COMMENT IS 'Value at which the signal looks fully mule-like (mule-likeness 1)',
    mule_sentence   VARCHAR(300)  NOT NULL COMMENT IS 'Reason text when the signal looks mule-like',
    honest_sentence VARCHAR(300)  NOT NULL COMMENT IS 'Reason text when the signal looks honest'
) COMMENT IS 'Scoring rules loaded from config/scoring.yaml, so the scoring SQL reads them inside Exasol.';

CREATE OR REPLACE TABLE PH.PROFILE_BAND (
    profile_band          VARCHAR(10)   NOT NULL COMMENT IS 'low, medium or high',
    expected_monthly_inr  DECIMAL(18,2) NOT NULL COMMENT IS 'Credits per month expected for an account declaring this band'
) COMMENT IS 'Declared-profile bands used by the credit-vs-profile signal (config/scoring.yaml).';

CREATE OR REPLACE TABLE PH.ACCOUNT_SIGNALS (
    case_id       VARCHAR(20)   NOT NULL COMMENT IS 'Case the account was scored for',
    account_id    VARCHAR(40)   NOT NULL COMMENT IS 'Account scored',
    as_of         TIMESTAMP     NOT NULL COMMENT IS 'Signals use the 90 days before this moment',
    signal_name   VARCHAR(40)   NOT NULL COMMENT IS 'Signal from config/scoring.yaml',
    signal_value  DOUBLE                 COMMENT IS 'Raw value of the signal',
    weight        DOUBLE        NOT NULL COMMENT IS 'Hand-set weight from config/scoring.yaml',
    mule_likeness DOUBLE                 COMMENT IS '0 = honest-like, 1 = mule-like',
    contribution  DOUBLE                 COMMENT IS 'weight * mule_likeness / total weight; contributions sum to the score',
    sentence      VARCHAR(300)           COMMENT IS 'The signal written as a plain sentence for the reasons',
    mule_rank     DECIMAL(3,0)           COMMENT IS 'Rank of this signal as a reason the account looks like a mule (1 = strongest)',
    honest_rank   DECIMAL(3,0)           COMMENT IS 'Rank of this signal as a reason the account looks honest (1 = strongest)',
    stmt_id       DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 3 output: one row per account per signal, so every reason is explainable (FR-08).';

CREATE OR REPLACE TABLE PH.ACCOUNT_SCORE (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    account_id   VARCHAR(40)   NOT NULL COMMENT IS 'Account scored',
    as_of        TIMESTAMP     NOT NULL COMMENT IS 'Hold time the score is for',
    score        DOUBLE        NOT NULL COMMENT IS 'Mule score 0..1 (sum of signal contributions)',
    band         VARCHAR(6)    NOT NULL COMMENT IS 'MULE (>= 0.70), REVIEW (>= 0.40) or HONEST',
    account_type VARCHAR(10)            COMMENT IS 'savings, current, nodal, pool or escrow',
    never_account_hold BOOLEAN NOT NULL COMMENT IS 'TRUE for nodal, pool and escrow accounts: never fully held (SOP 9.1(iii))',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 3 output: score and band per account (FR-08).';

-- ---------------------------------------------------------------- the agent's ticket work (docs/DESIGN.md)

-- Tables from the first design that the golden-hour design cut (docs/DESIGN.md, "What gets cut"). Dropped so the
-- MCP server and the console never describe stale objects on a database built by an older version.
DROP TABLE IF EXISTS PH.RECOMMENDATION;
DROP TABLE IF EXISTS PH.LANE_RULE;
DROP TABLE IF EXISTS PH.DEADLINE;
DROP TABLE IF EXISTS PH.NOTICE;

CREATE OR REPLACE TABLE PH.TICKET (
    case_id         VARCHAR(20)   NOT NULL COMMENT IS 'Complaint the ticket is for (PH.COMPLAINT)',
    received_at     TIMESTAMP     NOT NULL COMMENT IS 'When the ticket reached this bank''s CFCFRMS queue (simulated clock, IST)',
    ticket_status   VARCHAR(10)   NOT NULL COMMENT IS 'QUEUED, WORKING, DONE, SENT_BACK (incomplete notice) or FAILED',
    picked_up_at    TIMESTAMP              COMMENT IS 'When the agent picked it up (simulated clock)',
    run_id          DECIMAL(18,0)          COMMENT IS 'Agent run that worked it (PH.AGENT_RUN)',
    CONSTRAINT pk_ticket PRIMARY KEY (case_id) DISABLE
) COMMENT IS 'Simulated CFCFRMS queue at the bank. The agent picks each ticket up the moment it lands, at any hour.';

CREATE OR REPLACE TABLE PH.CASE_ACCOUNT (
    case_id           VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'Bank working the ticket; it acts only on its own accounts (DECISIONS D-01)',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit the money reached',
    bank_id           VARCHAR(20)            COMMENT IS 'Bank holding the account (NULL for exits)',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the victim''s payment (1 = first-layer account)',
    is_exit           BOOLEAN       NOT NULL COMMENT IS 'TRUE when this is where money left banking',
    is_own_bank       BOOLEAN       NOT NULL COMMENT IS 'TRUE when the account is at the viewing bank',
    paid_by_own_bank  BOOLEAN       NOT NULL COMMENT IS 'TRUE when one of the viewing bank''s accounts paid it (after the victim''s own payment)',
    never_full        BOOLEAN       NOT NULL COMMENT IS 'TRUE for nodal, pool and escrow accounts: amount only, never the whole account (SOP 9.1(iii))',
    trail_amount_inr  DECIMAL(18,2)          COMMENT IS 'Sum of traced payments into the account: the amount the trail names for it (DECISIONS D-07)',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'This victim''s money in it at the hold time (LIBR)',
    other_victims_inr DECIMAL(18,2)          COMMENT IS 'Other victims'' money in it (LIBR)',
    balance_now_inr   DECIMAL(18,2)          COMMENT IS 'Balance at the hold time',
    hold_amount_inr   DECIMAL(18,2)          COMMENT IS 'Mandatory hold: the trail amount, never more than the balance (SOP 9.1(i))',
    onward_share_inr  DECIMAL(18,2)          COMMENT IS 'Victim money the viewing bank sent to this other-bank account (onward entry)',
    band              VARCHAR(6)             COMMENT IS 'Account evidence summary: MULE, REVIEW or HONEST (input to the rules, not a verdict)',
    score             DOUBLE                 COMMENT IS 'Mule-likeness 0..1 from PH.ACCOUNT_SCORE',
    pass_through      DOUBLE                 COMMENT IS 'Share of credits sent on within an hour (90 days)',
    cash_exits        DOUBLE                 COMMENT IS 'Cash or crypto withdrawals (90 days)',
    complaint_links   DOUBLE                 COMMENT IS 'Distinct fraud complaints that paid into it in 7 days',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Rules engine input: every fact the action rules read, one row per account on the trail.';

CREATE OR REPLACE TABLE PH.CASE_ACTION (
    case_id           VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'Bank working the ticket',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit',
    bank_id           VARCHAR(20)            COMMENT IS 'Bank holding the account',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the victim''s payment',
    action_code       VARCHAR(20)   NOT NULL COMMENT IS 'MANDATORY_HOLD, HOLD_MARKER, RECORD_EXIT, ONWARD_ENTRY, NONE (automatic) or PROPOSE_NARROW, PROPOSE_FULL_FREEZE, CONFIRM_HOLD (officer)',
    act_mode          VARCHAR(10)   NOT NULL COMMENT IS 'AUTOMATIC (the rules require it; the agent does it at any hour), OFFICER (a judgment call; only proposed) or INFO',
    amount_inr        DECIMAL(18,2)          COMMENT IS 'Amount to hold, record or report; for proposals, the proposed amount',
    current_hold_inr  DECIMAL(18,2)          COMMENT IS 'For proposals: the mandatory hold already on the account',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'This victim''s money in the account (LIBR)',
    other_victims_inr DECIMAL(18,2)          COMMENT IS 'Other victims'' money in the account',
    balance_now_inr   DECIMAL(18,2)          COMMENT IS 'Balance at the hold time',
    trail_amount_inr  DECIMAL(18,2)          COMMENT IS 'Amount the trail names for the account',
    band              VARCHAR(6)             COMMENT IS 'Account evidence summary (input, not a label shown as a verdict)',
    score             DOUBLE                 COMMENT IS 'Mule-likeness 0..1',
    reason_code       VARCHAR(30)   NOT NULL COMMENT IS 'Which rule in config/rules.yaml produced the action',
    legal_basis       VARCHAR(300)           COMMENT IS 'Legal basis of the action',
    action_status     VARCHAR(10)   NOT NULL COMMENT IS 'TO_DO, DONE (automatic); PROPOSED, WAITING, APPROVED, EDITED, REJECTED (officer); INFO',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Rules engine output: what is allowed for each account, and whether the agent does it or the officer decides.';

CREATE OR REPLACE TABLE PH.CHECK_RESULT (
    case_id         VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank working the ticket',
    check_name      VARCHAR(40)   NOT NULL COMMENT IS 'conservation, holds_within_balance, holds_within_trail, automatic_allowed, own_accounts_only, never_full_respected',
    check_passed    BOOLEAN       NOT NULL COMMENT IS 'TRUE when the check passed. Any FALSE stops every change-making step',
    detail          VARCHAR(500)           COMMENT IS 'The numbers behind the check',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'The checker: deterministic policy and conservation checks run before the agent may change anything (maker-checker).';

CREATE OR REPLACE TABLE PH.HOLD_LEDGER (
    hold_id         DECIMAL(18,0) IDENTITY COMMENT IS 'Hold number; reference PH-H<hold_id>',
    case_id         VARCHAR(20)   NOT NULL COMMENT IS 'Case the hold is for',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank that placed it',
    account_id      VARCHAR(80)   NOT NULL COMMENT IS 'Account held',
    hold_kind       VARCHAR(15)   NOT NULL COMMENT IS 'LIEN (amount only), ACCOUNT_FREEZE (whole account) or MARKER (catch later credits, first layer)',
    amount_inr      DECIMAL(18,2)          COMMENT IS 'Amount under lien; for a freeze, the balance it locked',
    placed_at       TIMESTAMP     NOT NULL COMMENT IS 'When the hold took effect (simulated clock for the agent; officer decisions at the time they were made)',
    placed_by       VARCHAR(60)   NOT NULL COMMENT IS 'agent, or the officer who approved it',
    hold_status     VARCHAR(10)   NOT NULL COMMENT IS 'ACTIVE or REPLACED (by a narrower or wider hold)',
    replaced_by     DECIMAL(18,0)          COMMENT IS 'Hold (hold_id) that replaced this one',
    action_code     VARCHAR(20)   NOT NULL COMMENT IS 'Action that led to it',
    reason_text     VARCHAR(2000)          COMMENT IS 'Why, in words (officer comment for officer holds)',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Simulated core banking hold ledger. The agent can write only MANDATORY_HOLD liens and first-layer markers; anything wider needs an officer.';

CREATE OR REPLACE TABLE PH.PORTAL_ENTRY (
    entry_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Entry number',
    case_id           VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'Bank filing it',
    entry_kind        VARCHAR(10)   NOT NULL COMMENT IS 'ONWARD (money moved to another bank), EXIT (money left banking) or KYC (holder details)',
    from_account      VARCHAR(80)            COMMENT IS 'Own-bank account the money left from',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Receiving account, exit, or the account whose KYC this is',
    bank_id           VARCHAR(20)            COMMENT IS 'Receiving bank (ONWARD) or holding bank (KYC)',
    txn_id            VARCHAR(40)            COMMENT IS 'Transaction reported',
    utr               VARCHAR(40)            COMMENT IS 'UTR reported',
    channel           VARCHAR(10)            COMMENT IS 'IMPS, UPI, ATM, CRYPTO ...',
    amount_inr        DECIMAL(18,2)          COMMENT IS 'Amount moved',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'How much of it was the victim''s money (LIBR); the next bank sizes its hold with it',
    moved_at          TIMESTAMP              COMMENT IS 'When the money moved',
    detail            VARCHAR(2000)          COMMENT IS 'Exit identifiers (ATM ID, wallet) or the KYC packet text',
    entry_status      VARCHAR(10)   NOT NULL COMMENT IS 'FILED (sent to CFCFRMS) or DRAFT (waiting for the officer)',
    filed_at          TIMESTAMP              COMMENT IS 'When it was filed (simulated clock for the agent)',
    filed_by          VARCHAR(60)            COMMENT IS 'agent or the officer',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Simulated CFCFRMS entries: onward transfers (one new ticket per receiving bank), exits and KYC packets (SOP 6.1(k), 9.1(i)).';

CREATE OR REPLACE TABLE PH.DECISION (
    decision_id        DECIMAL(18,0) IDENTITY COMMENT IS 'Decision number',
    case_id            VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id    VARCHAR(20)   NOT NULL COMMENT IS 'Bank whose officer decided',
    account_id         VARCHAR(80)   NOT NULL COMMENT IS 'Account decided on',
    action_code        VARCHAR(20)   NOT NULL COMMENT IS 'Proposal decided: PROPOSE_NARROW, PROPOSE_FULL_FREEZE, CONFIRM_HOLD, or SEND_KYC',
    decision_outcome   VARCHAR(10)   NOT NULL COMMENT IS 'APPROVED, EDITED or REJECTED',
    amount_inr         DECIMAL(18,2)          COMMENT IS 'Amount the officer approved (may differ from the proposal when EDITED)',
    proposed_amount    DECIMAL(18,2)          COMMENT IS 'Amount the agent proposed',
    decided_by         VARCHAR(60)   NOT NULL COMMENT IS 'Officer; never empty',
    decided_at         TIMESTAMP     NOT NULL COMMENT IS 'When the officer decided',
    reason_text        VARCHAR(2000)          COMMENT IS 'Officer''s reason: required for rejections, edits and full freezes',
    statement_ids      VARCHAR(200)           COMMENT IS 'Exasol statement IDs behind the numbers the officer saw'
) COMMENT IS 'Officer decisions on the agent''s proposals. Append-only.';

CREATE OR REPLACE TABLE PH.AGENT_RUN (
    run_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Run number',
    case_id         VARCHAR(20)   NOT NULL COMMENT IS 'Ticket worked',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank the agent works for',
    run_mode        VARCHAR(10)   NOT NULL COMMENT IS 'pipeline (fixed order) or llm (a local model picks the next tool; the pipeline takes over if it fails)',
    model_name      VARCHAR(100)           COMMENT IS 'Local model used in llm mode',
    sim_started_at  TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock when the ticket was picked up',
    sim_holds_at    TIMESTAMP              COMMENT IS 'Simulated clock when the mandatory holds landed',
    wall_started_at TIMESTAMP              COMMENT IS 'Real time the run started',
    wall_seconds    DOUBLE                 COMMENT IS 'Real seconds the whole run took',
    run_status      VARCHAR(12)            COMMENT IS 'DONE, SENT_BACK, CHECK_FAILED or FAILED',
    summary_text    VARCHAR(4000)          COMMENT IS 'Case summary for the officer''s morning review'
) COMMENT IS 'One row per agent run over a ticket.';

CREATE OR REPLACE TABLE PH.AGENT_STEP (
    run_id      DECIMAL(18,0) NOT NULL COMMENT IS 'Run (PH.AGENT_RUN)',
    step_no     DECIMAL(4,0)  NOT NULL COMMENT IS 'Order within the run',
    tool_name   VARCHAR(40)   NOT NULL COMMENT IS 'Tool called',
    chosen_by   VARCHAR(10)   NOT NULL COMMENT IS 'pipeline, llm, fallback (pipeline took over from the model) or runtime (the checker, always run)',
    can_change  BOOLEAN       NOT NULL COMMENT IS 'TRUE for the tools that write holds or portal entries',
    result_text VARCHAR(2000)          COMMENT IS 'What the tool found or did, in one line',
    stmt_ids    VARCHAR(400)           COMMENT IS 'Exasol statement IDs the tool ran',
    wall_ms     DECIMAL(9,0)           COMMENT IS 'Real milliseconds the tool took'
) COMMENT IS 'Append-only log of every tool call the agent made, with the Exasol statements behind it.';

-- ---------------------------------------------------------------- today's process, for the Compare screen

CREATE OR REPLACE TABLE PH.TODAY_RAW (
    case_id      VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    probe_id     VARCHAR(80)   NOT NULL COMMENT IS 'Account whose action time this replay was cut at',
    row_kind     VARCHAR(5)    NOT NULL COMMENT IS 'HOLD, EXIT, MOVE or SHORT (as PH.ATTRIBUTION_RAW)',
    ref_id       VARCHAR(80)   NOT NULL COMMENT IS 'Account, exit or transaction',
    component    VARCHAR(40)   NOT NULL COMMENT IS 'clean or a victim tag',
    paise        DECIMAL(18,0) NOT NULL COMMENT IS 'Amount in paise',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'LIBR replay cut at the moment today''s desk would reach each account (attribution UDF, same as step 2).';

CREATE OR REPLACE TABLE PH.TODAY_OUTCOME (
    case_id           VARCHAR(20)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'Bank',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Own-bank account on the trail',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the victim''s payment',
    action_at         TIMESTAMP     NOT NULL COMMENT IS 'When today''s desk reaches it (desk opening + lookups)',
    balance_then_inr  DECIMAL(18,2)          COMMENT IS 'Balance at that moment',
    victim_then_inr   DECIMAL(18,2)          COMMENT IS 'Victim money still in it at that moment (LIBR)',
    today_action      VARCHAR(15)   NOT NULL COMMENT IS 'WHOLE_FREEZE, LIEN or NOTHING_LEFT',
    locked_inr        DECIMAL(18,2)          COMMENT IS 'Money locked by today''s action',
    honest_locked_inr DECIMAL(18,2)          COMMENT IS 'Locked money that is not victims'' money',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Simulated baseline: the same ticket worked by today''s desk (bank hours, one lookup at a time, whole-account freezes).';

CREATE OR REPLACE TABLE PH.WATCH_EVENT (
    event_id        DECIMAL(18,0) IDENTITY COMMENT IS 'Event number',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank whose accounts are watched',
    account_id      VARCHAR(80)   NOT NULL COMMENT IS 'Watched account: held by the agent, or with a freeze waiting for the officer',
    txn_id          VARCHAR(40)   NOT NULL COMMENT IS 'Transaction that caused the event',
    event_at        TIMESTAMP     NOT NULL COMMENT IS 'When it happened (simulated clock)',
    event_kind      VARCHAR(15)   NOT NULL COMMENT IS 'BLOCKED (tried to move held money: refused), MOVED (moved money that was not held), NEW_MONEY (credit on a mule-like account) or MARKER_HOLD (credit caught by a first-layer marker)',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Amount of the transaction',
    counterparty    VARCHAR(200)           COMMENT IS 'Other side: account, ATM, wallet',
    channel         VARCHAR(10)            COMMENT IS 'Channel of the transaction',
    held_inr        DECIMAL(18,2)          COMMENT IS 'Money under hold on the account at that moment',
    balance_inr     DECIMAL(18,2)          COMMENT IS 'Balance after the event (a refused debit leaves it unchanged)',
    case_ids        VARCHAR(400)           COMMENT IS 'Tickets that hold or name this account at that moment',
    response        VARCHAR(15)   NOT NULL COMMENT IS 'EVIDENCE (added to the officer''s decision), ALERT (also alerted the officer out of hours), HELD (marker caught it)',
    allowed_responses VARCHAR(200)         COMMENT IS 'Responses the rules allowed for this event, comma-separated (OPTIONS is reserved in Exasol)',
    chosen_by       VARCHAR(10)   NOT NULL COMMENT IS 'pipeline (fixed policy), llm (local model) or fallback (model failed; fixed policy)',
    alert_text      VARCHAR(1000)          COMMENT IS 'What the out-of-hours alert said',
    hold_id         DECIMAL(18,0)          COMMENT IS 'Hold placed for a MARKER_HOLD event',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'The watch: what the agent saw and did on held accounts after the holds went on (pipeline/watch.py).';

CREATE OR REPLACE TABLE PH.NETWORK_MEMBER (
    network_id      VARCHAR(10)   NOT NULL COMMENT IS 'N1, N2, ... largest first',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank whose accounts these are',
    account_id      VARCHAR(80)   NOT NULL COMMENT IS 'Mule-like own-bank account in the network'
) COMMENT IS 'Mule networks: accounts on worked tickets joined by money moving between them or a shared cash-out point (pipeline/networks.py).';

CREATE OR REPLACE TABLE PH.NETWORK_LINK (
    network_id      VARCHAR(10)   NOT NULL COMMENT IS 'Network',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'Bank',
    from_account    VARCHAR(80)   NOT NULL COMMENT IS 'Payer (MONEY) or one of the two accounts (SHARED_EXIT)',
    to_account      VARCHAR(80)   NOT NULL COMMENT IS 'Receiver (MONEY) or the other account (SHARED_EXIT)',
    link_kind       VARCHAR(12)   NOT NULL COMMENT IS 'MONEY (direct transfers), TRIED (transfers refused because the money was held) or SHARED_EXIT (cashed out at the same ATM or wallet)',
    txn_count       DECIMAL(9,0)  NOT NULL COMMENT IS 'Transactions behind the link',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Their total',
    first_at        TIMESTAMP     NOT NULL COMMENT IS 'First of them',
    last_at         TIMESTAMP     NOT NULL COMMENT IS 'Last of them',
    detail          VARCHAR(200)           COMMENT IS 'The shared ATM or wallet, for SHARED_EXIT'
) COMMENT IS 'Why the accounts of a network belong together: one row per pair and kind of link.';

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