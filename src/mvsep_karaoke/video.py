"""Render a karaoke lyrics video: a scrolling column of lyrics over slowly turning colours taken
from the song's cover art, the row being sung held in the centre and filled word by word."""
import colorsys
import random
import tempfile
import zlib
from dataclasses import dataclass
from math import ceil, hypot
from pathlib import Path

from .lyrics import Line
from .media import cover, run

FPS = 60
FONT = "Segoe UI"
SUNG, PLAIN = "00D7FF", "FFFFFF"  # ASS colours are BBGGRR: gold, white
SCROLL = 0.35  # seconds one scroll step takes
LEAD = 0.25  # seconds before its first word that a row scrolls into the centre
GAP = 4.0  # a pause longer than this scrolls the next row in early and counts it in
LINGER = 1.0  # seconds a finished row stays in the centre before such a pause
COUNT_IN = 3.0  # seconds of count-in dots
POP = 105  # percent of its size a row swells to as it lands in the centre
GLOW_FADE = 450  # milliseconds a word's glow takes to die away once the word is over

# The backdrop is all blur, so it is worked out small and at half the frame rate, then blown up.
SHRINK, BACKDROP_FPS = 6, 30
COVER = 64  # pixels a side the cover art is cut down to before it is blurred into the backdrop
PEAK, MEAN = 0.36, 0.18  # brightest spot and average the backdrop is held to, so lyrics stay readable
BEAT = 0.5  # how much brighter the loudest bass makes the backdrop: 0.5 is up to about a third


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

    @property
    def edge(self) -> int:
        """Thickness of the progress line along the bottom."""
        return self.font_size // 14


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


def glow_text(row: Line, appear: float) -> str:
    """The same row for the glow behind it: each word's halo comes on as the word is sung and dies
    away after, so the brightest spot on screen is always the word to sing now."""
    parts = []
    for word in row:
        start = max(0, round((word.start - appear) * 1000))
        end = max(start + 10, round((word.end - appear) * 1000))
        lit = f"\\t({max(0, start - 40)},{start + 60},\\3a&H30&)\\t({end},{end + GLOW_FADE},\\3a&HFF&)"
        parts.append(f"{{\\3a&HFF&{lit}}}{clean(word.text)} ")
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


