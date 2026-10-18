"""Render a karaoke lyrics video: a scrolling column of lyrics over slowly turning colours taken
from the song's cover art, the row being sung held in the centre and filled word by word while a
ball bounces from word to word."""
import colorsys
import random
import shutil
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from math import ceil, hypot
from pathlib import Path

import uharfbuzz as hb

from . import aurora
from .lyrics import Line
from .media import cover, run

FPS = 24  # the default; --fps sets another
# Video encoders by the name --codec takes: the pixel format the picture is made in, and the
# ffmpeg options. FFV1 is lossless, for archiving or editing further, and many times larger. It
# keeps full colour resolution: 4:2:0 halves it, which leaves dark fringes around gold lyrics.
CODECS = {
    "avc": ("yuv420p", ["-c:v", "libx264", "-preset", "slow", "-crf", "20"]),
    "ffv1": ("yuv444p10le", ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "16", "-slicecrc", "1"]),
}
# The lyrics' typeface ships with the package, so every machine renders the same: Bricolage
# Grotesque, under the SIL Open Font Licence in fonts/OFL.txt. The file is the variable font as
# published; its default instance is the 96pt ExtraBold that FONT names, which is what libass uses.
FONT_FILE = Path(__file__).parent / "fonts" / "BricolageGrotesque.ttf"
FONT = "Bricolage Grotesque 96pt ExtraBold"
TRACKING = 0.02  # letter spacing, as a fraction of the font size
SUNG, PLAIN, SHINE = "00D7FF", "FFFFFF", "C4F6FF"  # ASS colours are BBGGRR: gold, white, pale gold
SCROLL = 0.35  # seconds one scroll step takes
LEAD = 0.25  # seconds before its first word that a row scrolls into the centre
GAP = 4.0  # a pause longer than this scrolls the next row in early and counts it in
LINGER = 1.0  # seconds a finished row stays in the centre before such a pause
COUNT_IN = 3.0  # seconds of count-in dots
POP = 105  # percent of its size a row swells to as it lands in the centre
BALL = 0.14  # radius of the bouncing ball, as a fraction of the font size
LIFT = 0.66  # how far above the middle of a row the ball lands, as a fraction of the font size
DROP = 0.6  # seconds the ball takes to drop in onto a row's first word

# "aurora": ribbons of the cover's colours that react to beats, drawn frame by frame in aurora.py.
# "cover": the cover art itself, turned and blurred by ffmpeg, brightening with the bass.
BACKDROP = "aurora"
# The backdrop is all soft shapes, so it is worked out small and at no more than BACKDROP_FPS, then blown up.
SHRINK, BACKDROP_FPS = 6, 30
COVER = 64  # pixels a side the cover art is cut down to before it is blurred into the backdrop
PEAK, MEAN = 0.36, 0.18  # brightest spot and average the backdrop is held to, so lyrics stay readable
BEAT = 0.5  # how much brighter the loudest bass makes the backdrop: 0.5 is up to about a third


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    font_size: int  # the em in pixels, as browsers and HarfBuzz mean it; libass counts differently (ass_size)
    max_chars: int  # longest row that still fits the width at font_size, even in capitals
    row_height: int
    reach: int  # rows visible above and below the one being sung

    @property
    def centre(self) -> int:
        return self.height // 2


LAYOUTS = {
    "horizontal": Layout(1920, 1080, 90, 30, 150, 3),
    "vertical": Layout(1080, 1920, 81, 18, 150, 5),
}


def table(data: bytes, tag: bytes) -> int:
    """Where a table starts in a TrueType file."""
    for record in range(12, 12 + 16 * int.from_bytes(data[4:6]), 16):
        if data[record:record + 4] == tag:
            return int.from_bytes(data[record + 8:record + 12])
    raise ValueError(f"{tag.decode()} table missing")


def em_ratio(path: Path) -> float:
    """libass takes a font size as the height from usWinAscent to usWinDescent, as VSFilter did,
    where browsers and HarfBuzz take it as the em. For this font that box is 1.56 em, so an ASS
    size is that many times the em it gives."""
    data = path.read_bytes()
    head, metrics = table(data, b"head"), table(data, b"OS/2")
    win = int.from_bytes(data[metrics + 74:metrics + 76]) + int.from_bytes(data[metrics + 76:metrics + 78])
    return win / int.from_bytes(data[head + 18:head + 20])


EM_RATIO = em_ratio(FONT_FILE)


