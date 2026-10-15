"""Workflow orchestration: state machine, store, confidence, escalation, aggregate export."""
import datetime as dt
import hashlib
import json
import os
import time

from . import extract as ex
from . import llm as llmmod
from . import pii, prompts, validate
from .audit import AuditLog
from .transcribe import TranscriptionUnavailable, transcribe

TRANSITIONS = {
    "RECEIVED": {"REDACTED", "FAILED"},
    "REDACTED": {"EXTRACTED", "FAILED"},
    "EXTRACTED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"READY_FOR_CONFIRMATION", "NEEDS_CLARIFICATION", "ESCALATED"},
    "NEEDS_CLARIFICATION": {"REDACTED", "ESCALATED"},
    "READY_FOR_CONFIRMATION": {"CONFIRMED", "REDACTED"},
    "ESCALATED": {"CONFIRMED", "CLOSED"},
    "CONFIRMED": {"CLOSED"},
    "FAILED": {"REDACTED", "CLOSED"},
    "CLOSED": set(),
}

ESC_TARGET = {
    "SECRET_SHARED": ("Eko risk team", "OTP/PIN share ho gaya hai. Abhi PIN badlein aur kisi ko na batayein."),
    "MONEY_DEDUCTED": ("Eko payments support", "Paise kat gaye - transaction check karwana hai."),
    "FRAUD": ("Eko risk team", "Dhokha/fraud ki report - human review zaroori."),
    "SAFETY": ("Field supervisor", "Suraksha ka mamla - turant supervisor se baat karein."),
}


class InvalidTransition(Exception):
    pass


