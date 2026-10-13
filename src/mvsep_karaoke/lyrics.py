"""Lyrics with word timings: LRC files, LRCLIB lookup, and alignment to transcribed words."""
import difflib
import re
import time
from dataclasses import dataclass

import requests

LRCLIB = "https://lrclib.net/api"
DURATION_TOLERANCE = 2.0  # seconds an LRCLIB entry may differ from the song and still be the same recording
WINDOW_SLACK = 0.75  # seconds a heard word may fall outside its line's timestamps and still match
SECONDS_PER_CHAR = 0.06  # sung-duration estimate for words the transcription did not catch
PAUSE = 0.4  # gaps longer than this between two words of a line are written to the LRC

LINE_TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
WORD_TAG = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")


@dataclass
class Word:
    text: str
    start: float
    end: float


Line = list[Word]


@dataclass
class Draft:
    """A lyric line as given: its text, maybe a start time, maybe finished word timings."""
    text: str
    start: float | None = None
    end: float | None = None
    words: Line | None = None


def seconds(minutes: str, rest: str) -> float:
    return int(minutes) * 60 + float(rest.replace(":", "."))


def stamp(at: float) -> str:
    centis = max(0, round(at * 100))
    return f"{centis // 6000:02d}:{centis % 6000 / 100:05.2f}"


def guess(text: str) -> float:
    """Roughly how long a word takes to sing."""
    return SECONDS_PER_CHAR * (len(norm(text)) + 2)


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def parse_words(body: str) -> Line | None:
    """Words of an enhanced-LRC line body (`<mm:ss.xx> word ...`), or None if it has no word tags."""
    pieces = WORD_TAG.split(body)
    if len(pieces) == 1:
        return None
    words, open_ended = [], False
    for minutes, rest, text in zip(pieces[1::3], pieces[2::3], pieces[3::3]):
        at = seconds(minutes, rest)
        if open_ended:
            words[-1].end = at
        open_ended = bool(text.strip())
        if open_ended:
            words.append(Word(text.strip(), at, at + guess(text)))
    return words or None


def parse_lrc(text: str) -> list[Draft]:
    """Read LRC text. Lines may carry word tags (kept as they are) or only a line timestamp."""
    drafts = []
    for raw in text.splitlines():
        raw, starts = raw.strip(), []
        while match := LINE_TAG.match(raw):
            starts.append(seconds(*match.groups()))
            raw = raw[match.end():].lstrip()
        for start in starts:
            words = parse_words(raw)
            drafts.append(Draft(" ".join(word.text for word in words) if words else raw, start, words=words))
    drafts.sort(key=lambda draft: draft.start)
    # A timestamp with no text only marks where the line before it ends.
    lines = []
    for draft in drafts:
        if draft.text:
            lines.append(draft)
        elif lines and lines[-1].end is None:
            lines[-1].end = draft.start
    for line, following in zip(lines, lines[1:]):
        line.end = line.end if line.end is not None else following.start
    return lines


def parse_plain(text: str) -> list[Draft]:
    return [Draft(line.strip()) for line in text.splitlines() if line.strip()]


def format_lrc(lines: list[Line]) -> str:
    """Enhanced LRC: each word is preceded by its start time; an empty tag marks where a word ends."""
    rows = []
    for line in lines:
        parts = [f"[{stamp(line[0].start)}]"]
        for word, following in zip(line, [*line[1:], None]):
            parts.append(f"<{stamp(word.start)}> {word.text}")
            if following is None or following.start - word.end > PAUSE:
                parts.append(f"<{stamp(word.end)}>")
        rows.append(" ".join(parts))
    return "\n".join(rows) + "\n"


def search_lrclib(params: dict) -> list[dict]:
    for attempt in range(4):  # the service drops connections and returns 503 fairly often
        try:
            response = requests.get(f"{LRCLIB}/search", params=params, headers={"User-Agent": "mvsep-karaoke"}, timeout=30)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    print("LRCLIB did not answer; continuing without it")
    return []


