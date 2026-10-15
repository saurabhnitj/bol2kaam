#!/usr/bin/env python3
"""Bol2Kaam CLI - works on any machine with Python 3.8+. No third-party packages."""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile

from worker.pipeline import Worker, aggregate_export


def render(n):
    r = n["result"] or {}
    print("\n=== %s  [%s]  confidence=%.2f  engine=%s ===" % (n["id"], n["state"], n["confidence"], n["engine"]))
    if n["pii_findings"]:
        print("Privacy: redacted locally ->", ", ".join("%s x%d" % kv for kv in n["pii_findings"].items()))
    print("Samjha hua text :", n["redacted_text"])
    if r:
        t = r["totals"]
        print("Bikri: Rs %.0f | Kharcha: Rs %.0f | Baaki (milna): Rs %.0f | Dena: Rs %.0f | Mila: Rs %.0f" % (
            t["sales_total"], t["expenses_total"], t["receivable_total"], t["payable_total"], t["settled_total"]))
        for x in r["reminders"]:
            print("  Yaad dilao: %-12s %s" % (x["when"] or "kab? (poochna hai)", x["what"]))
        for x in r["exceptions"]:
            print("  Samasya [%s] %s: %s" % (x["severity"], x["type"], x["detail"]))
    for e in n["escalations"]:
        print("  ESCALATE (%s) -> %s: %s" % (e["level"], e["to"], e["reason"]))
    for q in n["questions"]:
        print("  SAWAAL:", q["question"])
    print("Next:", {"READY_FOR_CONFIRMATION": "confirm karein (human approval)",
                    "NEEDS_CLARIFICATION": "upar ke sawaal ka jawab dein (answer)",
                    "ESCALATED": "human supervisor ke paas gaya (resolve)",
                    "FAILED": "note dobara bhejein"}.get(n["state"], "-"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="bol2kaam")
    p.add_argument("--home", default=os.environ.get("BOL2KAAM_HOME", os.path.expanduser("~/.bol2kaam")))
    p.add_argument("--llm", default=os.environ.get("BOL2KAAM_LLM", "none"))
    p.add_argument("--today", help="YYYY-MM-DD (for reproducible demos)")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest"); s.add_argument("text", nargs="?"); s.add_argument("--file"); s.add_argument("--audio")
    s = sub.add_parser("answer"); s.add_argument("id"); s.add_argument("text")
    for c in ("confirm", "resolve", "close", "show"):
        s = sub.add_parser(c); s.add_argument("id")
    sub.add_parser("list"); sub.add_parser("tasks"); sub.add_parser("audit-verify")
    s = sub.add_parser("export-aggregate"); s.add_argument("--consent", action="store_true")
    a = p.parse_args(argv)

    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    w = Worker(a.home, llm=a.llm, today=today)
    out = None
    if a.cmd == "ingest":
        text = open(a.file, encoding="utf-8").read() if a.file else a.text
        out = w.ingest(text=text, audio=a.audio)
    elif a.cmd == "answer":
        out = w.answer(a.id, a.text)
    elif a.cmd == "confirm":
        out = w.confirm(a.id)
    elif a.cmd == "resolve":
        out = w.resolve(a.id)
    elif a.cmd == "close":
        out = w.close(a.id)
    elif a.cmd == "show":
        out = w.store.load(a.id)
    elif a.cmd == "list":
        for n in w.store.all():
            print(n["id"], n["state"], "%.2f" % n["confidence"], n["redacted_text"][:60])
        return 0
    elif a.cmd == "tasks":
        for t in w.open_tasks():
            print(t["id"], t["when"] or "-", t["what"])
        return 0
    elif a.cmd == "audit-verify":
        ok, bad, n = w.store.audit.verify()
        print("audit chain OK (%d records)" % n if ok else "TAMPERED at seq %s" % bad)
        return 0 if ok else 2
    elif a.cmd == "export-aggregate":
        try:
            print(json.dumps(aggregate_export(w, a.consent), indent=1, ensure_ascii=False))
        except PermissionError as e:
            print("REFUSED:", e)
            return 3
        return 0
    if a.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        render(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
