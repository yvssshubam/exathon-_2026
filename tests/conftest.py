import os
import sys
from pathlib import Path

# The tests check the fixed pipeline's exact words, so the local model stays off even when .env turns it on
# (python-dotenv never overrides a variable that is already set). The model's own tests use a fake model.
os.environ["PH_LLM"] = "off"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))