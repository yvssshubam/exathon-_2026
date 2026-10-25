-- 010_schema.sql — one bank's schema (PH_X, PH_Y, PH_Z). docs/DESIGN.md has the picture.
-- pipeline/run_sql.py runs every file in sql/bank/ once per bank in config/banks.yaml, after OPEN SCHEMA <that bank's
-- schema>, so every name here is unqualified and lands in the bank's own schema. Each bank sees only its own data:
-- its own accounts with KYC, the transactions that touch them, its own tickets, holds and filings. The simulated
-- CFCFRMS portal (schema PH_PORTAL, sql/portal/) is the only thing banks share, and only the portal service reads it.
-- Idempotent: `make db` rebuilds everything (CREATE OR REPLACE). `make seed` reloads the data.
-- Every table and column has a COMMENT so the Exasol MCP server can describe them.
-- Money is DECIMAL(18,2) rupees. Times are IST, stored as TIMESTAMP without zone.
-- Column names avoid SQL keywords on purpose (e.g. trace_rule not rule, action_code not action).

-- Objects from earlier designs that this one cut (docs/DESIGN.md, "What gets cut"). Dropped so the MCP server and the
-- console never describe stale objects on a database built by an older version.
DROP TABLE IF EXISTS RECOMMENDATION;
DROP TABLE IF EXISTS LANE_RULE;
DROP TABLE IF EXISTS DEADLINE;
DROP TABLE IF EXISTS NOTICE;
DROP TABLE IF EXISTS NETWORK_MEMBER;
DROP TABLE IF EXISTS NETWORK_LINK;
DROP VIEW IF EXISTS COMPLAINT_CHECK;

CREATE OR REPLACE TABLE BANK (
    bank_id      VARCHAR(20)  NOT NULL COMMENT IS 'Bank identifier, e.g. BANK_X',
    bank_name    VARCHAR(100) NOT NULL COMMENT IS 'Display name',
    is_own_bank  BOOLEAN      NOT NULL COMMENT IS 'TRUE for the bank this schema belongs to; its accounts can be held, others get an onward entry',
    CONSTRAINT pk_bank PRIMARY KEY (bank_id) DISABLE
) COMMENT IS 'Banks this bank knows about. Exactly one has is_own_bank = TRUE: the owner of this schema.';

CREATE OR REPLACE TABLE ACCOUNT (
    account_id      VARCHAR(40)  NOT NULL COMMENT IS 'Account identifier (demo: ACC_A, ACC_C, ACC_D)',
    bank_id         VARCHAR(20)  NOT NULL COMMENT IS 'Bank holding the account, see BANK',
    holder_name     VARCHAR(100)          COMMENT IS 'Holder name (for another bank''s account: the beneficiary name on the payment message)',
    opened_on       DATE                  COMMENT IS 'Account opening date; feeds the account-age signal. NULL for another bank''s account',
    account_type    VARCHAR(10)           COMMENT IS 'savings, current, nodal, pool or escrow. Nodal, pool and escrow accounts are never fully held (SOP 9.1(i), 9.1(iii)). NULL for another bank''s account',
    holder_type     VARCHAR(20)           COMMENT IS 'individual, merchant or business. NULL for another bank''s account',
    profile_band    VARCHAR(10)           COMMENT IS 'Declared income/turnover band: low, medium, high; feeds the credit-vs-profile signal',
    history_profile VARCHAR(30)           COMMENT IS 'Which synthetic history the generator gives this account (fast_pass_through, merchant_300_per_day, none)',
    customer_id     VARCHAR(20)           COMMENT IS 'Synthetic customer ID; accounts of one customer share it (KYC packet)',
    ifsc            VARCHAR(11)           COMMENT IS 'Synthetic branch code (KYC packet)',
    mobile          VARCHAR(20)           COMMENT IS 'Synthetic, masked mobile number (KYC packet)',
    email           VARCHAR(100)          COMMENT IS 'Synthetic e-mail at example.com (KYC packet)',
    address         VARCHAR(200)          COMMENT IS 'Synthetic address (KYC packet)',
    pan             VARCHAR(20)           COMMENT IS 'Synthetic PAN placeholder, never a real PAN (KYC packet)',
    CONSTRAINT pk_account PRIMARY KEY (account_id) DISABLE
) COMMENT IS 'This bank''s accounts with synthetic KYC, plus bare rows (ID, bank, beneficiary name) for other banks'' accounts its customers paid or were paid by.';

CREATE OR REPLACE TABLE TXN (
    txn_id          VARCHAR(40)   NOT NULL COMMENT IS 'Transaction identifier',
    utr             VARCHAR(40)   NOT NULL COMMENT IS 'Unique transaction reference as printed on statements; tickets point at a UTR',
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
) COMMENT IS 'Every money movement touching this bank''s accounts, as it would happen if nobody acted (Compare replays this). Debits the bank refused are in DECLINED_ATTEMPT; TXN_LIVE leaves them out.';

CREATE OR REPLACE TABLE BALANCE_SNAPSHOT (
    account_id  VARCHAR(40)   NOT NULL COMMENT IS 'Account',
    ts          TIMESTAMP     NOT NULL COMMENT IS 'Balance is as of this moment',
    balance_inr DECIMAL(18,2) NOT NULL COMMENT IS 'Balance in rupees; the opening balance for the attribution replay (LIBR)'
) COMMENT IS 'Known balances of this bank''s accounts. The attribution replay starts from the latest snapshot before the window.';