def ass_size(em: float) -> str:
    """The ASS font size that sets text at an em of `em` pixels."""
    return f"{em * EM_RATIO:.1f}"


class Ruler:
    """Widths of text as libass sets it in FONT at an em of `size` pixels: shaped by HarfBuzz, as
    libass shapes it, plus the letter spacing after every character. libass kerns only when the
    script says Kerning: yes and the letter spacing is 0; with TRACKING at 2% it does not kern
    (measured: "AV To Wa" 391 px wide, against 370 kerned)."""
    face = hb.Face(hb.Blob.from_file_path(str(FONT_FILE)))
    font = hb.Font(face)
    features = {} if TRACKING == 0 else {"kern": False}

    def __init__(self, size: float):
        self.size = size

    def width(self, text: str) -> float:
        if not text:
            return 0.0  # HarfBuzz leaves an empty buffer without positions
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(self.font, buffer, self.features)
        advance = sum(position.x_advance for position in buffer.glyph_positions)
        return advance * self.size / self.face.upem + self.size * TRACKING * len(text)


def header(layout: Layout) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {layout.width}
PlayResY: {layout.height}
WrapStyle: 2
ScaledBorderAndShadow: yes
Kerning: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{FONT},{ass_size(layout.font_size)},&H00{SUNG},&H00{PLAIN},&H00000000,&H00000000,-1,0,0,0,100,100,{layout.font_size * TRACKING:.1f},0,1,0,0,5,0,0,0,1

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


def stamp(centis: int) -> str:
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def timestamp(seconds: float) -> str:
    return stamp(max(0, round(seconds * 100)))


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


def appearance(offset: int, reach: int) -> tuple[int, int, float]:
    """How a row `offset` rows below the centre looks, as its ASS alpha, scale in percent and blur:
    rows fade, shrink and blur with distance, and rows still to come stay more readable than rows
    already sung."""
    distance = abs(offset)
    if distance == 0:
        return 0x00, 100, 0.0
    if distance > reach:
        return 0xFF, 70, 4.0
    far = (distance - 1) / (reach - 1)
    nearest = 0x58 if offset > 0 else 0x98
    return round(nearest + (0xE0 - nearest) * far), round(84 - 10 * far), 0.5 + 2.5 * far


def look(offset: int, reach: int, filling: bool = False) -> str:
    """Override tags for a row `offset` rows below the centre. A filling row keeps the style's own
    colours, which the karaoke tags switch between."""
    alpha, scale, blur = appearance(offset, reach)
    colour = "" if filling else f"\\1c&H{SUNG if offset == 0 else PLAIN}&"
    return f"\\alpha&H{alpha:02X}&\\fscx{scale}\\fscy{scale}\\blur{blur:.1f}{colour}"


def event(start: float, end: float, text: str, layer: int = 1) -> str:
    """Layers from the back: 0 specks, 1 everything to read, 2 the bouncing ball."""
    return f"Dialogue: {layer},{timestamp(start)},{timestamp(end)},Karaoke,,0,0,0,,{text}"


def frame_event(frame: int, fps: int, text: str, layer: int = 2) -> str:
    """An event for one video frame. Its times are rounded down to whole centiseconds, which keeps
    the frame's own time inside it and out of its neighbours' at any frame rate under 100."""
    return f"Dialogue: {layer},{stamp(frame * 100 // fps)},{stamp((frame + 1) * 100 // fps)},Karaoke,,0,0,0,,{text}"


def disc(radius: int) -> str:
    """An ASS drawing of a filled circle."""
    r, k = radius, round(radius * 0.448)  # k: how far short of the corner a quarter circle's control points sit
    d = 2 * r
    return (f"m {r} 0 b {d - k} 0 {d} {k} {d} {r} b {d} {d - k} {d - k} {d} {r} {d} "
            f"b {k} {d} 0 {d - k} 0 {r} b 0 {k} {k} 0 {r} 0")


