# Third-party notices

## IBM AMLworld (synthetic transactions)

Used as background traffic: HI-Small (about 5 million transactions), downloaded by hand from Kaggle
(`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`) into `data/raw/` and never committed. Amounts are
converted to rupees and timestamps moved to October 2026 when loaded (`docs/DECISIONS.md`, D-37).
Released under a Community Data License Agreement.
TODO: confirm the exact CDLA variant on the Kaggle page and quote it here.
Paper: Altman et al., "Realistic Synthetic Financial Transactions for Anti-Money Laundering Models", NeurIPS 2023.

## Exasol Personal

The database is proprietary software, free for personal use under the Exasol Personal EULA. It is not included
in or redistributed by this repository. The `exasol` launcher is MIT-licensed.

## Python libraries

pyexasol (MIT), PyYAML (MIT), python-dotenv (BSD-3-Clause), pytest (MIT), Hypothesis (MPL-2.0), Ruff (MIT).
Front-end libraries are listed here when the console is added.