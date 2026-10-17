# Two-minute demo script

**Live app:** https://brimsel-kids-chatbot-153163979718.us-central1.run.app

Total run time: **1:55**. Every line is spoken in English. The app is driven live;
nothing here is pre-recorded or mocked.

> Before you start: open the URL in a **fresh private window** so the session
> starts at level 1, and set the window to about half the screen so the panel on
> the right is visible. Have the Notion architecture page open in another tab for
> the last section.

---

## 0:00 – 0:15 · The setup (15s)

**Say:** "This is Buddy, an English tutor for children under seven. The whole
design rests on one rule: **code decides, the model only speaks.**"

**Show:** the landing page. Point at the four big mood faces, the large text, the
suggestion buttons, and the fact that the text box works on its own.

**Say:** "Everything is tappable for small hands, and nothing here needs a
microphone — a child can type or tap."

---

## 0:15 – 0:25 · A warm start (10s)

**Type:** `hello buddy`

**Say:** "That is a **Conversation** turn — the mode badge says so. Unclear
signals default to chat, never to a lesson."

---

## 0:25 – 0:45 · Support outranks everything (20s)

**Type:** `I feel sad`

**Say:** "Now watch the mode badge. That is **Support**, the highest-priority
mode — above Engagement, Conversation and Learning."

**Point at:** the reply, and that there is **no question and no quiz**.

**Say:** "A sad child is never drilled. And note the latency: 1 millisecond.
Support turns never call the model at all — the comfort line is a hand-written
constant, so it is both faster and safer."

---

## 0:45 – 1:05 · Learning (20s)

**Type:** `let's learn`

**Say:** "**Learning** mode. Buddy asks for one word."

**Type:** `cat`

**Say:** "Correct — praise for the word just said, then the *next* word. The
model is told the answered word and the target word as two separate fields, so it
can never say 'Yes! dog!' after the child said 'cat'."

---

## 1:05 – 1:25 · A miss is never "wrong" (20s)

**Type:** `apple`

**Say:** "That is a miss. Watch the wording: **'Almost! Let's try together.'**
The word *wrong* never appears anywhere in this app, and the engine never shames
a child."

**Point at:** the mode badge still reads Learning, and the level badge has not
moved.

**Say:** "And notice the model could not have changed that — the score was
already decided in Python before the model was asked to speak."

---

## 1:25 – 1:40 · Engagement, and the 3-turn ceiling (15s)

**Type:** `let's play a game`

**Say:** "**Engagement** — a play request is a game, not a lesson."

**Point at:** the learning counter in the panel, then the badge.

**Say:** "Buddy is capped at **three learning turns in a row**. The engine forces
a break, so a child can never be drilled."

---

## 1:40 – 1:50 · The session survives a refresh (10s)

**Press:** browser reload.

**Say:** "The server holds **no** child state. The session lives in the browser as
an opaque blob, clamped field by field when it comes back. So a refresh, or a
redeploy, or a new server instance resumes the same lesson — the level, the
words, the streaks. Nothing is stored on the server, so there is nothing to
leak."

---

## 1:50 – 1:55 · Close (5s)

**Switch to:** the architecture page in the other tab.

**Say:** "Safety, then mode, then scoring and progression, then the fallback
reply — all of it deterministic. Only the last step can call the model, and it is
bounded by a hard timeout. If the model is slow, rate-limited or completely down,
the child still gets a safe reply. One container, one process, no database. Thank
you."

---

## If something goes wrong live

| Symptom | What to say |
| --- | --- |
| A reply sounds like a template | "That is the deterministic fallback — the model was too slow, so the pre-built safe reply ran instead. The child still gets an answer, and the turn stays inside the two-second budget." |
| A provider error | "The provider chain moved to the backup; if both fail, the fallback speaks. Nothing a child sees depends on an API being healthy." |
| Slow first response | Point at the metrics endpoint and note it is the first request after a cold start; the service runs with `min-instances=1` to avoid this. |

## Numbers to have ready

- **200** offline tests.
- **p50 ≈ 1.0 s, p95 ≈ 1.6 s** end-to-end measured client-side against the live
  deployment; see the README for sample size and limitations.
- Hard per-call ceiling: **1800 ms**.
- Curriculum: **3 levels, 42 items**, version `2026.10.1`.
- **Support turns call the model zero times** — ~1 ms.
