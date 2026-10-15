"""Deterministic rule-based extractor for English / Hinglish business notes.

Works fully offline in a few milliseconds with zero dependencies. It is also the
safety net and cross-check when an optional local LLM is enabled.
Input is ALWAYS already-redacted text.
"""
import datetime as dt
import re

HIGH_VALUE = 50000

MULTS = {"k": 1000, "hazaar": 1000, "hazar": 1000, "thousand": 1000,
         "lakh": 100000, "lac": 100000, "sau": 100}

AMT_RE = re.compile(r"""
 (?:(?:\b(?:rs\.?|inr|rupees?)|₹)\s*(?P<n1>\d[\d,]*(?:\.\d+)?)(?:\s*(?P<m1>k|hazaar|hazar|thousand|lakh|lac)\b)?)
 |
 (?:(?P<n2>\d[\d,]*(?:\.\d+)?)\s*(?P<m2>k|hazaar|hazar|thousand|lakh|lac|sau)?\s*(?:rs\.?|rupees?|rupaye|rupay|rupiya|₹|/-)(?![a-z]))
 |
 (?:(?P<n3>\d+(?:\.\d+)?)\s*(?P<m3>k|hazaar|hazar|thousand|lakh|lac|sau)\b)
""", re.I | re.X)
PLAIN_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])")
UNITS = ("kg|kgs|gram|gm|g|packet|packets|pkt|piece|pieces|pcs|litre|liter|ltr|bottle|bottles|"
         "dozen|box|sim|recharge|recharges|bag|bags|unit|units")
QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%s)\b" % UNITS, re.I)
NONAMT_NEXT = set((UNITS + "|log|din|baar|times|days|ghante|hours|minute|min|baje|tarikh|tareekh|th|st|nd|rd|customers|customer").split("|"))


def _kw(*words):
    return re.compile(r"\b(?:%s)\b" % "|".join(words), re.I)


SALE = _kw("becha", "bech", "bechi", "bechna", "beche", "bika", "bikri", "sold", "sale", "sales", "sell")
EXPENSE = _kw("kharcha", "kharch", "kharida", "kharide", "kharidi", "bought", "spent", "purchase",
              "kiraya", "rent", "bijli", "electricity", "salary", "tankhwah", "petrol", "diesel")
CREDIT = _kw("udhaar", "udhar", "baaki", "baki", "bakaya", "due", "owe", "owes", "owed")
SETTLED = re.compile(r"\b(?:chuka|chukaya|chukadiya|paid|received|mile|mila|jama|returned)\b|"
                     r"\bwapas\s+(?:kiya|diya|kar\s+diya)\b|\bchuka\s+diya\b", re.I)
PAY_PARTY = _kw("supplier", "wholesaler", "distributor", "vendor", "mahajan")
PAY_VERB = _kw("dena", "dene", "pay", "owe")
REMIND = re.compile(r"\b(?:remind|reminder|yaad|follow\s?up|followup)\b", re.I)
ACTION = _kw("call", "phone", "milna", "milne", "dena", "dene", "bhejna", "bhej", "collect",
             "vasool", "vasuli", "visit", "jaana", "jana", "lena", "lene", "pay", "deliver")
PAST = _kw("tha", "thi", "the", "kiya", "gaya", "gayi", "hua", "hui", "aaya", "aayi", "yesterday")
NEGATION = _kw("nahi", "nhi", "no", "not", "zero")

WEEKDAYS = {"monday": 0, "somvar": 0, "tuesday": 1, "mangalvar": 1, "wednesday": 2, "budhvar": 2,
            "thursday": 3, "guruvar": 3, "veervar": 3, "friday": 4, "shukravar": 4,
            "saturday": 5, "shanivar": 5, "sunday": 6, "ravivar": 6, "itvar": 6}