CREATE OR REPLACE TABLE COMPLAINT (
    case_id             VARCHAR(40)   NOT NULL COMMENT IS 'Case identifier at this bank (demo: CASE_DEMO; an onward ticket: CASE_DEMO-Y1)',
    ack_no              VARCHAR(14)   NOT NULL COMMENT IS 'NCRP acknowledgement number, 14 digits; starts with 2 (NCRP) or 3 (1930). Onward tickets carry the original one',
    complaint_source    VARCHAR(10)   NOT NULL COMMENT IS '1930, NCRP, branch, IO (investigating officer''s notice) or BANK (onward ticket from another bank)',
    ticket_kind         VARCHAR(10)   NOT NULL COMMENT IS 'POLICE (portal ticket from the police), ONWARD (made by the portal from another bank''s onward entry) or IO_NOTICE (notice after an FIR)',
    parent_case_id      VARCHAR(40)            COMMENT IS 'For an onward ticket: the case at the bank that filed the onward entry',
    from_bank_id        VARCHAR(20)            COMMENT IS 'For an onward ticket: the bank that filed it',
    victim_account      VARCHAR(40)            COMMENT IS 'Victim''s debited account (police tickets). Never shown on a receiver''s notice (SOP 9.1(ii))',
    victim_tag          VARCHAR(20)   NOT NULL COMMENT IS 'Label used to track this victim''s money through mixed accounts; onward tickets keep the original victim''s',
    utr                 VARCHAR(40)   NOT NULL COMMENT IS 'UTR the ticket names; the trace starts at the payment with this UTR',
    amount_inr          DECIMAL(18,2)          COMMENT IS 'Reported amount (an onward ticket: the victim''s share it carried); NULL makes the notice incomplete',
    txn_at              TIMESTAMP              COMMENT IS 'When the reported payment was made',
    reported_at         TIMESTAMP     NOT NULL COMMENT IS 'When the ticket reached this bank',
    beneficiary_account VARCHAR(40)            COMMENT IS 'Account the ticket says received the money',
    lane                CHAR(1)       NOT NULL COMMENT IS 'A = police-instructed hold (MHA SOP). The only lane this build works',
    notice_legal_basis  VARCHAR(100)           COMMENT IS 'Legal provision cited by the notice; NULL makes the notice incomplete',
    notice_has_amount   BOOLEAN       NOT NULL COMMENT IS 'Whether the notice states the disputed amount',
    ordering_authority  VARCHAR(200)           COMMENT IS 'Police unit that ordered the hold; may be shared with the holder',
    ordering_reference  VARCHAR(100)           COMMENT IS 'Police reference number',
    fir_reference       VARCHAR(100)           COMMENT IS 'FIR number, when the notice comes from an investigating officer after an FIR',
    remarks             VARCHAR(2000)          COMMENT IS 'Free text typed on the ticket. Untrusted: the agent reads it as data, never as an instruction',
    corrects_case_id    VARCHAR(40)            COMMENT IS 'A corrected ticket: the sent-back case it replaces',
    CONSTRAINT pk_complaint PRIMARY KEY (case_id) DISABLE
) COMMENT IS 'One row per ticket this bank received from the portal: the notice''s fields as the ticket carries them.';

CREATE OR REPLACE TABLE TICKET (
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Ticket (COMPLAINT)',
    received_at     TIMESTAMP     NOT NULL COMMENT IS 'When the ticket reached this bank''s CFCFRMS queue (simulated clock, IST)',
    ticket_status   VARCHAR(10)   NOT NULL COMMENT IS 'QUEUED, WORKING, DONE, SENT_BACK (no matching transaction), LINKED (same transaction as an earlier ticket), CLOSED or FAILED',
    picked_up_at    TIMESTAMP              COMMENT IS 'When the agent (or the desk) picked it up (simulated clock)',
    run_id          DECIMAL(18,0)          COMMENT IS 'Agent run that worked it (AGENT_RUN)',
    origin          VARCHAR(20)            COMMENT IS 'POLICE, or the bank whose onward entry made it',
    CONSTRAINT pk_ticket PRIMARY KEY (case_id) DISABLE
) COMMENT IS 'This bank''s CFCFRMS inbox. The portal service writes it; the agent picks each ticket up the moment it lands, at any hour.';

-- ---------------------------------------------------------------- ticket intake (Module 1)