def count_in(begin: float, first: float, x: int, y: int, radius: int) -> list[str]:
    """Three dots over the waiting row. A gold dot grows inside each in turn, and the third is
    full, with a bump, at the moment the singing starts."""
    beat = (first - begin) / 3
    full = round(beat * 1000)
    grow = (f"\\fscx1\\fscy1\\t(0,{full},\\fscx100\\fscy100)"
            f"\\t({full},{full + 80},\\fscx145\\fscy145)\\t({full + 80},{full + 220},\\fscx100\\fscy100)")
    events = []
    for dot in range(3):
        where = f"\\an5\\pos({x + (dot - 1) * radius * 4},{y})\\p1"
        events.append(event(begin, first + 0.15, f"{{{where}\\1c&H{PLAIN}&\\alpha&HB0&\\fad(200,150)}}{disc(radius)}"))
        events.append(event(begin + dot * beat, first + 0.15, f"{{{where}\\1c&H{SUNG}&\\fad(0,150){grow}}}{disc(radius)}"))
    return events


def title_card(title: str, artist: str, start: float, until: float, y: int, layout: Layout) -> list[str]:
    """Title and artist, the artist on the line `y`: the title's letters draw together out of a
    blur, a rule opens under it from the middle, and the artist follows."""
    middle, rows = layout.width // 2, layout.row_height
    # 1.3 times the lyrics, shrunk if needed so a long title still fits the width.
    size = layout.font_size * 1.3
    size = min(size, size * 0.9 * layout.width / Ruler(size).width(clean(title)))
    spacing = size * TRACKING
    leave = round((until - start) * 1000)
    settle = (f"\\blur8\\fsp{size * 0.2 + spacing:.1f}\\t(0,900,0.4,\\blur0\\fsp{spacing:.1f})"
              f"\\t({leave - 500},{leave},\\blur8)")
    heading = f"\\pos({middle},{y - round(rows * 0.85)})\\fs{ass_size(size)}\\1c&H{PLAIN}&\\fad(500,500)"
    events = [event(start, until, f"{{{heading}{settle}}}{clean(title)}")]
    if artist:
        span, thick = round(layout.width * 0.06), max(2, layout.font_size // 28)
        rule = f"\\an5\\pos({middle},{y - round(rows * 0.27)})\\1c&H{SUNG}&\\fad(0,500)\\fscx1\\t(200,1000,0.4,\\fscx100)\\p1"
        events.append(event(start + 0.2, until, f"{{{rule}}}m 0 0 l {span * 2} 0 {span * 2} {thick} 0 {thick}"))
        byline = f"\\pos({middle},{y})\\fs{ass_size(layout.font_size * 0.6)}\\1c&H{SUNG}&\\fad(600,500)"
        events.append(event(start + 0.4, until, f"{{{byline}}}{clean(artist)}"))
    return events


def hops(row: Line, ruler: Ruler) -> tuple[list[tuple[float, float]], float]:
    """Where the ball lands on a row: for each word, when it starts and the x of its middle from
    the row's centre. Also half the row's width."""
    texts = [clean(word.text) for word in row]
    half = ruler.width(" ".join(texts)) / 2
    spots, before = [], ""
    for word, text in zip(row, texts):
        spots.append((word.start, ruler.width(before) + ruler.width(text) / 2 - half))
        before += text + " "
    return spots, half


def bounce(spots: list[tuple[float, float]], half: float, end: float, t: float, size: float,
           drop: float) -> tuple[float, float, float, float] | None:
    """The ball at time t as its offset from the row's centre, how squashed it is (0 to 1) and
    how opaque, or None while it is not shown. It drops in from the left onto the first word in
    `drop` seconds, then hops from word to word and lands on each as the word starts. The hops
    stay low enough to clear the row above."""
    top = -size * LIFT
    (first, x), (last, there) = spots[0], spots[-1]
    if not first - drop <= t <= end + 0.5:
        return None
    if t < first:
        u = (t - first + drop) / drop
        start = -half - 0.9 * size
        return start + (x - start) * u, top - 0.45 * size * (1 - u * u), 0.0, min(1.0, u * 3)

    def landed(since: float) -> float:
        return max(0.0, 1 - since / 0.08)

    if t >= last:
        return there, top, landed(t - last), min(1.0, (end + 0.5 - t) / 0.3)
    (at, x), (then, there) = next((a, b) for a, b in zip(spots, spots[1:]) if t < b[0])
    u = (t - at) / max(0.01, then - at)
    height = min(size * 0.4, abs(there - x) * 0.35 + size * 0.1)
    return x + (there - x) * u, top - height * 4 * u * (1 - u), landed(t - at), 1.0


def ball(row: Line, ruler: Ruler, start: float, stop: float, fps: int, where) -> list[str]:
    """The bouncing ball over `row` between `start` and `stop`, one event per video frame, since
    an ASS move is a straight line. `where(t)` gives the row's centre, scale and opacity at t."""
    spots, half = hops(row, ruler)
    radius = max(2, round(ruler.size * BALL))
    shine = max(1, round(radius * 0.4))
    # A row that follows straight on from the last one only arrives in the centre a moment before
    # its first word, so the drop is shortened to fit; without the time for one, the ball starts on the word.
    drop = row[0].start - start if row[0].start - start >= 0.1 else 1e-3
    events = []
    for frame in range(ceil(start * fps), ceil(stop * fps)):
        t = frame / fps
        at = bounce(spots, half, row[-1].end, t, ruler.size, drop)
        if at is None:
            continue
        dx, dy, squash, opacity = at
        (cx, cy), scale, shown = where(t)
        wide, tall = 1 + 0.25 * squash, 1 - 0.25 * squash  # flattened just after landing, resting on the same spot
        x, y = cx + dx * scale, cy + (dy + radius * (1 - tall)) * scale
        alpha = round(255 * (1 - opacity * shown))
        size = f"\\fscx{wide * scale * 100:.0f}\\fscy{tall * scale * 100:.0f}"
        events.append(frame_event(frame, fps, f"{{\\an5\\pos({x:.1f},{y:.1f}){size}\\1c&H{SUNG}&\\alpha&H{alpha:02X}&\\p1}}{disc(radius)}"))
        glint = f"\\an5\\pos({x - 0.3 * radius * scale:.1f},{y - 0.3 * radius * scale:.1f})\\blur{shine * 0.6:.1f}"
        events.append(frame_event(frame, fps, f"{{{glint}{size}\\1c&H{SHINE}&\\alpha&H{max(alpha, 0x30):02X}&\\p1}}{disc(shine)}"))
    return events


def specks(layout: Layout, duration: float) -> list[str]:
    """Soft specks of light that drift up behind the lyrics, the larger ones dimmer and more
    out of focus."""
    chance = random.Random(0)  # the same specks on every render
    events, at = [], -12.0  # the ones due before the start all appear with it, so the frame opens full
    while at < duration:
        size = chance.random() ** 2  # mostly small
        radius = round(layout.font_size * (0.04 + 0.3 * size))
        x, y = chance.uniform(0, layout.width), chance.uniform(0.1, 1.1) * layout.height
        drift = chance.uniform(-0.03, 0.03) * layout.width
        rise = chance.uniform(0.08, 0.22) * layout.height
        tags = (f"\\an5\\move({x:.0f},{y:.0f},{x + drift:.0f},{y - rise:.0f})\\fad(2500,2500)"
                f"\\1c&H{PLAIN}&\\alpha&H{round(0xC0 + 0x28 * size):02X}&\\blur{1 + radius * 0.35:.1f}\\p1")
        born, gone = max(0.0, at), at + chance.uniform(9, 16)
        if gone - born > 5:  # long enough to fade in and out again
            events.append(event(born, gone, f"{{{tags}}}{disc(radius)}", layer=0))
        at += 0.7
    return events


def landing(begin: float, until: float, step: float, current: int, count: int, leaving: bool, layout: Layout):
    """Where the row being sung is at time t, for the ball to follow it: its centre, scale and
    opacity as the tags in subtitles() move it, from the row below the centre into the centre,
    swelling to POP on the way. The opening row stands still and fades in; the last one may fade out."""
    start_alpha, start_scale, _ = appearance(1, layout.reach)

    def where(t: float) -> tuple[tuple[float, float], float, float]:
        if current == 0:
            done, scale, shown = 1.0, 1.0, min(1.0, t / 0.5)
        else:
            done = min(1.0, max(0.0, (t - begin) / step))
            lit = min(1.0, done / 0.7)
            scale = (start_scale + (POP - start_scale) * lit if done < 0.7 else POP + (100 - POP) * (done - 0.7) / 0.3) / 100
            shown = 1 - start_alpha * (1 - lit) / 255
        if leaving and current == count - 1:
            shown *= min(1.0, max(0.0, (until - t) / 0.8))
        return (layout.width // 2, layout.centre + (1 - done) * layout.row_height), scale, shown
    return where


def subtitles(lines: list[Line], duration: float, layout: Layout, title: str = "", artist: str = "",
              fps: int = FPS) -> str:
    """An ASS script: the lyrics as a column that scrolls up one row each time a new row starts."""
    rows = [row for line in lines for row in split_line(line, layout.max_chars)]
    middle, centre, reach = layout.width // 2, layout.centre, layout.reach
    ruler = Ruler(layout.font_size)

    # turns[m] is when row m scrolls into the centre; the last entry closes the final row.
    turns, counted = [0.0], [rows[0][0].start >= COUNT_IN]
    for before, row in zip(rows, rows[1:]):
        start, ended = row[0].start, before[-1].end
        paused = start - ended > GAP
        at = ended + LINGER if paused else min(start, max(ended, start - LEAD))
        turns.append(max(at, turns[-1] + 0.05))
        counted.append(paused)
    # The column stays to the end, unless the song plays on long after the last word: then it
    # fades out and the title comes back.
    leaving = duration - rows[-1][-1].end > GAP
    turns.append(max(rows[-1][-1].end + LINGER + 0.8 if leaving else duration, turns[-1] + 0.05))

    events = specks(layout, duration) if BACKDROP == "cover" else []  # the aurora was chosen without them
    for index, row in enumerate(rows):
        # While row `current` is in the centre, this row sits `offset` rows below it.
        for current in range(max(0, index - reach), min(len(rows), index + reach + 2)):
            offset = index - current
            begin, until = turns[current], turns[current + 1]
            step = min(SCROLL, until - begin)
            filling = offset == 0
            text = karaoke_text(row, begin) if filling else clean(" ".join(word.text for word in row))
            y = centre + offset * layout.row_height
            fade = f"\\fad({500 if current == 0 else 0},{800 if leaving and current == len(rows) - 1 else 0})"
            if current == 0:  # the opening layout: nothing to scroll in from
                tags = f"\\pos({middle},{y}){fade}{look(offset, reach, filling)}"
            else:
                done = round(step * 1000)
                tags = (f"\\move({middle},{y + layout.row_height},{middle},{y},0,{done}){fade}"
                        f"{look(offset + 1, reach, filling)}")
                if filling:  # the row to sing swells past its size as it lands, then settles
                    landed = round(done * 0.7)
                    tags += (f"\\t(0,{landed},{look(offset, reach, filling)}\\fscx{POP}\\fscy{POP})"
                             f"\\t({landed},{done},\\fscx100\\fscy100)")
                else:
                    tags += f"\\t(0,{done},{look(offset, reach, filling)})"
            if offset < -reach:  # scrolled out of reach: gone once the step is over
                until = begin + step
            events.append(event(begin, until, f"{{{tags}}}{text}"))
            if filling:
                events += ball(row, ruler, max(begin, row[0].start - DROP), min(until, row[-1].end + 0.5), fps,
                               landing(begin, until, step, current, len(rows), leaving, layout))

        if counted[index]:
            first = row[0].start
            # High enough above the row that the ball dropping onto its first word passes under them.
            events += count_in(max(turns[index], first - COUNT_IN), first, middle,
                               centre - round(layout.row_height * 0.8), round(layout.font_size * 0.17))

    intro = min(rows[0][0].start - 0.5, 6.0)
    if title and intro > 2.0:  # above the waiting first row
        events += title_card(title, artist, 0, intro, centre - round(layout.row_height * 1.5), layout)
    if title and leaving:  # in the middle of the frame the lyrics have left
        events += title_card(title, artist, turns[-1], duration, centre + round(layout.row_height * 0.35), layout)
    return header(layout) + "\n".join(events) + "\n"


def colours(song: Path, title: str) -> bytes:
    """What the backdrop takes its colours from, as COVER x COVER raw RGB pixels: the song's cover
    art, or without one, four neighbouring colours chosen by the title, one in each corner."""
    pixels = cover(song, COVER)
    if pixels is None:
        # Somewhere from cyan round to pink: the hues gold lyrics stand out on.
        hue = 0.45 + 0.4 * zlib.crc32(title.encode()) / 2**32
        corners = [colorsys.hsv_to_rgb((hue + turn) % 1, 0.8, value)
                   for turn, value in ((0, 0.9), (0.1, 0.55), (-0.08, 0.3), (0.2, 0.8))]
        last = COVER - 1
        pixels = bytes(
            round(255 * ((corners[0][part] * (last - x) + corners[1][part] * x) * (last - y)
                         + (corners[2][part] * (last - x) + corners[3][part] * x) * y) / last ** 2)
            for y in range(COVER) for x in range(COVER) for part in range(3))
    return pixels


def artwork(pixels: bytes) -> tuple[bytes, float]:
    """The cover backdrop's picture as a PPM file, and the gain that brings it to a brightness the
    lyrics stay readable on."""
    gain = min(PEAK * 255 / max(1, *pixels), MEAN * 255 / max(1, sum(pixels) / len(pixels)), 1.5)
    return b"P6 %d %d 255\n" % (COVER, COVER) + pixels, gain


def saturate(amount: float) -> str:
    """colorchannelmixer options that multiply saturation by `amount`, around BT.601 luma as
    ffmpeg's hue filter does."""
    luma = {"r": 0.299, "g": 0.587, "b": 0.114}
    return ":".join(f"{out}{src}={(1 - amount) * weight + amount * (out == src):.4f}"
                    for out in luma for src, weight in luma.items())


def finish(layout: Layout, duration: float, fps: int, pix_fmt: str) -> str:
    """The end of either backdrop's graph: the small picture scaled up to the frame, at `fps`
    frames a second in `pix_fmt`, cut to length, with the lyrics on top."""
    return (
        # showwaves ends its stream hours after its last frame, even for a few seconds of audio,
        # and fps pads the gap with copies until ffmpeg runs out of memory. trim stops it.
        f"scale={layout.width}:{layout.height}:flags=bicubic,fps={fps},trim=duration={duration:.3f},"
        # BT.709 limited range, what players assume for HD video; the scale above converts to it, and
        # the ass filter uses it for the lyric colours because the script's header names it (without
        # that line the filter uses BT.601, as VSFilter does). The encoder takes its colour tags
        # from the frames and ignores -color_primaries and -color_trc, so setparams adds those two.
        f"format=pix_fmts={pix_fmt}:color_spaces=bt709:color_ranges=tv,"
        f"setparams=color_primaries=bt709:color_trc=bt709,"
        f"ass=lyrics.ass:fontsdir=fonts,fade=t=in:d=0.5,fade=t=out:st={duration - 1:.3f}:d=1"
    )


def backdrop(layout: Layout, duration: float, gain: float, rate: int, fps: int, pix_fmt: str) -> str:
    """The ffmpeg filter graph for the cover backdrop: input 0 (the artwork, at `rate` frames a
    second) as slowly turning blurs of colour that swell with the bass of input 1 (the karaoke
    track), then the lyrics on top, at `fps` frames a second in `pix_fmt`."""
    across, down = layout.width // SHRINK, layout.height // SHRINK
    side = ceil(hypot(across, down) / 2) * 2  # a square that covers the small frame at any angle
    loud = gain * (1 + BEAT)
    fall = 0.001 ** (1 / rate)  # the pulse dies away within a second
    # The vignette, made as two still pictures because ffmpeg's vignette filter only works in 8 bits.
    # One multiplies the colours by `fade` and also by `loud`, which sets them to their brightest.
    # The other darkens them a little more towards the corners, by up to 16/219 of full scale,
    # which is what that filter does on the limited-range YUV it was once given.
    fade = "cos(PI/5*sqrt((X-W/2)^2+(Y-H/2)^2)/sqrt((W/2)^2+(H/2)^2))^4"
    still = f"color=s={across}x{down}:r=1:d=1,format=gbrpf32le,geq=r={{0}}:g={{0}}:b={{0}}"
    return (
        # Two copies of the artwork turn opposite ways and are blurred into each other. From the
        # blur on, the colours are floating point: in 8 bits the gain and saturation magnify the
        # rounding steps, and scaling up SHRINK times turns those steps into visible blotches.
        f"[0:v]scale={side}:{side}:flags=bicubic,format=gbrp,split[near][far];"
        f"[near]rotate=a=t*0.07:ow={across}:oh={down}[near];"
        f"[far]scale={side * 3 // 2}:-1,rotate=a=2-t*0.045:ow={across}:oh={down}[far];"
        f"[near][far]blend=all_mode=average,format=gbrpf32le,gblur=sigma={across / 50:.1f},"
        f"colorchannelmixer={saturate(1.4)}[colours];"
        # The bass as one grey level per frame: its waveform is drawn as bars from the middle, so
        # the average of that drawing is how loud it is. lagfun lets the level fall off slowly.
        # The vignette sets the colours to their brightest, and silence multiplies them back down.
        f"[1:a]aformat=sample_rates=48000:channel_layouts=mono,lowpass=f=150,"
        f"showwaves=s=200x100:mode=cline:rate={rate}:scale=lin:draw=full:colors=white,format=gray,"
        f"scale=1:1:flags=area,lagfun=decay={fall:.3f},lut=c0=(255+{BEAT}*val)/{1 + BEAT},"
        f"scale={across}:{down}:flags=neighbor,format=gbrpf32le[beat];"
        f"{still.format(f'{loud:.3f}*{fade}')}[shade];{still.format(f'(1-{fade})*16/219')}[dark];"
        f"[colours][beat]blend=all_mode=multiply[lit];[lit][shade]blend=all_mode=multiply[lit];"
        f"[lit][dark]blend=all_mode=subtract,{finish(layout, duration, fps, pix_fmt)}[video]"
    )


def feed(command: list[str], frames, cwd: Path) -> None:
    """Run ffmpeg with the backdrop's frames written to its standard input. Its messages go to a
    file: a stderr pipe that nobody reads fills up and stalls ffmpeg while frames still come."""
    log = cwd / "ffmpeg.log"
    with open(log, "wb") as messages:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stderr=messages)
        try:
            for frame in frames:
                process.stdin.write(frame.tobytes())
        except OSError:
            pass  # ffmpeg stopped reading (a broken pipe); its log says whether that was an error
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
        code = process.wait()
    if code != 0:
        sys.exit(f"ffmpeg failed:\n{log.read_text(encoding='utf-8', errors='replace')[-800:]}")


def render(lines: list[Line], karaoke: Path, original: Path, duration: float, target: Path,
           layout: Layout, title: str = "", artist: str = "", codec: str = "avc", fps: int = FPS) -> None:
    """Encode the lyrics over the backdrop as an MKV with two FLAC audio tracks:
    the karaoke track (default) and the original song."""
    pix_fmt, encoder = CODECS[codec]
    rate = min(BACKDROP_FPS, fps)
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        # A Windows path inside an ffmpeg filter needs layers of escaping, so the subtitle
        # file and the font get plain names and ffmpeg runs from their folder instead.
        (work / "lyrics.ass").write_text(subtitles(lines, duration, layout, title, artist, fps), encoding="utf-8")
        (work / "fonts").mkdir()
        shutil.copy(FONT_FILE, work / "fonts")
        pixels = colours(original, title)
        if BACKDROP == "aurora":
            across, down = layout.width // SHRINK, layout.height // SHRINK
            frames = aurora.frames(across, down, duration, rate, aurora.palette(pixels), aurora.listen(karaoke, rate))
            inputs = ["-f", "rawvideo", "-pix_fmt", "gbrpf32le", "-s", f"{across}x{down}", "-framerate", str(rate),
                      "-i", "-", "-i", str(original.resolve()), "-i", str(karaoke.resolve())]
            graph, sung, sang = f"[0:v]{finish(layout, duration, fps, pix_fmt)}[video]", 2, 1
        else:
            picture, gain = artwork(pixels)
            (work / "cover.ppm").write_bytes(picture)
            frames = None
            inputs = ["-loop", "1", "-framerate", str(rate), "-t", f"{duration:.3f}", "-i", "cover.ppm",
                      "-i", str(karaoke.resolve()), "-i", str(original.resolve()),
                      # The karaoke track again, for the copy: one input feeding both the filter and the
                      # copy stalls while the encoder holds back frames, and ffmpeg runs out of memory.
                      "-i", str(karaoke.resolve())]
            graph, sung, sang = backdrop(layout, duration, gain, rate, fps, pix_fmt), 3, 2
        command = ["ffmpeg", "-hide_banner", "-nostats", "-y", *inputs,
                   "-filter_complex", graph,
                   "-map", "[video]", "-map", f"{sung}:a:0", "-map", f"{sang}:a:0",
                   *encoder, "-pix_fmt", pix_fmt,
                   # The karaoke track is already the FLAC we want; the original is brought to the same format.
                   "-c:a:0", "copy", "-c:a:1", "flac", "-ar:a:1", "48000", "-sample_fmt:a:1", "s16",
                   "-metadata:s:a:0", "title=Karaoke", "-metadata:s:a:1", "title=Original",
                   "-disposition:a:0", "default", "-disposition:a:1", "0",
                   "-shortest", str(target.resolve())]
        if frames is None:
            run(command, cwd=work)
        else:
            feed(command, frames, work)
    print(f"saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