class Store:
    def __init__(self, home):
        self.home = home
        os.makedirs(os.path.join(home, "notes"), exist_ok=True)
        self.audit = AuditLog(os.path.join(home, "audit.jsonl"))
        self.vault_path = os.path.join(home, "vault.json")  # LOCAL ONLY: name -> alias

    def vault(self):
        if os.path.exists(self.vault_path):
            with open(self.vault_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_vault(self, v):
        with open(self.vault_path, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False)
        try:
            os.chmod(self.vault_path, 0o600)
        except OSError:
            pass

    def _p(self, nid):
        return os.path.join(self.home, "notes", nid + ".json")

    def new_id(self):
        n = len([x for x in os.listdir(os.path.join(self.home, "notes")) if x.endswith(".json")])
        return "N%04d" % (n + 1)

    def save(self, note):
        with open(self._p(note["id"]), "w", encoding="utf-8") as f:
            json.dump(note, f, ensure_ascii=False, indent=1)

    def load(self, nid):
        with open(self._p(nid), encoding="utf-8") as f:
            return json.load(f)

    def all(self):
        return [self.load(x[:-5]) for x in sorted(os.listdir(os.path.join(self.home, "notes"))) if x.endswith(".json")]


class Worker:
    def __init__(self, home, llm=None, today=None, price_per_1k_tokens=0.0):
        self.store = Store(home)
        self.llm = llmmod.get_backend(llm) if isinstance(llm, str) or llm is None else llm
        self.today = today or dt.date.today()
        self.price = price_per_1k_tokens

    # ---- state machine -------------------------------------------------
    def _go(self, note, new, **data):
        old = note["state"]
        if new not in TRANSITIONS.get(old, set()):
            raise InvalidTransition("%s -> %s" % (old, new))
        note["state"] = new
        note["history"].append({"state": new, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        self.store.audit.append(note["id"], "state:%s->%s" % (old, new), data)

    # ---- public API ----------------------------------------------------
    def ingest(self, text=None, audio=None):
        note = {"id": self.store.new_id(), "state": "RECEIVED", "history": [], "attempt": 0,
                "redacted_text": "", "pii_findings": {}, "result": None, "confidence": 0.0,
                "questions": [], "escalations": [], "tasks": [], "engine": "none", "metrics": {}}
        self.store.audit.append(note["id"], "note_received", {"source": "audio" if audio else "text"})
        if audio:
            try:
                text = transcribe(audio)
                self.store.audit.append(note["id"], "transcribed", {"chars": len(text)})
            except TranscriptionUnavailable as e:
                self.store.audit.append(note["id"], "transcription_unavailable", {"reason": str(e)})
                note["state"] = "NEEDS_CLARIFICATION"
                note["questions"] = [{"code": "need_text", "ref": "-",
                                      "question": "Awaaz samajh nahi aayi / offline speech-to-text nahi hai. Kripya note type karke bhejein."}]
                note["history"].append({"state": "NEEDS_CLARIFICATION", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                self.store.save(note)
                return note
        return self._run(note, text or "")

    def answer(self, note_id, text):
        """Human clarification: appended to the redacted text and the note is re-processed."""
        note = self.store.load(note_id)
        if note["state"] not in ("NEEDS_CLARIFICATION", "READY_FOR_CONFIRMATION", "FAILED"):
            raise InvalidTransition("cannot answer in state %s" % note["state"])
        note["attempt"] += 1
        self.store.audit.append(note_id, "clarification_received", {"attempt": note["attempt"]})
        return self._run(note, self._merge_answer(note, text), reprocess=True)

    @staticmethod
    def _merge_answer(note, answer):
        """Attach the user's answer to the first clause that was missing information, so the
        original incomplete clause is completed (not duplicated and not re-asked forever)."""
        refs = note.get("missing_refs") or []
        flat = [c for s in ex.split_sentences(note["redacted_text"]) for c in ex.split_clauses(s)]
        for ref in refs:
            if ref.startswith("c") and ref[1:].isdigit() and 0 < int(ref[1:]) <= len(flat):
                i = int(ref[1:]) - 1
                flat[i] = flat[i] + " " + answer
                return ". ".join(flat)
        return note["redacted_text"] + ". " + answer

    def confirm(self, note_id):
        note = self.store.load(note_id)
        self._go(note, "CONFIRMED", by="user")
        for i, r in enumerate(note["result"]["reminders"]):
            note["tasks"].append({"id": "%s-T%d" % (note_id, i + 1), "what": r["what"], "when": r["when"],
                                  "customer": r["customer"], "status": "open"})
        self.store.save(note)
        return note

    def resolve(self, note_id, by="supervisor"):
        """A human has handled an escalated note."""
        note = self.store.load(note_id)
        self._go(note, "CLOSED", by=by)
        self.store.save(note)
        return note

    def close(self, note_id):
        note = self.store.load(note_id)
        self._go(note, "CLOSED", by="user")
        self.store.save(note)
        return note

    def open_tasks(self):
        t = [x for n in self.store.all() for x in n["tasks"] if x["status"] == "open"]
        return sorted(t, key=lambda x: (x["when"] is None, x["when"] or ""))

    # ---- core ------------------------------------------------------------
    def _run(self, note, text, reprocess=False):
        t0 = time.perf_counter()
        try:
            if reprocess:
                self._go(note, "REDACTED")
            vault = self.store.vault()
            red, findings, secret = pii.redact(text, vault)
            self.store.save_vault(vault)
            note["redacted_text"], note["pii_findings"] = red, findings
            if not reprocess:
                self._go(note, "REDACTED", findings=findings, text_sha256=hashlib.sha256(red.encode()).hexdigest()[:16])
            else:
                self.store.audit.append(note["id"], "redacted", {"findings": findings})

            rules = ex.extract(red, self.today)
            result, engine, extra_pen = self._maybe_llm(note, red, rules)
            self._go(note, "EXTRACTED", engine=engine, clauses=rules["stats"]["clauses"])

            result["totals"] = validate.totals(result)
            note["result"], note["engine"] = result, engine
            conf = self._confidence(rules["stats"], result, extra_pen)
            note["confidence"] = conf
            self._go(note, "VALIDATED", confidence=conf, missing=[m["code"] for m in result["missing"]])

            esc = self._escalations(result)
            note["escalations"], note["questions"] = esc, result["missing"]
            note["missing_refs"] = [m["ref"] for m in result["missing"]]
            stop = [e for e in esc if e["level"] == "stop"]
            has_content = any(result[k] for k in ("sales", "expenses", "credits", "reminders", "exceptions"))
            if not has_content and not result["missing"]:
                note["questions"] = [{"code": "unclear", "ref": "-",
                                      "question": "Note samajh nahi aaya. Dobara bolein: kya becha, kitna kharcha, kisko udhaar, aur kab kya karna hai?"}]
            needs_clar = (not has_content) or bool(result["missing"]) or conf < 0.6
            if stop:
                self._go(note, "ESCALATED", reasons=[e["reason"] for e in stop])
            elif needs_clar and note["attempt"] >= 2:
                note["escalations"].append({"level": "stop", "to": "Field supervisor",
                                            "reason": "3 koshish ke baad bhi note poora samajh nahi aaya."})
                self._go(note, "ESCALATED", reasons=["clarification_exhausted"])
            elif needs_clar:
                self._go(note, "NEEDS_CLARIFICATION", questions=[q["code"] for q in note["questions"]])
            else:
                self._go(note, "READY_FOR_CONFIRMATION")
        except InvalidTransition:
            raise
        except Exception as e:  # fail safe, never crash on the user's device
            self.store.audit.append(note["id"], "unhandled_error", {"type": type(e).__name__})
            note["state"] = "FAILED"
            note["questions"] = [{"code": "internal_error", "ref": "-",
                                  "question": "Kuch gadbad ho gayi. Kripya note dobara bhejein."}]
        note["metrics"] = {"latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
        self.store.save(note)
        return note

    def _maybe_llm(self, note, red, rules):
        """Rules are the oracle; the LLM only enriches when its output passes every check."""
        if self.llm is None:
            return rules, "rules", 0.0
        prompt = prompts.build(red, self.today)
        note["metrics"]["est_tokens"] = (len(prompt) // 4) * 2
        hint = {k: v for k, v in rules.items() if k != "stats"}
        for attempt in (1, 2):
            try:
                raw = self.llm.complete(prompt, hint)
                parsed = llmmod.parse_json_strict(raw)
                errs = validate.validate_schema(parsed)
                if errs:
                    raise llmmod.LLMError("schema: " + errs[0])
                bad = validate.ungrounded_amounts(parsed, red)
                if bad:
                    raise llmmod.LLMError("ungrounded amount(s) - possible hallucination")
            except llmmod.LLMError as e:
                self.store.audit.append(note["id"], "llm_rejected", {"attempt": attempt, "reason": str(e)[:80]})
                continue
            # cross-check against the deterministic extractor
            merged = {k: parsed.get(k, []) for k in ("sales", "expenses", "credits", "reminders", "exceptions")}
            merged["missing"], merged["stats"] = rules["missing"], rules["stats"]
            a, b = validate.totals(merged), validate.totals(rules)
            if a != b:
                self.store.audit.append(note["id"], "llm_rules_disagree", {"llm": a, "rules": b})
                return rules, "rules(llm-disagreed)", 0.2
            return merged, "llm+rules", 0.0
        self.store.audit.append(note["id"], "llm_fallback_to_rules", {})
        return rules, "rules-fallback", 0.0

    @staticmethod
    def _confidence(stats, result, extra_pen):
        if not any(result[k] for k in ("sales", "expenses", "credits", "reminders", "exceptions")):
            return 0.0
        cov = stats["classified"] / stats["substantive"] if stats["substantive"] else 0.5
        pen = min(0.5, 0.15 * len(result["missing"]))
        return round(max(0.0, min(1.0, cov * (1 - pen) - extra_pen)), 2)

    @staticmethod
    def _escalations(result):
        esc, seen = [], set()
        for e in result["exceptions"]:
            if e["type"] in ESC_TARGET and e["type"] not in seen:
                seen.add(e["type"])
                to, why = ESC_TARGET[e["type"]]
                esc.append({"level": "stop", "to": to, "reason": why, "type": e["type"]})
            elif e["severity"] == "MEDIUM" and e["type"] not in seen:
                seen.add(e["type"])
                esc.append({"level": "inform", "to": "Field supervisor", "reason": e["type"] + " - nazar rakhein", "type": e["type"]})
        big = [a for k in ("sales", "expenses", "credits") for a in (x.get("amount") for x in result[k]) if isinstance(a, (int, float)) and a >= ex.HIGH_VALUE]
        if big:
            esc.append({"level": "stop", "to": "Field supervisor", "reason": "Badi raqam (>= Rs %d) - verify karein." % ex.HIGH_VALUE, "type": "HIGH_VALUE"})
        return esc


# ---- privacy-safe aggregate for Eko ------------------------------------------
def aggregate_export(worker, consent):
    """Only counts and rounded totals. No names, no free text, no IDs. Needs explicit consent."""
    if not consent:
        raise PermissionError("consent required: nothing is shared without --consent")
    notes = [n for n in worker.store.all() if n["state"] in ("CONFIRMED", "CLOSED") and n["result"]]
    k = 3

    def sup(n):  # small-count suppression
        return n if n >= k else "<%d" % k

    def rnd(v):
        return int(round(v / 500.0) * 500)

    exc = {}
    for n in notes:
        for e in n["result"]["exceptions"]:
            exc[e["type"]] = exc.get(e["type"], 0) + 1
    tot = {"sales": 0.0, "expenses": 0.0}
    for n in notes:
        tot["sales"] += n["result"]["totals"]["sales_total"]
        tot["expenses"] += n["result"]["totals"]["expenses_total"]
    payload = {"schema": "bol2kaam.aggregate.v1", "period_end": str(worker.today),
               "notes_confirmed": sup(len(notes)),
               "sales_total_rounded_500": rnd(tot["sales"]) if len(notes) >= k else None,
               "expenses_total_rounded_500": rnd(tot["expenses"]) if len(notes) >= k else None,
               "exception_counts": {t: sup(c) for t, c in sorted(exc.items())}}
    worker.store.audit.append("-", "aggregate_exported",
                              {"sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]})
    return payload