# (type, severity, regex)
EXC_RULES = [
    ("SECRET_SHARED", "HIGH", re.compile(r"\[SECRET\]|\botp\s+(?:bata|share|diya|di)\b", re.I)),
    ("MONEY_DEDUCTED", "HIGH", re.compile(r"\bpais[ae]\s+kat\b|\bmoney\s+deducted\b|\bdeducted\b|\bdouble\s+debit\b|\bwrongly\s+debited\b", re.I)),
    ("FRAUD", "HIGH", re.compile(r"\b(?:fraud|scam|dhokha|dhoka|fake|hacked)\b", re.I)),
    ("SAFETY", "HIGH", re.compile(r"\b(?:chori|theft|stolen|police|dhamki|threat)\b", re.I)),
    ("REFUND_REQUEST", "MEDIUM", re.compile(r"\brefund\b", re.I)),
    ("COMPLAINT", "MEDIUM", re.compile(r"\b(?:complaint|shikayat|chargeback)\b", re.I)),
    ("TXN_FAILED", "MEDIUM", re.compile(r"\b(?:fail(?:ed|s|ure)?|atak(?:a|gaya)?|stuck|error|declined|time\s?out)\b|\bnahi\s+hua\b|\bnhi\s+hua\b", re.I)),
    ("TECH_ISSUE", "LOW", re.compile(r"\bnetwork\s+(?:nahi|issue|problem|down)\b|\bapp\s+(?:band|crash|hang)\b|\bnot\s+working\b|\bserver\s+down\b|\binternet\s+(?:nahi|down)\b", re.I)),
]

QUESTIONS = {
    "sale_amount": "Kitne ka becha? (amount batayein)",
    "expense_amount": "Kharcha kitna tha? (amount batayein)",
    "credit_amount": "Udhaar / baaki kitna hai? (amount batayein)",
    "credit_customer": "Yeh udhaar kiska hai? (naam batayein)",
    "settled_customer": "Yeh paisa kisne diya? (naam batayein)",
    "reminder_when": "Yeh kaam kab karna hai? (kal / parso / din batayein)",
}


def _val(n, m):
    return float(n.replace(",", "")) * MULTS.get((m or "").lower(), 1)


def parse_amounts(clause):
    """Return (marked, plain) lists of numeric values, in order of appearance."""
    marked, spans = [], []
    for m in AMT_RE.finditer(clause):
        g = m.groupdict()
        for i in "123":
            if g.get("n" + i):
                marked.append(_val(g["n" + i], g.get("m" + i)))
        spans.append(m.span())
    plain = []
    for m in PLAIN_RE.finditer(clause):
        if any(a <= m.start() < b for a, b in spans):
            continue
        nxt = re.match(r"\s*([A-Za-z]+)", clause[m.end():])
        if nxt and nxt.group(1).lower() in NONAMT_NEXT:
            continue
        plain.append(float(m.group(1).replace(",", "")))
    return marked, plain


def pick_amount(clause):
    marked, plain = parse_amounts(clause)
    if marked:
        return marked[-1]
    return plain[-1] if plain else None


def numbers_in(text):
    """All numeric values a human could have meant - used for hallucination/grounding checks."""
    vals = set()
    marked, plain = parse_amounts(text)
    vals.update(marked)
    vals.update(plain)
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        vals.add(float(m.group(0).replace(",", "")))
    base = list(vals)
    for i, a in enumerate(base):  # qty x unit-price products are legitimate derived amounts
        for b in base[i + 1:]:
            vals.add(a * b)
    return vals


def resolve_when(clause, today):
    t = clause.lower()
    if re.search(r"\b(?:parso|day after tomorrow)\b", t):
        return (today + dt.timedelta(days=2)).isoformat()
    if re.search(r"\b(?:kal|tomorrow|tmrw)\b", t):
        return (today + dt.timedelta(days=1)).isoformat()
    if re.search(r"\b(?:aaj|today)\b", t):
        return today.isoformat()
    if re.search(r"\b(?:next week|agle hafte|agle week)\b", t):
        return (today + dt.timedelta(days=7)).isoformat()
    m = re.search(r"\b(\d{1,3})\s*(?:din|days?)\b", t)
    if m:
        return (today + dt.timedelta(days=int(m.group(1)))).isoformat()
    for name, wd in WEEKDAYS.items():
        if re.search(r"\b%s\b" % name, t):
            delta = (wd - today.weekday()) % 7 or 7
            return (today + dt.timedelta(days=delta)).isoformat()
    m = re.search(r"\b(\d{1,2})\s*(?:tarikh|tareekh|st|nd|rd|th)\b", t)
    if m and 1 <= int(m.group(1)) <= 28:
        d = int(m.group(1))
        y, mo = today.year, today.month
        if d < today.day:
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
        return dt.date(y, mo, d).isoformat()
    return None


def split_sentences(text):
    text = re.sub(r"\b(rs|inr|mr|mrs|ms|no)\.", r"\1", text, flags=re.I)
    return [s.strip() for s in re.split(r"\.(?=\s|$)|[\n;!?|]+", text) if s.strip()]


