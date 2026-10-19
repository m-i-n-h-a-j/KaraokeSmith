"""The aurora backdrop: ribbons of the cover's colours flowing across a dark sky, drawn here frame
by frame and piped into ffmpeg. The flow runs as fast as the words come, so a rapper's fast verse
races and a slow hook drifts, and where there are few words the loudness keeps it moving. The
ribbons burst at each beat drop, where the beat comes back hard after a quieter stretch; the sky
never brightens."""
import colorsys
import subprocess
from dataclasses import dataclass
from math import ceil, pi, sqrt

import numpy as np

from .lyrics import Line

SPEED = 0.08  # how fast the ribbons flow in silence
DRIVE = 0.25  # how much loudness speeds the flow up, where the words come slower than that
PACE, PACE_CURVE = 0.0095, 1.5  # the words speed the flow up by PACE x (letters a second) ** PACE_CURVE:
# with the curve, twice the letters is 2.8 times the speed, so a fast verse runs away from a normal one
PACE_BLUR = 1.0  # seconds: the spread of the bell curve the words' pace is averaged over
SURGE = 0.6  # how far the flow lurches forward at a drop
SWELL = 0.6  # how much wider the ribbons get at a drop
LIGHT = 1.0  # how much brighter the ribbons get at a drop
ATTACK, FADE = 0.04, 0.6  # seconds a drop's burst takes to come on, and its time constant dying away
GLOW = 0.62  # overall brightness, which keeps the lyrics readable
SHADE = 0.38  # how much darker the band behind the lyrics is
RATE = 48000
HOP, SPAN = 240, 1024  # the prescan looks at the bass every 5 ms, through a 21 ms window
DROP_RISE = 2.0  # a drop's bass is at least this many times as strong as in the 4 s before it
DROP_GAP = 8.0  # seconds between drops at the least


@dataclass
class Sound:
    """What the backdrop follows: for each backdrop frame, how far the ribbons have flowed by then
    on top of SPEED; and each beat drop's time and strength (0.5 to 1)."""
    flow: np.ndarray
    drops: list[tuple[float, float]]


def decode(path, filters: str = "anull") -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-af", filters, "-ac", "1", "-ar", str(RATE),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32)


def find_drops(mix: np.ndarray, bass: np.ndarray) -> list[tuple[float, float]]:
    """The beat drops in a track, scanned ahead of the render: onsets of the kick (rises in the
    30-150 Hz band, every 5 ms) where the bass over the next 2 s is loud for this song and at least
    DROP_RISE times what it was over the 4 s before. Each is then moved back to where its bass
    starts to rise: the band's onsets fire halfway up the attack, about 0.1 s late on a soft 808."""
    count = 1 + (len(mix) - SPAN) // HOP
    if count < 1000:
        return []
    window = np.hanning(SPAN).astype(np.float32)
    low = (np.fft.rfftfreq(SPAN, 1 / RATE) >= 30) & (np.fft.rfftfreq(SPAN, 1 / RATE) <= 150)
    frames = np.lib.stride_tricks.sliding_window_view(mix, SPAN)
    flux, level, last = np.zeros(count, np.float32), np.zeros(count, np.float32), None
    for start in range(0, count, 4096):  # in blocks, to keep the memory down
        rows = np.arange(start, min(count, start + 4096))
        magnitude = np.abs(np.fft.rfft(frames[rows * HOP] * window, axis=1))[:, low]
        level[rows] = np.sqrt((magnitude ** 2).mean(1))
        heard = np.log1p(100 * magnitude)
        flux[rows] = np.maximum(0, heard - np.vstack([heard[:1] if last is None else last, heard[:-1]])).sum(1)
        last = heard[-1:]
    times = (np.arange(count) * HOP + SPAN / 2) / RATE
    near = np.lib.stride_tricks.sliding_window_view(np.pad(flux, 20, mode="edge"), 41).max(1)  # 0.1 s either side
    usual = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(flux, 100, mode="edge"), 201), axis=1)
    onsets = np.where((flux >= near) & (flux > usual + 0.5 * flux.std()))[0]
    energy = np.convolve(level, np.ones(50) / 50, mode="same")  # over 0.25 s
    loud = np.percentile(energy, 90)
    found = []
    for i in onsets:
        if i < 140 or i + 400 > count:  # a beat that starts the song still counts, 0.7 s in or later
            continue
        before, after = energy[max(0, i - 800):i - 40].mean(), energy[i:i + 400].mean()
        rise = after / (before + 0.02 * loud)
        if after > 0.55 * loud and rise > DROP_RISE:
            found.append((times[i], rise))
    drops = []
    for at, rise in sorted(found, key=lambda drop: -drop[1]):  # the strongest of each stretch
        if all(abs(at - kept) > DROP_GAP for kept, _ in drops):
            drops.append((at, rise))
    envelope = np.convolve(np.abs(bass), np.ones(48) / 48, mode="same")  # 1 ms
    refined = []
    for at, rise in sorted(drops):
        s, back = int(at * RATE), int(0.15 * RATE)
        calm = np.median(envelope[max(0, s - int(0.4 * RATE)):max(1, s - back)])
        top = envelope[s:s + int(0.1 * RATE)].max()
        search = envelope[max(0, s - back):s + int(0.05 * RATE)]
        start = int(np.argmax(search > calm + 0.2 * (top - calm)))
        refined.append(((max(0, s - back) + start) / RATE, float(np.clip((rise - DROP_RISE) / 6 + 0.5, 0.5, 1))))
    return refined


