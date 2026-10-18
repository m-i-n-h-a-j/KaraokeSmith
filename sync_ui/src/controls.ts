// The steps, the buttons and the keyboard, and saving and closing.
import { draw, paint } from "./column";
import { applyLyrics, openEditor } from "./lyrics";
import { $, STEPS, audio, base, say, state, type Step } from "./state";
import {
  clearFromHere,
  cut,
  move,
  shiftAll,
  tap,
  todo,
  undoLast,
} from "./timing";

export function setStep(next: Step) {
  state.step = next;
  state.cursor = todo();
  state.wordCursor = 0;
  document.body.dataset.step = next;
  for (const [name, { button }] of Object.entries(STEPS))
    $(button).classList.toggle("on", name === next);
  $("hint").textContent = STEPS[next].hint;
  const { space, enter } = STEPS[next];
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
  labelPad();
  const line = state.lines[state.cursor];
  if (next !== "review" && line?.start != null)
    audio.currentTime = Math.max(0, line.start - 2);
  draw();
}

// The touch pad's labels say what Tap and End do now. Tap works like Space: while the song
// stands still, it starts the song instead.
function labelPad() {
  const { space, enter } = STEPS[state.step];
  $("toggle").textContent = $("play").textContent = audio.paused
    ? "Play"
    : "Pause";
  $("tapWhat").textContent =
    audio.paused && state.step !== "review" ? "plays the song" : space;
  $("endWhat").textContent = enter ?? "";
  $<HTMLButtonElement>("end").disabled = !enter;
}

export async function save() {
  const round = (t: number | null) =>
    t == null ? null : Math.round(t * 1000) / 1000;
  const rows = state.lines.map((line) => ({
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
  const untimed = state.lines.filter((line) => line.start == null).length;
  say(
    (await response.text()) +
      (response.ok && untimed ? `; ${untimed} lines still untimed` : ""),
  );
  if (response.ok) {
    state.dirty = false;
    $("save").classList.remove("unsaved");
  }
}

export async function closeEditor() {
  if (state.dirty && !state.closeArmed) {
    state.closeArmed = true;
    return say(
      "Unsaved changes. Press Close again to discard them, or Save first.",
    );
  }
  await fetch(base + "close", { method: "POST" });
  audio.pause();
  state.closed = true;
  state.dirty = false;
  document.body.innerHTML =
    '<p style="margin:auto;font-size:20px">Editor closed. Go back to the terminal.</p>';
}

export function toggle() {
  if (audio.paused) audio.play();
  else audio.pause();
}

function setSpeed(value: string) {
  $<HTMLSelectElement>("speed").value = value;
  audio.playbackRate = Number(value);
}

$("play").onclick = toggle;
audio.onplay = audio.onpause = () => {
  labelPad();
  paint();
};
audio.onseeked = paint;
$<HTMLInputElement>("seek").oninput = (event) => {
  audio.currentTime = Number((event.target as HTMLInputElement).value);
};
$<HTMLSelectElement>("speed").onchange = (event) => {
  const speed = event.target as HTMLSelectElement;
  audio.playbackRate = Number(speed.value);
  speed.blur();
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
$("more").onclick = () => document.body.classList.toggle("more");
document.addEventListener("click", (event) => {
  // The menu closes on any press, except the one that opened it.
  if (!(event.target as Element).closest("#more"))
    document.body.classList.remove("more");
});

// Tap and End act as the finger lands, as a key does, not when it lifts. Starting the song waits
// for the click, because browsers only let a page start sound once the finger lifts.
$("tap").onpointerdown = () => {
  if (state.step !== "review" && !audio.paused) tap();
};
$("tap").onclick = () => {
  if (state.step === "review" || audio.paused) toggle();
};
$("end").onpointerdown = cut;
$("undo").onclick = undoLast;
$("toggle").onclick = toggle;
$("back").onclick = () => {
  audio.currentTime = Math.max(0, audio.currentTime - 3);
};
addEventListener("beforeunload", (event) => {
  if (state.dirty) event.preventDefault();
});

document.addEventListener("keydown", (event) => {
  if (state.closed) return;
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
  const keys: Record<string, () => void> = {
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
  const target = event.target as HTMLElement;
  if (target.matches("button, select, input")) target.blur();
  action();
});
