"""Read-only check: why did Bank X file no onward entry for M07 -> M14 (16:36) and M04 -> M14 (16:50)?
Run from the project folder:  python check_onward.py"""
from pipeline.db import connect

c = connect()


def show(title, sql):
    print(f"\n{title}")
    rows = c.execute(sql).fetchall()
    for r in rows:
        print("   ", tuple(str(x)[:60] if x is not None else None for x in r))
    if not rows:
        print("    (none)")


for case in ("CASE_X05", "CASE_X12"):
    show(f"{case}: intake at Bank X",
         f"SELECT match_status, related, related_case_id FROM PH_X.INTAKE_CHECK WHERE case_id = '{case}'")
    show(f"{case}: ticket and agent run",
         f"SELECT t.ticket_status, t.received_at, r.run_status, SUBSTR(r.summary_text, 1, 300) FROM PH_X.TICKET t "
         f"LEFT JOIN PH_X.AGENT_RUN r ON r.run_id = t.run_id WHERE t.case_id = '{case}'")
    show(f"{case}: trail at Bank X (hop, from, to, amount, time, own bank, exit)",
         f"SELECT hop, from_account, to_account, amount_inr, ts, is_own_bank, is_exit FROM PH_X.TRACE_EDGE "
         f"WHERE case_id = '{case}' ORDER BY hop, ts")
    show(f"{case}: what Bank X filed (kind, account, utr, amount, status)",
         f"SELECT entry_kind, account_id, utr, amount_inr, entry_status FROM PH_X.PORTAL_ENTRY "
         f"WHERE case_id = '{case}' ORDER BY entry_id")
    show(f"{case}: proposals (action, account, status, checker note)",
         f"SELECT action_code, account_id, proposal_status, SUBSTR(checker_note, 1, 120) FROM PH_X.PROPOSAL "
         f"WHERE case_id = '{case}' ORDER BY proposal_id")
show("Watch events on M07, M04, M10 at Bank X (time, account, kind, amount, counterparty)",
     "SELECT event_at, account_id, event_kind, amount_inr, counterparty FROM PH_X.WATCH_EVENT "
     "WHERE account_id IN ('ACC_M07', 'ACC_M04', 'ACC_M10') ORDER BY event_at, event_id")
show("Payments out of M07 and M04 that went through (Bank X's view)",
     "SELECT txn_id, ts, from_account, to_account, amount_inr FROM PH_X.TXN_LIVE "
     "WHERE from_account IN ('ACC_M07', 'ACC_M04') AND ts >= '2026-10-18 16:00:00' ORDER BY ts")
show("The open pre-alert on M10 at Bank X",
     "SELECT proposal_id, proposed_at, SUBSTR(reasons_json, 1, 300) FROM PH_X.PROPOSAL "
     "WHERE action_code = 'PROPOSE_PRE_ALERT' AND account_id = 'ACC_M10'")
c.close()