def pace(lines: list[Line], count: int, rate: int) -> np.ndarray:
    """How fast the words come at each backdrop frame, in letters a second: each word's letters
    counted at its start and spread over a bell curve PACE_BLUR seconds wide either side, so the
    flow picks up just ahead of a fast verse and eases off after it instead of stopping at every
    breath. Letters, not words, because a fast verse packs in long words as well as more of them."""
    letters = np.zeros(count)
    for line in lines:
        for word in line:
            if 0 <= word.start * rate < count:
                letters[int(word.start * rate)] += sum(c.isalnum() for c in word.text)
    reach = int(3 * PACE_BLUR * rate)
    bell = np.exp(-0.5 * (np.arange(-reach, reach + 1) / (PACE_BLUR * rate)) ** 2)
    return np.convolve(np.pad(letters, reach), bell / bell.sum(), mode="valid") * rate


def listen(path, rate: int, lines: list[Line]) -> Sound:
    """Scan a track ahead of the render: the karaoke track when there is one, since its beat is
    not masked by the vocals. The flow runs at whichever is faster, the pace of the lyrics or the
    drive of the loudness."""
    mix, bass = decode(path), decode(path, "lowpass=f=150")
    hop = RATE / rate
    count = int(len(mix) / hop)
    edges = (np.arange(count + 1) * hop).astype(int)
    loud = np.array([np.sqrt((mix[a:b] ** 2).mean()) for a, b in zip(edges, edges[1:])])
    swell, keep = np.zeros(count), np.exp(-1 / (rate * 1.5))
    target = np.clip((loud - 0.05) / 0.35, 0, 1)
    for i in range(count):
        swell[i] = target[0] if i == 0 else swell[i - 1] * keep + target[i] * (1 - keep)
    speed = np.maximum(DRIVE * swell, PACE * pace(lines, count, rate) ** PACE_CURVE)
    return Sound(np.cumsum(speed) / rate, find_drops(mix, bass))


def palette(pixels: bytes) -> tuple[np.ndarray, list[np.ndarray]]:
    """A dark sky and three bright colours from the cover (raw RGB pixels): the strongest hues,
    weighted by how saturated and bright they are, pushed to full colour."""
    rgb = np.frombuffer(pixels, np.uint8).reshape(-1, 3) / 255
    hsv = np.array([colorsys.rgb_to_hsv(*c) for c in rgb])
    weight = hsv[:, 1] ** 2 * hsv[:, 2]
    bins = (hsv[:, 0] * 12).astype(int) % 12
    ranked = []
    for b in np.argsort(-np.bincount(bins, weight, 12)):
        chosen = bins == b
        if weight[chosen].sum() > 1:
            ranked.append(colorsys.rgb_to_hsv(*(rgb[chosen] * weight[chosen, None]).sum(0) / weight[chosen].sum()))
    if not ranked:
        ranked.append(colorsys.rgb_to_hsv(*rgb.mean(0)))
    while len(ranked) < 3:
        ranked.append(((ranked[0][0] + 0.12 * len(ranked)) % 1, 0.7, 0.9))
    hue, saturation, _ = colorsys.rgb_to_hsv(*rgb.mean(0))
    sky = np.array(colorsys.hsv_to_rgb(hue, min(saturation, 0.6), 0.07))
    return sky, [np.array(colorsys.hsv_to_rgb(h, max(s, 0.55), max(v, 0.85))) for h, s, v in ranked[:3]]


