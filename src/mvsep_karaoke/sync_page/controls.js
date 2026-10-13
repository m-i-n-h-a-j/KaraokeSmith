// The steps, the buttons and the keyboard, and saving and closing.

function setStep(next) {
  step = next;
  cursor = todo();
  wordCursor = 0;
  document.body.dataset.step = step;
  for (const [name, { button }] of Object.entries(STEPS))
    $(button).classList.toggle("on", name === step);
  $("hint").textContent = STEPS[step].hint;
  const { space, enter } = STEPS[step];
  const keys = [
    ["Space", space],
    ...(enter ? [["Enter", enter]] : []),
    ["Backspace", "undo"],
    ["P", "play / pause"],
    ["← →", "3 s"],
    ["↑ ↓", "line"],
    ["1 2 3", "speed"],
  ];
  $("keys").replaceChildren(
    ...keys.map(([names, what]) => {
      const item = document.createElement("span");
      for (const name of names.split(" ")) {
        const key = document.createElement("kbd");
        key.textContent = name;
        item.append(key, " ");
      }
      item.append(what);
      return item;
    }),
  );
  const line = lines[cursor];
  if (step !== "review" && line?.start != null)
    audio.currentTime = Math.max(0, line.start - 2);
  draw();
}

async function save() {
  const round = (t) => (t == null ? null : Math.round(t * 1000) / 1000);
  const rows = lines.map((line) => ({
    start: round(line.start),
    end: round(line.end),
    words: line.words.map((word) => ({
      text: word.text,
      start: round(word.start),
      end: round(word.end),
    })),
  }));
  const response = await fetch(base + "save", {
    method: "POST",
    body: JSON.stringify(rows),
  });
  const untimed = lines.filter((line) => line.start == null).length;
  say(
    (await response.text()) +
      (response.ok && untimed ? `; ${untimed} lines still untimed` : ""),
  );
  if (response.ok) {
    dirty = false;
    $("save").classList.remove("unsaved");
  }
}

async function closeEditor() {
  if (dirty && !closeArmed) {
    closeArmed = true;
    return say(
      "Unsaved changes. Press Close again to discard them, or Save first.",
    );
  }
  await fetch(base + "close", { method: "POST" });
  audio.pause();
  closed = true;
  dirty = false;
  document.body.innerHTML =
    '<p style="margin:auto;font-size:20px">Editor closed. Go back to the terminal.</p>';
}

function toggle() {
  audio.paused ? audio.play() : audio.pause();
}

$("play").onclick = toggle;
audio.onplay = audio.onpause = () => {
  $("play").textContent = audio.paused ? "Play" : "Pause";
  paint();
};
audio.onseeked = paint;
$("seek").oninput = (event) => {
  audio.currentTime = Number(event.target.value);
};
$("speed").onchange = (event) => {
  audio.playbackRate = Number(event.target.value);
  event.target.blur();
};
$("byLine").onclick = () => setStep("line");
$("byWord").onclick = () => setStep("word");
$("review").onclick = () => setStep("review");
$("earlier").onclick = () => shiftAll(-0.05);
$("later").onclick = () => shiftAll(0.05);
$("clear").onclick = clearFromHere;
$("edit").onclick = openEditor;
$("apply").onclick = applyLyrics;
$("cancel").onclick = () => document.body.classList.remove("editing");
$("save").onclick = save;
$("close").onclick = closeEditor;
addEventListener("beforeunload", (event) => {
  if (dirty) event.preventDefault();
});

document.addEventListener("keydown", (event) => {
  if (closed) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (document.body.classList.contains("editing")) {
    if (key === "Escape") $("cancel").click();
    return;
  }
  if (event.ctrlKey && key === "s") {
    event.preventDefault();
    return save();
  }
  if (event.ctrlKey && key === "z") {
    event.preventDefault();
    return undoLast();
  }
  if (event.ctrlKey || event.altKey || event.metaKey) return;
  const keys = {
    // A tap while the song stands still would time nothing useful, so there Space starts it.
    " ": () => (audio.paused ? audio.play() : tap()),
    Enter: cut,
    Backspace: undoLast,
    p: toggle,
    ArrowLeft: () => {
      audio.currentTime = Math.max(0, audio.currentTime - 3);
    },
    ArrowRight: () => {
      audio.currentTime = audio.currentTime + 3;
    },
    ArrowUp: () => move(-1),
    ArrowDown: () => move(1),
    1: () => setSpeed("0.5"),
    2: () => setSpeed("0.75"),
    3: () => setSpeed("1"),
  };
  const action = keys[key];
  if (!action) return;
  event.preventDefault();
  if (event.repeat && (key === " " || key === "Enter")) return; // a held key is one tap
  if (event.target.matches("button, select, input")) event.target.blur();
  action();
});

function setSpeed(value) {
  $("speed").value = value;
  audio.playbackRate = Number(value);
}
