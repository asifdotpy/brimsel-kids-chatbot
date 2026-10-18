# Buddy — two-minute demo

**Live:** https://brimsel-kids-chatbot-153163979718.us-central1.run.app
**Time:** 2:00 · **Read:** the bold lines are spoken, *Do* is your cue.

> Fresh private window, ~1366×768 at 100% zoom, sound on. Speak slowly — the app
> is doing the work. Numbers for the floor are at the bottom.

---

### 0:00 – Meet Buddy *(15s)*
*Do:* let the page sit.

**"This is Buddy, an English tutor for children under seven. One idea holds all of
it together: the software decides what happens, and the AI only says it.
Everything you see is live."**

---

### 0:15 – A friendly hello *(10s)*
*Do:* type `hello buddy`.

**"The badge says Conversation, not Lesson. When Buddy isn't sure what a child
wants, he chats. A warm hello never turns into a test."**

---

### 0:25 – Comfort comes first *(16s)*
*Do:* type `I feel sad`.

**"Now it says Support — and Support outranks everything. Notice what's missing: no
question, no quiz. A sad child is never drilled. That reply is written by hand, so
it arrives instantly and can't come out wrong."**

---

### 0:41 – A lesson *(15s)*
*Do:* type `let's learn`, then `cat`.

**"A lesson. Buddy asks for one word, the answer is right, so he praises that word
and moves on. He's told the two words separately, so he can never praise a child
for something they didn't say."**

---

### 0:56 – The gentle miss *(16s)*
*Do:* type `apple`.

**"That one's a miss. Listen to the words: 'Almost! Let's try together.' The word
*wrong* doesn't appear anywhere in this app. And the score was settled before the
AI was asked to speak, so it can't soften it, and it can't change it."**

---

### 1:12 – Play, and the brake *(12s)*
*Do:* type `let's play a game`.

**"Ask for a game, you get a game — not another lesson. Buddy never asks more than
three questions in a row. That brake is in the software, not in the AI's
judgement."**

---

### 1:24 – Switching levels *(12s)*
*Do:* press `L3`.

**"For reviewing, I can jump to the hardest level. And listen — the voice starts as
the words arrive. A child who can't read yet needs to hear the lesson, not wait for
it."**

---

### 1:36 – Refresh *(10s)*
*Do:* reload the page.

**"I'll reload. Nothing is lost — same level, same progress. The lesson lives in the
child's browser, so no personal information sits on a server."**

---

### 1:46 – Close *(14s)*
*Do:* switch to the architecture tab.

**"Everything you saw follows one order. Keep the child safe. Decide what happens.
Mark the answer. Then let the AI speak — and if it's slow, or down entirely, a child
still gets a kind answer. One small service. No database. Thank you."**

---

## If something hiccups

Don't apologise, don't stop — each of these is designed behaviour.

- **Generic-sounding reply** — "That's the app's own writing. If the AI is too slow,
  this is what a child hears: still kind, still correct."
- **Voice a beat behind** — "Buddy writes instantly; the voice follows. A line he's
  said before is instant, and so is a level change."
- **A different voice, or none** — "The computer's own voice, only if Buddy's can't
  be reached. Sound is a bonus here, never a requirement."
- **Running long** — drop Play. **Running short** — get an answer wrong twice.

## Numbers, if asked

- **306 tests**, all offline.
- **A reply takes about half a second**; hard cap 1.8 s. *(p50 589 ms, p95 792 ms
  over 417 live turns.)* In a browser, about a second end to end.
- **The AI answered 388 of 391 asks** (99.2%). Comfort replies never touch it; a
  level change is ~6 ms.
- **The voice** takes ~3 s the first time only, then it's remembered.
- **Fits 1366×768, 1280×720 and 390×844 at 100% zoom.** 3 levels, 42 items
  (`2026.10.1`).
