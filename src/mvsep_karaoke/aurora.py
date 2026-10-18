"""The aurora backdrop: ribbons of the cover's colours flowing across a dark sky, drawn here frame
by frame and piped into ffmpeg. The flow runs faster in loud passages, and every beat makes the
ribbons swell, light up and lurch forward; the sky itself never brightens."""
import colorsys
import subprocess
from dataclasses import dataclass
from math import ceil, pi, sqrt

import numpy as np

SPEED = 0.08  # how fast the ribbons flow in silence
DRIVE = 0.35  # how much loudness speeds the flow up
SURGE = 0.12  # how far the flow lurches forward on each beat
SWELL = 0.35  # how much wider a ribbon gets on a beat
LIGHT = 0.6  # how much brighter a ribbon gets on a beat
ATTACK, FADE = 0.03, 0.22  # seconds a beat takes to come on, and to die away
GLOW = 0.62  # overall brightness, which keeps the lyrics readable
SHADE = 0.38  # how much darker the band behind the lyrics is
RATE = 48000


@dataclass
class Sound:
    """What the backdrop follows: for each backdrop frame, the loudness of the whole mix (slowly
    smoothed, 0 to 1) added up over the song so far, in seconds; and the times of the beats."""
    loudness: np.ndarray
    beats: np.ndarray


def decode(path, filters: str) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-af", filters, "-ac", "1", "-ar", str(RATE),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32)


def listen(path, rate: int) -> Sound:
    """Analyse the karaoke track. A beat is a jump in the bass (below 150 Hz, the band the old
    pulse followed) to 1.2 times its level over the quarter second before. On 40 s of GIVE ME A
    REASON that found 52 beats, half a second apart at the median; at 1.35 times it missed every
    other one (31)."""
    hop = RATE / rate
    bass, mix = decode(path, "lowpass=f=150"), decode(path, "anull")
    count = int(len(mix) / hop)
    edges = (np.arange(count + 1) * hop).astype(int)
    level = np.array([np.abs(bass[a:b]).mean() for a, b in zip(edges, edges[1:])])
    loud = np.array([np.sqrt((mix[a:b] ** 2).mean()) for a, b in zip(edges, edges[1:])])
    window, beats, last = max(2, round(0.27 * rate)), [], -1.0
    for i in range(window, count):
        if (level[i] > 0.06 and level[i] > 1.2 * level[i - window:i].mean()
                and level[i] - level[i - 1] > 0.01 and i / rate - last > 0.22):
            last = i / rate
            beats.append(last)
    swell, keep = np.zeros(count), np.exp(-1 / (rate * 1.5))
    target = np.clip((loud - 0.05) / 0.35, 0, 1)
    for i in range(count):
        swell[i] = target[0] if i == 0 else swell[i - 1] * keep + target[i] * (1 - keep)
    return Sound(np.cumsum(swell) / rate, np.array(beats))


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
    beats = sound.beats
    for frame in range(ceil(duration * rate)):
        t = frame / rate
        past = t - beats[beats <= t]
        lit = 0.0 if not len(past) else min(past.min() / ATTACK, np.exp(-(past.min() - ATTACK) / FADE))
        loudness = sound.loudness[min(frame, len(sound.loudness) - 1)]
        phase = SPEED * t + DRIVE * loudness + SURGE * (1 - np.exp(-past / 0.18)).sum()
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
