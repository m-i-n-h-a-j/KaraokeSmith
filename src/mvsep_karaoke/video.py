"""Render a karaoke lyrics video: a scrolling column of lyrics over a plain colour, the row
being sung held in the centre and filled word by word."""
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .lyrics import Line
from .media import run

FPS = 60
BACKGROUND = "0x101014"
FONT = "Segoe UI"
SUNG, PLAIN = "00D7FF", "FFFFFF"  # ASS colours are BBGGRR: gold, white
SCROLL = 0.35  # seconds one scroll step takes
LEAD = 0.25  # seconds before its first word that a row scrolls into the centre
GAP = 4.0  # a pause longer than this scrolls the next row in early and counts it in
LINGER = 1.0  # seconds a finished row stays in the centre before such a pause
COUNT_IN = 3.0  # seconds of count-in dots


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    font_size: int
    max_chars: int  # longest row that still fits the width at font_size, even in bold capitals
    row_height: int
    reach: int  # rows visible above and below the one being sung

    @property
    def centre(self) -> int:
        return self.height // 2


LAYOUTS = {
    "horizontal": Layout(1920, 1080, 84, 30, 140, 3),
    "vertical": Layout(1080, 1920, 76, 20, 140, 5),
}


def header(layout: Layout) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {layout.width}
PlayResY: {layout.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{FONT},{layout.font_size},&H00{SUNG},&H00{PLAIN},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def width(words: Line) -> int:
    return sum(len(word.text) + 1 for word in words) - 1


def split_line(line: Line, max_chars: int) -> list[Line]:
    """Break a lyric line into rows of at most max_chars, preferring even halves and punctuation."""
    if width(line) <= max_chars or len(line) == 1:
        return [line]

    def cost(cut: int) -> int:
        after_punctuation = line[cut - 1].text[-1] in ",.;:!?)"
        return abs(width(line[:cut]) - width(line[cut:])) - (10 if after_punctuation else 0)

    cut = min(range(1, len(line)), key=cost)
    return split_line(line[:cut], max_chars) + split_line(line[cut:], max_chars)


def timestamp(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def karaoke_text(row: Line, appear: float) -> str:
    """ASS karaoke tags for a row whose event starts at `appear`: each word fills as it is sung."""
    parts, cursor = [], round(appear * 100)
    for word in row:
        start = max(cursor, round(word.start * 100))
        end = max(start + 1, round(word.end * 100))
        if start > cursor:
            parts.append(f"{{\\k{start - cursor}}}")
        parts.append(f"{{\\kf{end - start}}}{clean(word.text)} ")
        cursor = end
    return "".join(parts).rstrip()


def look(offset: int, reach: int, filling: bool = False) -> str:
    """Override tags for a row `offset` rows below the centre: rows fade, shrink and blur with
    distance, and rows still to come stay more readable than rows already sung. A filling row
    keeps the style's own colours, which the karaoke tags switch between."""
    distance = abs(offset)
    if distance == 0:
        alpha, scale, blur = 0x00, 100, 0.0
    elif distance > reach:
        alpha, scale, blur = 0xFF, 70, 4.0
    else:
        far = (distance - 1) / (reach - 1)
        nearest = 0x58 if offset > 0 else 0x98
        alpha, scale, blur = round(nearest + (0xE0 - nearest) * far), round(84 - 10 * far), 0.5 + 2.5 * far
    colour = "" if filling else f"\\1c&H{SUNG if offset == 0 else PLAIN}&"
    return f"\\alpha&H{alpha:02X}&\\fscx{scale}\\fscy{scale}\\blur{blur:.1f}{colour}"


def event(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{timestamp(start)},{timestamp(end)},Karaoke,,0,0,0,,{text}"


def subtitles(lines: list[Line], duration: float, layout: Layout, title: str = "", artist: str = "") -> str:
    """An ASS script: the lyrics as a column that scrolls up one row each time a new row starts."""
    rows = [row for line in lines for row in split_line(line, layout.max_chars)]
    middle, centre, reach = layout.width // 2, layout.centre, layout.reach

    # turns[m] is when row m scrolls into the centre; the last entry closes the final row.
    turns, counted = [0.0], [rows[0][0].start >= COUNT_IN]
    for before, row in zip(rows, rows[1:]):
        start, ended = row[0].start, before[-1].end
        paused = start - ended > GAP
        at = ended + LINGER if paused else min(start, max(ended, start - LEAD))
        turns.append(max(at, turns[-1] + 0.05))
        counted.append(paused)
    turns.append(max(duration, turns[-1] + 0.05))

    events = []
    for index, row in enumerate(rows):
        # While row `current` is in the centre, this row sits `offset` rows below it.
        for current in range(max(0, index - reach), min(len(rows), index + reach + 2)):
            offset = index - current
            begin, until = turns[current], turns[current + 1]
            step = min(SCROLL, until - begin)
            filling = offset == 0
            text = karaoke_text(row, begin) if filling else clean(" ".join(word.text for word in row))
            y = centre + offset * layout.row_height
            if current == 0:  # the opening layout: nothing to scroll in from
                tags = f"\\pos({middle},{y})\\fad(500,0){look(offset, reach, filling)}"
            else:
                tags = (f"\\move({middle},{y + layout.row_height},{middle},{y},0,{round(step * 1000)})"
                        f"{look(offset + 1, reach, filling)}"
                        f"\\t(0,{round(step * 1000)},{look(offset, reach, filling)})")
            if offset < -reach:  # scrolled out of reach: gone once the step is over
                until = begin + step
            events.append(event(begin, until, f"{{{tags}}}{text}"))

        if counted[index]:
            first = row[0].start
            begin = max(turns[index], first - COUNT_IN)
            beat = max(1, round((first - begin) * 100 / 3))
            dots = " ".join(f"{{\\kf{beat}}}●" for _ in range(3))
            where = f"\\pos({middle},{centre - round(layout.row_height * 0.53)})\\fs{round(layout.font_size * 0.67)}"
            events.append(event(begin, first, f"{{{where}\\2a&HB0&\\fad(200,100)}}{dots}"))

    intro = min(rows[0][0].start - 0.5, 6.0)
    if title and intro > 2.0:
        # Shown above the waiting first row, shrunk if needed so a long title still fits the width.
        size = round(min(layout.font_size * 1.3, 0.9 * layout.width / (0.68 * len(title))))
        heading = f"\\pos({middle},{centre - round(layout.row_height * 2.2)})\\fs{size}\\1c&H{PLAIN}&"
        events.append(event(0, intro, f"{{{heading}\\fad(500,500)}}{clean(title)}"))
        if artist:
            byline = f"\\pos({middle},{centre - round(layout.row_height * 1.5)})\\fs{round(layout.font_size * 0.6)}\\1c&H{SUNG}&"
            events.append(event(0, intro, f"{{{byline}\\fad(500,500)}}{clean(artist)}"))
    return header(layout) + "\n".join(events) + "\n"


def render(lines: list[Line], karaoke: Path, original: Path, duration: float, target: Path,
           layout: Layout, title: str = "", artist: str = "") -> None:
    """Encode the lyrics over a plain background as an MKV with two FLAC audio tracks:
    the karaoke track (default) and the original song."""
    with tempfile.TemporaryDirectory() as folder:
        # A Windows path inside an ffmpeg filter needs layers of escaping, so the subtitle
        # file gets a plain name and ffmpeg runs from its folder instead.
        script = subtitles(lines, duration, layout, title, artist)
        (Path(folder) / "lyrics.ass").write_text(script, encoding="utf-8")
        run(
            ["ffmpeg", "-hide_banner", "-nostats", "-y",
             "-f", "lavfi", "-i", f"color=c={BACKGROUND}:s={layout.width}x{layout.height}:r={FPS}:d={duration:.3f}",
             "-i", str(karaoke.resolve()), "-i", str(original.resolve()),
             "-map", "0:v", "-map", "1:a:0", "-map", "2:a:0",
             "-vf", "ass=lyrics.ass",
             "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p",
             # The karaoke track is already the FLAC we want; the original is brought to the same format.
             "-c:a:0", "copy", "-c:a:1", "flac", "-ar:a:1", "48000", "-sample_fmt:a:1", "s16",
             "-metadata:s:a:0", "title=Karaoke", "-metadata:s:a:1", "title=Original",
             "-disposition:a:0", "default", "-disposition:a:1", "0",
             "-shortest", str(target.resolve())],
            cwd=Path(folder),
        )
    print(f"saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
