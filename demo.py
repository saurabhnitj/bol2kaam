#!/usr/bin/env python3
"""Scripted demo for the screen recording (deterministic, ~60 seconds). Run: python3 demo.py"""
import datetime as dt
import tempfile

from cli import render
from worker.pipeline import Worker, aggregate_export

TODAY = dt.date(2026, 10, 10)


def hdr(t):
    print("\n" + "#" * 78 + "\n# " + t + "\n" + "#" * 78)


home = tempfile.mkdtemp()
w = Worker(home, today=TODAY)

hdr("1. HAPPY PATH - Hinglish voice-note transcript -> structured action (offline, rules engine)")
t = "Aaj subah 10 packet chai becha 450 me, 5 packet namkeen becha 250 me. Kiraya 3000 diya. Anil ko 600 ka udhaar diya, parso wapas lena hai."
print("INPUT:", t)
n1 = w.ingest(text=t)
render(n1)
w.confirm(n1["id"])
print("-> user confirmed; reminder task created (nothing is scheduled before human approval)")

hdr("2. PRIVACY - phone, UPI, PAN and an OTP in the note; PII never leaves the device")
t = "Sunita didi ka number 9876543210, UPI sunita@okaxis. Customer ne OTP 482913 maanga aur maine bata diya."
print("INPUT:", t)
render(w.ingest(text=t))

hdr("3. INTENTIONAL FAILURE A - incomplete input: worker refuses to guess, asks, then recovers")
n = w.ingest(text="Aaj kuch maal becha.")
render(n)
print("\n(user answers the question)")
render(w.answer(n["id"], "Aaj 5 packet biscuit 250 rupees me becha."))

hdr("4. INTENTIONAL FAILURE B - local LLM returns malformed JSON: reject + fall back to rules")
w2 = Worker(home, llm="fake-malformed", today=TODAY)
n = w2.ingest(text="3 kg rice 180 rupees me becha.")
render(n)
print("(audit shows: llm_rejected x2 -> llm_fallback_to_rules; the user still gets a correct answer)")

hdr("5. INTENTIONAL FAILURE C - LLM hallucinates an amount: grounding check rejects it")
w3 = Worker(home, llm="fake-hallucinate", today=TODAY)
render(w3.ingest(text="3 kg rice 180 rupees me becha."))

hdr("6. INTENTIONAL FAILURE D - voice input but no offline speech-to-text installed")
render(w.ingest(audio="/tmp/voice_note.wav"))

hdr("7. ESCALATION - money deducted + payment failed: worker STOPS and routes to a human")
render(w.ingest(text="Paytm se payment fail ho gaya, customer ke paise kat gaye."))

hdr("8. AGGREGATE FOR EKO - needs consent; small counts suppressed; no names, no free text")
try:
    aggregate_export(w, consent=False)
except PermissionError as e:
    print("REFUSED without consent:", e)
for t in ("1200 rupees ka maal becha.", "800 rupees ka maal becha.", "Kiraya 2500 diya."):  # more confirmed notes
    w.confirm(w.ingest(text=t)["id"])
import json
print(json.dumps(aggregate_export(w, consent=True), indent=1))

hdr("9. AUDIT - tamper-evident hash chain")
ok, bad, n = w.store.audit.verify()
print("chain OK:", ok, "| records:", n)
