"""python -m app.api [--port 8000] [--reload]  starts the API on http://127.0.0.1:8000 (docs at /docs)."""
import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="restart when code changes (development)")
    args = ap.parse_args()
    uvicorn.run("app.api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()