def event(start: float, end: float, text: str, layer: int = 2) -> str:
    """Layers from the back: 0 specks and the progress line, 1 glow, 2 everything to read."""
    return f"Dialogue: {layer},{timestamp(start)},{timestamp(end)},Karaoke,,0,0,0,,{text}"


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
    # Shrunk if needed so a long title still fits the width.
    size = round(min(layout.font_size * 1.3, 0.9 * layout.width / (0.68 * len(title))))
    leave = round((until - start) * 1000)
    settle = (f"\\blur8\\fsp{round(size * 0.2)}\\t(0,900,0.4,\\blur0\\fsp0)"
              f"\\t({leave - 500},{leave},\\blur8)")
    heading = f"\\pos({middle},{y - round(rows * 0.7)})\\fs{size}\\1c&H{PLAIN}&\\fad(500,500)"
    events = [event(start, until, f"{{{heading}{settle}}}{clean(title)}")]
    if artist:
        span, thick = round(layout.width * 0.06), max(2, layout.edge // 2)
        rule = f"\\an5\\pos({middle},{y - round(rows * 0.32)})\\1c&H{SUNG}&\\fad(0,500)\\fscx1\\t(200,1000,0.4,\\fscx100)\\p1"
        events.append(event(start + 0.2, until, f"{{{rule}}}m 0 0 l {span * 2} 0 {span * 2} {thick} 0 {thick}"))
        byline = f"\\pos({middle},{y})\\fs{round(layout.font_size * 0.6)}\\1c&H{SUNG}&\\fad(600,500)"
        events.append(event(start + 0.4, until, f"{{{byline}}}{clean(artist)}"))
    return events


def progress(layout: Layout, duration: float) -> list[str]:
    """A line along the bottom edge that fills with gold as the song plays."""
    line = f"m 0 0 l {layout.width} 0 {layout.width} {layout.edge} 0 {layout.edge}"
    where = f"\\an7\\pos(0,{layout.height - layout.edge})\\p1"
    fill = f"\\fscx0\\t(0,{round(duration * 1000)},\\fscx100)"
    return [event(0, duration, f"{{{where}\\1c&H{PLAIN}&\\alpha&HD8&}}{line}", layer=0),
            event(0, duration, f"{{{where}\\1c&H{SUNG}&{fill}}}{line}", layer=0)]


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
    # The column stays to the end, unless the song plays on long after the last word: then it
    # fades out and the title comes back.
    leaving = duration - rows[-1][-1].end > GAP
    turns.append(max(rows[-1][-1].end + LINGER + 0.8 if leaving else duration, turns[-1] + 0.05))

    events = specks(layout, duration) + progress(layout, duration)
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
                # The glow is the same row moving the same way, with only a blurred gold outline showing.
                halo = (f"{tags}\\1a&HFF&\\3c&H{SUNG}&\\bord{layout.font_size * 0.04:.1f}"
                        f"\\blur{layout.font_size * 0.13:.1f}")
                events.append(event(begin, until, f"{{{halo}}}{glow_text(row, begin)}", layer=1))

        if counted[index]:
            first = row[0].start
            events += count_in(max(turns[index], first - COUNT_IN), first, middle,
                               centre - round(layout.row_height * 0.53), round(layout.font_size * 0.17))

    intro = min(rows[0][0].start - 0.5, 6.0)
    if title and intro > 2.0:  # above the waiting first row
        events += title_card(title, artist, 0, intro, centre - round(layout.row_height * 1.5), layout)
    if title and leaving:  # in the middle of the frame the lyrics have left
        events += title_card(title, artist, turns[-1], duration, centre + round(layout.row_height * 0.35), layout)
    return header(layout) + "\n".join(events) + "\n"


def artwork(song: Path, title: str) -> tuple[bytes, float]:
    """The picture the backdrop is made from, as a PPM file, and the gain that brings it to a
    brightness the lyrics stay readable on. It is the song's cover art, or without one, four
    neighbouring colours chosen by the title, one in each corner."""
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
    gain = min(PEAK * 255 / max(1, *pixels), MEAN * 255 / max(1, sum(pixels) / len(pixels)), 1.5)
    return b"P6 %d %d 255\n" % (COVER, COVER) + pixels, gain


def backdrop(layout: Layout, duration: float, gain: float) -> str:
    """The ffmpeg filter graph for the picture: input 0 (the artwork) as slowly turning blurs of
    colour that swell with the bass of input 1 (the karaoke track), then the lyrics on top."""
    across, down = layout.width // SHRINK, layout.height // SHRINK
    side = ceil(hypot(across, down) / 2) * 2  # a square that covers the small frame at any angle
    loud = gain * (1 + BEAT)
    fall = 0.001 ** (1 / BACKDROP_FPS)  # the pulse dies away within a second
    return (
        # Two copies of the artwork turn opposite ways and are blurred into each other.
        f"[0:v]scale={side}:{side}:flags=bicubic,format=gbrp,split[near][far];"
        f"[near]rotate=a=t*0.07:ow={across}:oh={down}[near];"
        f"[far]scale={side * 3 // 2}:-1,rotate=a=2-t*0.045:ow={across}:oh={down}[far];"
        f"[near][far]blend=all_mode=average,gblur=sigma={across / 50:.1f},"
        f"lutrgb=r=val*{loud:.3f}:g=val*{loud:.3f}:b=val*{loud:.3f}[colours];"
        # The bass as one grey level per frame: its waveform is drawn as bars from the middle, so
        # the average of that drawing is how loud it is. lagfun lets the level fall off slowly.
        # The colours are set to their brightest above, and silence multiplies them back down.
        f"[1:a]aformat=sample_rates=48000:channel_layouts=mono,lowpass=f=150,"
        f"showwaves=s=200x100:mode=cline:rate={BACKDROP_FPS}:scale=lin:draw=full:colors=white,format=gray,"
        f"scale=1:1:flags=area,lagfun=decay={fall:.3f},lut=c0=(255+{BEAT}*val)/{1 + BEAT},"
        f"scale={across}:{down}:flags=neighbor,format=gbrp[beat];"
        f"[colours][beat]blend=all_mode=multiply,hue=s=1.4,"
        f"vignette=a=PI/5,scale={layout.width}:{layout.height}:flags=bicubic,fps={FPS},format=yuv420p,"
        f"ass=lyrics.ass,fade=t=in:d=0.5,fade=t=out:st={duration - 1:.3f}:d=1[video]"
    )


def render(lines: list[Line], karaoke: Path, original: Path, duration: float, target: Path,
           layout: Layout, title: str = "", artist: str = "") -> None:
    """Encode the lyrics over the backdrop as an MKV with two FLAC audio tracks:
    the karaoke track (default) and the original song."""
    with tempfile.TemporaryDirectory() as folder:
        # A Windows path inside an ffmpeg filter needs layers of escaping, so the subtitle
        # file gets a plain name and ffmpeg runs from its folder instead.
        script = subtitles(lines, duration, layout, title, artist)
        (Path(folder) / "lyrics.ass").write_text(script, encoding="utf-8")
        picture, gain = artwork(original, title)
        (Path(folder) / "cover.ppm").write_bytes(picture)
        run(
            ["ffmpeg", "-hide_banner", "-nostats", "-y",
             "-loop", "1", "-framerate", str(BACKDROP_FPS), "-t", f"{duration:.3f}", "-i", "cover.ppm",
             "-i", str(karaoke.resolve()), "-i", str(original.resolve()),
             "-filter_complex", backdrop(layout, duration, gain),
             "-map", "[video]", "-map", "1:a:0", "-map", "2:a:0",
             "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
             # The karaoke track is already the FLAC we want; the original is brought to the same format.
             "-c:a:0", "copy", "-c:a:1", "flac", "-ar:a:1", "48000", "-sample_fmt:a:1", "s16",
             "-metadata:s:a:0", "title=Karaoke", "-metadata:s:a:1", "title=Original",
             "-disposition:a:0", "default", "-disposition:a:1", "0",
             "-shortest", str(target.resolve())],
            cwd=Path(folder),
        )
    print(f"saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
