"""Print the oracle's answer for a case, all three rules side by side.

    python -m tests.show_case                       # demo case at tool_as_of
    python -m tests.show_case data/loop_case.yaml
    python -m tests.show_case --as-of "2026-10-18 13:05:00"

`make explain` prints this next to Exasol's tables, so the first step that disagrees is easy to spot.
"""
from __future__ import annotations

import argparse

from pipeline.cases import DEMO_CASE, load_case
from tests.helpers import run
from tests.oracle import RULES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEMO_CASE))
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args()

    case = load_case(args.path)
    as_of = args.as_of or case.tool_as_of
    results = {rule: run(case, rule, as_of) for rule in RULES}
    places = sorted({p for r in results.values() for p in list(r.holdings) + list(r.exits)
                     if any(r.tainted(p) for r in results.values())})

    print(f"{case.case_id}  as_of={as_of or 'end'}  (victim money, Rs)")
    print(f"{'account / exit':32}" + "".join(f"{rule:>14}" for rule in RULES))
    for place in places:
        print(f"{place:32}" + "".join(f"{results[rule].tainted(place) / 100:>14,.2f}" for rule in RULES))
    for rule, r in results.items():
        for victim, reported in r.reported.items():
            ok = "ok" if r.victim_total(victim) == reported else "BROKEN"
            print(f"conservation {rule:9} {victim}: {r.victim_total(victim) / 100:,.2f} of "
                  f"{reported / 100:,.2f}  {ok}")


if __name__ == "__main__":
    main()