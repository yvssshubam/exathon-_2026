"""Generate the 20 extra victims and their mule network (requirements section 8: "plus 20 extra").

Deterministic: fixed seed 42, so the file is identical on every machine (NFR-04).
The output is committed as data/extra_cases.yaml; tests check that regenerating it changes nothing.

Shape, chosen to exercise the tests:
  * victims 1-3 all pay first-layer mule ACC_M01 -> a mixed account with 3 victims (AT-06, FR-17)
  * first-layer mules (Bank X) forward 90-100% within minutes to second-layer mules (Banks X/Y)
  * second-layer mules cash out by ATM or crypto and sometimes pay honest merchants
  * every outflow is covered by money that arrived earlier, so there are no opening shortfalls

Usage:  python -m pipeline.gen_extra_cases [--check]
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "extra_cases.yaml"
SEED = 42
DAY = datetime(2026, 10, 18)
FMT = "%Y-%m-%d %H:%M:%S"


def generate(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    accounts: list[dict] = []
    txns: list[dict] = []
    complaints: list[dict] = []

    def acct(aid, bank, holder, kind, htype, opened_days_ago, band, opening, history):
        accounts.append({
            "account_id": aid, "bank_id": bank, "holder": holder, "account_type": kind,
            "holder_type": htype, "opened_on": (DAY - timedelta(days=opened_days_ago)).strftime("%Y-%m-%d"),
            "profile_band": band, "opening_balance_inr": opening, "history": history,
        })

    first = [f"ACC_M{i:02d}" for i in range(1, 9)]
    second = [f"ACC_M{i:02d}" for i in range(9, 15)]
    merchants = [f"ACC_MER{i:02d}" for i in range(1, 5)]
    for aid in first:
        acct(aid, "BANK_X", f"Account M{aid[-2:]}", "savings", "individual", rng.randint(5, 60), "low",
             float(rng.randint(0, 20) * 100), "fast_pass_through")
    for aid in second:
        acct(aid, rng.choice(["BANK_X", "BANK_Y"]), f"Account M{aid[-2:]}", "savings", "individual",
             rng.randint(5, 80), "low", 0.0, "fast_pass_through")
    for i, aid in enumerate(merchants):
        acct(aid, ["BANK_X", "BANK_Z", "BANK_X", "BANK_Z"][i], f"Merchant {i + 1}", "current", "merchant",
             rng.randint(400, 3000), "high", float(rng.randint(20, 150) * 10000), "merchant_300_per_day")

    seq = {"n": 0}

    def utr(channel: str, ts: datetime) -> str:
        seq["n"] += 1
        return f"{channel}{ts.strftime('%Y%m%d%H%M')}{seq['n']:06d}"

    def add(vid: int, n: int, ts: datetime, frm: str, to: str, rupees: int, channel: str, **extra) -> None:
        row = {"txn_id": f"T_X{vid:02d}_{n:02d}", "utr": utr(channel, ts), "ts": ts.strftime(FMT),
               "from": frm, "to": to, "amount_inr": float(rupees), "channel": channel}
        row.update(extra)
        txns.append(row)

    for v in range(1, 21):
        victim_acc = f"ACC_VX{v:02d}"
        tag = f"V_X{v:02d}"
        acct(victim_acc, rng.choice(["BANK_Y", "BANK_Z"]), f"Victim {v}", "savings", "individual",
             rng.randint(800, 9000), "low", None, "none")

        mule = "ACC_M01" if v <= 3 else rng.choice(first[1:])
        t0 = DAY + timedelta(hours=9, minutes=rng.randint(0, 480))
        amount = rng.randint(200, 3000) * 100
        n = 1
        add(v, n, t0, victim_acc, mule, amount, "IMPS", victim_tag=tag)

        # First layer: forward 90-100% in one or two transfers, maybe one small merchant payment.
        budget = amount * rng.randint(90, 100) // 100
        t = t0 + timedelta(minutes=rng.randint(3, 25))
        if rng.random() < 0.3:
            pay = rng.randint(2, 20) * 100
            n += 1
            add(v, n, t, mule, rng.choice(merchants), pay, "UPI")
            budget -= pay
            t += timedelta(minutes=rng.randint(1, 5))
        parts = [budget] if rng.random() < 0.5 else [budget // 2, budget - budget // 2]
        for part in parts:
            dest = rng.choice(second)
            n += 1
            add(v, n, t, mule, dest, part, "IMPS")
            # Second layer: cash out 40-70%, maybe a merchant payment.
            t2 = t + timedelta(minutes=rng.randint(5, 40))
            out = part * rng.randint(40, 70) // 100
            n += 1
            if rng.random() < 0.6:
                atm = f"BX-ATM-{rng.randint(100, 999):04d}"
                add(v, n, t2, dest, f"EXIT:ATM:{atm}", out, "ATM", exit_identifier=f"ATM {atm}")
            else:
                wallet = f"0x{rng.getrandbits(40):010x}"
                add(v, n, t2, dest, f"EXIT:CRYPTO:{wallet}", out, "CRYPTO",
                    exit_identifier=f"Crypto wallet {wallet}")
            if rng.random() < 0.5:
                n += 1
                spend = min(rng.randint(5, 50) * 100, part - out)
                add(v, n, t2 + timedelta(minutes=rng.randint(1, 30)), dest, rng.choice(merchants), spend, "UPI")
            t += timedelta(minutes=rng.randint(1, 4))

        lane = "A"                     # police-instructed holds only (docs/DESIGN.md: Lane B is out of scope)
        source = rng.choice(["1930", "NCRP"])
        complaints.append({
            "case_id": f"CASE_X{v:02d}",
            "ack_no": ("3" if source == "1930" else "2") + f"{rng.randint(0, 10**13 - 1):013d}",
            "source": source, "victim_account": victim_acc, "victim_tag": tag,
            "utr": next(r["utr"] for r in txns if r["txn_id"] == f"T_X{v:02d}_01"),
            "amount_inr": float(amount), "txn_at": t0.strftime(FMT),
            "reported_at": (t0 + timedelta(minutes=rng.randint(30, 180))).strftime(FMT),
            "beneficiary_account": mule, "lane": lane,
            "notice_legal_basis": "S.168 r/w S.94 BNSS",
            "notice_has_amount": True,
        })

    return {
        "case_id": "CASE_EXTRA",
        "generated_by": "pipeline/gen_extra_cases.py",
        "seed": seed,
        "banks": [
            {"bank_id": "BANK_X", "name": "Bank X", "is_own_bank": True},
            {"bank_id": "BANK_Y", "name": "Bank Y", "is_own_bank": False},
            {"bank_id": "BANK_Z", "name": "Bank Z", "is_own_bank": False},
        ],
        "complaints": complaints,
        "accounts": accounts,
        "transactions": sorted(txns, key=lambda r: (r["ts"], r["txn_id"])),
    }


def render(data: dict) -> str:
    header = "# GENERATED by pipeline/gen_extra_cases.py (seed 42). Do not edit by hand; regenerate.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = ap.parse_args()
    text = render(generate())
    if args.check:
        if not OUT.exists() or yaml.safe_load(OUT.read_text(encoding="utf-8")) != generate():
            print(f"{OUT} is stale: run python -m pipeline.gen_extra_cases", file=sys.stderr)
            return 1
        print(f"{OUT} is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())