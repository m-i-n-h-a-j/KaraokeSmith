// Starting up: reads the lyrics and the song from the server and works out what was timed by hand.
import "./style.css";
import { frame, paint, refresh } from "./column";
import { closeEditor, save, setStep } from "./controls";
import { openEditor } from "./lyrics";
import {
  $,
  JOIN,
  PEAKS,
  SECONDS_PER_CHAR,
  audio,
  base,
  done,
  lineEnd,
  place,
  say,
  state,
  sum,
  view,
  weight,
} from "./state";
import { sketch, spans } from "./timeline";
import {
  autoLength,
  clearFromHere,
  cut,
  select,
  spread,
  tap,
  undoLast,
} from "./timing";

// The rows as the server sends them: times may still be unknown, and nothing says what was tapped.
interface Saved {
  title: string;
  duration: number;
  lines: {
    start: number | null;
    words: { text: string; start: number | null; end: number | null }[];
  }[];
}

// A loaded line whose word times are an even spread by length was estimated, not tapped.
function recognise(index: number) {
  const line = state.lines[index],
    words = line.words,
    begin = line.start!,
    length = lineEnd(line) - begin;
  const weights = words.map((word) => weight(word.text)),
    total = sum(weights);
  let used = 0;
  const even = words.every((word, k) => {
    const from = begin + (length * used) / total;
    used += weights[k];
    return (
      Math.abs(word.start! - from) < 0.0125 &&
      Math.abs(word.end! - (begin + (length * used) / total)) < 0.0125
    );
  });
  line.byWord = !even;
  words.forEach((word, k) => {
    word.cut = k + 1 < words.length && words[k + 1].start! - word.end! > JOIN;
  });
  // An end that is not where the estimate would put it was set on purpose.
  if (
    !even ||
    Math.abs(length - autoLength(index, begin, SECONDS_PER_CHAR * total)) > 0.02
  )
    line.end = begin + length;
}

// Loudness of the song over time, read from the WAV the server sends (16-bit PCM). It is the
// average level and not the peak: a loud master peaks at full scale nearly all the time, which
// would draw as a solid bar.
function loudness(buffer: ArrayBuffer) {
  const data = new DataView(buffer);
  let channels = 2,
    rate = 44100;
  for (let at = 12; at + 8 <= data.byteLength;) {
    const name = String.fromCharCode(
      data.getUint8(at),
      data.getUint8(at + 1),
      data.getUint8(at + 2),
      data.getUint8(at + 3),
    );
    const size = data.getUint32(at + 4, true);
    if (name === "fmt ") {
      channels = data.getUint16(at + 10, true);
      rate = data.getUint32(at + 12, true);
    }
    if (name === "data") {
      const samples = new Int16Array(
        buffer,
        at + 8,
        Math.min(size, data.byteLength - at - 8) >> 1,
      );
      const per = (rate * channels) / PEAKS;
      const levels = new Float64Array(Math.ceil(samples.length / per));
      for (let i = 0; i < samples.length; i++)
        levels[Math.floor(i / per)] += samples[i] * samples[i];
      const top = Math.sqrt(levels.reduce((a, b) => Math.max(a, b), 1));
      return levels.map((level) => Math.sqrt(level) / top);
    }
    at += 8 + size + (size & 1);
  }
  return null;
}

// For tests that drive the page with Playwright: the state and the functions to act on it.
Object.assign(window, {
  editor: {
    state,
    view,
    audio,
    tap,
    cut,
    undoLast,
    select,
    setStep,
    clearFromHere,
    save,
    closeEditor,
    openEditor,
    paint,
    place,
    refresh,
    spans,
  },
});

const saved: Saved = await (await fetch(base + "state")).json();
$("title").textContent = saved.title;
document.title = `${saved.title}: lyrics sync`;
state.duration = saved.duration;
$<HTMLInputElement>("seek").max = String(saved.duration);
const song = await (await fetch(base + "audio")).blob();
audio.src = URL.createObjectURL(song);
state.lines = saved.lines.map((line) => ({
  start: line.start,
  end: null,
  byWord: false,
  words: line.words.map((word) => ({ ...word, cut: false })),
}));
state.lines.forEach((line, index) => {
  if (line.start == null) return;
  // A line that has a start but no word times (line-level lyrics) gets its words spread by length.
  if (line.words.some((word) => word.start == null)) spread(index);
  else recognise(index);
});
const { lines } = state,
  untimed = lines.filter((line) => line.start == null).length,
  guessed = lines.filter((line) => !done(line)).length - untimed;
setStep(untimed || !lines.length ? "line" : guessed ? "word" : "review");
if (!lines.length) openEditor();
say(
  !lines.length
    ? "Add the lyrics to begin"
    : untimed
      ? `${untimed} of ${lines.length} lines untimed`
      : guessed
        ? `${guessed} of ${lines.length} lines have estimated word times`
        : "Every line is timed",
);
frame();
try {
  state.peaks = loudness(await song.arrayBuffer());
} catch (problem) {
  console.warn("no waveform:", problem);
}
sketch();
