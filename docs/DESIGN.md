# Design: a golden-hour copilot for the bank nodal officer

The agent works every cyber-fraud ticket the moment it lands, at any hour: it traces the money through the bank,
works out how much of each account is the victim's, places the holds the rules require, files the onward entries
and exits, and hands the officer a ready proposal for every judgment call, sized to the victim's share, with reasons.

> **The agent orchestrates, the database calculates, the rules permit, the officer decides.**

Why this and not something else is in [PROBLEM_DISCOVERY.md](PROBLEM_DISCOVERY.md): the money is decided at the
first bank inside the golden hour, and the bank nodal officer's ticket work is a fixed script (find, hold, report
onward, report exits, upload KYC) that cannot refuse an alert. That makes it the cleanest role for an agent to take
over with a human reviewer.

## What happens to one ticket

```
 CFCFRMS ticket lands (02:13)                                     simulated queue: PH.TICKET
        |
   read_ticket ── incomplete notice? ──> send_back_for_correction (nothing frozen on bad data)
        |
   trace_money             Exasol: TRACE_LINK join + CONNECT BY NOCYCLE          sql/steps/040_trace.sql
   attribute_victim_share  Exasol: Python UDF, lowest-balance rule (LIBR)        sql/steps/050_attribute.sql
   get_account_evidence    Exasol: eight signals over 90 days                    sql/steps/060_score.sql
   allowed_actions         Exasol: the rules engine (config/rules.yaml)          sql/steps/070_actions.sql
        |
   CHECKER (always runs)   Exasol: conservation to the paisa + policy checks     sql/steps/075_check.sql
        |  any failure: no change-making tool is offered; the case goes to the officer
        v
   place_mandatory_hold    lien up to the trail amount, capped at the balance    sql/steps/081_hold.sql
   record_exit             ATM / crypto / purchase, with identifiers             sql/steps/082_exits.sql
   draft_onward_entry      one per transfer to another bank, with victim share   sql/steps/083_onward.sql
   draft_kyc_packet        drafted; the officer sends it                         sql/steps/084_kyc.sql
   escalate_to_officer     narrow / full freeze / borderline -> "Waiting for you"
   write_case_summary      for the 9 a.m. review
```

Every tool call goes to the append-only `PH.AGENT_STEP` with the Exasol statement IDs behind it.

## The watch: after the holds

The run ends when the holds are placed, but the money keeps moving. The watch (`pipeline/watch.py`) replays every
later transaction that touches a watched account (one the agent held, or one with a freeze waiting for the officer)
in time order, as the core banking system would see it:

| Event | What happened | What the agent may do |
| --- | --- | --- |
| BLOCKED | A debit needed held money, so it was refused; the money stays | Add it to the evidence; out of hours, also alert the officer |
| MOVED | A mule-like account moved money that was not held | Add it to the evidence; out of hours, also alert |
| NEW_MONEY | A mule-like account received new money that a freeze would protect | Add it to the evidence; out of hours, also alert |
| MARKER_HOLD | A first-layer marker caught a later credit | Hold it (the marker rule), within the trail amount; out of hours, also alert |

A refused debit never reaches its receiver, so one block can stop a whole chain. The rules decide what is allowed for
each event; the controller chooses within that: the fixed policy alerts out of hours on any refused exit or an amount
of ₹25,000 or more, or the local model chooses and words the alert (same fallback and number check as the run). The
only money the watch can hold is a marker's catch. Events land in `PH.WATCH_EVENT` and show under each decision as
"Since the hold". On the demo night the watch refuses C's ATM withdrawal at 04:30 and A's crypto transfer at 05:10,
and alerts the officer both times.

## Mule networks

A ticket shows one victim's trail, but the same mule accounts turn up across many tickets and pay each other.
`pipeline/networks.py` links them after the watch: the candidates are own-bank accounts on worked tickets that look
like mules or have a freeze waiting; two are linked when money moved directly between them in the 30 days before
the latest ticket (one pass over `PH.TXN` in Exasol, cases and AMLworld alike) or when both cashed out at the same
ATM or wallet. Connected groups of two or more are networks (`PH.NETWORK_MEMBER`, `PH.NETWORK_LINK`).

On Review a network is one card and one decision: "Freeze all N accounts" approves every waiting freeze of its
members across all their tickets, with one set of reasons that doubles as the note to the police listing the tickets
to investigate together. On the 22 demo tickets the agent finds four: a ring of eight accounts around Mule 10, three
around Mule 11, A with C from the 02:13 ticket, and the loop case.

## Automatic vs officer (maker, checker, approver)

| The agent does at once, any hour | The officer decides |
| --- | --- |
| Hold up to the amount the trail names on each own-bank account, capped at the balance (the bank cannot refuse) | **Narrow:** a likely honest receiver holds less victim money than the mandatory hold; narrow the lien to the victims' share |
| First-layer account already empty: a marker that catches later credits | **Widen:** a mule-like account showing the pattern courts accept (repeated complaints, or pass-through plus cash-outs); a whole-account freeze, with the material written down |
| File every exit (ATM, crypto) with its identifiers | **Confirm:** borderline evidence with nothing to narrow |
| File every onward entry with the victim's share, so the next bank gets its ticket now | Send each drafted KYC packet |

