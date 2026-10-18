// The "Edit lyrics" box: changing the text while the rows that stay keep their timing.
import { draw } from "./column";
import { $, audio, type Line, lineEnd, state, textOf } from "./state";
import { remember, spread, todo } from "./timing";

const box = () => $<HTMLTextAreaElement>("text");

export function openEditor() {
  box().value = state.lines.map(textOf).join("\n");
  document.body.classList.add("editing");
  audio.pause();
  box().focus();
}

// A row whose text changed. Words tapped by hand keep their times if there are still as many;
// otherwise the line keeps its place in the song and its words are estimated again.
function reword(old: Line | null, text: string): Line {
  const texts = text.split(" ");
  if (old?.byWord && old.words.length === texts.length) {
    return {
      ...old,
      words: old.words.map((word, k) => ({ ...word, text: texts[k] })),
    };
  }
  const words = texts.map((word) => ({
    text: word,
    start: null,
    end: null,
    cut: false,
  }));
  return {
    start: old?.start ?? null,
    end: old?.start != null ? lineEnd(old) : null,
    byWord: false,
    words,
  };
}

export function applyLyrics() {
  const texts = box()
    .value.split("\n")
    .map((row) => row.trim().replace(/\s+/g, " "))
    .filter(Boolean);
  const old = state.lines,
    was = old.map(textOf),
    n = was.length,
    m = texts.length;
  remember();
  // The longest run of rows that are still there, in order, keeps its timing untouched.
  const common = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      common[i][j] =
        was[i] === texts[j]
          ? common[i + 1][j + 1] + 1
          : Math.max(common[i + 1][j], common[i][j + 1]);
    }
  }
  const kept: [number, number][] = [];
  for (let i = 0, j = 0; i < n && j < m;) {
    if (was[i] === texts[j]) kept.push([i++, j++]);
    else if (common[i + 1][j] >= common[i][j + 1]) i++;
    else j++;
  }
  kept.push([n, m]);
  const lines: Line[] = [];
  let i = 0,
    j = 0;
  for (const [keptOld, keptNew] of kept) {
    // Between two kept rows, as many rows as before means they were reworded, not added or removed.
    const reworded = keptOld - i === keptNew - j;
    for (; j < keptNew; i++, j++)
      lines.push(reword(reworded ? old[i] : null, texts[j]));
    if (keptOld < n) lines.push(old[keptOld]);
    i = keptOld + 1;
    j = keptNew + 1;
  }
  state.lines = lines;
  lines.forEach((line, index) => {
    if (line.start != null && line.words[0].start == null) spread(index);
  });
  state.cursor = todo();
  state.wordCursor = 0;
  document.body.classList.remove("editing");
  draw();
}
