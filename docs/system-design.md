# System design — BRIMSEL Kids English Chatbot

An English tutor chatbot for children under 7. The product rule that shapes
every decision below:

> **Code decides. The model only speaks.**

Level, score, mode and item choice are computed by deterministic Python. The
language model is handed a one-line instruction and asked to phrase a reply. It
has no authority over the child's progress, so a hallucination, a slow response,
a rate limit or a total provider outage can never move a child up a level, mark
them wrong, or leave them without an answer.

- [1. Scope and constraints](#1-scope-and-constraints)
- [2. Architecture](#2-architecture)
- [3. The turn pipeline](#3-the-turn-pipeline)
- [4. Interaction modes](#4-interaction-modes)
- [5. Progression rules](#5-progression-rules)
- [6. Answer scoring](#6-answer-scoring)
- [7. The model layer](#7-the-model-layer)
- [8. Safety](#8-safety)
- [9. Session state and context](#9-session-state-and-context)
- [10. Latency](#10-latency)
- [11. Testing](#11-testing)
- [12. Trade-offs and what was deliberately left out](#12-trade-offs-and-what-was-deliberately-left-out)

---

## 1. Scope and constraints

The brief asked for one deployable application, lightweight storage, no
multi-agent framework, and no assumption of pre-provisioned cloud credentials.
Those constraints drove six choices:

| Constraint | Decision |
| --- | --- |
| One deployable app | A single FastAPI service serving both the JSON API and the static UI. No separate front-end build, no bundler. |
| Lightweight storage | No database at all. The session is an opaque JSON blob held in the browser's `localStorage` and sent with each turn. |
| No complex auth | There is no account and therefore no login. Nothing personal is ever stored server-side. |
| No multi-agent | One model call, one directive, one reply. There is no planner, no critic, no agent loop. |
| Child under 7 | Large targets, short sentences, one question per turn, no dense text, no timers, no streaks the child can lose. |
| Must degrade safely | Every mode has a hand-written deterministic reply ready *before* the model is called. |

### Why no database

For a single child on a single device, server-side storage buys nothing and
costs a lot: connection pooling, migrations, a datastore to secure, and a
container that can no longer be replaced at any moment. Keeping state in the
browser means:

- the service is **stateless**, so Cloud Run can scale to zero, restart mid-lesson
  and serve from any instance without losing a lesson;
- a **refresh does not reset the session** — the blob is in `localStorage`, so a
  reload resumes the same level, item, mastery boxes and mood history;
- there is **nothing to leak** — the server never persists what a child said.

The trade-off is that a child is tied to one browser profile. That is the right
trade for "under 7, one device, no accounts", and it is reversible: the blob is
a plain dict, so a `sessions` table is an additive change behind
`normalize_state()` rather than a rewrite.

---

## 2. Architecture

![Architecture](architecture.png)

> Source: [`architecture.mmd`](architecture.mmd) · re-render with
> `npx @mermaid-js/mermaid-cli -i docs/architecture.mmd -o docs/architecture.png --size 1600 -s 2`

```
brimsel-kids-chatbot/
├── app/
│   ├── main.py          FastAPI routes, request models, metrics wiring
│   ├── engine.py        plan_turn / finalize_turn / advance — the whole turn
│   ├── modes.py         Support > Engagement > Conversation > Learning
│   ├── progression.py   mastery boxes, promote / ease / demote, item choice
│   ├── text.py          normalisation, scoring, input and output safety
│   ├── fallbacks.py     every deterministic child-safe reply
│   ├── prompts.py       system prompt, directive, user prompt
│   ├── llm.py           provider adapters + the latency-bounded chain
│   ├── state.py         session shape and tamper-resistant normalisation
│   ├── curriculum.py    loads and validates the versioned curriculum
│   └── metrics.py       in-process latency recorder
├── data/curriculum.json the versioned curriculum (content, not code)
├── static/              index.html, styles.css, app.js — no build step
├── tests/               225 tests
├── scripts/smoke.py     public-URL smoke test + client-side latency
├── scripts/bench_models.py  model latency benchmark
├── scripts/progression_check.py  drives a live deployment L1 -> L2 -> L3
├── docs/                this document, the diagram, the demo script
└── Dockerfile           one image, one process
```

The layering is strict and one-directional:

```
main.py  →  engine.py  →  modes.py / progression.py / prompts.py / fallbacks.py
                       →  text.py    (pure functions, no I/O)
                       →  llm.py     (the only module that touches the network)
```

`text.py`, `modes.py` and `progression.py` import nothing that does I/O, so the
entire teaching policy is testable synchronously with no server and no model.

---

## 3. The turn pipeline

`POST /api/chat` carries `{session, message, mood, reply_ms}`. `engine.plan_turn()`
runs six stages and produces a `TurnPlan` **before** any model call:

| # | Stage | Module | Output |
| --- | --- | --- | --- |
| 1 | **Input safety** | `text.py` | clean text, `blocked`, `worry`, `pii_removed`, `too_long` |
| 2 | **Pace signals** | `engine.py` | repeat count, short-reply count, last message |
| 3 | **Mode** | `modes.py` | exactly one of Support / Engagement / Conversation / Learning + reason |
| 4 | **Teaching** | `progression.py` | `correct` (True/False/None), mastery update, promote/ease/demote, next item |
| 5 | **Prompt** | `prompts.py` | system prompt, directive, user prompt, **and the fallback reply** |
| 6 | **Commit** | `state.py` | child turn appended to history |

`finalize_turn()` then takes either the model's text or the pre-built fallback,
runs output safety, commits the bot turn, and returns.

### Why the split matters

Everything that determines a child's experience is in stage 1–5 and is
synchronous. Stage 6 is the only part that can be slow or fail. This is what
makes "the model is optional" true rather than aspirational: the fallback is not
an error path bolted on afterwards, it is built alongside the directive, from the
same facts, as a first-class reply.

It also means the primary test surface needs no network. `tests/test_engine.py`
drives whole conversations through `plan_turn` and asserts on the resulting
level, item and reply — with `NullProvider` installed by an autouse fixture.

### One request per turn

`use_llm` is false for Support turns, for unsafe input, and when the child sent
nothing at all. Those turns answer from templates in ~1 ms. Every other turn
makes at most one model call (see [§7](#7-the-model-layer) for the single
fast-fail exception).

---

## 4. Interaction modes

Four modes, with a strict priority:

**Support > Engagement > Conversation > Learning**

Any turn is exactly one mode. `detect_mode()` is a straight-line function — no
scoring model, no model call — so the same inputs always produce the same mode.

| Mode | Triggered by | Behaviour |
| --- | --- | --- |
| **Support** | A worry disclosure, a distress word ("sad", "scared"), a support mood, or frustration *combined with* a miss streak or a low mood. | Comfort only. Never teaches, never quizzes, and **never calls the model** — the deterministic line is safer and instant. A worry disclosure always names a trusted adult. |
| **Engagement** | 3 learning turns already used, boredom words, an explicit play request, two consecutive short replies, two repeats, or a pause over 25 s. | A game, a silly question, a movement break. Deliberately resets the learning streak. |
| **Conversation** | A greeting, personal news, an off-topic question, or anything unclear. | Warm chat. This is the **default**: unclear signals never force a lesson. |
| **Learning** | The child is answering a pending item, or explicitly asked to learn/practise. | Score, progress, ask the next item. |

### The 3-learning-turn ceiling

`MAX_LEARN_STREAK = 3`. `state["learn_streak"]` increments on every Learning turn
and resets on any other. The check is the **first** Engagement rule, so a fourth
consecutive learning turn is impossible — the engine forces Engagement before it
considers boredom or a play request.

Deliberate detail: a one-word reply like `"cat"` is a *success*, not
disengagement. Both `short_replies` and `repeat_count` are suppressed while the
child is answering a pending item, so brevity during practice cannot be
misread as boredom.

### When the drill text reads like small talk

Two level-3 drills are literal sentences — *"I went to the park with my mom"* and
*"it is raining and I have an umbrella"* — and both match the personal-news
lexicon (`i went`, `my mom`, `i have`). Any level-1 word can arrive the same way
("I have a cat" for `cat`). Mode detection is therefore handed one extra boolean:
whether the reply is recognisably aimed at the pending item, computed by
`attempts_item()`, which asks *"did they mean to answer?"* and never *"was it
right?"*.

That flag can only move a turn from Conversation to Learning. Support and
Engagement are evaluated before it, so the priority order and the 3-turn ceiling
are unchanged, and the flag is `False` whenever there is no pending item.

### Support is a floor, not a feature

Support outranks everything, including a pending question. A child who is asked
to say "cat" and answers *"I am sad"* is comforted, not marked wrong, and the
question is dropped.

---

## 5. Progression rules

Deterministic arithmetic over session state. The model cannot see, let alone
change, any of it.

| Rule | Condition | Effect |
| --- | --- | --- |
| **Promote** | level < 3 **and** ≥ 8 attempts at this level **and** ≥ 4 of the last 5 correct | level += 1; attempts, window and streaks reset |
| **Ease** | 3 consecutive misses | serve the weakest item again **with a hint**; miss streak resets |
| **Demote** | level > 1 **and** ≥ 6 attempts **and** ≤ 1 correct in the last 6 | level -= 1 |

Order is **promote → ease → demote**, and each returns immediately, so at most
one fires per attempt. Demotion is an extra safety net beyond the brief's two
named rules; it is only reachable with no active 3-miss streak, and it protects a
child who was promoted too early and is now quietly failing everything.

### Mastery boxes

Every item holds a mastery box in `0..3`. A correct answer moves it up one, a
miss moves it down one. `select_item()` serves items with box ≤ 1 first, then
rotates the rest by a per-session cursor, skipping the item just asked. So a
struggling item returns sooner — within the session and, because the boxes live
in the session blob, in the child's next session too.

Ties keep curriculum order and there is no randomness anywhere, so a demo is
reproducible.

### Attempts cannot be faked

`record_result()` is only called when the child produced a *real* answer
(`correct` is `True`/`False`). Sending "um" or pressing send repeatedly yields
`correct = None`, which re-asks the same item with a hint and **does not count as
an attempt**. Promotion cannot be farmed by spamming the send button.

---

## 6. Answer scoring

`evaluate_answer()` returns three-valued logic: `True`, `False`, or `None` for
"no usable answer", which the engine treats as a re-ask rather than a failure.

A second, deliberately separate function — `attempts_item()` — answers a
different question for mode detection: whether the reply is *aimed at* the
pending item. It is not scoring, and keeping the two apart is what lets a
correct answer to a sentence that reads like small talk still be scored. See
[§4](#when-the-drill-text-reads-like-small-talk).

**Closed items (`repeat`)** — the child must say the target word or phrase:

1. `None` if the reply is a non-answer ("i don't know", "um", an emoji alone).
2. `True` on a normalised similarity match (handles `"cats"` for `"cat"`,
   punctuation, casing, an apostrophe variant).
3. `True` if the reply *contains* the expected token sequence — `"the cat is big"`
   counts for `"cat"`, which is how a child naturally answers.
4. Otherwise `False`.

Deliberately asymmetric: a **long** reply containing the target is credited, but
a bare fragment is **not** credited for a long phrase — `"cat"` does not count as
`"i went to the park with my mom"`. Whole-token matching also prevents the
classic bug where `"apple"` scores correct for target `"a"` and `"the cat is
big"` scores correct for target `"the"`.

**Open items (level 3)** use an `accept` list in the curriculum:

- non-empty `accept` → an accepted keyword must appear; the child must stay on
  topic but need not match a script;
- empty `accept` ("what did you do today?") → there is no right answer, so any
  reply of two or more real words counts as a genuine attempt.

`False` on an open item is always phrased as *"almost, let's try together"* and
never as "wrong".

---

## 7. The model layer

### Adapters

`llm.py` defines one `complete(system, user, timeout_ms, max_tokens)` interface
with five implementations:

| Adapter | Notes |
| --- | --- |
| `OpenAICompatibleProvider` | Shared chat-completions implementation, so Groq and OpenRouter differ only in base URL, default model and headers. |
| `GroqProvider` | Groq's free tier. **Preferred lead**, because its free quota is explicit — unlike the OpenRouter key this project started on, which reported `total_credits: 0` while pointed at a *paid* model. |
| `OpenRouterProvider` | OpenAI-compatible; broadest model choice. Leads when no Groq key is present. |
| `GeminiProvider` | Google `generateContent`. Sends `thinkingConfig.thinkingBudget = 0` so hidden reasoning tokens cannot eat a two-sentence reply's output budget. |
| `ChainProvider` | A lead plus a backup sharing **one** time budget. |
| `NullProvider` | No key configured. Always fails, so the deterministic reply runs. |

When `LLM_PROVIDER` is unset the lead is chosen automatically in the order
**groq → gemini → openrouter**, and whichever remaining key exists becomes the
backup. The deployment sets it explicitly rather than relying on the order, so
adding a key does not silently reroute the live service.

### The chain, and why it only retries fast failures

A rate limit should not cost a child a reply, so a second provider is tried —
but a **timeout must not be retried**. Two stacked timeouts would turn a 1.8 s
worst case into 3.6 s and blow the latency target outright. So the chain walks
its providers only while time remains in the budget, and stops the moment one
reports `timeout`:

```
started = now
for provider in providers:
    remaining = budget - (now - started)
    if remaining < 250ms: break
    result = provider.complete(..., timeout_ms=remaining)
    if result.ok: return result
    if result.error == "timeout": break   # budget spent; let the template speak
return last_failure                        # engine uses the deterministic reply
```

Net effect: **exactly one successful model call per turn**. A second request
happens only when the first fails outright *and* time is left, and both share the
same ceiling — so the child's worst-case wait is unchanged.

### Hard timeout

`httpx` timeouts are per-socket-operation, so a slowly trickling response body
can outlive them. Each request is therefore wrapped in `asyncio.wait_for`, which
gives the turn an actual wall-clock guarantee rather than a per-read one.

### The directive

The model receives a system prompt of hard rules plus a single flat directive
line:

```
mode=learn, level=1, last_answer=almost, streak=0, mood=happy, praise=effort,
reason=answering_prompt, answered="cat", item_kind=repeat, target="dog",
ask="Can you say dog?"
```

Two things are worth calling out.

**`answered` and `target` are separate.** The first user testing round produced
*"Yes! dog!"* after the child had said *"cat"* — the classic off-by-one where
praise and question get crossed. Splitting them, in the directive and in the
system prompt, fixed it: praise belongs to `answered`, the question to `target`.
The deterministic learning fallbacks are built from the same two fields, so the
model and the fallback can never disagree about which word was just said.

**The directive cannot be mistaken for authority.** It contains only facts and
one instruction. The system prompt states that the model must never change the
level, score or mode and never mention them; and since the code has already
decided all of it, a model that ignores that rule changes nothing.

### Prompt budget

`SYSTEM_PROMPT` is ~1.3 KB and the user prompt ~0.6 KB — a few hundred tokens
including the three most recent child turns. Short prompts keep
time-to-first-token low, which is the dominant cost at this latency.

---

## 8. Safety

Four independent layers. Safety never depends on the model, because the
deterministic reply is built either way.

**1 · Input** (`sanitize_input`)

- unsafe-topic terms are matched on **word boundaries**, so `"assault"` never
  trips a rule aimed at a substring, and `"good morning"` never matches `"go"`;
- emails, phone numbers and URLs are replaced with a placeholder before anything
  else happens;
- length is capped; over-long input is truncated rather than rejected, so a child
  mashing keys still gets a friendly reply;
- **worry detection** flags disclosures and routes to Support.

Blocked text is never forwarded to the model. It is stored as
`[hidden for safety]` so an unsafe string is not preserved in the session blob
either.

**2 · Mode floor.** Support outranks every other mode, and Support turns never
call the model — the comfort line is a hand-written constant.

**3 · Output** (`sanitize_output`). The model's reply is stripped of URLs and
markdown, checked against block lists and an overreach filter (no diagnosing, no
promising, no personal questions), then trimmed to a sentence budget — counting
only sentences that contain letters, and treating an ellipsis as *not* a sentence
boundary. If it is empty, blocked or degraded, the pre-built fallback is used
instead and the turn is flagged `output_filtered`.

**4 · Tone rules, enforced in text.** Never "wrong", never shaming. A miss is
*"Almost! Let's try together."* Effort is praised, not ability. A worrying
disclosure always points to a trusted adult, and the code never diagnoses:
the response is comfort plus a referral, never an interpretation.

---

## 9. Session state and context

The session is a plain dict of bounded fields: level, mastery boxes, attempts,
the last-10 accuracy window, streaks, current and pending item, modes, the last
12 turns, recent moods (last 5), the worry flag, and counters.

**Tamper resistance.** The blob round-trips through the browser, so it is
untrusted input. `normalize_state()` rebuilds it field by field: unknown keys are
dropped, numbers are clamped to legal ranges (`level` to 1–3, boxes to 0–3,
strings truncated, history capped), and invalid entries are discarded rather
than trusted. A child who edits `localStorage` to say `"level": 99` gets level 3.

**Context awareness.** Each turn the prompt carries the nickname, level and
level focus, correct and miss streaks, up to three weak words, and the last three
things the child said. That is what lets Buddy follow up naturally ("that sounds
fun — what did you play on?") instead of restarting every turn.

**Refresh safety.** Because state lives in `localStorage` and the server keeps
none, a refresh, a redeploy or a load-balancer hop resumes the same lesson
exactly. Worth noting: the child's messages do travel to the browser, so this is
device-local persistence, not private-within-the-browser storage.

---

## 10. Latency

The budget is 2 s for a complete turn. Two mechanisms enforce it and one
decides whether the model speaks at all:

1. **A hard per-call timeout** — `LLM_TIMEOUT_MS`, default 1800 ms, enforced with
   `asyncio.wait_for`. Worst case for a model turn is the cap plus a few ms of
   JSON work.
2. **A pre-built fallback** — if the model does not answer in time, the turn ends
   at the cap with a real reply, not an error.
3. **Model selection by measurement** — `scripts/bench_models.py` times
   candidates against the real prompt.

### Measured on the live deployment

417 turns of real traffic, as recorded by the engine itself at `GET /api/metrics`:

| Metric | Value |
| --- | --- |
| **p50** | **589 ms** |
| **p95** | **792 ms** |
| max | 1804 ms — exactly the configured ceiling |
| Model calls / succeeded | 391 / **388 (99.2%)** |
| Fallbacks | 29 (7.0%), of which 26 are Support turns that never call the model |

End-to-end from a workstation, over two consecutive 60-turn runs, was p50
1084 ms and 1082 ms, p95 1615 ms and 1265 ms. The isolated network hop — 20 bare
`GET /api/health` calls per run — measured p50 442 ms and 461 ms, which is
consistent with the per-turn `client − server` difference (471 ms and 438 ms).
So of the ~1.08 s a remote child waits, roughly 0.45 s is the network hop and
roughly 0.6 s is this service.

`scripts/smoke.py` reports all three of those numbers separately and on purpose:
one figure alone would either hide the network or overstate the app.

### Speech and layout, measured on the same revision

Both were added in the bug-fix revision and measured rather than estimated.
Neither is on the critical path, so neither appears in the turn numbers above.

| Measurement | Result |
| --- | --- |
| Cold synthesis, short reply (**n=6**) | **p50 3311 ms** (min 2606, max 4910) server |
| Cache hit | **0 ms** server, ~390–500 ms client (pure hop) |
| Client wait, then browser voice | 5000 ms |
| Server ceiling | 8000 ms (`SPEECH_TIMEOUT_MS`) |
| Layout at 1366×768 / 1280×720 / 390×844 | page never scrolls; composer always visible; only the message list scrolls |

A local run against the same code timed out model calls at exactly the 1805 ms
ceiling, which looked alarming until the same call was made directly: **Gemini
answered in 1808–2379 ms from that sandbox** while OpenRouter answered in
1322–1746 ms. The sandbox's network hop is ~1.3 s. On Cloud Run the same chain
answered in **886 ms**. The lesson is worth recording: a latency problem measured
through a slow link is a measurement of the link.

The speech timings come from `scripts/speech_probe.py`, which is also the script
that rejected per-sentence synthesis — see section 11.

### Model benchmark

`scripts/bench_models.py` times candidates against the real prompt, p50 over 4–5
samples each:

| Model | p50 | Notes |
| --- | --- | --- |
| `google/gemini-2.5-flash` (OpenRouter) | 0.90 s | |
| `google/gemini-3.5-flash-lite` (OpenRouter) | 1.34 s | **in production** |
| `openai/gpt-4o-mini` (OpenRouter) | 1.75 s | |
| `gemini-3.5-flash` (Gemini API) | 1.83 s | **the backup** |
| `gemini-3.1-flash-lite` (Gemini API) | 2.76 s | |

Two findings from that table shaped the code. `gemini-3.5-flash-lite` answers
HTTP 400 to `thinkingConfig.thinkingBudget: 0` while `gemini-3.5-flash` needs it,
so the budget is configurable (`GEMINI_THINKING_BUDGET`, `none` to omit the
field). And upstream latency was **highly variable** during development — the
same call and the same model ranged from **0.85 s to 7.3 s** depending on the
moment. That volatility, not the average, is the real design input: no single
upstream can be trusted to be fast, which is exactly why the fallback is a
first-class reply rather than an error path. Measured results, sample sizes and
this limitation are reported honestly in the [README](../README.md#measured-latency).

---

## 11. Testing

**288 tests**, all offline: an autouse fixture strips every API key and installs
`NullProvider`, so the suite never touches the network. It also swaps the speech
service for a disabled one, because `edge-tts` *is* installed in the development
venv and a speech test would otherwise reach the real synthesiser.

| File | Covers |
| --- | --- |
| `test_progression.py` | promote at 8 attempts / 4-of-5, no promote at 7 or 3-of-5, ease after exactly 3 misses, demote, mastery boxes, deterministic item selection |
| `test_modes.py` | all four modes, the full priority order, the 3-learning-turn ceiling, play-vs-learn requests, greeting and boredom signals, unclear → Conversation |
| `test_safety.py` | input blocking, PII scrubbing, worry detection, tone rules ("never wrong"), output filtering, every fallback context producing a safe reply |
| `test_engine.py` | whole conversations: level changes, streak reset, refresh persistence, tamper resistance, one-model-call-per-turn |
| `test_llm.py` | parsing, HTTP 429/500, dead socket, enforced timeout, chain behaviour, thinking-budget payload, provider selection (Groq leading; OpenRouter and Gemini as fallbacks) |
| `test_api.py` | routes, status codes, `X-Response-Time-Ms`, empty input, metrics |
| `test_speech.py` | prosody mapping and clamping, emoji stripping, the SSML wire format checked against `edge_tts.communicate.mkssml`'s own output, one-request synthesis, the in-process cache, timeout, the 503 fallback contract, and that a client cannot inject markup |
| `test_level_switch.py` | the demo level control: what a jump clears (level counters) and what it keeps (mastery, conversation), clamping, that it never calls the model, and that ordinary promotion still works afterwards |
| `test_static.py` | UI contracts: the `[hidden]` guard, panels starting hidden, the settings panel only reserving a column when open, fluid sizing, the message list being the only scroll region, stale audio never playing over a newer reply, and a scan proving no key, provider or model name reaches the browser |

The fallback templates are covered by iterating *every* `FallbackContext` and
asserting the reply is non-empty, child-safe and within budget — so adding a new
context without a template fails the suite.

```bash
python -m pytest        # 287 passed
```

Layout is the one thing a unit test cannot see, so it is checked in a real
browser by `scripts/layout_check.js` (headless Chromium, three viewports, 30
injected turns). It is a developer script rather than part of the suite, because
the suite is deliberately offline and dependency-light.

### Bugs that reached a real conversation

The unit suite is fast, but it cannot see a stylesheet, and it cannot easily
notice that a *correct* answer never gets scored. Driving the live app in a real
browser and the live API through a full promotion path found the defects below
that no unit test had.

1. **`.typing { display: flex }` silently defeated `hidden`.** A `display`
   declaration beats the user agent's `[hidden] { display: none }`, so
   *"Buddy is thinking…"* was visible on every page load, permanently, layered
   over the mood buttons. Fixed with an explicit
   `[hidden] { display: none !important }` guard, and pinned by a test asserting
   the guard exists — because the failure mode is an edit, not a logic error.
2. **The chat card was being auto-placed into the narrow column.** The wide
   layout declared `grid-template-columns: 1fr 340px` unconditionally, with the
   debug panel placed in column 2 *only when visible*. With the panel hidden
   (the default), grid auto-placement put the **chat** card in the 340 px column
   and the mood card in the wide one — exactly backwards, and it swapped when the
   panel opened. Fixed by gating the second column on `is-open`, so the aside
   only reserves space while it is actually open.
3. **Two level-3 sentence drills could never be completed.** The curriculum
   sentences *"I went to the park with my mom"* and *"it is raining and I have an
   umbrella"* match the personal-news markers (`i went`, `my mom`, `i have`).
   Mode detection therefore classified the child's **correct** answer as
   Conversation, the item was never scored, no attempt was recorded, and the
   child sat at level 3 forever without ever clearing a sentence. The same
   collision would hit any level-1 word said inside a chatty sentence ("I have a
   cat" for `cat`).

   The fix keeps the stated priority intact. The engine computes
   `attempts_item(reply, item)` — *"did the child mean to answer this
   question?"*, not *"was it right?"* — and passes it to mode detection as
   `attempts_pending`. When the child is mid-exercise and the utterance is
   recognisably aimed at the prompt, the drill wins over the incidental
   small-talk signal. **Support and Engagement are checked first and are
   untouched**, so a distressed child is still comforted and the 3-turn ceiling
   still fires; and a genuinely unrelated remark ("I went to the beach") still
   lands in Conversation, which drops the question rather than nagging.

   This one was caught by `scripts/progression_check.py`, which drives a live
   deployment all the way from L1 to L3. Its first version was itself wrong: it
   sent "let's learn" before each answer, not realising that a message sent while
   a question is pending is *scored as an answer to that question*. Every second
   turn was recorded as a miss, the 4-of-5 window never filled, and promotion
   correctly never fired — which looked exactly like a progression bug. Answering
   the pending item directly is the only faithful way to drive the engine.

Both were verified in the browser by reading back the computed geometry
(chat and mood card 664 px wide in one column; 786 px with the 380 px panel
beside them) rather than by eyeballing a screenshot.

The bug-fix revision (UI scale, settings panel, natural voice, visible level
control) added five more, four of which were found by *new* checks rather than by
the existing suite:

4. **The level jump scored its own question as a miss.** The first version of the
   switcher called `POST /api/level` and then automatically sent `"let's learn"`
   so the child saw something happen. But a message sent while a question is
   pending is *scored as an answer to that question* — so jumping to a level
   silently marked the first item wrong and moved straight past it. Found by
   driving `/api/level` then `/api/chat` against the live revision and reading the
   `item_asked` field: the item the jump asked and the item that got scored were
   not the same item. The fix is to send nothing at all; the jump already asks a
   level-appropriate question. Pinned by a test that asserts the switcher body
   contains no `send(`.
5. **`set_level` returns `None`.** `change_level` was written as
   `state = set_level(normalize_state(state), level)` — but `set_level` mutates in
   place and returns nothing, exactly like `set_mode`. The state became `None`, was
   re-normalised to a *fresh* level-1 session, and the jump silently did nothing.
   Caught immediately by the new test `test_a_jump_asks_an_item_from_that_level`,
   which checks the asked item's level rather than just "the reply is non-empty".
6. **The speed slider was inverted for slow modes.** `rate` is a signed offset, and
   the first implementation scaled it: `rate * factor`. The comfort rate is
   **negative**, so a "faster" setting of 1.3 multiplied −22 by 1.3 and made the
   voice *slower*. The fix nudges the scale instead of rescaling the offset. The
   test that caught it compares all three slider positions.
7. **The settings panel pushed the page into scrolling.** The panel is taller than
   a 768 px laptop. `align-self: start` made it size to its content rather than the
   grid row, so it overflowed and grew `documentElement.scrollHeight` — the exact
   failure the fixed layout exists to prevent, reintroduced by the new panel. The
   fix is `align-self: stretch` plus `max-height: 100%`, so it is bounded and
   `.settings-body` scrolls inside. Measured: 977 px tall in a 768 px viewport
   before, 658 px after.
8. **The quick-say chips were crushed to slivers.** Flex items shrink by default,
   so the growing message list squeezed the quick-say row instead of scrolling
   itself — the buttons were visible only as their top edges. Fixed with
   `flex: 0 0 auto` on every control row, so the message list is the only thing
   that gives way. The layout check now asserts the row's height, not just that the
   composer is on screen, because the original check passed while this bug was
   present.

The lesson from 7 and 8 is the same: an assertion that "the composer is visible"
was satisfied while the controls above it were being crushed. The check had to be
made specific.

### Measured, then rejected: per-sentence synthesis

With `<break>` and `<emphasis>` rejected by the synthesiser (see
`scripts/speech_probe.py`), the obvious way to get a pause between sentences, or a
slower final question, is to synthesise each sentence separately and concatenate
the MP3 frames. That was built, and then removed, because measurement killed it:

| | Server time |
| --- | --- |
| 2 segments | 2276 ms |
| 3 segments | 4236 ms |
| **ratio** | **1.86×** |

Wall-clock grows with the sentence count, so the connections are serialised rather
than concurrent. It bought a slightly different rate per sentence and cost latency
in direct proportion to reply length — while the naturalness the user actually
complained about comes from the *neural voice*, not from per-sentence prosody, and
the voice already pauses at punctuation on its own. One request is now sent, always.

This is the same failure mode as the speech-recognition and streaming-token
decisions in section 12: an attractive feature whose cost only appears when it is
measured. The probe script exists so the measurement can be repeated rather than
believed.

---

## 12. Trade-offs and what was deliberately left out

| Not built | Why |
| --- | --- |
| Database / Redis | One child, one device. A stateless service is simpler, safer and free. |
| Accounts or logins | The brief said no complex auth, and nothing is stored server-side. |
| Multi-agent / planner | The pedagogy is arithmetic. A planner would add latency and non-determinism for no gain. |
| Front-end framework | No build step means no toolchain to break and a genuinely small page. |
| Streaming tokens | A two-sentence reply is faster delivered whole than streamed, and streaming complicates the safety filter that must see the full text. |
| Speech recognition | Required to work without a microphone. Browser speech input is offered as a progressive enhancement, never a dependency. |
| Randomness | Determinism keeps tests, demos and bug reports reproducible. |
| Model-authored SSML | It would need a second model call (breaking the one-call-per-turn rule), add latency, and hand an injection surface to the model. The model gives *intent*; `app/speech.py` builds the document. |
| Per-sentence synthesis | **Measured and rejected** — wall-clock grows 1.86× from 2 to 3 sentences, because the connections are serialised. See section 11. |
| Cloud Text-to-Speech / Vertex AI | Both require a **billing account** on the project, which it does not have. `edge-tts` needs no key, so it is the voice; the browser voice is the fallback. |
| Streaming audio to the browser | The obvious next step for time-to-first-sound, and genuinely better. Not implemented: the reply text is already on screen first, so the delay is not blocking, and the cache makes repeats instant. |

**Known limitations**

- A session lives in one browser profile; clearing site data starts over.
- Upstream model latency is outside our control and varies by more than 8×. The
  design bounds it but cannot eliminate it.
- The `accept` keyword lists for level-3 questions are hand-written and
  necessarily incomplete; an unusual but valid answer can score as a miss.
- Answer scoring is lexical, not phonetic. A child with a strong accent who
  *says* "cat" correctly but types "kat" is scored as a miss. Speech input, where
  available, is the mitigation; a phonetic scorer is the real fix.
- Provider health is not monitored beyond per-turn failover. A sustained outage
  degrades to deterministic replies — acceptable for this scale, but a real
  deployment would want alerting on the fallback rate.
- **Cold synthesized audio starts about three seconds after the reply appears.**
  A cache hit is instant and the cache fills fast, but a *new* phrase pays the
  synthesis cost. Measured on the live revision over six short phrases: p50
  3311 ms, max 4910 ms, and one earlier sample exceeded the 8 s ceiling and fell
  back. The client waits 5 s and then uses the browser voice. The reply text is
  always on screen first, and a superseded speech request is dropped rather than
  played over a newer reply.
- **The lead model is a free tier.** The OpenRouter key this project started on
  reported `is_free_tier: true` with `total_credits: 0` while pointed at a *paid*
  model, so it worked on an undocumented allowance and could have failed silently.
  Groq is preferred for exactly this reason (explicit free quota) and is wired in,
  but the deployed instance has no Groq key yet, so it runs on the measured-good
  OpenRouter + Gemini pairing.
- **The level control is a demo affordance, not part of the pedagogy.** It is
  labelled as such in the UI. A reviewer can move the child anywhere; the promotion
  rule is unchanged and still applies if the control is left alone.

---

## 13. Natural speech — added in the bug-fix revision

The report said the audio was *"absolutely robotic"*. It was: the first version
used the browser's own `SpeechSynthesis`, which on Linux and many Android devices
is a formant synthesiser. The fix is a real neural voice, `edge-tts`, with the
browser voice demoted to a fallback.

### Why the server synthesises, and why the model does not write the markup

The client sends **plain text plus three clamped values** (mode, `correct`,
speed). It never sends SSML, because there is no parameter for it — injection is
designed out rather than filtered. The server turns the model's *intent* into the
document and caches the result.

The model is deliberately not asked for SSML. That would need a second model call
(violating the one-call-per-turn rule), add a second latency hop, and hand an
injection surface to a component whose output is already treated as untrusted. It
is asked for *energy* — "gentle", "bright", "slow" — in the same call it is
already making.

### The document shape is verified, not assumed

The synthesiser accepts exactly one shape and answers `NoAudioReceived` to
anything else, so a wrong document is **silent rather than loud** — the worst
possible failure, since nothing would look broken. Two guards:

1. `scripts/speech_probe.py --section ssml` drove eight documents and read the
   duration back from the byte count. `<break>`, `<emphasis>`, nested `<prosody>`
   and a bare tag in `<voice>` are all rejected. Only a single `<prosody>` inside
   `<voice>` works, and only `rate` can be verified that way (pitch and volume do
   not change how long a sentence takes).
2. `test_speech.py` asserts our builder's output is **byte-identical** to
   `edge_tts.communicate.mkssml`'s for the same inputs. If the wire format ever
   drifts, that test fails instead of the audio going quiet.

That test found a real detail: edge-tts expands `en-US-AvaMultilingualNeural` into
`Microsoft Server Speech Text to Speech Voice (en-US, AvaMultilingualNeural)`
before sending. `wire_voice()` mirrors that expansion, so our document is what
actually goes on the wire.

### Rate is derived, not fixed

`style_for(mode, correct, energy, speed)` maps the turn onto one rate/pitch pair:

| Input | Effect |
| --- | --- |
| `Support` | slowest and slightly lower |
| `Learning`, correct | brighter |
| `Learning`, incorrect | slower, never sharper — it is a correction, not a rebuke |
| `Engagement` | a little livelier |
| energy `calm` / `bright` / `slow` | the model's hint, applied within limits |
| speed slider | nudges the whole scale by ±15 % |

Everything is clamped (rate ±40 %, pitch ±8 Hz), so a tampered client cannot ask
for a chipmunk. The speed slider **nudges** rather than **scales** the rate: the
comfort rate is negative, so scaling would have made "faster" mean slower — which
it did, until a test caught it (section 11, bug 6).

### The cache is part of the design, not an optimisation

A cold synthesis costs about **three seconds** from a data centre (p50 3311 ms
over six phrases; the spread is wide and the tail exceeds 8 s); a hit costs
**0 ms** of server time. That gap is the difference between a child waiting and a
child hearing Buddy straight away, so the LRU cache (512 entries, keyed by voice,
prosody and text) is load-bearing. The vocabulary is small, so it fills quickly.

Speech is also deliberately **off the critical path**: the client renders the text
first and fetches the audio afterwards, so none of this appears in the turn
latency numbers in section 10. When speech fails — endpoint down, timeout, autoplay
blocked — the client falls back to the browser voice rather than failing the turn.

---

## 14. The demo level control

A reviewer needs to see level-2 and level-3 content without spending twenty
minutes earning promotion. `L1 / L2 / L3` in the header does that, and the same
control appears in Settings. It is labelled **"demo control"** in both places,
because it must not be mistaken for the promotion rule in section 5.

`change_level()` is deliberately thin. It routes through the normal `plan_turn()`
so the plan is built by the same machinery as any other turn, then drops the
synthetic child turn the planner writes (the child typed nothing — a reviewer
pressed a button) and returns the deterministic reply, which already asks a
level-appropriate item. Consequences worth stating:

- **It never calls the model.** The reply is the deterministic fallback, so a jump
  works with no provider configured and costs ~6 ms. `debug["use_llm"]` is forced
  to `false` so the debug block does not claim a call that did not happen.
- **The session still round-trips through `normalize_state()`**, so a tampered blob
  cannot inject a bad level.
- **It sends no follow-up message.** The first version did, and the follow-up was
  scored as an answer to the question the jump had just asked (section 11, bug 4).

Switching levels clears the counters that decide promotion — `attempts_at_level`,
the 4-of-5 window, the miss and learn streaks, the pending item — so a freshly
chosen level does not arrive part-way to promoting itself, and the learn-streak
reset means practice is not blocked on arrival. It **keeps** per-item mastery and
the whole conversation, because mastery is a record of *this child*, not of the
level they happen to be on.

After a jump, ordinary promotion works exactly as before; a test drives a correct
answer at the new level and asserts it scores and counts.
