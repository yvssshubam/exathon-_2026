"""Read-only check: were the pre-alerts on Account M14 still open when the note was written, and did Bank Y get
the onward tickets that should close them? Run from the project folder:  python check_prealerts.py"""
from pipeline.db import connect

c = connect()


def q(sql):
    return c.execute(sql).fetchall()


print("Simulated clock now:", q("SELECT MAX(clock_at) FROM PH_PORTAL.SIM_CLOCK")[0][0])
for schema in ("PH_X", "PH_Y"):
    print(f"\n{schema}: latest handover note covers up to", q(f"SELECT MAX(period_to) FROM {schema}.MORNING_LOG")[0][0])
    print(f"{schema}: pre-alerts")
    for r in q(f"SELECT proposal_id, account_id, proposal_status, proposed_at, checker_note FROM {schema}.PROPOSAL "
               "WHERE action_code = 'PROPOSE_PRE_ALERT' ORDER BY proposal_id"):
        print("   ", r)
print("\nPH_Y: tickets naming payments into ACC_M14")
for r in q("SELECT t.case_id, c.ticket_kind, c.utr, c.amount_inr, t.received_at, t.ticket_status FROM PH_Y.TICKET t "
           "JOIN PH_Y.COMPLAINT c ON c.case_id = t.case_id WHERE c.beneficiary_account = 'ACC_M14' "
           "ORDER BY t.received_at"):
    print("   ", r)
c.close()