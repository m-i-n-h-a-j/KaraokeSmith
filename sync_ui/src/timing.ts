// Timing lines and words: taps, cuts and undo, and the estimate that spreads a line's words by length.
import { draw } from "./column";
import { toggle } from "./controls";
import {
  $,
  MIN,
  SECONDS_PER_CHAR,
  audio,
  clamp,
  done,
  type Line,
  say,
  state,
  sum,
  weight,
} from "./state";

export function nextStart(index: number) {
  for (const line of state.lines.slice(index + 1))
    if (line.start != null) return line.start;
  return null;
}

export function previousTimed(index: number) {
  for (let i = index - 1; i >= 0; i--)
    if (state.lines[i].start != null) return i;
  return -1;
}

// How long words last when nothing says where they end: their estimated length, stretched or
// squeezed towards the start of the next line.
export function autoLength(index: number, begin: number, guess: number) {
  return Math.max(
    0.5 * guess,
    Math.min(1.5 * guess, (nextStart(index) ?? Infinity) - begin),
  );
}

// Time the words of a line from word `from` on, by length, up to the line's end or the next line.
export function spread(index: number, from = 0) {
  const line = state.lines[index];
  if (line.start == null) return;
  const rest = line.words.slice(from);
  const begin = from === 0 ? line.start : rest[0].start!;
  const weights = rest.map((word) => weight(word.text));
  const total = sum(weights);
  if (line.end != null && line.end - begin < MIN * rest.length) line.end = null; // the known end is already behind
  const length =
    line.end != null
      ? line.end - begin
      : autoLength(index, begin, SECONDS_PER_CHAR * total);
  let used = 0;
  rest.forEach((word, k) => {
    word.start = begin + (length * used) / total;
    used += weights[k];
    word.end = begin + (length * used) / total;
    word.cut = false;
  });
}

export function shift(line: Line, delta: number) {
  const move = (t: number | null) => (t == null ? t : Math.max(0, t + delta));
  line.start = move(line.start);
  line.end = move(line.end);
  for (const word of line.words) {
    word.start = move(word.start);
    word.end = move(word.end);
  }
}

// Taps rewind the song a little when they are undone; other changes leave it where it is.
export function remember(rewind = false) {
  const { lines, cursor, wordCursor } = state;
  state.undo.push({
    snapshot: JSON.stringify({ lines, cursor, wordCursor }),
    time: rewind ? audio.currentTime : null,
  });
  if (state.undo.length > 1000) state.undo.shift();
  state.dirty = true;
  state.closeArmed = false;
  $("save").classList.add("unsaved");
  say("");
}

// Line `index` has a new start: the timed line before it cannot run past that.
function settleBefore(index: number) {
  const at = previousTimed(index);
  if (at < 0) return;
  const before = state.lines[at],
    t = state.lines[index].start!;
  if (!before.byWord) {
    if (before.end != null && before.end > t) before.end = t;
    return spread(at);
  }
  const last = before.words[before.words.length - 1];
  if (last.end! > t) {
    last.end = Math.max(last.start! + MIN, t);
    if (before.end != null) before.end = last.end;
  }
}

export function tap() {
  const { step, lines, cursor, wordCursor } = state;
  if (step === "review") return toggle();
  if (cursor >= lines.length)
    return say("Every line is timed. Click a line to time it again.");
  const t = audio.currentTime,
    line = lines[cursor],
    words = line.words;
  const floor =
    step === "word" && wordCursor > 0
      ? words[wordCursor - 1].start
      : lines[previousTimed(cursor)]?.start;
  if (floor != null && t <= floor)
    return say(
      "Not timed: this is earlier than the timing before it. Click the line you want to time again.",
    );
  remember(true);
  if (step === "line") {
    if (line.byWord && line.start != null) {
      shift(line, t - line.start); // words tapped by hand keep their rhythm
    } else {
      Object.assign(line, { start: t, end: null, byWord: false });
      spread(cursor);
    }
    settleBefore(cursor);
    state.cursor++;
  } else {
    line.byWord = true;
    if (wordCursor === 0) {
      line.start = t;
      settleBefore(cursor);
    } else {
      const before = words[wordCursor - 1];
      before.end = before.cut ? Math.min(before.end!, t) : t;
    }
    words[wordCursor].start = t;
    spread(cursor, wordCursor); // the words still to tap get a provisional time
    if (++state.wordCursor >= words.length) {
      state.cursor++;
      state.wordCursor = 0;
    }
  }
  draw();
}

// End the last tapped line or word at the current time instead of at the next tap.
export function cut() {
  const { step, lines, cursor, wordCursor } = state;
  if (step === "review") return;
  const mid = step === "word" && wordCursor > 0;
  const index = mid ? cursor : cursor - 1;
  const line = lines[index];
  if (!line || line.start == null) return;
  const words = line.words,
    word = mid ? words[wordCursor - 1] : words[words.length - 1];
  const single = mid || line.byWord; // one word ends here, not a whole estimated line
  const t = Math.min(
    audio.currentTime,
    mid ? Infinity : (nextStart(index) ?? Infinity),
  );
  if (t < (single ? word.start! : line.start) + MIN) return;
  remember();
  if (!single) {
    line.end = t;
    spread(index);
  } else {
    line.byWord = true;
    Object.assign(word, { end: t, cut: true });
    if (mid) {
      words[wordCursor].start = t;
      spread(cursor, wordCursor);
    } else {
      line.end = t;
    }
  }
  draw();
}

export function undoLast() {
  const last = state.undo.pop();
  if (!last) return say("Nothing to undo.");
  Object.assign(state, JSON.parse(last.snapshot));
  if (last.time != null) audio.currentTime = Math.max(0, last.time - 1.5);
  draw();
}

// Make a line, or in the words step one of its words, the next thing to tap.
export function select(index: number, k = 0) {
  const line = state.lines[index];
  if (state.step === "review") {
    if (line.start != null) audio.currentTime = Math.max(0, line.start - 0.5);
    return;
  }
  state.cursor = index;
  state.wordCursor =
    state.step === "word" && line.words[k].start != null ? k : 0;
  const at = state.wordCursor ? line.words[state.wordCursor].start : line.start;
  if (at != null) audio.currentTime = Math.max(0, at - 2);
  draw();
}

// Up and down: to the start of the line being tapped, then the line before, or the line after.
export function move(delta: number) {
  const { lines, step, cursor, wordCursor } = state;
  if (!lines.length) return;
  if (step === "review") {
    const t = audio.currentTime,
      starts = lines.flatMap((line) =>
        line.start == null ? [] : [line.start],
      );
    const start =
      delta > 0
        ? starts.find((start) => start > t + 0.6)
        : starts.findLast((start) => start < t - 0.6);
    if (start != null) audio.currentTime = Math.max(0, start - 0.5);
    return;
  }
  if (delta < 0 && wordCursor > 0) return select(cursor);
  select(clamp(cursor + delta, 0, lines.length - 1));
}

export function shiftAll(delta: number) {
  remember();
  for (const line of state.lines) shift(line, delta);
  draw();
}

export function clearFromHere() {
  if (state.cursor >= state.lines.length) return;
  remember();
  for (const line of state.lines.slice(state.cursor)) {
    Object.assign(line, { start: null, end: null, byWord: false });
    for (const word of line.words)
      Object.assign(word, { start: null, end: null, cut: false });
  }
  state.wordCursor = 0;
  draw();
}

// Where the current step has work left: the first line it has not dealt with.
export function todo() {
  const index = state.lines.findIndex((line) =>
    state.step === "word" ? !done(line) : line.start == null,
  );
  return index < 0 ? state.lines.length : index;
}
