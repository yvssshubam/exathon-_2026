"""90 days of synthetic history for the case accounts, so the mule-or-honest signals have something to read.

Deterministic (seed 42, NFR-04) and generated in memory at load time, so no large file is committed.
Everything ends before 18 Oct 2026 00:00, the moment the opening balances are taken, so the case-day numbers
(section 9) are untouched (DECISIONS D-23).

Two shapes, chosen by each account's `history` field in the case files:
  merchant_300_per_day  ~300 small UPI payments a day from thousands of different customers, swept to a
                        supplier once a day in the evening (Murugan's pump, the extra merchants)
  fast_pass_through     a handful of large credits from a few payers, sent on within minutes, often as cash
                        (A, B, C and the generated mules); only after the account was opened
Counterparties that are not case accounts are named EXT_... and are not rows in PH.ACCOUNT.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

SEED = 42
END = datetime(2026, 10, 18)            # exclusive: history stops before the case day
DAYS = 90
FMT = "%Y-%m-%d %H:%M:%S"


def _row(txn_id, utr, ts, frm, to, rupees, channel, exit_id=None):
    return {"txn_id": txn_id, "utr": utr, "ts": ts.strftime(FMT), "from": frm, "to": to,
            "amount_paise": int(rupees) * 100, "channel": channel, "exit_identifier": exit_id}


def merchant(acc: str, start: datetime, rng: random.Random) -> list[dict]:
    rows, n = [], 0
    day = start
    while day < END:
        takings = 0
        for _ in range(rng.randint(270, 330)):
            ts = day + timedelta(seconds=rng.randint(7 * 3600, 22 * 3600 + 1800))
            rupees = rng.randint(5, 200) * 10                     # Rs 50 - Rs 2,000: fuel, snacks, small goods
            n += 1
            rows.append(_row(f"H_{acc}_{n:06d}", f"UPIH{acc[-4:]}{n:08d}", ts,
                             f"EXT_PAYER_{rng.randint(1, 5000):05d}", acc, rupees, "UPI"))
            takings += rupees
        n += 1
        sweep = takings * 85 // 100                               # pays the supplier most of the day's takings
        rows.append(_row(f"H_{acc}_{n:06d}", f"NEFTH{acc[-4:]}{n:08d}", day + timedelta(hours=21, minutes=30),
                         acc, f"EXT_SUPPLIER_{acc}", sweep, "NEFT"))
        day += timedelta(days=1)
    return rows


def pass_through(acc: str, start: datetime, rng: random.Random) -> list[dict]:
    rows, n = [], 0
    span = max(int((END - start).total_seconds()) - 3600, 3600)
    feeders = [f"EXT_FEEDER_{acc}_{i}" for i in range(1, 4)]
    episodes = sorted(start + timedelta(seconds=rng.randint(0, span)) for _ in range(rng.randint(5, 8)))
    for k, t0 in enumerate(episodes):
        credit = rng.randint(200, 1500) * 100                     # Rs 20,000 - Rs 1,50,000
        n += 1
        rows.append(_row(f"H_{acc}_{n:06d}", f"IMPSH{acc[-4:]}{n:08d}", t0, rng.choice(feeders), acc,
                         credit, "IMPS"))
        out = credit * rng.randint(95, 100) // 100
        t = t0 + timedelta(minutes=rng.randint(2, 20))
        cash = out * rng.randint(30, 60) // 100 if (k == 0 or rng.random() < 0.5) else 0
        if cash:
            atm = f"EXT-ATM-{rng.randint(100, 999)}"
            n += 1
            rows.append(_row(f"H_{acc}_{n:06d}", f"ATMH{acc[-4:]}{n:08d}", t, acc, f"EXIT:ATM:{atm}", cash,
                             "ATM", f"ATM {atm}"))
            t += timedelta(minutes=rng.randint(1, 5))
        n += 1
        rows.append(_row(f"H_{acc}_{n:06d}", f"IMPSH{acc[-4:]}{n:08d}", t, acc,
                         f"EXT_NEXT_{rng.randint(1, 500):03d}", out - cash, "IMPS"))
    return rows


def generate(accounts: list[dict], seed: int = SEED) -> list[dict]:
    """accounts: the case files' account dicts (account_id, opened_on, history)."""
    rng = random.Random(seed)
    window_start = END - timedelta(days=DAYS)
    rows: list[dict] = []
    for a in sorted(accounts, key=lambda a: a["account_id"]):
        opened = datetime.strptime(str(a["opened_on"]), "%Y-%m-%d")
        start = max(opened, window_start)
        if start >= END:
            continue
        if a.get("history") == "merchant_300_per_day":
            rows += merchant(a["account_id"], start, rng)
        elif a.get("history") == "fast_pass_through":
            rows += pass_through(a["account_id"], start + timedelta(hours=1), rng)
    return rows