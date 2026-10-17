/* Buddy - client.
 *
 * Responsibilities kept deliberately small:
 *   - hold the session blob in localStorage so a refresh never resets the lesson
 *   - render bubbles, the mood check-in and the quick-say buttons
 *   - measure how long the child took to reply (a boredom signal for the engine)
 *   - read replies aloud with the browser's built-in speech synthesis
 *
 * No API key and no model name ever reach this file. All pedagogy happens on
 * the server.
 */

(() => {
  "use strict";

  const STORAGE_KEY = "buddy.session.v1";
  const SOUND_KEY = "buddy.sound.v1";
  const REQUEST_TIMEOUT_MS = 20000;

  const el = {
    chat: document.getElementById("chat"),
    typing: document.getElementById("typing"),
    composer: document.getElementById("composer"),
    text: document.getElementById("text"),
    send: document.getElementById("send"),
    levelBadge: document.getElementById("levelBadge"),
    modeBadge: document.getElementById("modeBadge"),
    soundToggle: document.getElementById("soundToggle"),
    debugToggle: document.getElementById("debugToggle"),
    debugPanel: document.getElementById("debugPanel"),
    debugOut: document.getElementById("debugOut"),
    refreshMetrics: document.getElementById("refreshMetrics"),
    newSession: document.getElementById("newSession"),
    statusNote: document.getElementById("statusNote"),
    buildInfo: document.getElementById("buildInfo"),
    moodRow: document.querySelector(".mood-row"),
  };

  let session = null;
  let lastBotAt = 0;      // when Buddy's last line appeared, for reply_ms
  let busy = false;
  let showTags = false;
  let soundOn = localStorage.getItem(SOUND_KEY) !== "off";

  // ------------------------------------------------------------------ helpers

  const nowMs = () => Date.now();

  function saveSession() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch (err) {
      /* private mode or quota: the session simply will not survive a refresh */
    }
  }

  function loadSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function clearSession() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (err) { /* ignore */ }
  }

  async function postJSON(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`server returned ${response.status}`);
      }
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`server returned ${response.status}`);
    return await response.json();
  }

  // -------------------------------------------------------------- rendering

  function scrollToEnd() {
    el.chat.scrollTop = el.chat.scrollHeight;
  }

  function addBubble(role, text, tag) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${role}`;
    bubble.appendChild(document.createTextNode(text));
    if (tag) {
      bubble.dataset.tag = tag;
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = showTags ? tag : "";
      bubble.appendChild(span);
    }
    el.chat.appendChild(bubble);
    scrollToEnd();
    return bubble;
  }

  function addErrorBubble(message, retry) {
    const bubble = document.createElement("div");
    bubble.className = "bubble error";
    bubble.appendChild(document.createTextNode(message));
    if (typeof retry === "function") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn-again";
      button.textContent = "Try again";
      button.addEventListener("click", () => {
        bubble.remove();
        retry();
      });
      bubble.appendChild(button);
    }
    el.chat.appendChild(bubble);
    scrollToEnd();
  }

  function refreshTags() {
    document.querySelectorAll(".bubble[data-tag] .tag").forEach((span) => {
      span.textContent = showTags ? span.parentElement.dataset.tag : "";
    });
  }

  function updateBadges(level, mode) {
    if (typeof level === "number") el.levelBadge.textContent = `L${level}`;
    if (mode) el.modeBadge.textContent = String(mode).toLowerCase();
  }

  function setTyping(on) {
    el.typing.hidden = !on;
    el.send.disabled = on;
    if (on) scrollToEnd();
  }

  // ------------------------------------------------------------------ speech

  function speak(text) {
    if (!soundOn || !("speechSynthesis" in window)) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, ""));
      utterance.lang = "en-US";
      utterance.rate = 0.9;
      utterance.pitch = 1.15;
      utterance.volume = 1;
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      /* speech is a bonus, never a failure */
    }
  }

  function stopSpeaking() {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }

  // -------------------------------------------------------------------- turn

  function takeReplyMs() {
    if (!lastBotAt) return null;
    const delta = nowMs() - lastBotAt;
    lastBotAt = 0;
    if (delta < 0 || delta > 600000) return null;
    return Math.round(delta);
  }

  async function send(rawText, mood) {
    const text = (rawText || "").trim();
    if (!text || busy) return;

    busy = true;
    addBubble("child", text);
    setTyping(true);
    el.statusNote.textContent = "";

    const replyMs = takeReplyMs();

    try {
      const data = await postJSON("/api/chat", {
        session,
        message: text,
        mood: mood || null,
        reply_ms: replyMs,
      });

      session = data.session;
      saveSession();

      const debug = data.debug || {};
      const tag = `L${session.level} | ${debug.mode || "?"} | ${data.server_ms} ms`
        + (debug.fallback_used ? " | fallback" : "");
      addBubble("buddy", data.reply, tag);
      lastBotAt = nowMs();

      updateBadges(session.level, debug.mode);
      renderDebug(debug, data.server_ms);
      speak(data.reply);
    } catch (err) {
      const offline = err && err.name === "AbortError";
      el.statusNote.textContent = "Buddy could not answer. " + (offline ? "The server is slow." : "Check the connection.");
      addErrorBubble("Oops, Buddy could not hear you.", () => send(text, mood));
    } finally {
      busy = false;
      setTyping(false);
      el.text.focus();
    }
  }

  function renderDebug(debug, serverMs) {
    if (!debug) return;
    const view = { server_ms: serverMs, ...debug };
    if (view.directive) view.directive = JSON.stringify(view.directive);
    el.debugOut.textContent = JSON.stringify(view, null, 2);
  }

  // ------------------------------------------------------------------ session

  async function startNewSession() {
    stopSpeaking();
    clearSession();
    el.chat.innerHTML = "";
    lastBotAt = 0;
    try {
      const data = await postJSON("/api/session", {});
      session = data.session;
      saveSession();
      addBubble("buddy", data.reply);
      lastBotAt = nowMs();
      updateBadges(session.level, "Conversation");
      renderDebug(data.debug, 0);
      el.statusNote.textContent = "";
    } catch (err) {
      addErrorBubble("Buddy is waking up. Please refresh the page.", null);
    }
  }

  async function resume() {
    session = loadSession();
    if (!session || !session.session_id) {
      await startNewSession();
      return;
    }
    // Restore the visible conversation from the stored session.
    const turns = Array.isArray(session.turns) ? session.turns.slice(-12) : [];
    if (!turns.length) {
      await startNewSession();
      return;
    }
    for (const turn of turns) {
      if (turn.role === "child") {
        addBubble("child", turn.text === "[hidden for safety]" ? "(hidden)" : turn.text);
      } else {
        addBubble("buddy", turn.text);
      }
    }
    updateBadges(session.level, session.mode);
    lastBotAt = nowMs();
  }

  async function loadHealth() {
    try {
      const info = await getJSON("/api/health");
      const llm = info.llm || {};
      const model = llm.configured ? `${llm.provider}/${llm.model}` : "fallback replies only";
      el.buildInfo.textContent = `curriculum ${info.curriculum_version} | model: ${model}`;
    } catch (err) {
      el.buildInfo.textContent = "";
    }
  }

  // -------------------------------------------------------------------- events

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el.text.value;
    el.text.value = "";
    send(text, null);
  });

  el.moodRow.addEventListener("click", (event) => {
    const button = event.target.closest(".mood");
    if (!button) return;
    const mood = button.dataset.mood;
    el.moodRow.querySelectorAll(".mood").forEach((b) => b.classList.toggle("chosen", b === button));
    send(`I feel ${mood}`, mood);
  });

  document.querySelector(".quick-row").addEventListener("click", (event) => {
    const button = event.target.closest(".quick");
    if (!button) return;
    send(button.dataset.say, null);
  });

  el.soundToggle.addEventListener("click", () => {
    soundOn = !soundOn;
    el.soundToggle.setAttribute("aria-pressed", String(soundOn));
    el.soundToggle.textContent = soundOn ? "🔊" : "🔇";
    localStorage.setItem(SOUND_KEY, soundOn ? "on" : "off");
    if (!soundOn) stopSpeaking();
    else speak("Sound is on!");
  });

  el.debugToggle.addEventListener("click", () => {
    const open = el.debugPanel.hidden;
    el.debugPanel.hidden = !open;
    // Wide screens only reserve a column for the panel while it is open.
    document.querySelector(".layout").classList.toggle("is-debug", open);
    showTags = open;
    el.debugToggle.setAttribute("aria-expanded", String(open));
    refreshTags();
  });

  el.refreshMetrics.addEventListener("click", async () => {
    try {
      const data = await getJSON("/api/metrics");
      el.debugOut.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      el.debugOut.textContent = "Could not load metrics.";
    }
  });

  el.newSession.addEventListener("click", startNewSession);

  // --------------------------------------------------------------------- init

  el.soundToggle.textContent = soundOn ? "🔊" : "🔇";
  el.soundToggle.setAttribute("aria-pressed", String(soundOn));
  loadHealth();
  resume();
  el.text.focus();

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") stopSpeaking();
  });
})();
