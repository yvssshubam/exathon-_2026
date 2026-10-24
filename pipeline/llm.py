"""Optional local model for the agent (docs/DESIGN.md, "Where AI fits"). Free and offline: Ollama on the laptop.

The model only picks the next tool from the ones the runtime says are allowed, and writes words for people
(the case summary, a send-back note). It never computes an amount, never chooses an action, never writes to the
database. If it is switched off, not running, slow or wrong, the agent's fixed pipeline does the same work.

Settings (.env):
  PH_LLM=ollama                      turn it on (default: off)
  OLLAMA_URL=http://127.0.0.1:11434  where Ollama listens
  OLLAMA_MODEL=qwen2.5:7b            any local model with tool calling that fits an 8 GB GPU (qwen3:8b too;
                                     its thinking is switched off so it answers directly)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMUnavailable(RuntimeError):
    """The model could not be reached or answered with something unusable."""


def settings() -> dict:
    try:
        from dotenv import load_dotenv

        from pipeline.db import ROOT

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return {
        "enabled": os.environ.get("PH_LLM", "").strip().lower() == "ollama",
        "url": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
    }


class Ollama:
    """Minimal client for Ollama's /api/chat (standard library only)."""

    def __init__(self, url: str, model: str, timeout: float = 60.0):
        self.url, self.model, self.timeout = url, model, timeout
        self.think_off = True

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0}}
        if tools:
            body["tools"] = tools
        if self.think_off:
            body["think"] = False          # thinking models (qwen3, ...): answer directly, much faster
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                msg = json.loads(resp.read().decode())["message"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if self.think_off and exc.code == 400 and "think" in detail.lower():
                self.think_off = False     # a model without a thinking switch: ask again without it
                return self.chat(messages, tools)
            raise LLMUnavailable(f"Ollama at {self.url} ({self.model}): {exc} {detail[:200]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
            raise LLMUnavailable(f"Ollama at {self.url} ({self.model}): {exc}") from exc
        if msg.get("content"):
            msg["content"] = THINK.sub("", msg["content"]).strip()   # older Ollama leaves the thinking in
        return msg

    def pick_tool(self, system: str, state: str, tools: list[dict]) -> str:
        """Ask for exactly one tool call; return its name."""
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": state}], tools)
        calls = msg.get("tool_calls") or []
        if calls:
            return calls[0]["function"]["name"]
        # Some small models answer in text; accept a bare tool name.
        text = (msg.get("content") or "").strip()
        names = [t["function"]["name"] for t in tools]
        for name in names:
            if re.search(rf"\b{re.escape(name)}\b", text):
                return name
        raise LLMUnavailable(f"no tool call in the reply: {text[:120]!r}")

    def write(self, system: str, facts: str) -> str:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": facts}])
        text = (msg.get("content") or "").strip()
        if not text:
            raise LLMUnavailable("empty reply")
        return text


def client() -> Ollama | None:
    s = settings()
    return Ollama(s["url"], s["model"]) if s["enabled"] else None


AMOUNT = re.compile(r"(?:Rs\.?|₹)\s?([\d,]+(?:\.\d{1,2})?)")


def amounts(text: str) -> set[str]:
    """Every rupee figure in a text, normalised ('Rs 1,000' and '₹1000.00' both -> '1000')."""
    out = set()
    for m in AMOUNT.finditer(text):
        value = m.group(1).replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        out.add(value)
    return out


def numbers_are_grounded(written: str, facts: str) -> bool:
    """The guardrail on model text: every rupee figure it writes must already be in the facts it was given.
    The model may reword; it may not do arithmetic on money."""
    return amounts(written) <= amounts(facts)