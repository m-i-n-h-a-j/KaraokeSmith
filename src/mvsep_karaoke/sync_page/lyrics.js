// The "Edit lyrics" box: changing the text while the rows that stay keep their timing.

function openEditor() {
  $("text").value = lines.map(textOf).join("\n");
  document.body.classList.add("editing");
  audio.pause();
  $("text").focus();
}

// A row whose text changed. Words tapped by hand keep their times if there are still as many;
// otherwise the line keeps its place in the song and its words are estimated again.
function reword(old, text) {
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

function applyLyrics() {
  const texts = $("text")
    .value.split("\n")
    .map((row) => row.trim().replace(/\s+/g, " "))
    .filter(Boolean);
  const old = lines,
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
  const kept = [];
  for (let i = 0, j = 0; i < n && j < m;) {
    if (was[i] === texts[j]) kept.push([i++, j++]);
    else if (common[i + 1][j] >= common[i][j + 1]) i++;
    else j++;
  }
  kept.push([n, m]);
  lines = [];
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
  lines.forEach((line, index) => {
    if (line.start != null && line.words[0].start == null) spread(index);
  });
  cursor = todo();
  wordCursor = 0;
  document.body.classList.remove("editing");
  draw();
}