CLAUSE_SPLIT = re.compile(r"(?i)(?<!\d),\s*|,(?!\d{3}\b)\s*|\s+(?:aur|and|phir|then|lekin|but)\s+")


def split_clauses(sentence):
    return [c.strip() for c in CLAUSE_SPLIT.split(sentence) if c and c.strip()]


def _item(clause):
    m = QTY_RE.search(clause)
    if not m:
        return None, None, None
    qty, unit = float(m.group(1)), m.group(2).lower()
    rest = clause[m.end():]
    words = re.findall(r"[A-Za-z]+", rest)
    skip = {"ka", "ke", "ki", "for", "at", "me", "mein", "rs", "rupees", "becha", "sold", "of", "bhi", "aur", "and"}
    item = next((w.lower() for w in words if w.lower() not in skip), None)
    return qty, unit, item


def extract(text, today):
    out = {"sales": [], "expenses": [], "credits": [], "reminders": [], "exceptions": [], "missing": []}
    last_cust = None
    stats = {"clauses": 0, "substantive": 0, "classified": 0}

    def need(code, ref):
        if not any(m["code"] == code and m["ref"] == ref for m in out["missing"]):
            out["missing"].append({"code": code, "ref": ref, "question": QUESTIONS[code]})

    for sent in split_sentences(text):
        prev_type = None
        for clause in split_clauses(sent):
            stats["clauses"] += 1
            custs = re.findall(r"\bC\d+\b", clause)
            cust = custs[-1] if custs else last_cust
            if custs:
                last_cust = custs[-1]
            amt = pick_amount(clause)
            is_sale, is_exp = bool(SALE.search(clause)), bool(EXPENSE.search(clause))
            negated = bool(NEGATION.search(clause))
            if not (is_sale or is_exp) and amt is not None and prev_type in ("sale", "expense"):
                is_sale, is_exp = prev_type == "sale", prev_type == "expense"
            cr_kw = bool(CREDIT.search(clause))
            settled = bool(SETTLED.search(clause)) and amt is not None and bool(custs or last_cust) and not is_exp
            payable = bool(PAY_PARTY.search(clause)) and bool(PAY_VERB.search(clause))
            when = resolve_when(clause, today)
            remind_kw = bool(REMIND.search(clause))
            action = bool(ACTION.search(clause))
            past = bool(PAST.search(clause))
            excs = [(t, s) for t, s, rx in EXC_RULES if rx.search(clause)]
            ref = "c%d" % stats["clauses"]
            handled = False

            if is_sale:
                handled = True
                prev_type = "sale"
                if amt is not None:
                    q, u, item = _item(clause)
                    if q and re.search(r"\b(?:each|per|har|prati)\b|\bek\s+ka\b", clause, re.I):
                        amt = amt * q  # amount was a per-unit price
                    out["sales"].append({"item": item, "qty": q, "unit": u, "amount": amt, "customer": custs[-1] if custs else None})
                elif not negated:
                    need("sale_amount", ref)
            elif is_exp:
                handled = True
                prev_type = "expense"
                if amt is not None:
                    out["expenses"].append({"what": clause[:80], "amount": amt})
                elif not negated:
                    need("expense_amount", ref)
            if settled:
                handled = True
                out["credits"].append({"customer": cust, "amount": amt, "direction": "settled", "due": None})
            elif payable and (cr_kw or action):
                handled = True
                if amt is not None:
                    out["credits"].append({"customer": None, "amount": amt, "direction": "payable", "due": when})
                else:
                    need("credit_amount", ref)
            elif cr_kw:
                handled = True
                if amt is None:
                    need("credit_amount", ref)
                else:
                    if cust is None:
                        need("credit_customer", ref)
                    out["credits"].append({"customer": cust, "amount": amt, "direction": "receivable", "due": when})
            if remind_kw or (when and action and not past):
                handled = True
                if when is None:
                    need("reminder_when", ref)
                out["reminders"].append({"what": clause[:120], "when": when, "customer": cust})
            for t, s in excs:
                handled = True
                out["exceptions"].append({"type": t, "severity": s, "detail": clause[:120]})

            has_digit = bool(re.search(r"(?<![\w])\d", clause))
            kw_any = any([SALE.search(clause), EXPENSE.search(clause), CREDIT.search(clause), remind_kw, excs])
            if has_digit or kw_any or handled:
                stats["substantive"] += 1
                if handled:
                    stats["classified"] += 1
    out["stats"] = stats
    return out
