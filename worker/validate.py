"""Schema validation + grounding (hallucination) checks for any model-produced extraction."""
from .extract import numbers_in

SCHEMA = {
    "sales": ("amount",),
    "expenses": ("amount",),
    "credits": ("amount", "direction"),
    "reminders": ("what",),
    "exceptions": ("type", "severity"),
}


def validate_schema(d):
    errs = []
    if not isinstance(d, dict):
        return ["root is not an object"]
    for key, required in SCHEMA.items():
        items = d.get(key, [])
        if not isinstance(items, list):
            errs.append("%s is not a list" % key)
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errs.append("%s[%d] is not an object" % (key, i))
                continue
            for r in required:
                if r not in it or it[r] is None:
                    errs.append("%s[%d] missing %s" % (key, i, r))
            if "amount" in it and it["amount"] is not None and not isinstance(it["amount"], (int, float)):
                errs.append("%s[%d].amount is not a number" % (key, i))
    return errs


def amounts_of(d):
    vals = []
    for key in ("sales", "expenses", "credits"):
        for it in d.get(key, []):
            if isinstance(it.get("amount"), (int, float)):
                vals.append(float(it["amount"]))
    return vals


def ungrounded_amounts(d, source_text):
    """Every amount the model reports must be traceable to a number in the input."""
    allowed = numbers_in(source_text)
    return [a for a in amounts_of(d) if a not in allowed]


def totals(d):
    def s(key, pred=lambda x: True):
        return sum(float(x["amount"]) for x in d.get(key, []) if isinstance(x.get("amount"), (int, float)) and pred(x))
    return {
        "sales_total": s("sales"),
        "expenses_total": s("expenses"),
        "receivable_total": s("credits", lambda x: x.get("direction") == "receivable"),
        "payable_total": s("credits", lambda x: x.get("direction") == "payable"),
        "settled_total": s("credits", lambda x: x.get("direction") == "settled"),
    }
