#!/usr/bin/env python3
"""Evaluation harness: field accuracy, status accuracy, PII leaks, escalation recall,
hallucination guard, latency, tokens/cost. Usage: python eval/run_eval.py [--llm fake-malformed]"""
import argparse
import datetime as dt
import json
import os
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from worker.pipeline import Worker  # noqa: E402


def check(case, note):
    e, r, fails = case["expect"], note["result"] or {}, []
    t = r.get("totals", {})
    if "state" in e and note["state"] != e["state"]:
        fails.append("state %s != %s" % (note["state"], e["state"]))
    for k in ("sales_total", "expenses_total", "receivable_total", "payable_total", "settled_total"):
        if k in e and abs(t.get(k, 0) - e[k]) > 0.01:
            fails.append("%s %s != %s" % (k, t.get(k), e[k]))
    if "n_reminders" in e and len(r.get("reminders", [])) != e["n_reminders"]:
        fails.append("n_reminders %d != %d" % (len(r.get("reminders", [])), e["n_reminders"]))
    if "reminder_when" in e and [x["when"] for x in r.get("reminders", [])] != e["reminder_when"]:
        fails.append("reminder_when %s" % [x["when"] for x in r.get("reminders", [])])
    if "exceptions" in e and not set(e["exceptions"]) <= {x["type"] for x in r.get("exceptions", [])}:
        fails.append("exceptions %s" % sorted({x["type"] for x in r.get("exceptions", [])}))
    if "min_questions" in e and len(note["questions"]) < e["min_questions"]:
        fails.append("expected a clarification question")
    if "confidence_max" in e and note["confidence"] > e["confidence_max"]:
        fails.append("confidence too high")
    if e.get("has_inform_escalation") and not any(x["level"] == "inform" for x in note["escalations"]):
        fails.append("missing inform escalation")
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="none")
    ap.add_argument("--cases", default=os.path.join(os.path.dirname(__file__), "cases.jsonl"))
    ap.add_argument("--price-per-1k-tokens", type=float, default=0.0, help="Rs per 1k tokens (cloud LLM estimate)")
    a = ap.parse_args(argv)
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]
    home = tempfile.mkdtemp()
    w = Worker(home, llm=a.llm, today=dt.date(2026, 10, 10))
    passed, leaks, lat, toks, engines = 0, 0, [], 0, {}
    esc_exp = esc_hit = false_esc = 0
    rows = []
    for c in cases:
        n = w.ingest(text=c["text"])
        fails = check(c, n)
        blob = json.dumps(n, ensure_ascii=False).lower() + open(os.path.join(home, "audit.jsonl"), encoding="utf-8").read().lower()
        for s in c.get("no_leak", []):
            if s.lower() in blob:
                leaks += 1
                fails.append("PII LEAK: %r" % s)
        want_esc = c["expect"].get("state") == "ESCALATED"
        esc_exp += want_esc
        esc_hit += want_esc and n["state"] == "ESCALATED"
        false_esc += (not want_esc) and n["state"] == "ESCALATED"
        lat.append(n["metrics"]["latency_ms"])
        toks += n["metrics"].get("est_tokens", 0)
        engines[n["engine"]] = engines.get(n["engine"], 0) + 1
        passed += not fails
        rows.append((c["id"], "PASS" if not fails else "FAIL", n["state"], n["confidence"], "; ".join(fails)))
    ok, bad, nrec = w.store.audit.verify()
    print("%-26s %-5s %-24s %-5s %s" % ("case", "res", "state", "conf", "notes"))
    for r in rows:
        print("%-26s %-5s %-24s %-5.2f %s" % r)
    print("\n--- summary (llm=%s) ---" % a.llm)
    print("cases passed        : %d/%d (%.0f%%)" % (passed, len(cases), 100.0 * passed / len(cases)))
    print("PII leaks           : %d  (target 0)" % leaks)
    print("escalation recall   : %d/%d ; false escalations: %d" % (esc_hit, esc_exp, false_esc))
    print("engines used        : %s" % engines)
    print("latency ms          : mean %.2f, p95 %.2f" % (statistics.mean(lat), sorted(lat)[int(0.95 * (len(lat) - 1))]))
    print("est. tokens / cost  : %d tokens, Rs %.4f (local rules = Rs 0)" % (toks, toks / 1000.0 * a.price_per_1k_tokens))
    print("audit chain         : %s (%d records)" % ("OK" if ok else "BROKEN@%s" % bad, nrec))
    return 0 if passed == len(cases) and leaks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
