// The lyric column: one row per line, filling gold as the song plays.

// Rebuild the lyric column. Cheap enough to do after every change.
function draw() {
  cache = null;
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

function lineAt(t) {
  let found = -1;
  lines.forEach((line, index) => {
    if (line.start != null && line.start <= t) found = index;
  });
  return found;
}

// Show the fill as the renderer will: words turn gold as the playhead passes them. The marked
// row is the one to tap next, or in the review step the one being sung.
function paint() {
  if (closed) return;
  const t = audio.currentTime;
  $("clock").textContent = `${clock(t)} / ${clock(duration)}`;
  if (document.activeElement !== $("seek")) $("seek").value = t;
  const want =
    step === "review" ? lineAt(t + 0.3) : cursor < lines.length ? cursor : -1;
  if (want !== marked) {
    rows[marked]?.row.classList.remove("target");
    rows[want]?.row.classList.add("target");
    (
      rows[want] ?? rows[step === "review" ? 0 : rows.length - 1]
    )?.row.scrollIntoView({ block: "center" });
    marked = want;
  }
  lines.forEach((line, index) =>
    line.words.forEach((word, k) => {
      const span = rows[index]?.words[k],
        timed = word.start != null;
      if (!span) return;
      const active = timed && t >= word.start && t < word.end;
      span.classList.toggle("sung", timed && t >= word.end);
      span.classList.toggle("active", active);
      if (active)
        span.style.setProperty(
          "--fill",
          `${(100 * (t - word.start)) / (word.end - word.start)}%`,
        );
    }),
  );
  sketch();
}

function refresh() {
  cache = null;
  paint();
}

function frame() {
  if (!audio.paused) paint();
  requestAnimationFrame(frame);
}
