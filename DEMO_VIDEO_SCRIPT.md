# Demo video script (target 3–4 min, screen recording of a terminal)

1. **0:00 – Problem & goal (20 s).** "Shopkeepers don't write ledgers; they talk. Bol2Kaam turns one Hinglish note into records, reminders and escalations — offline, privacy-first, human-approved."
2. **0:20 – Run `python3 demo.py`** and narrate scene by scene (pause on each):
   * Scene 1 happy path — point at totals, resolved date (parso → 12 Oct), confirm.
   * Scene 2 privacy — show `[PHONE] [UPI] [SECRET]` and `C2`; say "the raw text is never saved".
   * Scene 3 **intentional failure A** — "it refuses to guess the amount, asks one question, then recovers".
   * Scenes 4–5 **failures B/C** — malformed / hallucinated LLM output → rejected → fallback.
   * Scene 6 failure D — no speech-to-text installed → asks for text instead of crashing.
   * Scene 7 escalation — money deducted → STOP, routed to human.
   * Scene 8 aggregate for Eko — refused without consent; with consent: counts only.
   * Scene 9 audit — hash chain OK.
3. **2:30 – Show `python3 eval/run_eval.py`** (24/24, 0 PII leaks) and say the honest caveat: self-written cases; bugs it found.
4. **3:15 – Show `tests` + README state diagram.** Close with "what's autonomous vs. human-led, and the next version".
