"""Text normalisation, answer evaluation and safety filters.

Everything here is deterministic and dependency-free so it can be unit tested
without a network or an LLM.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
ARTICLES = ("the ", "a ", "an ")

# A sentence boundary is a terminator that is not part of a run of dots, so an
# ellipsis ("in ... and out") is a pause, not three sentence ends.
SENTENCE_END_RE = re.compile(r"(?<!\.)[.!?](?!\.)")
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# Used only for the (deliberately conservative) hard trim of model output.
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Keeps apostrophes so ``don't`` survives, and keeps non-ASCII letters so a
    child typing in Bangla does not simply vanish.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def normalize_strict(text: str) -> str:
    """``normalize`` plus removal of leading articles.

    Lets ``cat`` match ``a cat`` and ``the cat is big`` match ``cat is big``.
    """
    norm = normalize(text)
    for article in ARTICLES:
        if norm.startswith(article):
            return norm[len(article):]
    return norm


def word_count(text: str) -> int:
    return len(normalize(text).split())


def split_sentences(text: str) -> list[str]:
    """Split on real sentence endings, keeping the terminator with its sentence."""
    if not text:
        return []
    sentences: list[str] = []
    cursor = 0
    for match in SENTENCE_END_RE.finditer(text):
        chunk = text[cursor:match.end()].strip()
        if chunk:
            sentences.append(chunk)
        cursor = match.end()
    tail = text[cursor:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def sentence_count(text: str) -> int:
    """Number of sentences that actually contain words.

    A fragment with no letters - a trailing emoji, for example - is not a
    sentence, so "Yes! cat! You did it! 🌟" counts as three.
    """
    return sum(1 for sentence in split_sentences(text) if LETTER_RE.search(sentence))


def first_sentence(text: str) -> str:
    """The first sentence of *text*.

    Used to keep a curriculum hint inside the reply budget: some hints carry a
    second sentence, which would push a gentle "almost" reply over the limit.
    An ellipsis inside the hint does not end it.
    """
    if not text:
        return ""
    sentences = split_sentences(text)
    return sentences[0] if sentences else text.strip()


def similarity(a: str, b: str) -> float:
    """Character-level ratio in ``0.0..1.0`` between two normalised strings.

    Purely a fuzzy match: containment is handled explicitly in
    :func:`evaluate_answer`, so that a fragment ("cat") is not credited for a
    full phrase ("the cat is big").
    """
    a, b = normalize_strict(a), normalize_strict(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def contains_tokens(expected: str, got: str) -> bool:
    """True when *expected* appears in *got* as a contiguous run of words.

    Accepts "I see a cat" for "cat" and "yes, the cat is big now" for
    "the cat is big", without crediting a short fragment for a long phrase.
    """
    exp = normalize_strict(expected).split()
    tokens = normalize_strict(got).split()
    if not exp or len(exp) > len(tokens):
        return False
    span = len(exp)
    return any(tokens[i:i + span] == exp for i in range(len(tokens) - span + 1))


NO_ANSWER_PHRASES = {
    "idk", "i dont know", "i do not know", "dont know", "do not know",
    "no idea", "nothing", "none", "no",
    # Non-committal fillers are not an attempt at an answer.
    "hmm", "hmmm", "hm", "mm", "mmm", "uhh", "umm", "huh", "meh", "blah", "dunno",
}

EMOJI_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def is_non_answer(text: str) -> bool:
    """True when the child has not actually produced an answer."""
    norm = normalize(text)
    if not norm:
        return True
    # Normalisation keeps apostrophes, so compare "don't" and "dont" alike.
    if norm in NO_ANSWER_PHRASES or norm.replace("'", "") in NO_ANSWER_PHRASES:
        return True
    return bool(EMOJI_ONLY_RE.match(text.strip()))


# --------------------------------------------------------------------------
# Answer evaluation
# --------------------------------------------------------------------------

# Thresholds are deliberately generous: a six year old saying "cat" for
# "a cat" is a success, and "the cat big" for "the cat is big" is close enough
# to keep momentum rather than to punish.
PARTIAL_SIMILARITY = 0.5
CORRECT_SIMILARITY = 0.8


def evaluate_answer(reply: str, item) -> bool | None:
    """Score a child's reply against a curriculum item.

    Returns ``True`` (correct), ``False`` (attempted but not correct) or
    ``None`` when the child produced no usable answer at all.
    """
    if is_non_answer(reply):
        return None

    if item.kind == "open":
        return _evaluate_open(reply, item)

    expected = item.expect or item.text
    norm_reply = normalize(reply)
    if not norm_reply:
        return None

    if similarity(expected, reply) >= CORRECT_SIMILARITY:
        return True

    # "cat" for "a cat", and "the cat is big" inside a longer sentence. A short
    # fragment is not credited for a long phrase, only the other way round.
    if contains_tokens(expected, reply):
        return True

    return False


def _evaluate_open(reply: str, item) -> bool | None:
    """Score an open question deterministically.

    An item with an ``accept`` list is checking that the child stayed on topic,
    so an accepted keyword is required. Items with an empty ``accept`` list
    ("what did you do today?") have no right answer at all, so any reply of two
    or more real words counts as a genuine attempt.

    The ``accept`` lists in the curriculum are deliberately generous, and a
    miss is always phrased as "almost, let's try together" rather than "wrong".
    """
    norm = normalize(reply)
    if not norm or is_non_answer(reply):
        return None

    padded = f" {norm} "
    for term in item.accept:
        if f" {term} " in padded or norm == term:
            return True

    if item.accept:
        return False

    return word_count(reply) >= 2


# --------------------------------------------------------------------------
# Safety: input
# --------------------------------------------------------------------------

# Topics that are redirected rather than answered. Matched on word boundaries
# so "assault" never triggers on a substring of an innocent word.
BLOCK_TERMS = frozenset({
    "kill", "killed", "killing", "murder", "gun", "guns", "pistol", "rifle",
    "knife", "bomb", "blood", "bloody", "war", "weapon", "weapons", "shoot",
    "shooting", "stab", "sex", "sexy", "porn", "naked", "nude", "drugs",
    "cocaine", "heroin", "cigarette", "vape", "alcohol", "beer", "whiskey",
    "suicide", "suicidal", "die", "dying", "dead", "corpse", "hate",
    "damn", "hell", "shit", "fuck", "fucking", "bitch", "bastard", "idiot",
    "stupid", "dumb", "ugly", "fat", "loser",
})

# Distress signals => Support mode.
DISTRESS_TERMS = frozenset({
    "sad", "sadness", "cry", "crying", "cried", "tears", "scared", "scary",
    "afraid", "frightened", "fear", "angry", "anger", "mad", "frustrated",
    "frustrating", "upset", "hurt", "hurts", "pain", "lonely", "alone",
    "worried", "worry", "nervous", "shy", "tired", "sleepy", "sick",
    "unhappy", "grumpy", "annoyed", "bad day", "miss you", "miss mom",
})

FRUSTRATION_TERMS = frozenset({
    "cant", "cannot", "cant do it", "too hard", "hard", "no no", "not fair",
    "i give up", "give up", "no more", "stop", "wrong", "i am bad",
    "im bad", "i cant", "difficult",
})

# Disclosures that must route to a trusted adult. Checked before anything else.
WORRY_PATTERNS = (
    re.compile(r"\b(hit|hits|hitting|hurt|hurts|hurting)\s+me\b"),
    re.compile(r"\b(someone|somebody|he|she|they)\s+(hit|hurt|touch|touched|scared)\s+me\b"),
    re.compile(r"\b(i am|im|i'm)\s+(scared|afraid)\s+(at home|of him|of her|of them)\b"),
    re.compile(r"\b(nobody|no one)\s+(loves|likes|cares)\b"),
    re.compile(r"\b(i want to|i wanna)\s+(run away|disappear|be dead)\b"),
    re.compile(r"\b(hurts|hurting)\s+inside\b"),
)

# Boredom / disengagement signals => Engagement mode.
BOREDOM_TERMS = frozenset({
    "bored", "boring", "bored now", "again", "not again", "no more",
    "dont want", "dont want to", "do not want", "why again", "meh", "blah",
    "this is boring", "i dont like", "i do not like", "no thanks",
})

GREETING_TERMS = frozenset({
    "hi", "hii", "hiii", "hello", "helo", "hey", "heyy", "yo", "hola",
    "salam", "assalamu alaikum", "assalamualaikum", "asalamualaikum",
    "good morning", "good afternoon", "good evening", "good night",
})

# Single words that, on their own, mean "yes, let's practise".
LEARN_REQUEST_TERMS = frozenset({
    "english", "practice", "practise", "study", "lesson", "start", "go",
    "ok", "okay", "k", "yes", "yeah", "yep", "sure", "ready", "learn",
})

# Phrases matched on word boundaries anywhere in the child's message.
LEARN_REQUEST_PHRASES = frozenset({
    "lets learn", "let us learn", "teach me", "learn english",
    "i want to learn", "lets start", "lets go", "let us go",
    "teach me english", "learn a word", "practise english",
    "practice english",
})

# A request to play is a request for Engagement, not for a lesson.
PLAY_REQUEST_TERMS = frozenset({"play", "game", "games"})

PLAY_REQUEST_PHRASES = frozenset({
    "lets play", "let us play", "play with me", "play a game",
    "lets play a game", "let us play a game", "play something",
    "lets have fun", "play a game with me",
})

PERSONAL_NEWS_MARKERS = (
    "i went", "i got", "i have", "i had", "my mom", "my dad", "my mother",
    "my father", "my brother", "my sister", "my friend", "yesterday i",
    "today i", "last night", "i saw", "i made", "i played", "i like my",
    "look at my", "i am going", "guess what",
)

QUESTION_WORDS = frozenset({
    "why", "what", "who", "where", "when", "how", "which", "can", "could",
    "is", "are", "do", "does", "did",
})

MOODS = ("happy", "okay", "shy", "tired", "sad", "angry", "scared", "excited")
MOOD_ALIASES = {
    "great": "happy", "good": "happy", "fine": "okay", "meh": "tired",
    "mad": "angry", "scared": "scared", "afraid": "scared", "sleepy": "tired",
    "😊": "happy", "😀": "happy", "🙂": "okay", "😐": "okay", "😴": "tired",
    "😢": "sad", "😠": "angry", "😨": "scared", "🤩": "excited", "😳": "shy",
}
SUPPORT_MOODS = {"sad", "angry", "scared"}

# --------------------------------------------------------------------------
# Safety: input filtering
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*)?\s+"
    r"(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|block|sector|house|flat)\b",
    re.IGNORECASE,
)

MAX_INPUT_CHARS = 300
MAX_INPUT_WORDS = 60


class InputVerdict:
    """Outcome of inspecting one child message."""

    __slots__ = ("text", "blocked", "block_reason", "worry", "pii_removed", "too_long", "original_length")

    def __init__(
        self,
        text: str,
        blocked: bool = False,
        block_reason: str = "",
        worry: bool = False,
        pii_removed: bool = False,
        too_long: bool = False,
        original_length: int = 0,
    ) -> None:
        self.text = text
        self.blocked = blocked
        self.block_reason = block_reason
        self.worry = worry
        self.pii_removed = pii_removed
        self.too_long = too_long
        self.original_length = original_length

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "worry": self.worry,
            "pii_removed": self.pii_removed,
            "too_long": self.too_long,
            "original_length": self.original_length,
        }


def contains_term(text: str, terms) -> str | None:
    """Return the first matching term using word-boundary matching.

    Both the raw and apostrophe-stripped forms are checked, so "can't" matches
    the ``cant`` entry and "don't want" matches "dont want".

    Terms are iterated in sorted order so the reported reason is identical on
    every run regardless of ``PYTHONHASHSEED``.
    """
    norm = normalize(text)
    forms = (f" {norm} ", f" {norm.replace(chr(39), '')} ")
    for term in sorted(terms):
        needle = f" {term} "
        if any(needle in form for form in forms):
            return term
    return None


def detect_worry(text: str) -> bool:
    norm = normalize(text)
    return any(pattern.search(norm) for pattern in WORRY_PATTERNS)


def strip_pii(text: str) -> tuple[str, bool]:
    """Remove emails, phone numbers and street addresses.

    We never ask for or store this, and we do not want it in an LLM prompt.
    """
    cleaned = text
    for pattern in (EMAIL_RE, PHONE_RE, ADDRESS_RE):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()
    return cleaned, cleaned != text.strip()


def sanitize_input(raw: str) -> InputVerdict:
    """Length-cap, strip PII and screen one child message."""
    original = (raw or "").strip()
    verdict = InputVerdict(text=original, original_length=len(original))

    if not original:
        verdict.blocked = True
        verdict.block_reason = "empty"
        return verdict

    text = original
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        verdict.too_long = True

    words = text.split()
    if len(words) > MAX_INPUT_WORDS:
        text = " ".join(words[:MAX_INPUT_WORDS])
        verdict.too_long = True

    text, pii = strip_pii(text)
    verdict.pii_removed = pii
    verdict.text = text

    if not text:
        verdict.blocked = True
        verdict.block_reason = "pii_only"
        return verdict

    hit = contains_term(text, BLOCK_TERMS)
    if hit:
        verdict.blocked = True
        verdict.block_reason = f"blocked_term:{hit}"
        return verdict

    if detect_worry(text):
        verdict.worry = True

    return verdict


# --------------------------------------------------------------------------
# Safety: output filtering
# --------------------------------------------------------------------------

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
MARKDOWN_RE = re.compile(r"[*_`>#\[\]{}|]")
MAX_OUTPUT_CHARS = 320
MAX_OUTPUT_SENTENCES = 3

# The model is never allowed to talk about the machinery of the tutor. If it
# does, the reply is discarded in favour of the deterministic fallback.
OVERREACH_RE = re.compile(
    r"\b(level\s*[123]\b|promoted|demoted|\d+\s*points?\b|your score\b|"
    r"you (?:have been|are) (?:moved|promoted|demoted|advanced)\b)",
    re.IGNORECASE,
)

FALLBACK_SAFE_REPLY = "That was fun! Let's keep playing together. 💛"


def sanitize_output(text: str, fallback: str = FALLBACK_SAFE_REPLY) -> tuple[str, bool]:
    """Make an LLM reply safe to show a child.

    Returns ``(safe_text, was_modified)``. A reply that trips a banned term or
    loses all meaning is replaced by a deterministic safe line.
    """
    if not text:
        return fallback, True

    cleaned = URL_RE.sub("", text)
    cleaned = MARKDOWN_RE.sub("", cleaned)
    cleaned = cleaned.replace("\n", " ").strip()
    cleaned = _SPACE_RE.sub(" ", cleaned)

    if not cleaned:
        return fallback, True

    if contains_term(cleaned, BLOCK_TERMS):
        return fallback, True

    if OVERREACH_RE.search(cleaned):
        return fallback, True

    modified = cleaned != text.strip()

    # Trim to the sentence budget, then to the character budget. Only sentences
    # that carry words count, and an ellipsis is not a sentence boundary.
    sentences = split_sentences(cleaned)
    spoken = [s for s in sentences if LETTER_RE.search(s)]
    if len(spoken) > MAX_OUTPUT_SENTENCES:
        kept: list[str] = []
        for sentence in sentences:
            if len(kept) >= MAX_OUTPUT_SENTENCES:
                break
            kept.append(sentence)
        cleaned = " ".join(kept)
        modified = True

    if len(cleaned) > MAX_OUTPUT_CHARS:
        cleaned = cleaned[:MAX_OUTPUT_CHARS].rsplit(" ", 1)[0].rstrip() + " ..."
        modified = True

    if len(cleaned.strip(" .!?,")) < 1:
        return fallback, True

    return cleaned, modified