def erf(x: np.ndarray) -> np.ndarray:
    """Abramowitz and Stegun 7.1.26, good to 1.5e-7."""
    t = 1 / (1 + 0.3275911 * np.abs(x))
    y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return np.sign(x) * y


def burst(drops: list[tuple[float, float]], t: float) -> tuple[float, float]:
    """How lit the ribbons are at time t (0 to 1), and how far all drops so far have pushed the flow."""
    lit, push = 0.0, 0.0
    for at, strength in drops:
        since = t - at
        if since < 0:
            break
        lit = max(lit, strength * min(since / ATTACK, np.exp(-(since - ATTACK) / FADE)))
        push += strength * (1 - np.exp(-since / 0.35))
    return lit, push


def frames(width: int, height: int, duration: float, rate: int, colours, sound: Sound):
    """The backdrop at width x height, `rate` frames a second, as planar float GBR frames
    (gbrpf32le), ready to be scaled up to the video's size."""
    sky, accents = colours
    pad = 24 * max(width, height) / 320  # the ribbons run on past the edges
    tall = height + 2 * pad
    swing = min(tall, 0.75 * (width + 4 * pad))  # how far they wave: in a tall frame, by its width, or they zigzag
    soft = width / 28  # the softness of a ribbon's edge
    x = np.arange(width)[None, :] + 0.5
    y = np.arange(height)[:, None] + 0.5
    u = (x + 2 * pad) / (width + 4 * pad)
    du = 1 / (width + 4 * pad)
    # cos(pi/5 r)^4 from the middle to the corners, pressed a little harder than the cover's.
    r = np.hypot(x - width / 2, y - height / 2) / np.hypot(width / 2, height / 2)
    shade = 1 - (1 - np.cos(pi / 5 * r) ** 4) * 1.2
    shade *= 1 - SHADE * np.clip(1 - np.abs(y / height - 0.5) / 0.3, 0, 1)  # the band behind the lyrics
    luma = np.array([0.2126, 0.7152, 0.0722])
    for frame in range(ceil(duration * rate)):
        t = frame / rate
        # Half a frame early, so the frame nearest a drop is the one that lights up: a picture
        # that lags the sound is noticed sooner than one that leads it.
        lit, push = burst(sound.drops, t + 0.5 / rate)
        phase = SPEED * t + sound.flow[min(frame, len(sound.flow) - 1)] + SURGE * push
        picture = np.broadcast_to(sky[:, None, None], (3, height, width)).copy()
        for n, colour in enumerate(accents):
            turn = u * 5 + phase * (1.3 + 0.4 * n) + 2 * n
            wobble = u * 11 - phase * 0.7 + n
            middle = tall * (0.3 + 0.2 * n) + np.sin(turn) * swing * 0.16 + np.sin(wobble) * swing * 0.05 - pad
            slope = (np.cos(turn) * 5 * swing * 0.16 + np.cos(wobble) * 11 * swing * 0.05) * du
            half = tall * (0.18 + 0.06 * np.sin(phase + n)) * (1 + SWELL * lit) / 2
            away = np.abs(y - middle) / np.sqrt(1 + slope ** 2)  # distance across the ribbon
            body = 0.5 * (erf((half - away) / (soft * sqrt(2))) + erf((half + away) / (soft * sqrt(2))))
            alpha = np.clip(0.85 * (1 + LIGHT * lit) * body, 0, 1)
            picture = 1 - (1 - picture) * (1 - alpha[None] * colour[:, None, None])  # screen blend
        grey = np.tensordot(luma, picture, 1)
        picture = np.clip(grey + 1.2 * (picture - grey), 0, 1) * GLOW * shade
        yield picture[[1, 2, 0]].astype(np.float32)  # planar G, B, R