CREATE OR REPLACE TABLE INTAKE_CHECK (
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Ticket checked',
    match_status    VARCHAR(12)   NOT NULL COMMENT IS 'EXACT (UTR, amount, time and account all match a transaction), NEAR_MATCH (no exact match, a likely one on the named account), NO_MATCH or INCOMPLETE (a required field is missing)',
    matched_txn_id  VARCHAR(40)            COMMENT IS 'The transaction the ticket names, on an exact match',
    mismatched      VARCHAR(400)           COMMENT IS 'Fields that do not match the bank''s data, in plain words, one per line',
    problems        VARCHAR(400)           COMMENT IS 'Missing fields, one per line',
    near_txn_id     VARCHAR(40)            COMMENT IS 'Likely match: same account, same amount, same time window (never another customer''s account)',
    near_utr        VARCHAR(40)            COMMENT IS 'UTR of the likely match',
    near_at         TIMESTAMP              COMMENT IS 'Time of the likely match',
    near_amount_inr DECIMAL(18,2)          COMMENT IS 'Amount of the likely match',
    related         VARCHAR(20)            COMMENT IS 'NEW, DUPLICATE (same transaction as an earlier ticket), NEW_VICTIM (another victim''s money on an account already in a case) or CORRECTED (replaces a sent-back ticket)',
    related_case_id VARCHAR(40)            COMMENT IS 'The earlier case it duplicates, shares an account with, or corrects',
    checked_at      TIMESTAMP              COMMENT IS 'Simulated clock',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Ticket intake: is the ticket''s transaction really in this bank''s data, and is it new? Nothing is held without an exact match.';

CREATE OR REPLACE TABLE CASE_LINK (
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    linked_case_id  VARCHAR(40)   NOT NULL COMMENT IS 'The other case',
    link_kind       VARCHAR(20)   NOT NULL COMMENT IS 'DUPLICATE (same transaction), SHARED_ACCOUNT (another victim on the same account), CORRECTS (corrected ticket) or POLICE_CONFIRMED (a later notice backed by an FIR)',
    account_id      VARCHAR(40)            COMMENT IS 'The shared account, for SHARED_ACCOUNT',
    detail          VARCHAR(400)           COMMENT IS 'In plain words',
    linked_at       TIMESTAMP              COMMENT IS 'Simulated clock',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Links between cases found at intake or by a recalculation.';

-- ---------------------------------------------------------------- trace, attribution, evidence (Module 2)

CREATE OR REPLACE TABLE TRACE_LINK (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case being traced',
    parent_txn   VARCHAR(40)            COMMENT IS 'Transaction the money arrived by; NULL for the ticket''s own payment (the root)',
    child_txn    VARCHAR(40)   NOT NULL COMMENT IS 'Later transaction out of the same account that may carry that money on',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Sender of child_txn',
    to_account   VARCHAR(80)   NOT NULL COMMENT IS 'Receiver of child_txn, or an exit pseudo-account',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Amount of child_txn',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'Time of child_txn',
    is_exit      BOOLEAN       NOT NULL COMMENT IS 'TRUE when child_txn leaves the banking system'
) COMMENT IS 'Step 1a: which transaction can follow which (same account, not earlier, within the hop window, before the hold time). CONNECT BY walks this.';

CREATE OR REPLACE TABLE TRACE_EDGE (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case traced',
    hop          DECIMAL(3,0)  NOT NULL COMMENT IS 'Hop number from the ticket''s payment (1 = the payment itself)',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Sender on this edge',
    to_account   VARCHAR(80)   NOT NULL COMMENT IS 'Receiver on this edge (or an exit)',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Transaction on this edge',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Full transaction amount; how much of it was the victim''s is in ATTRIBUTION',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'Transaction time',
    is_cycle     BOOLEAN       NOT NULL COMMENT IS 'TRUE when the money came back to an account already on this path',
    is_exit      BOOLEAN       NOT NULL COMMENT IS 'TRUE when the edge leaves the banking system',
    is_own_bank  BOOLEAN       NOT NULL COMMENT IS 'TRUE when the receiving account is at this bank',
    trail_path   VARCHAR(2000)          COMMENT IS 'Printable path from the ticket''s payer, accounts separated by >',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 1 output: the money''s paths through this bank from the ticket''s UTR (CONNECT BY NOCYCLE). Another bank''s accounts end the path: that bank gets an onward ticket.';

CREATE OR REPLACE TABLE REFERRAL_OUT (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    from_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    from_account VARCHAR(40)   NOT NULL COMMENT IS 'Own account the money left from',
    bank_id      VARCHAR(20)   NOT NULL COMMENT IS 'Other bank that received the money',
    account_id   VARCHAR(40)   NOT NULL COMMENT IS 'Account at the other bank',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Onward transfer',
    utr          VARCHAR(40)   NOT NULL COMMENT IS 'UTR of the onward transfer',
    amount_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Amount transferred',
    victim_amount_inr DECIMAL(18,2)     COMMENT IS 'How much of it was the victim''s money under trace_rule; NULL = share unknown',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'Rule the victim share was computed with',
    ts           TIMESTAMP     NOT NULL COMMENT IS 'When it moved',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Money that left this bank for another bank, with the victim''s share: the source of onward entries.';

CREATE OR REPLACE TABLE ATTRIBUTION (
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    trace_rule        VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit pseudo-account',
    victim_tag        VARCHAR(20)   NOT NULL COMMENT IS 'Victim whose money this is',
    victim_amount_inr DECIMAL(18,2) NOT NULL COMMENT IS 'This victim''s money in the account as of the hold time',
    other_victims_inr DECIMAL(18,2) NOT NULL COMMENT IS 'Other victims'' money in the same account',
    own_funds_inr     DECIMAL(18,2) NOT NULL COMMENT IS 'The holder''s own (clean) money in the account',
    balance_now_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Total balance as of the hold time',
    as_of             TIMESTAMP     NOT NULL COMMENT IS 'Replay cut-off: the moment holds are placed, or the moment of a recalculation',
    row_source        VARCHAR(12)   NOT NULL COMMENT IS 'UDF (computed in Exasol by ATTRIBUTE_CASE)',
    session_id        DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2 output: the victim''s traceable money per account under each tracing rule. Earlier values live on in VICTIM_SPLIT.';

CREATE OR REPLACE TABLE ATTRIBUTION_RAW (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    row_kind     VARCHAR(5)    NOT NULL COMMENT IS 'HOLD (money left in an account), EXIT (left banking), MOVE (carried by a transaction) or SHORT (paid out beyond known balance)',
    ref_id       VARCHAR(80)   NOT NULL COMMENT IS 'Account, exit or transaction the row is about',
    component    VARCHAR(40)   NOT NULL COMMENT IS 'clean (the holder''s own money) or a victim tag',
    paise        DECIMAL(18,0) NOT NULL COMMENT IS 'Amount in paise (integer, exact)',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session that produced this row',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2 raw output of the ATTRIBUTE_CASE UDF, kept for inspection; ATTRIBUTION and ATTRIBUTION_FLOW are built from it.';

CREATE OR REPLACE TABLE ATTRIBUTION_FLOW (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    trace_rule   VARCHAR(10)   NOT NULL COMMENT IS 'LIBR, FIFO or PRO_RATA',
    txn_id       VARCHAR(40)   NOT NULL COMMENT IS 'Transaction',
    victim_tag   VARCHAR(20)   NOT NULL COMMENT IS 'Victim whose money it carried',
    victim_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'How much of that victim''s money this transaction carried',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 2: victim money carried by each transaction. Feeds onward shares, the trail labels and the LIBR steps in the hold record.';

CREATE OR REPLACE TABLE VICTIM_SPLIT (
    split_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Row number',
    account_id        VARCHAR(40)   NOT NULL COMMENT IS 'Own account whose money is split between victims',
    victim_tag        VARCHAR(20)   NOT NULL COMMENT IS 'Victim',
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'That victim''s case at this bank',
    victim_inr        DECIMAL(18,2) NOT NULL COMMENT IS 'This victim''s money in the account (LIBR)',
    all_victims_inr   DECIMAL(18,2) NOT NULL COMMENT IS 'Every reported victim''s money in the account',
    held_inr          DECIMAL(18,2) NOT NULL COMMENT IS 'Everything held on the account at that moment (unchanged by a split)',
    share_of_held_inr DECIMAL(18,2) NOT NULL COMMENT IS 'This victim''s pro rata share of what is held, for the refund stage',
    version_no        DECIMAL(9,0)  NOT NULL COMMENT IS '1 for the first pass; each recalculation adds a version',
    valid_from        TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock when this version was recorded',
    superseded_at     TIMESTAMP              COMMENT IS 'When a later version replaced it; NULL for the current one',
    trigger_kind      VARCHAR(20)   NOT NULL COMMENT IS 'first_pass, new_victim, corrected_ticket or new_money',
    trigger_case_id   VARCHAR(40)            COMMENT IS 'Ticket that triggered the recalculation',
    reason_text       VARCHAR(400)           COMMENT IS 'Why it was recalculated, in plain words',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement of the attribution behind the numbers'
) COMMENT IS 'How held money splits between the victims whose money reached an account. A record for the refund stage: it moves no money and never changes a hold.';

CREATE OR REPLACE TABLE SCORING_CONFIG (
    signal_name     VARCHAR(40)   NOT NULL COMMENT IS 'Signal, as named in config/scoring.yaml',
    weight          DOUBLE        NOT NULL COMMENT IS 'Hand-set weight (not validated on real data)',
    honest_at       DOUBLE        NOT NULL COMMENT IS 'Value at which the signal looks fully honest (mule-likeness 0)',
    mule_at         DOUBLE        NOT NULL COMMENT IS 'Value at which the signal looks fully mule-like (mule-likeness 1)',
    mule_sentence   VARCHAR(300)  NOT NULL COMMENT IS 'Reason text when the signal looks mule-like',
    honest_sentence VARCHAR(300)  NOT NULL COMMENT IS 'Reason text when the signal looks honest'
) COMMENT IS 'Scoring rules loaded from config/scoring.yaml, so the scoring SQL reads them inside Exasol.';

CREATE OR REPLACE TABLE PROFILE_BAND (
    profile_band          VARCHAR(10)   NOT NULL COMMENT IS 'low, medium or high',
    expected_monthly_inr  DECIMAL(18,2) NOT NULL COMMENT IS 'Credits per month expected for an account declaring this band'
) COMMENT IS 'Declared-profile bands used by the credit-vs-profile signal (config/scoring.yaml).';

CREATE OR REPLACE TABLE SCORE_TARGET (
    case_id    VARCHAR(60)   NOT NULL COMMENT IS 'Case, or WATCH:<account> when the watch checks an account no ticket named',
    account_id VARCHAR(40)   NOT NULL COMMENT IS 'Own account to read the evidence for'
) COMMENT IS 'Which accounts the evidence step reads: the trail''s own accounts, or one account the watch looks at.';

CREATE OR REPLACE TABLE ACCOUNT_SIGNALS (
    case_id       VARCHAR(60)   NOT NULL COMMENT IS 'Case the account was scored for (or WATCH:<account>)',
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
) COMMENT IS 'Step 3 output: one row per account per signal, so every reason is explainable.';

CREATE OR REPLACE TABLE ACCOUNT_SCORE (
    case_id      VARCHAR(60)   NOT NULL COMMENT IS 'Case (or WATCH:<account>)',
    account_id   VARCHAR(40)   NOT NULL COMMENT IS 'Account scored',
    as_of        TIMESTAMP     NOT NULL COMMENT IS 'Moment the score is for',
    score        DOUBLE        NOT NULL COMMENT IS 'Mule-likeness 0..1 (sum of signal contributions)',
    band         VARCHAR(6)    NOT NULL COMMENT IS 'MULE (>= 0.70), REVIEW (>= 0.40) or HONEST: an input to the rules, never shown as a verdict',
    account_type VARCHAR(10)            COMMENT IS 'savings, current, nodal, pool or escrow',
    never_account_hold BOOLEAN NOT NULL COMMENT IS 'TRUE for nodal, pool and escrow accounts: never fully held (SOP 9.1(iii))',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Step 3 output: score and band per account.';

-- ---------------------------------------------------------------- the rules engine, the maker's proposals, the checker

CREATE OR REPLACE TABLE CASE_ACCOUNT (
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'This bank; it acts only on its own accounts',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit the money reached',
    bank_id           VARCHAR(20)            COMMENT IS 'Bank holding the account (NULL for exits)',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the ticket''s payment (1 = first-layer account)',
    is_exit           BOOLEAN       NOT NULL COMMENT IS 'TRUE when this is where money left banking',
    is_own_bank       BOOLEAN       NOT NULL COMMENT IS 'TRUE when the account is at this bank',
    paid_by_own_bank  BOOLEAN       NOT NULL COMMENT IS 'TRUE when one of this bank''s accounts paid it (after the ticket''s own payment)',
    never_full        BOOLEAN       NOT NULL COMMENT IS 'TRUE for nodal, pool and escrow accounts: amount only, never the whole account (SOP 9.1(iii))',
    trail_amount_inr  DECIMAL(18,2)          COMMENT IS 'Sum of traced payments into the account: the amount the trail names for it',
    reported_inr      DECIMAL(18,2)          COMMENT IS 'Amount the ticket reports: no automatic hold goes above it',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'This victim''s money in it at the hold time (LIBR)',
    other_victims_inr DECIMAL(18,2)          COMMENT IS 'Other victims'' money in it (LIBR)',
    balance_now_inr   DECIMAL(18,2)          COMMENT IS 'Balance at the hold time',
    hold_amount_inr   DECIMAL(18,2)          COMMENT IS 'Mandatory hold: the trail amount, never more than the balance or the reported amount',
    onward_share_inr  DECIMAL(18,2)          COMMENT IS 'Victim money this bank sent to this other-bank account (onward entry)',
    band              VARCHAR(6)             COMMENT IS 'Account evidence summary: MULE, REVIEW or HONEST (input to the rules, not a verdict)',
    score             DOUBLE                 COMMENT IS 'Mule-likeness 0..1 from ACCOUNT_SCORE',
    pass_through      DOUBLE                 COMMENT IS 'Share of credits sent on within an hour (90 days)',
    cash_exits        DOUBLE                 COMMENT IS 'Cash or crypto withdrawals (90 days)',
    complaint_links   DOUBLE                 COMMENT IS 'Distinct fraud tickets that paid into it in 7 days',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Rules engine input: every fact the action rules read, one row per account on the trail.';

CREATE OR REPLACE TABLE CASE_ACTION (
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Account or exit',
    bank_id           VARCHAR(20)            COMMENT IS 'Bank holding the account',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the ticket''s payment',
    action_code       VARCHAR(20)   NOT NULL COMMENT IS 'MANDATORY_HOLD, HOLD_MARKER, RECORD_EXIT, ONWARD_ENTRY, NONE (automatic) or PROPOSE_NARROW, PROPOSE_FULL_FREEZE (officer)',
    act_mode          VARCHAR(10)   NOT NULL COMMENT IS 'AUTOMATIC (the rules require it; done at any hour), OFFICER (a judgment call; only proposed) or INFO',
    amount_inr        DECIMAL(18,2)          COMMENT IS 'Amount to hold, record or report; for proposals, the proposed amount',
    current_hold_inr  DECIMAL(18,2)          COMMENT IS 'For proposals: the mandatory hold already on the account',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'This victim''s money in the account (LIBR)',
    other_victims_inr DECIMAL(18,2)          COMMENT IS 'Other victims'' money in the account',
    balance_now_inr   DECIMAL(18,2)          COMMENT IS 'Balance at the hold time',
    trail_amount_inr  DECIMAL(18,2)          COMMENT IS 'Amount the trail names for the account',
    band              VARCHAR(6)             COMMENT IS 'Account evidence summary (input, not a label shown as a verdict)',
    score             DOUBLE                 COMMENT IS 'Mule-likeness 0..1',
    reason_code       VARCHAR(30)   NOT NULL COMMENT IS 'Which rule in config/rules.yaml produced the action',
    legal_basis       VARCHAR(300)           COMMENT IS 'Legal basis of the action, from the rule''s fixed citation list',
    action_status     VARCHAR(10)   NOT NULL COMMENT IS 'TO_DO, DONE (automatic); PROPOSED (officer, see PROPOSAL); INFO',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Rules engine output: what the facts allow for each account, and whether it is automatic or the officer''s decision. Allowing is not doing: only the checker carries anything out.';

CREATE OR REPLACE TABLE PROPOSAL (
    proposal_id     DECIMAL(18,0) IDENTITY COMMENT IS 'Proposal number',
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    account_id      VARCHAR(80)   NOT NULL COMMENT IS 'Account, exit or other bank''s account the proposal is about',
    action_code     VARCHAR(20)   NOT NULL COMMENT IS 'MANDATORY_HOLD, HOLD_MARKER, RECORD_EXIT, ONWARD_ENTRY, KYC_PACKET, SEND_BACK, LINK_DUPLICATE, WATCH_HOLD, RECORD_SPLIT (automatic) or PROPOSE_NARROW, PROPOSE_FULL_FREEZE, UNMATCHED_NOTICE, PROPOSE_RELEASE (the officer''s four decisions)',
    act_mode        VARCHAR(10)   NOT NULL COMMENT IS 'AUTOMATIC or OFFICER',
    amount_inr      DECIMAL(18,2)          COMMENT IS 'Amount proposed',
    proposed_by     VARCHAR(10)   NOT NULL COMMENT IS 'pipeline (fixed order), llm (the local model chose to propose it), watch or intake',
    proposed_at     TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock',
    run_id          DECIMAL(18,0)          COMMENT IS 'Agent run that proposed it',
    reasons_json    VARCHAR(20000)         COMMENT IS 'Structured reasons from the rules engine: the rule, the signals, the numbers, the citation and the case against (JSON)',
    draft_text      VARCHAR(4000)          COMMENT IS 'The reasons in words: a fixed template, or the local model''s wording after the claims check',
    draft_by        VARCHAR(10)            COMMENT IS 'template or model',
    claims_check    VARCHAR(400)           COMMENT IS 'Why a model draft was rejected, when it was',
    proposal_status VARCHAR(10)   NOT NULL COMMENT IS 'NEW, DONE (checker carried it out), WAITING (for the officer), APPROVED, EDITED, REJECTED (officer), REFUSED (checker), CANCELLED',
    checker_note    VARCHAR(1000)          COMMENT IS 'What the checker found',
    checked_at      TIMESTAMP              COMMENT IS 'Simulated clock when the checker handled it',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'The maker''s proposals. The maker (the agent, model or fixed order) can only write here; the checker validates each one and is the only component that carries anything out.';

CREATE OR REPLACE TABLE CHECK_RESULT (
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    check_name      VARCHAR(40)   NOT NULL COMMENT IS 'conservation, holds_within_balance, holds_within_trail, holds_within_report, automatic_allowed, own_accounts_only, never_full_respected',
    check_passed    BOOLEAN       NOT NULL COMMENT IS 'TRUE when the check passed. Any FALSE stops every change for the case',
    detail          VARCHAR(500)           COMMENT IS 'The numbers behind the check',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'The checker''s case-level checks: deterministic, run under the checker''s own database user before anything changes.';

CREATE OR REPLACE TABLE HOLD_LEDGER (
    hold_id         DECIMAL(18,0) IDENTITY COMMENT IS 'Hold number; reference PH-H<hold_id>',
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Case the hold is for',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    account_id      VARCHAR(80)   NOT NULL COMMENT IS 'Account held',
    hold_kind       VARCHAR(15)   NOT NULL COMMENT IS 'LIEN (amount only), ACCOUNT_FREEZE (whole account, officer only) or MARKER (catch later credits, first layer)',
    amount_inr      DECIMAL(18,2)          COMMENT IS 'Amount under lien; for a freeze, the balance it locked',
    hold_tag        VARCHAR(20)            COMMENT IS 'Whose money: the victim tag, or NOT_LINKED (caught on a mule-like account after the first pass; not linked to a reported victim yet)',
    source_txn_id   VARCHAR(40)            COMMENT IS 'For a watch hold: the credit it caught',
    placed_at       TIMESTAMP     NOT NULL COMMENT IS 'When the hold took effect (simulated clock)',
    placed_by       VARCHAR(60)   NOT NULL COMMENT IS 'agent (automatic, carried out by the checker), or the officer who approved it',
    hold_status     VARCHAR(10)   NOT NULL COMMENT IS 'ACTIVE, REPLACED (by a narrower, wider or linked hold) or RELEASED',
    replaced_by     DECIMAL(18,0)          COMMENT IS 'Hold (hold_id) that replaced this one',
    action_code     VARCHAR(20)   NOT NULL COMMENT IS 'Action that led to it',
    reason_text     VARCHAR(2000)          COMMENT IS 'Why, in words',
    proposal_id     DECIMAL(18,0)          COMMENT IS 'Proposal the checker carried out',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Simulated core banking hold ledger. Only the checker writes it: automatic holds up to the reported amount, and what an officer approved.';

CREATE OR REPLACE TABLE PORTAL_ENTRY (
    entry_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Entry number',
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'This bank (filing it)',
    entry_kind        VARCHAR(10)   NOT NULL COMMENT IS 'ONWARD (money moved to another bank), EXIT (money left banking), KYC (holder details) or SEND_BACK (ticket returned for correction)',
    from_account      VARCHAR(80)            COMMENT IS 'Own account the money left from',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Receiving account, exit, the account whose KYC this is, or the account a sent-back ticket names',
    bank_id           VARCHAR(20)            COMMENT IS 'Receiving bank (ONWARD) or holding bank (KYC)',
    txn_id            VARCHAR(40)            COMMENT IS 'Transaction reported',
    utr               VARCHAR(40)            COMMENT IS 'UTR reported',
    channel           VARCHAR(10)            COMMENT IS 'IMPS, UPI, ATM, CRYPTO ...',
    amount_inr        DECIMAL(18,2)          COMMENT IS 'Amount moved',
    victim_amount_inr DECIMAL(18,2)          COMMENT IS 'How much of it was the victim''s money (LIBR); the next bank sizes its hold with it',
    moved_at          TIMESTAMP              COMMENT IS 'When the money moved',
    detail            VARCHAR(4000)          COMMENT IS 'Exit identifiers, the KYC packet, or the send-back letter',
    entry_status      VARCHAR(10)   NOT NULL COMMENT IS 'FILED: entries are filed at once, never left as drafts',
    filed_at          TIMESTAMP              COMMENT IS 'When it was filed (simulated clock)',
    filed_by          VARCHAR(60)            COMMENT IS 'agent (carried out by the checker) or the officer',
    proposal_id       DECIMAL(18,0)          COMMENT IS 'Proposal the checker carried out',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'This bank''s CFCFRMS outbox: onward entries, exits, KYC packets and send-backs. The portal service collects them; onward entries become the next bank''s ticket.';

CREATE OR REPLACE TABLE HOLDER_MESSAGE (
    message_id  DECIMAL(18,0) IDENTITY COMMENT IS 'Message number',
    case_id     VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    account_id  VARCHAR(40)   NOT NULL COMMENT IS 'Account whose holder is told',
    hold_id     DECIMAL(18,0)          COMMENT IS 'Hold it is about',
    message_text VARCHAR(1000) NOT NULL COMMENT IS 'Which authority, which notice, how much is held. Never the evidence',
    written_at  TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock',
    stmt_id     DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'What the account holder is told about a hold: only the facts the MP High Court requires. Telling a mule why it is suspected warns it off.';

CREATE OR REPLACE TABLE DECISION (
    decision_id        DECIMAL(18,0) IDENTITY COMMENT IS 'Decision number',
    case_id            VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id    VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    account_id         VARCHAR(80)   NOT NULL COMMENT IS 'Account decided on',
    action_code        VARCHAR(20)   NOT NULL COMMENT IS 'PROPOSE_NARROW, PROPOSE_FULL_FREEZE, UNMATCHED_NOTICE or PROPOSE_RELEASE',
    proposal_id        DECIMAL(18,0)          COMMENT IS 'Proposal decided',
    decision_outcome   VARCHAR(10)   NOT NULL COMMENT IS 'APPROVED, EDITED or REJECTED',
    amount_inr         DECIMAL(18,2)          COMMENT IS 'Amount the officer approved (may differ from the proposal when EDITED)',
    proposed_amount    DECIMAL(18,2)          COMMENT IS 'Amount the agent proposed',
    decided_by         VARCHAR(60)   NOT NULL COMMENT IS 'Officer; never empty',
    decided_at         TIMESTAMP     NOT NULL COMMENT IS 'When the officer decided',
    reason_text        VARCHAR(2000)          COMMENT IS 'Officer''s reason: required for rejections and edits',
    draft_text         VARCHAR(4000)          COMMENT IS 'The reasons as drafted for the officer',
    signed_text        VARCHAR(4000)          COMMENT IS 'The reasons as the officer signed them; the difference from the draft is the record of human judgment',
    statement_ids      VARCHAR(200)           COMMENT IS 'Exasol statement IDs behind the numbers the officer saw'
) COMMENT IS 'Officer decisions on the four judgment calls. Append-only.';

-- ---------------------------------------------------------------- the case stays live (Module 5)

CREATE OR REPLACE TABLE WATCH_LIST (
    account_id      VARCHAR(40)   NOT NULL COMMENT IS 'Own account watched',
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Case it is watched for',
    first_pass      VARCHAR(12)   NOT NULL COMMENT IS 'MULE_LIKE, HONEST (an honest receiver: new money is not held) or CHECKED (added by the watch, never held)',
    reported_inr    DECIMAL(18,2)          COMMENT IS 'The ticket''s reported amount: the most this case may ever hold on the account',
    added_at        TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock',
    added_why       VARCHAR(400)           COMMENT IS 'In plain words',
    left_at         TIMESTAMP              COMMENT IS 'When it left the watch',
    left_why        VARCHAR(20)            COMMENT IS 'CLOSED (officer closed the case), POLICE_CONFIRMED (a later notice backed by an FIR) or DAY_75 (no police direction in 75 days)',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'Accounts the agent keeps watching after the first pass, with the first-pass decision that sets what the watch may do.';

CREATE OR REPLACE TABLE PAYMENT_REQUEST (
    request_id      VARCHAR(40)   NOT NULL COMMENT IS 'Request identifier',
    ts              TIMESTAMP     NOT NULL COMMENT IS 'When the holder asked to pay',
    from_account    VARCHAR(40)   NOT NULL COMMENT IS 'Own account asked to pay',
    to_account      VARCHAR(80)   NOT NULL COMMENT IS 'Intended recipient, or an exit pseudo-account',
    to_bank_id      VARCHAR(20)            COMMENT IS 'Bank of the intended recipient',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Amount',
    channel         VARCHAR(10)   NOT NULL COMMENT IS 'ATM, UPI, IMPS, CRYPTO ...',
    exit_identifier VARCHAR(200)           COMMENT IS 'ATM ID or wallet for an exit',
    card_ref        VARCHAR(30)            COMMENT IS 'Masked card, for ATM requests',
    data_source     VARCHAR(20)   NOT NULL COMMENT IS 'demo_case'
) COMMENT IS 'Simulation input: payments a holder tries only because the money is held (the next try after a refused withdrawal). In a real bank these are requests to the card or UPI switch.';

CREATE OR REPLACE TABLE DECLINED_ATTEMPT (
    attempt_id      DECIMAL(18,0) IDENTITY COMMENT IS 'Attempt number',
    account_id      VARCHAR(40)   NOT NULL COMMENT IS 'Own account that tried to pay',
    attempted_at    TIMESTAMP     NOT NULL COMMENT IS 'When',
    amount_inr      DECIMAL(18,2) NOT NULL COMMENT IS 'Amount refused',
    channel         VARCHAR(10)   NOT NULL COMMENT IS 'ATM, UPI, IMPS, CRYPTO ...',
    exit_identifier VARCHAR(200)           COMMENT IS 'ATM ID or wallet, for a cash-out',
    card_ref        VARCHAR(30)            COMMENT IS 'Masked card, for ATM attempts',
    intended_account VARCHAR(80)           COMMENT IS 'For a transfer: the account the holder tried to pay',
    intended_bank_id VARCHAR(20)           COMMENT IS 'For a transfer: that account''s bank',
    decline_reason  VARCHAR(20)   NOT NULL COMMENT IS 'LIEN (the amount is held) or FREEZE (the account is frozen)',
    txn_ref         VARCHAR(40)   NOT NULL COMMENT IS 'The TXN row or PAYMENT_REQUEST it refused',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'The declined-attempts feed: payments the bank refused because of a hold. Not transactions (no money moved). In a real bank they come from the card or UPI switch logs; here the simulated core banking writes them.';

CREATE OR REPLACE TABLE WATCH_EVENT (
    event_id        DECIMAL(18,0) IDENTITY COMMENT IS 'Event number',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    account_id      VARCHAR(40)   NOT NULL COMMENT IS 'Watched account',
    case_id         VARCHAR(40)            COMMENT IS 'Case the account is watched for',
    event_at        TIMESTAMP     NOT NULL COMMENT IS 'When it happened (simulated clock)',
    event_kind      VARCHAR(24)   NOT NULL COMMENT IS 'NEW_MONEY_HELD, NEW_MONEY_ABOVE_NOTICE, NEW_MONEY_HONEST, CASHOUT_BLOCKED, TRANSFER_BLOCKED, RECIPIENT_WATCHED, RECIPIENT_BUSINESS, RECIPIENT_OTHER_BANK, MOVED, LINKED_TO_VICTIM, WATCH_LEFT or RELEASE_FLAGGED',
    amount_inr      DECIMAL(18,2)          COMMENT IS 'Amount involved',
    counterparty    VARCHAR(200)           COMMENT IS 'Other side: account, ATM, wallet',
    channel         VARCHAR(10)            COMMENT IS 'Channel',
    txn_ref         VARCHAR(40)            COMMENT IS 'Transaction or request behind it',
    attempt_id      DECIMAL(18,0)          COMMENT IS 'Declined attempt behind it',
    hold_id         DECIMAL(18,0)          COMMENT IS 'Hold placed, if any',
    proposal_id     DECIMAL(18,0)          COMMENT IS 'Proposal made, if any',
    sentence        VARCHAR(1000)          COMMENT IS 'What happened and why, in plain words (fixed template, no model)',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'The watch: what happened on watched accounts after the first pass, and what the agent did about it.';

-- ---------------------------------------------------------------- the agent's runs, the morning log, today's desk

CREATE OR REPLACE TABLE AGENT_RUN (
    run_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Run number',
    case_id         VARCHAR(40)   NOT NULL COMMENT IS 'Ticket worked',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    run_mode        VARCHAR(10)   NOT NULL COMMENT IS 'pipeline (fixed order), llm (a local model picks the next tool; the pipeline takes over if it fails) or desk (a human desk, simulated)',
    model_name      VARCHAR(100)           COMMENT IS 'Local model used in llm mode',
    sim_started_at  TIMESTAMP     NOT NULL COMMENT IS 'Simulated clock when the ticket was picked up',
    sim_holds_at    TIMESTAMP              COMMENT IS 'Simulated clock when the holds landed',
    wall_started_at TIMESTAMP              COMMENT IS 'Real time the run started',
    wall_seconds    DOUBLE                 COMMENT IS 'Real seconds the whole run took',
    run_status      VARCHAR(12)            COMMENT IS 'DONE, SENT_BACK, LINKED, CHECK_FAILED or FAILED',
    summary_text    VARCHAR(4000)          COMMENT IS 'One-paragraph summary of the run (fixed template)'
) COMMENT IS 'One row per agent run over a ticket.';

CREATE OR REPLACE TABLE AGENT_STEP (
    run_id      DECIMAL(18,0) NOT NULL COMMENT IS 'Run (AGENT_RUN)',
    step_no     DECIMAL(4,0)  NOT NULL COMMENT IS 'Order within the run',
    tool_name   VARCHAR(40)   NOT NULL COMMENT IS 'Tool called (maker tools) or checker',
    chosen_by   VARCHAR(10)   NOT NULL COMMENT IS 'pipeline, llm, fallback (pipeline took over from the model) or runtime (the checker, always run)',
    can_change  BOOLEAN       NOT NULL COMMENT IS 'TRUE only for the checker''s steps: maker tools change nothing outside their own work tables',
    result_text VARCHAR(2000)          COMMENT IS 'What the tool found or did, in one line',
    stmt_ids    VARCHAR(400)           COMMENT IS 'Exasol statement IDs the tool ran',
    wall_ms     DECIMAL(9,0)           COMMENT IS 'Real milliseconds the tool took'
) COMMENT IS 'Append-only log of every tool call in a run, with the Exasol statements behind it.';

CREATE OR REPLACE TABLE MORNING_LOG (
    log_id          DECIMAL(18,0) IDENTITY COMMENT IS 'Log number',
    viewing_bank_id VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    period_from     TIMESTAMP              COMMENT IS 'Start of the period summarised',
    period_to       TIMESTAMP     NOT NULL COMMENT IS 'End of the period (the officer''s morning)',
    log_text        VARCHAR(8000) NOT NULL COMMENT IS 'What the agent did, in plain words; only the four decisions need action',
    written_by      VARCHAR(10)   NOT NULL COMMENT IS 'template, or model (from the audit log only, after the numbers check)',
    facts_text      VARCHAR(8000)          COMMENT IS 'The facts the log was written from (the audit log, summarised by fixed code)',
    written_at      TIMESTAMP              COMMENT IS 'Real time',
    stmt_id         DECIMAL(9,0)           COMMENT IS 'Exasol statement that wrote it'
) COMMENT IS 'The morning log: what the agent did overnight, which the officer reads at the start of the day.';

CREATE OR REPLACE TABLE TODAY_RAW (
    case_id      VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    probe_id     VARCHAR(80)   NOT NULL COMMENT IS 'Account whose action time this replay was cut at',
    row_kind     VARCHAR(5)    NOT NULL COMMENT IS 'HOLD, EXIT, MOVE or SHORT (as ATTRIBUTION_RAW)',
    ref_id       VARCHAR(80)   NOT NULL COMMENT IS 'Account, exit or transaction',
    component    VARCHAR(40)   NOT NULL COMMENT IS 'clean or a victim tag',
    paise        DECIMAL(18,0) NOT NULL COMMENT IS 'Amount in paise',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'LIBR replay cut at the moment today''s desk would reach each account (attribution UDF, same as step 2).';

CREATE OR REPLACE TABLE TODAY_OUTCOME (
    case_id           VARCHAR(40)   NOT NULL COMMENT IS 'Case',
    viewing_bank_id   VARCHAR(20)   NOT NULL COMMENT IS 'This bank',
    account_id        VARCHAR(80)   NOT NULL COMMENT IS 'Own account on the trail',
    hop               DECIMAL(3,0)  NOT NULL COMMENT IS 'Hops from the ticket''s payment',
    action_at         TIMESTAMP     NOT NULL COMMENT IS 'When today''s desk reaches it (desk opening + lookups)',
    balance_then_inr  DECIMAL(18,2)          COMMENT IS 'Balance at that moment',
    victim_then_inr   DECIMAL(18,2)          COMMENT IS 'Victim money still in it at that moment (LIBR)',
    today_action      VARCHAR(15)   NOT NULL COMMENT IS 'WHOLE_FREEZE, LIEN or NOTHING_LEFT',
    locked_inr        DECIMAL(18,2)          COMMENT IS 'Money locked by today''s action',
    honest_locked_inr DECIMAL(18,2)          COMMENT IS 'Locked money that is not victims'' money',
    stmt_id           DECIMAL(9,0)           COMMENT IS 'Exasol statement that produced this row'
) COMMENT IS 'Simulated baseline: the same ticket worked by today''s desk (bank hours, one lookup at a time, whole-account freezes).';

CREATE OR REPLACE TABLE AUDIT_LOG (
    event_id     DECIMAL(18,0) IDENTITY COMMENT IS 'Event number',
    event_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT IS 'Real time',
    sim_at       TIMESTAMP              COMMENT IS 'Simulated clock, when the event belongs to the simulated night',
    actor        VARCHAR(60)   NOT NULL COMMENT IS 'maker, checker, portal, admin, or the officer''s user name',
    event_name   VARCHAR(60)   NOT NULL COMMENT IS 'What happened',
    case_id      VARCHAR(40)            COMMENT IS 'Case, if any',
    account_id   VARCHAR(80)            COMMENT IS 'Account, if any',
    detail       VARCHAR(2000)          COMMENT IS 'Free text or JSON',
    session_id   DECIMAL(20,0)          COMMENT IS 'Exasol session',
    stmt_id      DECIMAL(9,0)           COMMENT IS 'Exasol statement ID of the step logged'
) COMMENT IS 'Append-only audit log: the maker and the checker may only insert into it, never change or delete a row.';