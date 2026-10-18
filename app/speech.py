"""Natural speech: SSML assembled in code, spoken by a neural voice.

Why the markup is built here rather than by the model
----------------------------------------------------
The model is asked for *intent* - how lively the line should be, which word
matters - and this module turns that intent into the actual document. That means
a malformed or hostile tag can never reach the synthesiser, and it costs no
second model call.

What the endpoint actually accepts (measured, not assumed)
---------------------------------------------------------
`scripts/speech_probe.py` drove the voice several ways and read the duration back
from the MP3 size (the endpoint returns `audio-24khz-48kbitrate-mono-mp3`, so
48000/8 = 6000 bytes per second):

    <prosody rate='-50%'>          honoured   1.87s -> 2.90s
    <prosody rate='+100%'>         honoured   1.87s -> 0.77s
    <break time='700ms'/>          REJECTED   NoAudioReceived
    <emphasis level='strong'>      REJECTED   NoAudioReceived
    nested <prosody>               REJECTED   NoAudioReceived
    a tag bare in <voice>          REJECTED   NoAudioReceived

So exactly one document shape is accepted:

    <speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis'
           xml:lang='en-US'>
      <voice name='...'>
        <prosody pitch='+0Hz' rate='+0%' volume='+0%'>TEXT</prosody>
      </voice>
    </speak>

Why one utterance, not one per sentence
---------------------------------------
With `<break>` and `<emphasis>` unavailable, the obvious way to get a pause or a
slower final question is to synthesise each sentence separately and concatenate
the MP3 frames. That was built and then removed, because measurement killed it:
wall-clock grows with the sentence count (3 sentences took 1.86x the time of 2),
so the connections are serialised rather than concurrent. It bought a slightly
different rate per sentence and cost latency in direct proportion to reply
length - a bad trade, since the naturalness the user actually noticed comes from
the neural voice and the voice already pauses at punctuation on its own.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import OrderedDict
from dataclasses import dataclass

DEFAULT_VOICE = "en-US-AvaMultilingualNeural"

MAX_SPEECH_CHARS = 400
CACHE_SIZE = 512
SYNTH_TIMEOUT_S = 8.0
# A speculative warm-up gets less than a real request. If it cannot finish inside
# this window it is abandoned, leaving room for the child's own attempt to run.
PREWARM_TIMEOUT_S = 5.0

# Rate is clamped well inside the range the voice handles gracefully. A child is
# listening to this, so extremes are unhelpful even where they work.
MIN_RATE = -40
MAX_RATE = 40

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\u2190-\u21FF\uFE0F]+"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class SpeechStyle:
    """Prosody for one utterance. Strings are the form the endpoint expects."""

    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    label: str = "normal"

    def as_dict(self) -> dict[str, str]:
        return {"rate": self.rate, "pitch": self.pitch, "volume": self.volume, "label": self.label}


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    voice: str
    style: str
    chars: int
    cached: bool
    ms: int


class SpeechUnavailable(RuntimeError):
    """Raised when no audio could be produced; the client falls back."""


def _clamp_rate(value: int) -> int:
    return max(MIN_RATE, min(MAX_RATE, int(value)))


def style_for(
    mode: str | None,
    correct: bool | None = None,
    energy: str | None = None,
    speed: float = 1.0,
) -> SpeechStyle:
    """Turn mode, answer quality and model intent into one prosody setting.

    The mapping is deliberately small and legible: comfort is slowest, praise is
    a little brighter, and a correction is slowed down rather than sharpened.
    """
    rate = {
        "Support": -18,
        "Engagement": 6,
        "Conversation": 0,
        "Learning": -4,
    }.get(mode or "Conversation", 0)

    pitch = 0
    if (mode or "") == "Support":
        pitch = -3
        rate -= 4

    if correct is True:
        rate += 5
        pitch += 2
    elif correct is False:
        # "Almost, let's try together" - slower, never sharper.
        rate -= 10

    energy_key = (energy or "").strip().lower()
    if energy_key in ("calm", "gentle", "soft", "soothing"):
        rate -= 10
        pitch -= 2
    elif energy_key in ("warm", "kind", "friendly"):
        rate -= 3
    elif energy_key in ("bright", "lively", "excited", "cheerful", "happy"):
        rate += 6
        pitch += 3
    elif energy_key in ("slow", "careful", "clear"):
        rate -= 12

    try:
        factor = max(0.6, min(1.3, float(speed)))
    except (TypeError, ValueError):
        factor = 1.0
    # The slider nudges the whole scale rather than rescaling the mode's offset.
    # Rescaling would invert the control: the comfort rate is negative, so
    # multiplying it by 1.3 would make a faster setting *slower*.
    rate += round((factor - 1.0) * 50)
    rate = _clamp_rate(rate)
    pitch = max(-8, min(8, pitch))

    label = "slow" if rate <= -12 else "bright" if rate >= 8 else "normal"
    return SpeechStyle(rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz", volume="+0%", label=label)


def clean_for_speech(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    """Prepare text for the synthesiser.

    Emoji are removed rather than spoken ("grinning face" mid-sentence breaks the
    illusion), control characters are stripped, and the length is capped so a
    pathological string cannot run up synthesis cost.
    """
    if not text:
        return ""
    cleaned = _EMOJI.sub(" ", str(text))
    cleaned = _CONTROL.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit].strip()


def wire_voice(voice: str) -> str:
    """Expand a short voice name the way edge-tts does before sending.

    The synthesiser is given the long form
    ``Microsoft Server Speech Text to Speech Voice (en-US, AvaMultilingualNeural)``
    rather than ``en-US-AvaMultilingualNeural``. Mirrored here so `build_ssml`
    produces the document that really goes on the wire; a test pins the two
    together.
    """
    match = re.match(r"^([a-z]{2,})-([A-Z]{2,})-(.+Neural)$", voice)
    if match is None:
        return voice
    lang, region, name = match.group(1), match.group(2), match.group(3)
    if "-" in name:
        region = f"{region}-{name[:name.find('-')]}"
        name = name[name.find("-") + 1 :]
    return f"Microsoft Server Speech Text to Speech Voice ({lang}-{region}, {name})"


def build_ssml(text: str, style: SpeechStyle, voice: str = DEFAULT_VOICE) -> str:
    """The one document shape this endpoint accepts.

    Kept in step with `edge_tts.communicate.mkssml` by a test, so the wire format
    is verified rather than assumed. Note the caller must pass text that is
    already clean; escaping happens here.
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
        f"<voice name='{wire_voice(voice)}'>"
        f"<prosody pitch='{style.pitch}' rate='{style.rate}' volume='{style.volume}'>"
        f"{escaped}"
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def _edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except Exception:  # noqa: BLE001 - absence is a normal configuration
        return False
    return True


