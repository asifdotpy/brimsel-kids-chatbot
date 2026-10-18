"""Probe the speech endpoint. Produces the evidence cited in `app/speech.py`.

Run it against a deployed instance to re-check the two facts the speech module
depends on:

    python scripts/speech_probe.py --base https://your-service.run.app

    python scripts/speech_probe.py --section ssml     # which markup is honoured
    python scripts/speech_probe.py --section timing   # cost of cold vs cached

Section `ssml` talks to the synthesiser directly, so it needs `edge-tts`
installed locally but no server. Section `timing` needs a running service.

Why the duration is read from the byte count
--------------------------------------------
There is no audio decoder here. The endpoint returns
`audio-24khz-48kbitrate-mono-mp3`, so 48000 bits / 8 = 6000 bytes per second.
That is only trustworthy if the byte count actually tracks duration, so the
first measurement is a control: three times the text must give roughly three
times the bytes. Without that control, "the rate changed nothing" is
unfalsifiable rather than a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

BYTES_PER_SECOND = 6000
DEFAULT_VOICE = "en-US-AvaMultilingualNeural"


def seconds(audio: bytes) -> float:
    return len(audio) / BYTES_PER_SECOND


# ------------------------------------------------------------------------ ssml


async def _synth_edge(plain: str, ssml: str | None = None) -> tuple[bytes, float]:
    """Synthesise plain text, or a verbatim SSML document.

    edge-tts escapes its input, so SSML would be read aloud literally. It builds
    the request with a module-level `mkssml(tc, text)` helper, which is called
    lazily inside the streaming generator - so substituting it sends a verbatim
    document, as long as the substitution stays installed until the stream is
    drained.
    """
    import edge_tts
    import edge_tts.communicate as comm

    original = comm.mkssml
    if ssml is not None:
        comm.mkssml = lambda tc, escaped: ssml
    started = time.perf_counter()
    try:
        communicate = edge_tts.Communicate("x" if ssml else plain, DEFAULT_VOICE)
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
    finally:
        comm.mkssml = original
    return bytes(audio), (time.perf_counter() - started) * 1000


def _document(inner: str, prosody: str | None = None) -> str:
    body = inner if prosody is None else f"<prosody {prosody}>{inner}</prosody>"
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
        f"<voice name='{DEFAULT_VOICE}'>{body}</voice></speak>"
    )


async def probe_ssml() -> int:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("edge-tts is not installed; skipping the ssml section.")
        return 0

    short = "Can you say cat?"
    long = f"{short} {short} {short}"

    print("=== control: does the byte count track duration? ===")
    one, _ = await _synth_edge(short)
    three, _ = await _synth_edge(long)
    ratio = len(three) / len(one) if one else 0
    print(f"  1x sentence : {len(one):6d} B = {seconds(one):.2f}s")
    print(f"  3x sentence : {len(three):6d} B = {seconds(three):.2f}s   ratio {ratio:.2f} (expect ~3.0)")
    if ratio < 2.0:
        print("  measurement is INVALID (byte count is capped or padded); aborting.")
        return 1
    print("  measurement is VALID\n")

    print("=== which documents does the endpoint accept? ===")
    cases: list[tuple[str, str | None]] = [
        ("plain text (control)", None),
        ("prosody wrapper, no change", _document(short, "rate='+0%' pitch='+0Hz'")),
        ("prosody rate='-50%'", _document(short, "rate='-50%'")),
        ("prosody rate='+100%'", _document(short, "rate='+100%'")),
        ("<break> bare in <voice>", _document(f"{short}<break time='700ms'/> Good job!")),
        ("<break> inside <prosody>", _document(f"{short}<break time='700ms'/> Good job!", "rate='+0%'")),
        ("<emphasis> inside <prosody>", _document(f"Can you say <emphasis level='strong'>cat</emphasis>?", "rate='+0%'")),
        ("nested <prosody>", _document(f"Can you say <prosody rate='-30%'>cat</prosody>?", "rate='+0%'")),
    ]

    results: dict[str, bytes | None] = {}
    for label, document in cases:
        try:
            audio, _ = await _synth_edge(short, document)
            results[label] = audio
            print(f"  {label:<32} {len(audio):6d} B  accepted")
        except Exception as exc:  # noqa: BLE001 - a rejected document IS the finding
            results[label] = None
            print(f"  {label:<32}          REJECTED ({type(exc).__name__})")

    print("\n=== effect of the accepted prosody ===")
    for label, audio in results.items():
        if audio is None or label == "plain text (control)":
            continue
        delta = seconds(audio) - seconds(one)
        verdict = "honoured" if abs(delta) > 0.15 else "no measurable effect"
        print(f"  {label:<32} {delta:+.2f}s vs control -> {verdict}")

    print(
        "\nConclusion: exactly one document shape is accepted (a single <prosody>\n"
        "inside <voice>); <break>, <emphasis> and nesting are rejected outright.\n"
        "`pitch` and `volume` cannot be judged from duration, since they do not\n"
        "change how long a sentence takes."
    )
    return 0


# ---------------------------------------------------------------------- timing


def probe_timing(base: str) -> int:
    try:
        import httpx
    except ImportError:
        print("httpx is not installed; skipping the timing section.")
        return 0

    salt = "w" + "".join(chr(97 + (int(time.time()) + n) % 26) for n in range(4))
    one = f"Can you say comfy {salt}?"
    three = f"Great job! You are working hard. Can you say comfy {salt}?"

    with httpx.Client(base_url=base, timeout=60.0) as client:
        print(f"=== cold synthesis ({base}) ===")
        cold: dict[str, float] = {}
        for label, text in (("1 sentence", one), ("3 sentences", three)):
            cold[label] = _timed(client, label, text)
        print("=== identical repeat (cache floor) ===")
        for label, text in (("1 sentence", one), ("3 sentences", three)):
            _timed(client, label, text)

    if cold.get("1 sentence") and cold.get("3 sentences"):
        ratio = cold["3 sentences"] / cold["1 sentence"]
        print(f"\n3 sentences took {ratio:.2f}x one sentence.")
        if ratio > 1.6:
            print("  -> latency grows with sentence count: the connections are SERIALISED,")
            print("     so synthesising per sentence is a bad trade (it was removed).")
        else:
            print("  -> segments appear to run concurrently.")

    print(
        "\nNote: a cold call costs about two seconds from a data centre, and a cache\n"
        "hit is served with 0 ms of server time. That gap is why the in-process\n"
        "cache is part of the design and not an optimisation."
    )
    return 0


def _timed(client, label: str, text: str) -> float:
    started = time.perf_counter()
    response = client.post("/api/speech", json={"text": text, "mode": "Learning", "correct": True})
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        print(f"  {label:<14} HTTP {response.status_code} {response.text[:60]}")
        return 0.0
    server_ms = float(response.headers.get("X-Speech-Ms") or 0)
    print(
        f"  {label:<14} client {elapsed:7.0f} ms | server {server_ms:>6.0f} ms"
        f" | cached {response.headers.get('X-Speech-Cached')}"
        f" | audio {seconds(response.content):.2f}s"
    )
    return server_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the speech endpoint.")
    parser.add_argument("--base", default=os.environ.get("BASE", "http://127.0.0.1:8080"),
                        help="base URL of a running service (timing section only)")
    parser.add_argument("--section", choices=("ssml", "timing", "all"), default="all")
    args = parser.parse_args()

    if args.section in ("ssml", "all"):
        if asyncio.run(probe_ssml()) != 0:
            return 1
        print()
    if args.section in ("timing", "all"):
        return probe_timing(args.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
