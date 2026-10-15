"""Optional LLM backends. All are untrusted: output is parsed, schema-checked and grounded.

none            -> rules only (default, offline, Rs 0 per run)
ollama          -> local model via http://localhost:11434 (env BOL2KAAM_MODEL, default qwen2.5:0.5b)
fake-good       -> test double that returns correct JSON
fake-malformed  -> test double returning truncated JSON (failure demo)
fake-hallucinate-> test double returning an invented amount (failure demo)
"""
import json
import os
import re
import urllib.request


class LLMError(Exception):
    pass


class Ollama:
    name = "ollama"

    def __init__(self, model=None, url=None, timeout=20):
        self.model = model or os.environ.get("BOL2KAAM_MODEL", "qwen2.5:0.5b")
        self.url = url or os.environ.get("BOL2KAAM_OLLAMA", "http://localhost:11434/api/generate")
        self.timeout = timeout

    def complete(self, prompt, hint=None):
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False, "format": "json",
                           "options": {"temperature": 0}}).encode()
        try:
            req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())["response"]
        except Exception as e:  # connectivity, timeout, bad payload
            raise LLMError("ollama unavailable: %s" % type(e).__name__)


class FakeGood:
    name = "fake-good"

    def complete(self, prompt, hint=None):
        d = {k: v for k, v in (hint or {}).items() if k in ("sales", "expenses", "credits", "reminders", "exceptions")}
        return json.dumps(d)


class FakeMalformed:
    name = "fake-malformed"

    def complete(self, prompt, hint=None):
        return '{"sales": [ {"item": "biscuit", "amount": '


class FakeHallucinate:
    name = "fake-hallucinate"

    def complete(self, prompt, hint=None):
        return json.dumps({"sales": [{"item": "x", "qty": 1, "unit": None, "amount": 99999, "customer": None}]})


def get_backend(name):
    name = (name or "none").lower()
    if name == "none":
        return None
    table = {"ollama": Ollama, "fake-good": FakeGood, "fake-malformed": FakeMalformed,
             "fake-hallucinate": FakeHallucinate}
    if name not in table:
        raise ValueError("unknown llm backend: %s" % name)
    return table[name]()


def parse_json_strict(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except ValueError as e:
        raise LLMError("malformed JSON: %s" % e)