class SpeechService:
    """Synthesises short replies with a cache.

    The cache matters more than it looks. Synthesis costs roughly two seconds per
    call from a data centre, so the difference between a miss and a hit is the
    difference between a child waiting and a child hearing Buddy immediately
    (a hit is served with 0 ms of server time). Buddy repeats a small vocabulary
    of phrases, so the cache fills quickly in practice.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        enabled: bool = True,
        cache_size: int = CACHE_SIZE,
        timeout_s: float = SYNTH_TIMEOUT_S,
    ) -> None:
        self.voice = voice
        self.enabled = bool(enabled) and _edge_tts_available()
        self.cache_size = cache_size
        self.timeout_s = float(timeout_s)
        self._cache: "OrderedDict[tuple[str, str, str], bytes]" = OrderedDict()
        self._inflight: dict[tuple[str, str, str], "asyncio.Task[bytes]"] = {}
        self.provider = "edge" if self.enabled else "none"

    # ------------------------------------------------------------------ cache

    def _cache_get(self, key: tuple[str, str, str]) -> bytes | None:
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
        return hit

    def _cache_put(self, key: tuple[str, str, str], value: bytes) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def cache_info(self) -> dict[str, int]:
        return {"entries": len(self._cache), "size": self.cache_size, "inflight": len(self._inflight)}

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------ synthesis

    async def _synthesise(self, text: str, style: SpeechStyle) -> bytes:
        """One utterance, in one request. See the module docstring for why."""
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            self.voice,
            rate=style.rate,
            pitch=style.pitch,
            volume=style.volume,
        )
        buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buffer.extend(chunk["data"])
        if not buffer:
            raise SpeechUnavailable("the voice returned no audio")
        return bytes(buffer)

    # ------------------------------------------------------- pre-warm & coalesce

    def _key(self, cleaned: str, style: SpeechStyle) -> tuple[str, str, str]:
        return (self.voice, f"{style.rate}|{style.pitch}|{style.volume}", cleaned)

    def _spawn(
        self,
        key: tuple[str, str, str],
        cleaned: str,
        style: SpeechStyle,
        budget: float,
    ) -> tuple["asyncio.Task[bytes]", bool]:
        """Start, or join, the synthesis for one cache key.

        Returns ``(task, ours)``. Pre-warming is why this exists: the route that
        produced a reply starts composing its audio while the browser is still
        painting the text, so the browser's own request usually lands on a
        synthesis that is already running. Awaiting that shared task - instead of
        starting a second - avoids paying the same ~3 seconds twice.

        ``budget`` bounds the whole synthesis, retry included. A speculative
        warm-up asks for less than a real request so that, if it is abandoned,
        the child's own attempt still has time to run.
        """
        task = self._inflight.get(key)
        if task is not None:
            return task, False

        async def run() -> bytes:
            data = await self._synthesise_bounded(cleaned, style, budget)
            self._cache_put(key, data)
            return data

        task = asyncio.ensure_future(run())
        self._inflight[key] = task

        def _finished(done: "asyncio.Task[bytes]", cache_key: tuple[str, str, str] = key) -> None:
            self._inflight.pop(cache_key, None)
            if not done.cancelled():
                # Mark a failure as retrieved. Awaiters still see it; a detached
                # pre-warm must not log "exception was never retrieved".
                done.exception()

        task.add_done_callback(_finished)
        return task, True

    async def _synthesise_bounded(self, cleaned: str, style: SpeechStyle, budget: float) -> bytes:
        """One synthesis, retried once if the upstream refuses outright.

        This endpoint answers ``NoAudioReceived`` intermittently, so a single
        retry inside the same budget turns a flaky miss into a spoken reply. A
        genuine outage still gives up rather than hanging, because the whole
        attempt - retry included - is bounded by ``budget``.
        """

        async def attempt() -> bytes:
            try:
                return await self._synthesise(cleaned, style)
            except SpeechUnavailable:
                return await self._synthesise(cleaned, style)

        return await asyncio.wait_for(attempt(), timeout=budget)

    async def prewarm(
        self,
        text: str,
        mode: str | None = None,
        correct: bool | None = None,
        energy: str | None = None,
        speed: float = 1.0,
    ) -> bool:
        """Compose `text` before anyone asks for it. Best effort; never raises.

        A route already knows the exact sentence it just sent, so it can pay the
        synthesis cost while the browser renders that sentence: on Cloud Run a
        cold phrase costs roughly two to three seconds server-side, and a cache
        hit costs nothing. Anything already cached is left alone, and a failure
        is silent because speech is an enhancement, never a requirement.
        """
        if not self.enabled:
            return False
        cleaned = clean_for_speech(text)
        if not cleaned:
            return False

        style = style_for(mode, correct, energy, speed)
        key = self._key(cleaned, style)
        if self._cache_get(key) is not None:
            return True

        task, _ = self._spawn(key, cleaned, style, min(self.timeout_s, PREWARM_TIMEOUT_S))
        try:
            await asyncio.shield(task)
        except Exception:
            return False
        return True

    async def audio(
        self,
        text: str,
        mode: str | None = None,
        correct: bool | None = None,
        energy: str | None = None,
        speed: float = 1.0,
        timeout_s: float | None = None,
    ) -> SpeechAudio:
        if not self.enabled:
            raise SpeechUnavailable("speech is disabled")

        budget = self.timeout_s if timeout_s is None else float(timeout_s)

        cleaned = clean_for_speech(text)
        if not cleaned:
            raise SpeechUnavailable("nothing to say")

        style = style_for(mode, correct, energy, speed)
        key = self._key(cleaned, style)

        started = asyncio.get_event_loop().time()
        cached = self._cache_get(key)
        if cached is not None:
            return SpeechAudio(
                data=cached,
                voice=self.voice,
                style=style.label,
                chars=len(cleaned),
                cached=True,
                ms=0,
            )

        task, ours = self._spawn(key, cleaned, style, budget)
        try:
            data = await asyncio.wait_for(asyncio.shield(task), timeout=budget)
        except (asyncio.TimeoutError, SpeechUnavailable) as exc:
            if ours:
                if isinstance(exc, asyncio.TimeoutError):
                    raise SpeechUnavailable("speech timed out") from exc
                raise
            # We joined a speculative warm-up and it did not deliver. A warm-up is
            # allowed to fail; the child's request is not. Synthesise for real
            # with whatever is left of this request's own budget.
            remaining = budget - (asyncio.get_event_loop().time() - started)
            if remaining <= 0.5:
                raise SpeechUnavailable("speech timed out") from exc
            try:
                data = await self._synthesise_bounded(cleaned, style, remaining)
            except asyncio.TimeoutError as late:
                raise SpeechUnavailable("speech timed out") from late

        self._cache_put(key, data)
        elapsed = int((asyncio.get_event_loop().time() - started) * 1000)
        return SpeechAudio(
            data=data,
            voice=self.voice,
            style=style.label,
            chars=len(cleaned),
            cached=False,
            ms=elapsed,
        )

    def describe(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "voice": self.voice,
            "cache": self.cache_info(),
        }


def speech_from_env(env: dict[str, str] | None = None) -> SpeechService:
    env = env if env is not None else os.environ
    provider = (env.get("SPEECH_PROVIDER") or "edge").strip().lower()
    voice = (env.get("SPEECH_VOICE") or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    try:
        timeout_s = float(env.get("SPEECH_TIMEOUT_MS") or 0) / 1000 or SYNTH_TIMEOUT_S
    except (TypeError, ValueError):
        timeout_s = SYNTH_TIMEOUT_S
    return SpeechService(
        voice=voice,
        enabled=provider not in ("none", "off", "disabled", ""),
        timeout_s=timeout_s,
    )
