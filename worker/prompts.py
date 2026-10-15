"""Instructions for the OPTIONAL local LLM. The worker never depends on it."""

SYSTEM = """You convert a small-shop owner's note (English/Hinglish, already de-identified) into JSON.
RULES:
1. Output ONLY one JSON object. No prose, no markdown.
2. NEVER invent numbers. Every amount must appear in the note. If unknown use null.
3. Customers appear as aliases like C1, C2. Never guess real names.
4. Do not give advice. Only extract.
SCHEMA:
{"sales":[{"item":str|null,"qty":num|null,"unit":str|null,"amount":num,"customer":str|null}],
 "expenses":[{"what":str,"amount":num}],
 "credits":[{"customer":str|null,"amount":num,"direction":"receivable"|"payable"|"settled","due":"YYYY-MM-DD"|null}],
 "reminders":[{"what":str,"when":"YYYY-MM-DD"|null,"customer":str|null}],
 "exceptions":[{"type":str,"severity":"LOW"|"MEDIUM"|"HIGH","detail":str}]}
TODAY: {today}
NOTE: {note}
JSON:"""


def build(note, today):
    return SYSTEM.replace("{today}", str(today)).replace("{note}", note)