- **Maker:** the agent (`pipeline/agent.py`).
- **Checker:** deterministic SQL. Amounts reconcile to the paisa, no automatic hold exceeds the balance or the trail,
  only the four automatic actions are automatic, holds only on own accounts, no freeze proposal on nodal, pool or
  escrow accounts. A second model as checker would be weaker than this.
- **Approver:** the officer (`pipeline/officer.py`). No tool, route or button places a full freeze without an
  officer's name and written reasons.

## Where AI fits

Tracing, attribution and the rules are deterministic, which is what makes each hold defensible. A local model
(Ollama, free and offline, off by default) can do two things:

1. **Choose the next tool** from the ones the runtime allows at that moment. The order is guarded by prerequisites
   and the checker; a request for a tool that is not allowed is overruled and logged; if the model is slow, down or
   wrong, the fixed order takes over for the rest of the run.
2. **Write for people:** the case summary and send-back notes. Every rupee figure it writes must already be in the
   facts it was given, or its text is thrown away and the template is used.

3. **Respond to the watch's events out of hours:** add the event to the evidence only, or also alert the officer,
   from the options the rules allow for that event.
4. **Draft the reasons behind a whole-account freeze** (`pipeline/reasons.py`). Fixed code gathers the facts: the
   tickets that name the account, the victims' money in it, its 90-day history and what the watch saw after the
   hold. The model rewrites them as one paragraph; every number in it (rupees, percentages, days, times) must
   already be in the facts, or the fixed wording is used. The officer edits it before approving, and the approved
   text goes into the audit log and the reply to the police.
5. **Read police notices that arrive as messy text** (`pipeline/notices.py`). The model extracts the UTR, amount,
   date, legal provision and references as JSON; a value is kept only if the notice itself bears it out, otherwise
   the fixed parser's reading is used (and the parser reads alone when the model is off). Exasol then checks the
   notice against the transactions (the UTR's payment, its amount and date, the nearest UTR received that day by
   `EDIT_DISTANCE`), fixed rules decide, and for a notice that cannot be acted on the model may word the letter
   asking the police to correct it, with every number and UTR checked.

The model never does arithmetic on money, never chooses an action, never writes to the database. `make agent` runs
the fixed order; `make agent MODE=llm` lets the model orchestrate. Both produce the same holds.

## The demo ticket

Invented people and numbers (`data/demo_case.yaml`). Lakshmi pays ₹2,00,000 at 01:52; the ticket reaches Bank X at
02:13. By then A has sent ₹70,000 to B at Bank Y and ₹80,000 to C; C has paid Murugan's fuel pump ₹1,000 for diesel
and withdrawn ₹50,000 at an ATM. Without anyone acting, C cashes out the rest at 04:30 and A at 05:10.

| | Today's desk (opens 10:00) | With the agent |
| --- | --- | --- |
| Victim's money held at Bank X | ₹500 | ₹80,000 (A ₹50,000, C ₹29,500, Murugan ₹500 of his ₹1,000) |
| Honest money locked | ₹8,19,500 (Murugan's whole account) | ₹500 overnight, ₹0 once the officer narrows |
| Ticket to first action | 8 h 7 min | 3 min |
| Bank Y gets its onward ticket | 10:35 | 02:16 |

At the morning review three decisions wait: narrow Murugan's lien to ₹500 (only that much is traceable to the victim
under the lowest-balance rule), and full freezes proposed for A and C (both opened weeks ago, forward money within
minutes, cash out).

## Where Exasol does the work

Every number above is computed in Exasol Personal: the recursive trace, the attribution replay (a Python UDF, in
integer paise), the eight signals, the rules engine, the checker, the hold ledger and portal entries, and the
"today's desk" replay for the Compare screen (the same UDF, cut at each account's desk time). Python passes
parameters and orders the tools; it does no money arithmetic. Every result row carries the statement that wrote it.

## What was cut, and why

| Cut | Why |
| --- | --- |
| Lane B (RBI draft, bank-initiated holds) | Draft rules, a separate legal flow, outside the golden-hour story |
| Customer notices in English, Hindi and Tamil | Real pain, but not data-heavy; a project of its own |
| Deadline countdowns (7, 15, 90 days) | Phase 3 machinery, outside the build zone |
| Victim status and receiver notice views | Nice to have, not core |
| Investigator plain-English Q&A | No pain behind it in the journey; the read-only MCP settings remain for anyone who wants it |
| FIFO and pro-rata comparison on screen | Adds explanation, not decisions. Both rules are still computed and tested; pro rata splits several victims |

## Assumptions (open questions from the discovery)

- Same-bank hops are handled in the same pass as the ticket (one trace finds them all).
- The cross-bank trail is simulated by a shared synthetic dataset; a real bank sees only its own accounts, and hops
  to other banks travel through CFCFRMS as onward entries.
- Onward entries and exits are filed automatically, because the bank must report them and every minute counts for
  the next bank; KYC packets wait for the officer (due within a week).
- Today's desk is modelled as: opens at 10:00, 20 minutes for the first lookup, 15 per further account, whole-account
  freezes. These are stated settings (`config/rules.yaml`, `data/demo_case.yaml`), not measurements.
- Scoring weights are hand-set for the demo and not validated on real data. The evidence is an input to the rules,
  never shown as a verdict.