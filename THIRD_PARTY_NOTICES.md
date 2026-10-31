# Third-party notices

## IBM AMLworld (synthetic transactions)

Used as background traffic: HI-Small (about 5 million transactions), downloaded by hand from Kaggle
(`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`) into `data/raw/` and never committed. Amounts are
converted to rupees and timestamps moved to October 2026 when loaded (`docs/DECISIONS.md`, D-37).
Released by IBM under the Community Data License Agreement, Sharing, Version 1.0 (CDLA-Sharing-1.0).
Paper: Altman et al., "Realistic Synthetic Financial Transactions for Anti-Money Laundering Models", NeurIPS 2023.

## Exasol Personal

The database is proprietary software, free for personal use under the Exasol Personal EULA. It is not included
in or redistributed by this repository. The `exasol` launcher is MIT-licensed.

## Python libraries

pyexasol (MIT), pandas (BSD-3-Clause), NumPy (BSD-3-Clause), PyYAML (MIT), python-dotenv (BSD-3-Clause),
FastAPI (MIT), Uvicorn (BSD-3-Clause), pypdf (BSD-3-Clause). For development: pytest (MIT), Hypothesis (MPL-2.0),
Ruff (MIT), HTTPX (BSD-3-Clause).

Optional, for scanned notice letters: pytesseract (Apache-2.0) and pdf2image (MIT). Optional local model: Ollama
(MIT), with a model of your choice under that model's own licence.

## Officer console

React and React DOM (MIT), Vite and @vitejs/plugin-react (MIT). Fonts: Atkinson Hyperlegible Next and Atkinson
Hyperlegible Mono via Fontsource (SIL Open Font License 1.1).