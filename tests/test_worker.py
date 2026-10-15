import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from worker import pii  # noqa: E402
from worker.pipeline import InvalidTransition, Worker, aggregate_export  # noqa: E402

TODAY = dt.date(2026, 10, 10)


def mk(llm=None):
    return Worker(tempfile.mkdtemp(), llm=llm, today=TODAY)


class PrivacyTests(unittest.TestCase):
    def test_pii_never_reaches_disk_except_vault(self):
        w = mk()
        n = w.ingest(text="Sunita didi 9876543210 sunita@okaxis ABCDE1234F. 700 ka udhaar baaki hai.")
        blob = json.dumps(n).lower() + open(os.path.join(w.store.home, "audit.jsonl")).read().lower()
        for s in ("9876543210", "sunita", "okaxis", "abcde1234f"):
            self.assertNotIn(s, blob)

    def test_otp_removed_and_escalated(self):
        n = mk().ingest(text="Customer ne OTP 482913 maanga, bata diya.")
        self.assertNotIn("482913", n["redacted_text"])
        self.assertEqual(n["state"], "ESCALATED")

    def test_alias_is_stable_across_notes(self):
        v = {}
        a, _, _ = pii.redact("Ramesh ko 500 ka udhaar", v)
        b, _, _ = pii.redact("Ramesh se 100 mile", v)
        self.assertIn("C1", a)
        self.assertIn("C1", b)


class WorkflowTests(unittest.TestCase):
    def test_happy_path_and_human_confirmation(self):
        w = mk()
        n = w.ingest(text="Kal Ramesh se paisa lena hai. 5 packet biscuit 250 rupees me becha.")
        self.assertEqual(n["state"], "READY_FOR_CONFIRMATION")
        self.assertEqual(w.open_tasks(), [])  # nothing is scheduled before a human confirms
        w.confirm(n["id"])
        self.assertEqual(len(w.open_tasks()), 1)

    def test_illegal_transition_rejected(self):
        w = mk()
        n = w.ingest(text="Aaj kuch maal becha.")
        self.assertEqual(n["state"], "NEEDS_CLARIFICATION")
        with self.assertRaises(InvalidTransition):
            w.confirm(n["id"])

    def test_clarification_loop_then_ready(self):
        w = mk()
        n = w.ingest(text="Aaj kuch maal becha.")
        n = w.answer(n["id"], "Aaj 5 packet biscuit 250 rupees me becha.")
        self.assertEqual(n["attempt"], 1)
        self.assertEqual(n["result"]["totals"]["sales_total"], 250)
        self.assertEqual(n["state"], "READY_FOR_CONFIRMATION")  # regression: loop must resolve, not re-ask forever
        self.assertEqual(n["questions"], [])

    def test_escalates_after_repeated_failed_clarification(self):
        w = mk()
        n = w.ingest(text="hmm")
        n = w.answer(n["id"], "achha")
        n = w.answer(n["id"], "theek")
        self.assertEqual(n["state"], "ESCALATED")

    def test_transcription_unavailable_fails_safe(self):
        n = mk().ingest(audio="/nonexistent.wav")
        self.assertEqual(n["state"], "NEEDS_CLARIFICATION")
        self.assertEqual(n["questions"][0]["code"], "need_text")


class LLMSafetyTests(unittest.TestCase):
    def test_malformed_llm_falls_back_to_rules(self):
        n = mk("fake-malformed").ingest(text="5 packet biscuit 250 rupees me becha.")
        self.assertEqual(n["engine"], "rules-fallback")
        self.assertEqual(n["result"]["totals"]["sales_total"], 250)

    def test_hallucinated_amount_rejected(self):
        n = mk("fake-hallucinate").ingest(text="5 packet biscuit 250 rupees me becha.")
        self.assertEqual(n["engine"], "rules-fallback")
        self.assertNotEqual(n["result"]["totals"]["sales_total"], 99999)


class AuditAndExportTests(unittest.TestCase):
    def test_audit_tamper_detected(self):
        w = mk()
        w.ingest(text="250 rupees ka maal becha.")
        self.assertTrue(w.store.audit.verify()[0])
        p = w.store.audit.path
        txt = open(p).read().replace("note_received", "note_deleted", 1)
        open(p, "w").write(txt)
        self.assertFalse(w.store.audit.verify()[0])

    def test_audit_chain_survives_multiple_worker_instances(self):
        w1 = mk()
        w2 = Worker(w1.store.home, today=TODAY)
        w1.ingest(text="250 rupees ka maal becha.")
        w2.ingest(text="100 rupees ka maal becha.")
        w1.ingest(text="50 rupees ka maal becha.")
        self.assertTrue(w1.store.audit.verify()[0])

    def test_unit_price_not_mistaken_for_total(self):
        n = mk().ingest(text="Sold 10 bottles water at 20 each")
        self.assertEqual(n["result"]["totals"]["sales_total"], 200)

    def test_export_requires_consent(self):
        with self.assertRaises(PermissionError):
            aggregate_export(mk(), consent=False)

    def test_export_has_no_pii_and_suppresses_small_counts(self):
        w = mk()
        n = w.ingest(text="Ramesh ko 500 ka udhaar diya. 900 rupees ka maal becha.")
        w.confirm(n["id"])
        out = json.dumps(aggregate_export(w, consent=True))
        self.assertNotIn("Ramesh", out)
        self.assertNotIn("C1", out)
        self.assertEqual(json.loads(out)["notes_confirmed"], "<3")
        self.assertIsNone(json.loads(out)["sales_total_rounded_500"])


if __name__ == "__main__":
    unittest.main()
