/* Buddy - client.
 *
 * Responsibilities kept deliberately small:
 *   - hold the session blob in localStorage so a refresh never resets the lesson
 *   - render bubbles, the mood check-in and the quick-say buttons
 *   - measure how long the child took to reply (a boredom signal for the engine)
 *   - ask the server for spoken audio, and fall back to the browser voice
 *   - expose a Settings panel and a level control for the reviewer
 *
 * No API key and no model name ever reach this file. All pedagogy happens on
 * the server.
 */

(() => {
  "use strict";

  const STORAGE_KEY = "buddy.session.v1";
  const SOUND_KEY = "buddy.sound.v1";
  const MOOD_HIDDEN_KEY = "buddy.moodhidden.v1";
  const VOICE_KEY = "buddy.voice.v1";
  const SPEED_KEY = "buddy.speed.v1";
  const REQUEST_TIMEOUT_MS = 20000;
  const SPEECH_TIMEOUT_MS = 12000;

  const el = {
    chat: document.getElementById("chat"),
    typing: document.getElementById("typing"),
    composer: document.getElementById("composer"),
    text: document.getElementById("text"),
    send: document.getElementById("send"),
    modeBadge: document.getElementById("modeBadge"),
    soundToggle: document.getElementById("soundToggle"),
    settingsToggle: document.getElementById("settingsToggle"),
    settingsPanel: document.getElementById("settingsPanel"),
    settingsClose: document.getElementById("settingsClose"),
    levelSeg: document.getElementById("levelSeg"),
    levelSegPanel: document.getElementById("levelSegPanel"),
    moodCard: document.getElementById("moodCard"),
    moodRow: document.getElementById("moodRow"),
    moodToggle: document.getElementById("moodToggle"),
    quickRow: document.getElementById("quickRow"),
    statusNote: document.getElementById("statusNote"),
    buildInfo: document.getElementById("buildInfo"),
    voiceSelect: document.getElementById("voiceSelect"),
    voiceNote: document.getElementById("voiceNote"),
    speedRange: document.getElementById("speedRange"),
    testVoice: document.getElementById("testVoice"),
    newSession: document.getElementById("newSession"),
    copyDiagnostics: document.getElementById("copyDiagnostics"),
    debugOut: document.getElementById("debugOut"),
    kvLevel: document.getElementById("kvLevel"),
    kvMode: document.getElementById("kvMode"),
    kvAttempts: document.getElementById("kvAttempts"),
    kvWindow: document.getElementById("kvWindow"),
    kvItems: document.getElementById("kvItems"),
    kvMood: document.getElementById("kvMood"),
    kvConn: document.getElementById("kvConn"),
    kvModel: document.getElementById("kvModel"),
    kvSpeech: document.getElementById("kvSpeech"),
    kvCurriculum: document.getElementById("kvCurriculum"),
    kvLatency: document.getElementById("kvLatency"),
    kvSource: document.getElementById("kvSource"),
  };

  let session = null;
  let lastBotAt = 0;          // when Buddy's last line appeared, for reply_ms
  let busy = false;
  let soundOn = localStorage.getItem(SOUND_KEY) !== "off";
  let serverVoice = true;     // false once we learn the server cannot speak
  let health = null;
  let lastDebug = null;
  let lastServerMs = 0;
  let audio = new Audio();
  let browserVoices = [];
  // Speech is fetched asynchronously and can take seconds, so a slow request can
  // resolve after a newer turn has already been rendered. Bumping a generation
  // per utterance lets a superseded request drop its audio instead of playing
  // the previous reply over the current one.
  let speechGeneration = 0;

  // ------------------------------------------------------------------ helpers

  const nowMs = () => Date.now();

  function saveSession() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(session)); }
    catch (err) { /* private mode or quota: the session will not survive refresh */ }
  }

  function loadSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (err) { return null; }
  }

  function clearSession() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (err) { /* ignore */ }
  }

  async function postJSON(url, body, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`server returned ${response.status}`);
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
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = tag;
      bubble.appendChild(span);
      bubble.dataset.tag = tag;
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
      button.addEventListener("click", () => { bubble.remove(); retry(); });
      bubble.appendChild(button);
    }
    el.chat.appendChild(bubble);
    scrollToEnd();
  }

  function setTyping(on) {
    el.typing.hidden = !on;
    el.send.disabled = on;
    if (on) scrollToEnd();
  }

  function setLevelControls(level) {
    document.querySelectorAll("#levelSeg .seg-btn, #levelSegPanel .seg-btn").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(Number(btn.dataset.level) === Number(level)));
    });
    el.kvLevel.textContent = `L${level}`;
  }

  function updateBadges(level, mode) {
    if (typeof level === "number") setLevelControls(level);
    if (mode) {
      const shown = String(mode).toLowerCase();
      el.modeBadge.textContent = shown;
      el.kvMode.textContent = shown;
    }
  }

  /* The Settings panel is a normal panel with formatted rows; the raw engine
     payload stays available but is not what a person sees first. */
  function renderTurn(debug, serverMs) {
    if (!debug) return;
    lastDebug = debug;
    lastServerMs = serverMs || 0;
    el.debugOut.textContent = JSON.stringify({ server_ms: serverMs, ...debug }, null, 2);

    el.kvLatency.textContent = serverMs ? `${serverMs} ms` : "-";
    el.kvSource.textContent = debug.fallback_used ? "warm fallback" : "model";
    el.kvSource.className = debug.fallback_used ? "warn" : "good";
  }

  function renderSessionRows() {
    if (!session) return;
    setLevelControls(session.level);
    el.kvMode.textContent = String(session.mode || "chat").toLowerCase();
    el.kvAttempts.textContent = String(session.attempts_at_level ?? 0);
    const results = Array.isArray(session.recent_results) ? session.recent_results : [];
    el.kvWindow.textContent = results.length
      ? results.map((r) => (r ? "✓" : "✗")).join(" ")
      : "-";
    el.kvItems.textContent = String(Object.keys(session.mastery || {}).length);
    const moods = Array.isArray(session.moods) ? session.moods : [];
    el.kvMood.textContent = moods.length ? moods[moods.length - 1] : "-";
  }

  // ------------------------------------------------------------------ speech

  function pickBrowserVoice() {
    if (!("speechSynthesis" in window)) return null;
    browserVoices = window.speechSynthesis.getVoices() || [];
    if (!browserVoices.length) return null;

    const prefer = [
      /natural/i, /neural/i, /premium/i, /enhanced/i,
      /google us english/i, /microsoft (aria|jenny|guy|libby|sonia)/i,
      /samantha/i, /ava/i, /allison/i, /serena/i,
    ];
    const english = browserVoices.filter((v) => /^en(-|_)?/i.test(v.lang));
    for (const pattern of prefer) {
      const hit = english.find((v) => pattern.test(v.name));
      if (hit) return hit;
    }
    return english[0] || browserVoices[0] || null;
  }

  function browserSpeak(text) {
    if (!soundOn || !("speechSynthesis" in window)) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(
        text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
      );
      const chosen = pickBrowserVoice();
      if (chosen) utterance.voice = chosen;
      utterance.lang = (chosen && chosen.lang) || "en-US";
      utterance.rate = Number(localStorage.getItem(SPEED_KEY) || "1") * 0.95;
      utterance.pitch = 1.05;
      utterance.volume = 1;
      window.speechSynthesis.speak(utterance);
    } catch (err) { /* speech is a bonus, never a failure */ }
  }

  /* Ask the server for natural audio. The server rebuilds the SSML itself from
     the text plus a few enums, so nothing here can inject markup. */
  async function playServerVoice(text, hint, generation) {
    if (!soundOn || !serverVoice) return false;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SPEECH_TIMEOUT_MS);
    try {
      const response = await fetch("/api/speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          mode: (lastDebug && lastDebug.mode) || session?.mode || "Conversation",
          correct: lastDebug ? lastDebug.correct : null,
          target: (lastDebug && lastDebug.item_ask) || "",
          hint: hint || null,
          speed: Number(localStorage.getItem(SPEED_KEY) || "1"),
        }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`speech ${response.status}`);
      const blob = await response.blob();
      if (!blob.size) throw new Error("empty audio");
      // A newer turn has already spoken; this audio is stale.
      if (generation !== speechGeneration) return true;

      audio.pause();
      if (audio.src) URL.revokeObjectURL(audio.src);
      audio = new Audio(URL.createObjectURL(blob));
      audio.playbackRate = 1;
      try {
        await audio.play();
      } catch (err) {
        // Autoplay blocked before the first interaction: silent is fine, the
        // child has not touched the page yet.
        return true;
      }
      return true;
    } catch (err) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  async function speak(text, hint) {
    if (!soundOn || !text) return;
    const generation = ++speechGeneration;
    const spoke = await playServerVoice(text, hint, generation);
    if (generation !== speechGeneration) return;
    if (!spoke) {
      // The server voice is unavailable (offline deploy, endpoint change, or a
      // blocked autoplay). The browser voice is poorer, but silent is worse.
      serverVoice = false;
      refreshSpeechRow();
      browserSpeak(text);
    }
  }

  function stopSpeaking() {
    speechGeneration += 1;
    try { audio.pause(); } catch (err) { /* ignore */ }
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

  async function send(rawText, mood, opts) {
    const text = (rawText || "").trim();
    if (!text || busy) return;
    const quiet = opts && opts.quiet;

    busy = true;
    if (!quiet) addBubble("child", text);
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
      addBubble("buddy", data.reply);
      lastBotAt = nowMs();

      updateBadges(session.level, debug.mode);
      renderTurn(debug, data.server_ms);
      renderSessionRows();
      speak(data.reply, data.speech);
    } catch (err) {
      const offline = err && err.name === "AbortError";
      el.statusNote.textContent =
        "Buddy could not answer. " + (offline ? "The server is slow." : "Check the connection.");
      addErrorBubble("Oops, Buddy could not hear you.", () => send(text, mood));
    } finally {
      busy = false;
      setTyping(false);
      el.text.focus();
    }
  }

  // ------------------------------------------------------------------ session

  async function startNewSession() {
    stopSpeaking();
    clearSession();
    el.chat.innerHTML = "";
    lastBotAt = 0;
    lastDebug = null;
    try {
      const data = await postJSON("/api/session", {});
      session = data.session;
      saveSession();
      addBubble("buddy", data.reply);
      lastBotAt = nowMs();
      updateBadges(session.level, "Conversation");
      renderTurn(data.debug, 0);
      renderSessionRows();
      el.statusNote.textContent = "";
      speak(data.reply);
    } catch (err) {
      addErrorBubble("Buddy is waking up. Please refresh the page.", null);
    }
  }

  /* Jump to a level. The engine resets that level's attempts and accuracy window
     so the level starts clean, then we immediately ask its first item. */
  async function switchLevel(level) {
    if (busy || !session) return;
    if (Number(session.level) === Number(level)) return;
    busy = true;
    stopSpeaking();
    setTyping(true);
    try {
      const data = await postJSON("/api/level", { session, level: Number(level) });
      session = data.session;
      saveSession();
      addBubble("buddy", data.reply);
      lastBotAt = nowMs();
      updateBadges(session.level, data.debug ? data.debug.mode : "Conversation");
      renderTurn(data.debug, 0);
      renderSessionRows();
      speak(data.reply);
    } catch (err) {
      el.statusNote.textContent = "Could not change level.";
    } finally {
      busy = false;
      setTyping(false);
      // Deliberately no follow-up message here. The jump already asked a
      // level-appropriate question, so the child answers it directly. Sending
      // "let's learn" would be evaluated as an answer to that question and
      // scored as a miss.
    }
  }

  async function resume() {
    session = loadSession();
    if (!session || !session.session_id) { await startNewSession(); return; }
    const turns = Array.isArray(session.turns) ? session.turns.slice(-12) : [];
    if (!turns.length) { await startNewSession(); return; }
    for (const turn of turns) {
      if (turn.role === "child") {
        addBubble("child", turn.text === "[hidden for safety]" ? "(hidden)" : turn.text);
      } else {
        addBubble("buddy", turn.text);
      }
    }
    updateBadges(session.level, session.mode);
    renderSessionRows();
    lastBotAt = nowMs();
  }

  function refreshSpeechRow() {
    let label = "server voice";
    if (!serverVoice) label = "browser voice";
    if (!soundOn) label = "muted";
    el.kvSpeech.textContent = label;
  }

  async function loadHealth() {
    try {
      const info = await getJSON("/api/health");
      health = info;
      const llm = info.llm || {};
      const speech = info.speech || {};
      const model = llm.configured ? `${llm.provider} · ${llm.model}` : "fallback replies only";
      el.buildInfo.textContent = `curriculum ${info.curriculum_version} | ${model}`;
      el.kvConn.textContent = "connected";
      el.kvConn.className = "good";
      el.kvModel.textContent = model;
      el.kvSpeech.textContent = speech.enabled ? `${speech.provider} · ${speech.voice}` : "browser voice";
      el.kvCurriculum.textContent = String(info.curriculum_version);
      if (!speech.enabled) {
        serverVoice = false;
        el.kvSpeech.textContent = "browser voice";
      }
    } catch (err) {
      el.kvConn.textContent = "unreachable";
      el.kvConn.className = "warn";
      el.buildInfo.textContent = "";
    }
  }

  function diagnosticsText() {
    const rows = [
      ["Buddy session", session ? session.session_id : "none"],
      ["Level", session ? `L${session.level}` : "-"],
      ["Mode", session ? session.mode : "-"],
      ["Attempts at level", session ? session.attempts_at_level : "-"],
      ["Last 5", session && session.recent_results ? session.recent_results.join(",") : "-"],
      ["Last server ms", lastServerMs],
      ["Last source", lastDebug ? (lastDebug.fallback_used ? "fallback" : "model") : "-"],
      ["Connection", el.kvConn.textContent],
      ["Model", el.kvModel.textContent],
      ["Speech", el.kvSpeech.textContent],
      ["Curriculum", el.kvCurriculum.textContent],
    ];
    return rows.map(([k, v]) => `${k}: ${v}`).join("\n");
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

  el.quickRow.addEventListener("click", (event) => {
    const button = event.target.closest(".quick");
    if (!button) return;
    send(button.dataset.say, null);
  });

  el.soundToggle.addEventListener("click", () => {
    soundOn = !soundOn;
    el.soundToggle.setAttribute("aria-pressed", String(soundOn));
    el.soundToggle.textContent = soundOn ? "🔊" : "🔇";
    localStorage.setItem(SOUND_KEY, soundOn ? "on" : "off");
    refreshSpeechRow();
    if (!soundOn) stopSpeaking();
    else speak("Sound is on!");
  });

  el.levelSeg.addEventListener("click", (event) => {
    const button = event.target.closest(".seg-btn");
    if (button) switchLevel(button.dataset.level);
  });

  el.levelSegPanel.addEventListener("click", (event) => {
    const button = event.target.closest(".seg-btn");
    if (button) switchLevel(button.dataset.level);
  });

  el.moodToggle.addEventListener("click", () => {
    const hidden = !el.moodRow.hidden;
    el.moodRow.hidden = hidden;
    el.moodToggle.textContent = hidden ? "show" : "hide";
    el.moodToggle.setAttribute("aria-expanded", String(!hidden));
    localStorage.setItem(MOOD_HIDDEN_KEY, hidden ? "1" : "0");
  });

  el.settingsToggle.addEventListener("click", () => {
    const open = el.settingsPanel.hidden;
    el.settingsPanel.hidden = !open;
    document.querySelector(".layout").classList.toggle("is-open", open);
    el.settingsToggle.setAttribute("aria-expanded", String(open));
  });

  el.settingsClose.addEventListener("click", () => {
    el.settingsPanel.hidden = true;
    document.querySelector(".layout").classList.remove("is-open");
    el.settingsToggle.setAttribute("aria-expanded", "false");
  });

  el.newSession.addEventListener("click", startNewSession);

  el.speedRange.value = localStorage.getItem(SPEED_KEY) || "1";
  el.speedRange.addEventListener("input", () => {
    localStorage.setItem(SPEED_KEY, el.speedRange.value);
  });

  el.testVoice.addEventListener("click", () => {
    audio.pause();
    audio.currentTime = 0;
    el.kvSpeech.textContent = "trying...";
    speak("Hi! I am Buddy. Let's learn a new word together!", { energy: "warm" });
  });

  el.voiceSelect.addEventListener("change", () => {
    const name = el.voiceSelect.value;
    localStorage.setItem(VOICE_KEY, name);
    if (!name) return;
    const chosen = browserVoices.find((v) => v.name === name);
    if (chosen) browserSpeak("This is how I sound. Let's learn together!");
  });

  el.copyDiagnostics.addEventListener("click", async () => {
    const text = diagnosticsText();
    try {
      await navigator.clipboard.writeText(text);
      el.copyDiagnostics.textContent = "Copied";
    } catch (err) {
      el.debugOut.textContent = text;
      el.copyDiagnostics.textContent = "See raw payload";
    }
    setTimeout(() => { el.copyDiagnostics.textContent = "Copy diagnostics"; }, 1600);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      stopSpeaking();
      el.settingsPanel.hidden = true;
      document.querySelector(".layout").classList.remove("is-open");
      el.settingsToggle.setAttribute("aria-expanded", "false");
    }
  });

  // --------------------------------------------------------------------- init

  el.soundToggle.textContent = soundOn ? "🔊" : "🔇";
  el.soundToggle.setAttribute("aria-pressed", String(soundOn));
  refreshSpeechRow();

  if (localStorage.getItem(MOOD_HIDDEN_KEY) === "1") {
    el.moodRow.hidden = true;
    el.moodToggle.textContent = "show";
    el.moodToggle.setAttribute("aria-expanded", "false");
  }

  // Fill the fallback voice list, with a real option for the server voice.
  function refreshVoiceList() {
    const chosen = pickBrowserVoice();
    const current = localStorage.getItem(VOICE_KEY) || "";
    el.voiceSelect.innerHTML = "";
    const serverOption = document.createElement("option");
    serverOption.value = "";
    serverOption.textContent = "Buddy's voice (natural)";
    el.voiceSelect.appendChild(serverOption);
    for (const voice of browserVoices) {
      if (!/^en(-|_)?/i.test(voice.lang)) continue;
      const option = document.createElement("option");
      option.value = voice.name;
      option.textContent = `${voice.name} (${voice.lang})`;
      el.voiceSelect.appendChild(option);
    }
    el.voiceSelect.value = current;
    el.voiceNote.textContent = browserVoices.length
      ? `${browserVoices.length} browser voices found. Buddy's own voice is used by default.`
      : "No browser voices found; Buddy's own voice will be used.";
    return chosen;
  }

  if ("speechSynthesis" in window) {
    browserVoices = window.speechSynthesis.getVoices() || [];
    refreshVoiceList();
    window.speechSynthesis.addEventListener?.("voiceschanged", () => {
      browserVoices = window.speechSynthesis.getVoices() || [];
      refreshVoiceList();
    });
  }

  loadHealth().then(() => {
    renderSessionRows();
    resume();
  });
  el.text.focus();
})();
