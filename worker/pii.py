"""Local PII redaction + pseudonymisation.

Runs FIRST, before extraction, before any LLM, before anything is logged or stored.
Raw text is never persisted. The name->alias vault lives only on the device.
"""
import re

# Secrets (OTP/PIN/CVV/password) are removed AND flagged as a risk event.
SECRET_RE = re.compile(r"(?i)\b(?:otp|pin|cvv|password|passcode)\b[^\d\n]{0,15}\d{3,8}\b")

PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")),
    ("UPI", re.compile(r"\b[\w.\-]{2,}@[A-Za-z]{2,}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)|(?<!\d)\d{12}(?!\d)")),
    ("ACCOUNT", re.compile(r"(?<!\d)\d{9,18}(?!\d)")),
]

STOP = set("""
aaj kal parso today tomorrow yesterday monday tuesday wednesday thursday friday saturday sunday
somvar mangalvar budhvar guruvar veervar shukravar shanivar ravivar itvar
january february march april may june july august september october november december
customer grahak client supplier wholesaler distributor vendor dukandar mahajan
paytm phonepe gpay upi bank eko app google jio airtel bsnl sbi hdfc icici
aur and the for with from this that mere meri mera apne unka unki unke uska uski uske
sabko sab maal stock paise paisa rupees rupaye payment bill network refund order delivery
recharge sim note phone mobile shop dukan market mandi gaon hai hain tha thi the
bola kaha ek koi kuch kitna kitne wala wali sale sold spent call need collected
""".split())

_SUFFIX = r"(?:ko|ne|se|ka|ki|ke|ji|bhai|bhaiya|sahab|saab|sir|didi|bhabhi|chacha|mama|paid|owes|said|called|wants|asked|gave)"
_NAME_PATTERNS = [
    re.compile(r"(?i:customer|grahak|client|mr|mrs|ms|shri|smt)\.?\s+([A-Z][a-z]{2,})"),
    re.compile(r"(?i)\b([A-Za-z]{3,})\s+(?:ji|bhai|bhaiya|sahab|saab|didi|bhabhi|chacha|mama)\b"),
    re.compile(r"(?i)\b([A-Za-z]{3,})\s+(?:ko|se|ne)\s+(?:₹|rs\.?\s*)?\d"),
    re.compile(r"(?i)\b([A-Za-z]{3,})\s+(?:ka|ki|ke)\s+(?:udhaar|udhar|baaki|baki|due|bakaya)"),
    re.compile(r"\b([A-Z][a-z]{2,})\s+" + _SUFFIX + r"\b"),
]
_CAP = re.compile(r"\b[A-Z][a-z]{2,}\b")


def find_names(text):
    found = []
    for rx in _NAME_PATTERNS:
        for m in rx.finditer(text):
            found.append(m.group(1))
    # mid-sentence capitalised words (sentence starts are ignored)
    for m in _CAP.finditer(text):
        before = text[:m.start()].rstrip()
        if not before or before[-1] in ".!?\n;|":
            continue
        found.append(m.group(0))
    out = []
    for n in found:
        if n.lower() not in STOP and n.lower() not in [o.lower() for o in out]:
            out.append(n)
    return out


def redact(text, vault):
    """Return (redacted_text, findings, secret_flag). `vault` is mutated: {name_lower: 'C1'}."""
    findings = {}
    secret = False

    def _secret(_m):
        nonlocal secret
        secret = True
        return "[SECRET]"

    text, n = SECRET_RE.subn(_secret, text)
    if n:
        findings["SECRET"] = n
    for label, rx in PATTERNS:
        text, n = rx.subn("[%s]" % label, text)
        if n:
            findings[label] = n
    names = find_names(text)
    for name in sorted(names, key=len, reverse=True):
        key = name.lower()
        if key not in vault:
            vault[key] = "C%d" % (len(vault) + 1)
        text = re.sub(r"\b%s\b" % re.escape(name), vault[key], text, flags=re.I)
        findings["NAME"] = findings.get("NAME", 0) + 1
    return text, findings, secret
