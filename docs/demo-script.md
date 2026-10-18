# Two-minute demo script

**Live app:** https://brimsel-kids-chatbot-153163979718.us-central1.run.app

Total run time: **2:00**. Every line is spoken in English. The app is driven live;
nothing here is pre-recorded or mocked.

> Before you start: open the URL in a **fresh private window** so the session
> starts at level 1, set the window to about **1366×768**, and leave it at **100%
> zoom** — the layout is built to fit that without zooming out. Turn the sound on.
> Have the Notion architecture page open in another tab for the close.

---

## 0:00 – 0:15 · The setup (15s)

**Say:** "This is Buddy, an English tutor for children under seven. The whole
design rests on one rule: **code decides, the model only speaks.**"

**Show:** the landing page. Point at the four big mood faces, the large text, the
suggestion buttons, and the fact that the text box works on its own.

**Say:** "Everything is tappable for small hands, nothing needs a microphone — and
it already fits this window at 100% zoom. The input box stays at the bottom no
matter how long you talk."

---

## 0:15 – 0:25 · A warm start (10s)

**Type:** `hello buddy`

**Say:** "That is a **Conversation** turn — the mode badge says so. Unclear
signals default to chat, never to a lesson."

**Point at:** that Buddy **spoke**. "That voice is a neural voice synthesised on
the server, not the browser's robot voice."

---

## 0:25 – 0:43 · Support outranks everything (18s)

**Type:** `I feel sad`

**Say:** "Now watch the mode badge. That is **Support**, the highest-priority mode
— above Engagement, Conversation and Learning."

**Point at:** the reply, and that there is **no question and no quiz**.

**Say:** "A sad child is never drilled. And note the latency: zero milliseconds of
model time. Support turns never call the model at all — the comfort line is a
hand-written constant, so it is both faster and safer."

---

## 0:43 – 1:00 · Learning (17s)

**Type:** `let's learn`

**Say:** "**Learning** mode. Buddy asks for one word."

**Type:** `cat`

**Say:** "Correct — praise for the word just said, then the *next* word. The model
is told the answered word and the target word as two separate fields, so it can
never say 'Yes! dog!' after the child said 'cat'."

---

## 1:00 – 1:16 · A miss is never "wrong" (16s)

**Type:** `apple`

**Say:** "That is a miss. Watch the wording: **'Almost! Let's try together.'** The
word *wrong* never appears anywhere in this app, and the engine never shames a
child."

**Point at:** the mode badge still reads Learning, and the level badge has not
moved.

**Say:** "The model could not have changed that — the score was already decided in
Python before the model was asked to speak."

---

## 1:16 – 1:28 · Engagement, and the 3-turn ceiling (12s)

**Type:** `let's play a game`

**Say:** "**Engagement** — a play request is a game, not a lesson. Buddy is capped
at **three learning turns in a row**: the engine forces a break, so a child can
never be drilled."

---

## 1:28 – 1:40 · The level control, for reviewing (12s)

**Press:** `L3` in the header.

**Say:** "A reviewer should not have to earn promotion to see level three, so
there is a level control right here — labelled **demo control**, because it is not
the rule."

**Point at:** the reply, which already asks a level-3 sentence — **and that the voice
starts with it**, not three seconds later.

**Say:** "Switching clears that level's attempt counters so it starts clean, but it
keeps the conversation and everything Buddy has learned about this child. It never
calls the model — six milliseconds. And the audio is already composed: the server
builds each level's opening line in the background, so the voice arrives with the
text instead of after it."

---

## 1:40 – 1:50 · The session survives a refresh (10s)

**Press:** browser reload.

**Say:** "The server holds **no** child state. The session lives in the browser as
an opaque blob, clamped field by field when it comes back. So a refresh, or a
redeploy, or a new server instance resumes the same lesson — the level, the words,
the streaks. Nothing is stored on the server, so there is nothing to leak."

---

## 1:50 – 2:00 · Close (10s)

**Switch to:** the architecture page in the other tab.

**Say:** "Safety, then mode, then scoring and progression, then the fallback reply —
all of it deterministic. Only the last step can call the model, and it is bounded
by a hard timeout. If the model is slow, rate-limited or completely down, the child
still gets a safe reply. One container, one process, no database. Thank you."

---

## If something goes wrong live

| Symptom | What to say |
| --- | --- |
| A reply sounds like a template | "That is the deterministic fallback — the model was too slow, so the pre-built safe reply ran instead. The child still gets an answer, and the turn stays inside the two-second budget." |
| A provider error | "The provider chain moved to the backup; if both fail, the fallback speaks. Nothing a child sees depends on an API being healthy." |
| Slow first response | Point at the metrics endpoint and note it is the first request after a cold start; the service runs with `min-instances=1` to avoid this. |
| The audio is late | "Speech is off the critical path — the text renders first and the audio is fetched after, so text latency is unaffected. A phrase Buddy has said before is served from cache in zero milliseconds, and every line is pre-composed as soon as the server knows it — which is why a level jump speaks immediately." |
| Why is a *new* sentence slower? | "The neural voice is a round trip to the synthesiser: about three seconds cold, zero from cache. Streaming, connection pooling and a browser websocket were each measured and each changed nothing, so the fix is to never synthesise the same sentence twice." |
| The voice changes to a different person | "That is the browser fallback, used only if the neural voice is genuinely unreachable — it is pinned to a non-male voice so it does not change gender. A single slow phrase no longer switches it." |
| No sound at all | "The neural voice is unreachable, so the browser voice has taken over. Audio is an enhancement, never a dependency." |

## Numbers to have ready

- **306** offline tests.
- **App turn time: p50 589 ms, p95 792 ms, hard-capped at 1804 ms** — 417 live
  turns as recorded by the engine itself.
- **End-to-end from a workstation: p50 ≈ 1.08 s, p95 ≈ 1.3–1.6 s**, of which
  ≈0.45 s is the network hop (measured with bare `GET /api/health` probes).
- **99.2%** of model calls succeeded (388/391); the fallback spoke on 7% of
  turns, and 26 of those 29 were Support turns that never call the model.
- **Speech:** cold synthesis p50 **3.3 s** server-side (n=6, max 4.9 s), **0 ms** on a
  cache hit. The client waits 9 s — above the server's own 8 s ceiling, so the
  server decides — and only then uses the browser voice. A warm level jump returns
  its audio in **19–46 ms**. Measured with `scripts/speech_probe.py` and against the
  live revision.
- **Layout:** the page never scrolls at 1366×768, 1280×720 or 390×844; the composer
  is always inside the viewport. Measured with `scripts/layout_check.js`.
- **Level jump: ~6 ms**, no model call.
- Curriculum: **3 levels, 42 items**, version `2026.10.1`.
- **Support turns call the model zero times** — 0 ms.
