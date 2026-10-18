# BRIMSEL Kids English Chatbot

A warm English tutor for children **under 7**. Buddy chats, plays and practises
words, phrases and little sentences — and every decision that affects a child's
progress is made by deterministic code, not by a language model.

> **Code decides. The model only speaks.**

A slow API, a rate limit, a hallucination or a total provider outage cannot move
a child up a level, mark them wrong, or leave them without a reply.

**Live:** _see the ["Live URL"](#live-url) section at the bottom of this file._

![The child's view: mood check-in, Buddy's greeting, tappable suggestions and a text box that works without a microphone](docs/ui.png)

---

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Tests](#tests)
- [Measured latency](#measured-latency)
- [How it works](#how-it-works)
- [Deploy](#deploy)
- [Project layout](#project-layout)
- [Security and privacy](#security-and-privacy)
- [Accessibility and child-safety choices](#accessibility-and-child-safety-choices)
- [Live URL](#live-url)

---

## What it does

**Four modes, one per turn, with a strict priority.**

| Mode | What the child gets |
| --- | --- |
| **Support** | Comfort only, never a quiz. A worrying disclosure always points to a trusted adult. |
| **Engagement** | A game or a movement break, and the learning streak is reset. |
| **Conversation** | Warm chat. This is the default when signals are unclear — a child is never forced into a lesson. |
| **Learning** | One word, phrase or sentence at a time, scored, with gentle hints. |

Support outranks Engagement, which outranks Conversation, which outranks
Learning. After **3 consecutive learning turns** the engine forces Engagement —
the learning streak is capped at 3, so the child cannot be drilled.

**Three levels, one versioned curriculum file.**

| Level | Focus | Items |
| --- | --- | --- |
| 1 | Words and sounds — letters, colours, animals | 20 |
| 2 | Small phrases — noun plus verb, 2–3 words | 12 |
| 3 | Little sentences, questions, tiny stories | 10 |

Content lives in `data/curriculum.json` (version `2026.10.1`). Changing what the
child learns is a content change, not a code change.

**Progression is arithmetic, and it is unit-tested.**

- **Promote** after at least 8 attempts at the level and at least 4 of the last 5
  correct.
- **Ease** after 3 misses in a row: a hint, and the weakest item comes back.
- **Demote** as a safety net if a child promoted too early is struggling.

Non-answers ("um", an emoji, an empty send) never count as an attempt, so
promotion cannot be farmed by pressing send.

---

## Quick start

Requires Python 3.12.

```bash
git clone <this-repo> && cd brimsel-kids-chatbot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then put a real key in .env
python -m uvicorn app.main:app --port 8080
```

Open <http://127.0.0.1:8080>.

**No API key? It still works.** Without a key the engine replies from its
deterministic child-safe templates — the whole app, the modes, the scoring and
the progression are fully functional. Only the wording stops being generated.

### Try it in 30 seconds

```bash
python scripts/smoke.py http://127.0.0.1:8080
python scripts/smoke.py http://127.0.0.1:8080 --turns 30   # latency sample
```

---

## Configuration

Everything is environment-based; see [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `openrouter` | `openrouter`, `gemini` or `none` |
| `OPENROUTER_API_KEY` | — | enables the OpenRouter adapter |
| `GEMINI_API_KEY` | — | enables the Gemini adapter |
| `OPENROUTER_MODEL` | `google/gemini-3.5-flash-lite` | lead model |
| `GEMINI_MODEL` | `gemini-3.5-flash` | backup model |
| `LLM_FALLBACK_PROVIDER` | auto | backup provider; the other key is used if present |
| `LLM_TIMEOUT_MS` | `1800` | hard per-call ceiling |
| `LLM_MAX_TOKENS` | `48` | reply length ceiling |
| `GEMINI_THINKING_BUDGET` | `0` | `0` = answer immediately; `none` for models that reject `0` |
| `CURRICULUM_PATH` | `./data/curriculum.json` | point at a different curriculum |
| `PORT` | `8080` | server port |

If both keys are present the second becomes a **backup inside one shared time
budget**, so one provider's rate limit does not cost the child a reply.

> The `GEMINI_THINKING_BUDGET` knob exists for a real reason: `gemini-3.5-flash-lite`
> answers `400` to `thinkingBudget: 0`, while `gemini-3.5-flash` needs it to stay
> fast. Set it to `none` for the former.

### API

| Route | Purpose |
| --- | --- |
| `GET /` | the child's UI (static, no build step) |
| `POST /api/session` | new session + opening line |
| `POST /api/chat` | one complete turn |
| `GET /api/health` | liveness, provider, curriculum version (no secrets) |
| `GET /api/metrics` | live p50/p95 from real traffic |
| `GET /api/curriculum` | curriculum version and level summary |

---

## Tests

```bash
python -m pytest        # 225 passed
```

Fully offline: an autouse fixture strips every API key and installs a null
provider, so the suite never touches the network and cannot be flaky because an
API is slow.

| File | Covers |
| --- | --- |
| `tests/test_progression.py` | promotion at exactly 8 attempts / 4-of-5; no promotion at 7 attempts or 3-of-5; easing after exactly 3 misses; demotion; mastery boxes; deterministic item selection |
| `tests/test_modes.py` | all four modes; the full priority order; the 3-learning-turn ceiling; play requests vs learn requests; greeting, boredom and pause signals; unclear → Conversation |
| `tests/test_safety.py` | input blocking, PII scrubbing, worry detection, tone rules (never "wrong"), output filtering, and **every** fallback context producing a safe, in-budget reply |
| `tests/test_engine.py` | whole conversations end to end: level changes, streak reset, persistence across a refresh, tamper resistance, one model call per turn |
| `tests/test_llm.py` | parsing, HTTP 429/500, dead socket, enforced wall-clock timeout, provider chain, thinking-budget payload, provider selection |
| `tests/test_api.py` | routes, status codes, `X-Response-Time-Ms`, empty input, metrics |
| `tests/test_static.py` | UI contracts: the `[hidden]` guard, panels starting hidden, the engine panel only reserving a column when open, and a scan proving no key, provider or model name reaches the browser |

Every requirement called out in the brief maps to a named test; see
[`docs/system-design.md`](docs/system-design.md#11-testing).

---

## Measured latency

Measured against the **live deployment**. Reproduce with:

```bash
python scripts/smoke.py <url> --turns 60     # drives real turns and times them
curl <url>/api/metrics                       # the engine's own cumulative record
```

The smoke script deliberately reports **three** numbers, because one number
cannot be honest here: the client's view (what the child waits for), the
server's view (what the app controls), and the difference (the network hop,
which no app can control).

### 1. The engine's own turn — 417 live turns

This is the number the implementation is responsible for.

| Metric | Value |
| --- | --- |
| Turns | **417** |
| **p50** | **589 ms** |
| **p95** | **792 ms** |
| mean | 587 ms |
| max | 1804 ms (exactly the configured ceiling) |
| Model calls | 391 |
| Model calls that succeeded | **388 (99.2%)** |
| Turns answered from the deterministic fallback | 29 (7.0%) |

Per mode, server-side p50 / p95: Learning 591 / 806 ms · Engagement 599 / 746 ms ·
Conversation 609 / 783 ms · **Support 0 / 0 ms** (Support never calls the model).

### 2. End-to-end, client-measured — two consecutive 60-turn runs

| Metric | Run 1 | Run 2 |
| --- | --- | --- |
| Samples | 60 | 60 |
| **p50** | **1084 ms** | **1082 ms** |
| **p95** | **1615 ms** | **1265 ms** |
| min | 419 ms | 410 ms |
| max | 10513 ms | 1451 ms |
| Fallback replies | 4 | 4 |

### 3. The network hop, isolated

20 bare `GET /api/health` calls per run — no model, no state, so essentially pure
network round trip from the measuring workstation to `us-central1`:

| Metric | Run 1 | Run 2 |
| --- | --- | --- |
| hop p50 | 442 ms | 461 ms |
| hop p95 | 514 ms | 582 ms |

**Reading these numbers honestly.**

- **The p50 of ~1.08 s is ~0.49 s of app and ~0.45 s of network.**
  `client p50 − server p50` measured 471 ms and 438 ms in the two runs, which
  matches the bare-GET hop of 442 ms and 461 ms. The app's share is the
  589–628 ms the engine reports for itself.
- **The engine's p95 is 792 ms, comfortably inside the two-second budget, and its
  maximum is 1804 ms — exactly the configured `LLM_TIMEOUT_MS` ceiling.** The
  hard cap is real: it held across all 417 turns.
- **The 10.5 s client outlier was the network, not the app.** In that same run the
  engine's own maximum was 1212 ms, and the isolated hop measurements in the same
  run reached large values too. The measuring sandbox's link to `us-central1` is
  variable; a single stalled request explains the tail entirely. Run 2, taken
  minutes later on the same revision, has a 1451 ms maximum.
- **Only 3 of the 29 fallbacks were model failures.** 26 were Support turns,
  which by design never call the model. So the model answered on **99.2%** of the
  turns where it was actually asked to speak.
- **Sample sizes: 417 server-side turns, 120 client-side turns, 40 hop probes.**
  No longer run has been measured.

Two mechanisms keep this bounded, and one decides whether the model speaks:

1. **Hard per-call timeout** — `LLM_TIMEOUT_MS`, default 1800 ms, enforced with
   `asyncio.wait_for` (an `httpx` timeout alone is per-socket-operation and a slow
   body can outlive it).
2. **A deterministic reply built before the model is called** — if the model is
   too slow, the turn still ends with a real, child-safe sentence.
3. **Model chosen by measurement** — `scripts/bench_models.py` times candidates
   against the real prompt.

**Honest limitation.** Upstream latency was *highly variable* during development:
the same model with the same prompt ranged from **0.85 s to 7.3 s** depending on
the moment. No single upstream can be trusted to be fast. That volatility — not
the average — is why the fallback is a first-class reply rather than an error
path. Model benchmark, p50 over 4–5 samples each:

| Model | p50 |
| --- | --- |
| `google/gemini-2.5-flash` (OpenRouter) | 0.90 s |
| `google/gemini-3.5-flash-lite` (OpenRouter) ← in production | 1.34 s |
| `openai/gpt-4o-mini` (OpenRouter) | 1.75 s |
| `gemini-3.5-flash` (Gemini API) ← backup | 1.83 s |
| `gemini-3.1-flash-lite` (Gemini API) | 2.76 s |

---

## How it works

![Architecture](docs/architecture.png)

The short version: `plan_turn()` runs safety → mode → scoring → progression →
item choice → directive → **fallback reply**, and only then, if the mode warrants
it, is the model asked to speak. `finalize_turn()` filters whatever comes back and
commits the turn.

- Full design: [`docs/system-design.md`](docs/system-design.md)
- Diagram source: [`docs/architecture.mmd`](docs/architecture.mmd)
- Two-minute demo script: [`docs/demo-script.md`](docs/demo-script.md)

A few decisions worth knowing:

**The session lives in the browser.** The server keeps no per-child state: the
session is an opaque JSON blob in `localStorage`, sent with each turn and clamped
field-by-field on the way in. So a refresh, a redeploy or a load-balancer hop
resumes the same lesson, the service scales to zero, and there is nothing to leak
because nothing is persisted server-side.

**The model is given `answered` and `target` as separate fields.** Early testing
produced *"Yes! dog!"* right after the child said *"cat"*. Splitting "the word you
just said" from "the word I am asking for" fixed it, and the deterministic
fallbacks are built from the same two fields — so the model and the fallback can
never disagree about which word was just said.

**Support turns never reach the model.** The comfort line is a hand-written
constant, and a worrying disclosure always names a trusted adult. Faster, and
safer.

---

## Deploy

One container, one process. Any host that runs a container works.

```bash
docker build -t brimsel-kids-chatbot .
docker run -p 8080:8080 --env-file .env brimsel-kids-chatbot
```

Cloud Run:

```bash
gcloud run deploy brimsel-kids-chatbot \
  --source . --region us-central1 --allow-unauthenticated \
  --min-instances 1 --memory 512Mi \
  --env-vars-file env.yaml      # never commit this file
```

`min-instances=1` removes cold starts from the measured numbers. The service is
stateless, so it can be scaled down or replaced at any time without losing a
child's lesson.

---

## Project layout

```
app/
  main.py          FastAPI routes, request models, metrics wiring
  engine.py        plan_turn / finalize_turn / advance — the whole turn
  modes.py         Support > Engagement > Conversation > Learning
  progression.py   mastery boxes, promote / ease / demote, item choice
  text.py          normalisation, answer scoring, input and output safety
  fallbacks.py     every deterministic child-safe reply
  prompts.py       system prompt, directive, user prompt
  llm.py           provider adapters and the latency-bounded chain
  state.py         session shape and tamper-resistant normalisation
  curriculum.py    loads and validates the versioned curriculum
  metrics.py       in-process latency recorder
data/curriculum.json   versioned curriculum — content, not code
static/                index.html, styles.css, app.js — no build step
tests/                 225 offline tests
scripts/smoke.py       public-URL smoke test + client-side latency
scripts/bench_models.py   model latency benchmark
scripts/progression_check.py  drives a live deployment L1 -> L2 -> L3
docs/                  system design, architecture diagram, UI shot, demo script
Dockerfile             one image, one process
```

---

## Security and privacy

- **No API key ever reaches the browser.** The front end has no key, no provider
  URL and no model name; it only talks to `/api/chat`. Health output reports
  which provider is configured, never the credential.
- **No server-side persistence.** Nothing a child says is written to disk or to a
  database. The session blob lives in the child's own browser, which also means
  it is device-local rather than private-within-the-browser storage.
- **No accounts, no personal data.** Emails, phone numbers and URLs in a child's
  message are replaced with a placeholder before the turn continues.
- **Session blobs are untrusted.** They round-trip through the browser, so
  `normalize_state()` rebuilds every field and clamps it. Editing `localStorage`
  to claim `"level": 99` yields level 3.
- **Unsafe input is never forwarded to the model,** and is stored as
  `[hidden for safety]` so the string is not preserved either.
- `.env` is git-ignored, `.dockerignore` excludes it from images, and
  `.env.example` contains placeholders only.

---

## Accessibility and child-safety choices

- **Works without a microphone.** Text chat and tappable suggestion buttons are
  the primary path. Browser speech input is a progressive enhancement and is
  never required.
- **Big targets, big text.** Buttons and text are sized for small hands and
  early readers; the layout is responsive down to phone width.
- **One question per turn.** No dense paragraphs, no lists, no timers, no
  losing streaks that a child can see.
- **Mood check-in with emoji faces**, which also feeds mode selection — a sad
  face is comforted, not quizzed.
- **Effort is praised, ability is not.** The word "wrong" never appears. A miss
  is *"Almost! Let's try together."*
- **A worrying disclosure is never diagnosed.** The reply offers comfort and
  points to a trusted adult.
- **Read-aloud.** Where the browser supports it, Buddy's replies can be spoken
  for pre-readers.

---

## Live URL

**https://brimsel-kids-chatbot-153163979718.us-central1.run.app**

Deployed on Google Cloud Run (`us-central1`, `min-instances=1`), HTTPS, publicly
reachable. Health check: [`/api/health`](https://brimsel-kids-chatbot-153163979718.us-central1.run.app/api/health)
reports the active provider, the model and the curriculum version — never a
credential.

- Source: <https://github.com/asifdotpy/brimsel-kids-chatbot> (private)
- Architecture image: [`docs/architecture.png`](docs/architecture.png)
- Design document: [`docs/system-design.md`](docs/system-design.md)
- Two-minute demo script: [`docs/demo-script.md`](docs/demo-script.md)
