# Buddy — a two-minute demo

**Live app:** https://brimsel-kids-chatbot-153163979718.us-central1.run.app

**Running time:** 2:00.

**How to read this.** Lines in **bold** are spoken aloud, word for word. Lines
marked *Do* are what you do to the app. Everything else is a note for you and is
not said out loud.

> **Before you go on stage.** Open the link in a fresh private window, so the
> lesson starts from the beginning. Size the window to roughly **1366×768** and
> leave it at **100% zoom** — the layout is built for that, no zooming out. Turn
> the sound up. Keep the architecture page open in another tab for the last ten
> seconds.

> **Pace.** Speak slowly and let the pauses sit. The app is doing the work in
> front of the audience; you don't have to fill every second with words. All the
> plain numbers, in case anyone asks, are at the bottom.

---

## 0:00 – 0:15 · Meet Buddy *(15s)*

*Do:* leave the page alone and let the audience look at it.

**Say:** "This is Buddy — an English tutor for children under seven. Before I show
you what he does, here's the one idea underneath all of it: **the software decides
what happens, and the AI only says it.** Everything you're about to see is live."

*What they should notice:* four big mood faces, large friendly text, buttons a small
child can hit. Nothing here needs a microphone.

---

## 0:15 – 0:25 · A friendly hello *(10s)*

*Do:* type `hello buddy`.

**Say:** "Watch the badge — **Conversation**, not Lesson. When Buddy isn't sure what
a child wants, he chats with them. He never turns a warm hello into a test."

---

## 0:25 – 0:41 · Comfort comes first *(16s)*

*Do:* type `I feel sad`.

**Say:** "Now the badge reads **Support** — and Support outranks everything else.
Notice what's missing: no question, no quiz. A sad child is never drilled. That
reply is written by hand rather than by the AI, so it arrives instantly — and it
can't come out wrong."

---

## 0:41 – 0:56 · A lesson *(15s)*

*Do:* type `let's learn`, then `cat`.

**Say:** "This is a lesson. Buddy asks for one word — and the answer is right, so he
praises *that* word and moves on to the next. He's told the two words separately, so
he can never congratulate a child for something they never said."

---

## 0:56 – 1:12 · The gentle miss *(16s)*

*Do:* type `apple`.

**Say:** "That one's a miss — and listen to the words he chooses: *'Almost! Let's try
together.'* The word **wrong** doesn't appear anywhere in this app. And the score was
settled *before* the AI was asked to speak, so it cannot soften it, and it cannot
change it."

---

## 1:12 – 1:24 · Play, and the built-in brake *(12s)*

*Do:* type `let's play a game`.

**Say:** "Ask for a game and you get a game — not another lesson. Buddy will never
ask more than three questions in a row either. That brake lives in the software, not
in the AI's judgement."

---

## 1:24 – 1:36 · Switching levels *(12s)*

*Do:* press `L3` in the header.

**Say:** "For reviewing, I can jump straight to the hardest level. And listen —
**the voice starts as the words arrive.** That matters more than it sounds: a child
who can't read yet needs to *hear* the lesson, not wait for it."

*What they should notice:* text and voice land together. The button says **demo
control**, because the real progression rules still apply.

---

## 1:36 – 1:46 · Refresh *(10s)*

*Do:* reload the page.

**Say:** "I'll reload. Nothing is lost — same level, same words, same progress. The
lesson lives in the child's browser, so there's no personal information about a
child sitting on a server."

---

## 1:46 – 2:00 · Close *(14s)*

*Do:* switch to the architecture tab.

**Say:** "Everything you saw follows one order. Keep the child safe. Decide what
happens. Mark the answer. *Then* let the AI speak — and if it's slow, or rate-limited,
or down entirely, a child still gets a kind, safe answer. One small service. No
database. Thank you."

---

## If something hiccups

Don't apologise and don't stop. Say what's happening in plain words and keep the
demo moving — every one of these is a designed behaviour, not a fault.

| If you see | Say |
| --- | --- |
| The reply sounds a little generic | "That's the reply the app writes itself. If the AI is too slow, this is what a child hears — still kind, still correct." |
| The words appear, then the voice a moment later | "Buddy writes the words instantly; the voice follows a beat behind. A sentence he's said before is instant, and so is changing level." |
| A different-sounding voice | "That's the computer's own voice. It only steps in if Buddy's voice can't be reached at all." |
| No sound whatsoever | "The app has fallen back to the computer's voice — or to silence. Sound is a bonus here, never a requirement." |
| The first reply feels slow | "That's the very first request after the service wakes up. It stays warm after that." |
| You're running long | Drop the **Play** beat. The close matters more. |
| You're running short | Ask a wrong answer twice, so the audience sees the level stay put and the encouragement repeat. |

---

## Numbers, in plain words

Only for questions from the floor. Everything below was measured, not estimated.

- **306 automated tests**, and they all run offline — no network, no keys.
- **A typical reply takes about half a second.** The slowest recorded was 1.8
  seconds, and the AI is cut off hard at that point. *(p50 589 ms, p95 792 ms over
  417 live turns.)*
- **From a real browser, about a second end to end** — roughly half of which is
  simply the distance to the server. *(p50 ≈ 1.08 s.)*
- **The AI answered 388 of the 391 times it was asked** — 99.2%. The rest fell back
  to the app's own written reply.
- **Comfort replies never touch the AI at all** — that path is instant and offline.
- **Changing level costs about six milliseconds** and involves no AI whatsoever.
- **Buddy's voice** takes roughly three seconds the first time he says something
  new, and nothing at all after that, because it's remembered. That's why a level
  change speaks immediately.
- **It fits a laptop at 100% zoom** — 1366×768 and 1280×720 — and a phone at
  390×844. The page itself never scrolls; only the conversation does.
- **Three levels, 42 learning items** — single words, short sentences and a few open
  questions — versioned as `2026.10.1`.
