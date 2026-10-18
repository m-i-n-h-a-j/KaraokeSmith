// The lyric column: one row per line, filling gold as the song plays.
import { $, audio, clock, done, state } from "./state";
import { forget, sketch } from "./timeline";
import { select } from "./timing";

let rows: { row: HTMLDivElement; words: HTMLSpanElement[] }[] = []; // the column's elements
export let marked: number | null = null; // row that carries the highlight

// Rebuild the lyric column. Cheap enough to do after every change.
export function draw() {
  const { lines, step, cursor, wordCursor } = state;
  forget();
  marked = null;
  rows = lines.map((line, index) => {
    const row = document.createElement("div");
    row.className =
      "line " +
      (line.start == null ? "untimed" : done(line) ? "tapped" : "guess");
    row.onclick = () => select(index);
    const stamp = document.createElement("span");
    stamp.className = "stamp";
    stamp.textContent = line.start == null ? "--:--.--" : clock(line.start);
    const text = document.createElement("span");
    text.className = "words";
    const words = line.words.map((word, k) => {
      const span = document.createElement("span");
      span.className =
        "word" +
        (step === "word" && index === cursor && k === wordCursor
          ? " next"
          : "");
      span.textContent = word.text;
      span.onclick = (event) => {
        event.stopPropagation();
        select(index, k);
      };
      text.append(span, " ");
      return span;
    });
    row.append(stamp, text);
    return { row, words };
  });
  $("lines").replaceChildren(...rows.map((entry) => entry.row));
  $("lineCount").textContent =
    `${lines.filter((line) => line.start != null).length}/${lines.length}`;
  $("wordCount").textContent = `${lines.filter(done).length}/${lines.length}`;
  paint();
}

function lineAt(t: number) {
  let found = -1;
  state.lines.forEach((line, index) => {
    if (line.start != null && line.start <= t) found = index;
  });
  return found;
}

// Show the fill as the renderer will: words turn gold as the playhead passes them. The marked
// row is the one to tap next, or in the review step the one being sung.
export function paint() {
  const { lines, step, cursor, duration } = state;
  if (state.closed) return;
  const t = audio.currentTime;
  $("clock").textContent = `${clock(t)} / ${clock(duration)}`;
  const seek = $<HTMLInputElement>("seek");
  if (document.activeElement !== seek) seek.value = String(t);
  const want =
    step === "review" ? lineAt(t + 0.3) : cursor < lines.length ? cursor : -1;
  if (want !== marked) {
    if (marked != null) rows[marked]?.row.classList.remove("target");
    rows[want]?.row.classList.add("target");
    // Not scrollIntoView: that also scrolls the page itself when it does not fit the window,
    // which on a phone pushed the header off the screen.
    const row = (rows[want] ?? rows[step === "review" ? 0 : rows.length - 1])
        ?.row,
      column = $("lines");
    if (row)
      column.scrollTo({
        top: row.offsetTop + row.offsetHeight / 2 - column.clientHeight / 2,
      });
    marked = want;
  }
  lines.forEach((line, index) =>
    line.words.forEach((word, k) => {
      const span = rows[index]?.words[k];
      if (!span) return;
      const { start, end } = word;
      const active = start != null && t >= start && t < end!;
      span.classList.toggle("sung", start != null && t >= end!);
      span.classList.toggle("active", active);
      if (active)
        span.style.setProperty(
          "--fill",
          `${(100 * (t - start)) / (end! - start)}%`,
        );
    }),
  );
  sketch();
}

export function refresh() {
  forget();
  paint();
}

export function frame() {
  if (!audio.paused) paint();
  requestAnimationFrame(frame);
}