def fetch_lrclib(title: str, artist: str, duration: float, exact_title: bool = False) -> list[Draft]:
    """Lyrics for a song from LRCLIB: timed lines if it has them, else plain lines, else nothing.
    With exact_title, only entries named exactly `title` count (for titles guessed from a file name)."""
    # Artist tags are written many ways ("A, B" / "A & B"), so fall back to a free-text search.
    searches = [{"track_name": title, "artist_name": artist}, {"q": f"{title} {artist}"}] if artist else [{"track_name": title}]
    entries = []
    for params in searches:
        entries = [entry for entry in search_lrclib(params)
                   if abs((entry.get("duration") or 0) - duration) <= DURATION_TOLERANCE
                   and (not exact_title or norm(entry.get("trackName") or "") == norm(title))]
        if entries:
            break
    for key, kind, parse in (("syncedLyrics", "line timestamps", parse_lrc), ("plainLyrics", "text only", parse_plain)):
        for entry in entries:
            if entry.get(key):
                print(f"lyrics: LRCLIB entry {entry['id']}, \"{entry.get('trackName')}\" by {entry.get('artistName')} ({kind})")
                return parse(entry[key])
    return []


def match(wanted: list[str], heard: list[str]) -> dict[int, int]:
    """Indexes of wanted words mapped to the heard words they line up with."""
    blocks = difflib.SequenceMatcher(None, wanted, heard, autojunk=False).get_matching_blocks()
    return {block.a + k: block.b + k for block in blocks for k in range(block.size) if wanted[block.a + k]}


def place(texts: list[str], anchors: dict[int, Word], start: float | None, floor: float, ceiling: float) -> Line:
    """Time the words of a run of lyrics: anchored words keep the heard time, the rest are spread
    between them in proportion to their length. `start` is a known start time for the first word."""
    weights = [len(norm(text)) + 2 for text in texts]
    knots = {index: heard.start for index, heard in anchors.items()}
    if 0 not in knots:
        first = min(knots, default=None)
        if first is None:
            knots[0] = floor if start is None else start
        elif start is not None:
            knots[0] = min(start, knots[first])
        else:
            knots[0] = max(floor, knots[first] - SECONDS_PER_CHAR * sum(weights[:first]))
    # After the last known time, the words run to the ceiling, but no slower or faster than plausible.
    last = max(knots)
    tail = SECONDS_PER_CHAR * sum(weights[last:])
    knots[len(texts)] = knots[last] + max(0.5 * tail, min(1.5 * tail, ceiling - knots[last]))

    starts = []
    marks = sorted(knots)
    for low, high in zip(marks, marks[1:]):
        span, total = knots[high] - knots[low], sum(weights[low:high])
        starts += [knots[low] + span * sum(weights[low:index]) / total for index in range(low, high)]
    starts.append(knots[len(texts)])

    words = []
    for index, text in enumerate(texts):
        end = starts[index + 1]
        if index in anchors:
            end = min(end, max(anchors[index].end, starts[index] + 0.05))
        words.append(Word(text, starts[index], end))
    return words


def align(drafts: list[Draft], heard: list[Word], duration: float) -> list[Line]:
    """Give every draft line word timings, using the transcribed words as anchors."""
    pending = [draft for draft in drafts if draft.words is None]
    wanted = sum(len(draft.text.split()) for draft in pending)
    matched = 0
    if all(draft.start is not None for draft in pending):
        # Line timestamps are known: match each line only against what was heard around it,
        # so a repeated chorus cannot latch onto the wrong repetition.
        for draft in pending:
            texts = draft.text.split()
            end = draft.end if draft.end is not None else duration
            nearby = [word for word in heard if draft.start - WINDOW_SLACK <= word.start <= end + WINDOW_SLACK]
            pairs = match([norm(text) for text in texts], [norm(word.text) for word in nearby])
            matched += len(pairs)
            anchors = {index: nearby[position] for index, position in pairs.items()}
            draft.words = place(texts, anchors, draft.start, draft.start, end)
    elif pending:
        texts = [text for draft in pending for text in draft.text.split()]
        pairs = match([norm(text) for text in texts], [norm(word.text) for word in heard])
        matched = len(pairs)
        words = place(texts, {index: heard[position] for index, position in pairs.items()}, None, 0.0, duration)
        for draft in pending:
            count = len(draft.text.split())
            draft.words, words = words[:count], words[count:]
    if wanted:
        print(f"lyrics: {matched} of {wanted} words matched to the vocals; the rest are estimated")

    lines = sorted((draft.words for draft in drafts if draft.words), key=lambda line: line[0].start)
    # Keep words in order across line boundaries, where matching windows overlap.
    flat = [word for line in lines for word in line]
    for before, word in zip(flat, flat[1:]):
        word.start = max(word.start, before.start)
        before.end = min(before.end, word.start)
    for word in flat:
        word.end = max(word.end, word.start + 0.02)
    return lines
