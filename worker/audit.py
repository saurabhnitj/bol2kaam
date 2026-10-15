"""Tamper-evident audit log: append-only JSONL, each line hash-chained to the previous one.

Only metadata is logged (event names, counts, types, hashes) - never raw text or PII.
"""
import hashlib
import json
import os
import time

GENESIS = "0" * 64


def _h(rec):
    body = {k: rec[k] for k in sorted(rec) if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path):
        self.path = path

    def _tail(self):
        """(seq, hash) of the last record on disk - re-read each time so several Worker
        instances / processes sharing one store cannot fork the chain."""
        if not os.path.exists(self.path):
            return 0, GENESIS
        last = None
        with open(self.path, "rb") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS
        r = json.loads(last.decode("utf-8"))
        return r["seq"], r["hash"]

    def append(self, note_id, event, data=None, ts=None):
        seq, prev = self._tail()
        rec = {"seq": seq + 1, "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%S"), "note_id": note_id,
               "event": event, "data": data or {}, "prev": prev}
        rec["hash"] = _h(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def verify(self):
        """Return (ok, first_bad_seq or None, n_records)."""
        prev, n = GENESIS, 0
        if not os.path.exists(self.path):
            return True, None, 0
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                n += 1
                if r["prev"] != prev or r["hash"] != _h(r):
                    return False, r["seq"], n
                prev = r["hash"]
        return True, None, n
