// The lyrics editor's types and constants, its state, and the small helpers the other modules share.

export interface Word {
  text: string;
  start: number | null;
  end: number | null;
  cut: boolean; // its end was set on its own, apart from where the next word starts
}

// A line: `start` is null until it is timed, and once it is, every word has a start and an end.
// `end` is where it is known to end (Enter, or a dragged edge), null when that is worked out from
// its length; `byWord` says its word times were set by hand instead of being spread by length.
export interface Line {
  start: number | null;
  end: number | null;
  byWord: boolean;
  words: Word[];
}

export type Step = "line" | "word" | "review";

export const base = location.pathname.endsWith("/")
  ? location.pathname
  : location.pathname + "/";
export const SECONDS_PER_CHAR = 0.06; // the same sung-length estimate the renderer uses
export const MIN = 0.02; // shortest a word may be, in seconds
export const JOIN = 0.005; // a word that starts this close to the end of the one before shares an edge with it
export const PEAKS = 200; // waveform points per second
export const TOUCH = matchMedia("(pointer: coarse)").matches; // a finger, not a mouse
export const LANE = TOUCH ? 44 : 34,
  RULER = 16; // heights of the timeline's row of blocks and of its time ruler
export const STEPS: Record<
  Step,
  { button: string; space: string; enter?: string; hint: string }
> = {
  line: {
    button: "byLine",
    space: "line starts",
    enter: "line ends here",
    hint: "Press Space as each line starts. Its words are spread over the line by their length.",
  },
  word: {
    button: "byWord",
    space: "word starts",
    enter: "word ends here",
    hint: "Press Space as each word starts. Dotted lines still have estimated word times.",
  },
  review: {
    button: "review",
    space: "play / pause",
    hint: "Watch the fill as it will be in the video. Drag the edge of a word in the timeline to move it.",
  },
};

export const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
export const audio = new Audio();
export const canvas = $<HTMLCanvasElement>("timeline"),
  pen = canvas.getContext("2d")!;

export const state = {
  lines: [] as Line[],
  cursor: 0, // line the next tap times
  wordCursor: 0, // word within it, in the words step
  step: "line" as Step,
  undo: [] as { snapshot: string; time: number | null }[],
  dirty: false,
  closeArmed: false,
  closed: false,
  duration: 0,
  peaks: null as Float64Array | null, // loudness of the song, PEAKS values per second, for the waveform
};
export const view = { scale: 140, lead: 0.3 }; // timeline: pixels per second, and how far across the playhead sits

export const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
export const weight = (text: string) =>
  text.replace(/[^\p{L}\p{N}]/gu, "").length + 2;
export const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
export const clamp = (value: number, low: number, high: number) =>
  Math.max(low, Math.min(high, value));
export const say = (message: string) => {
  $("status").textContent = message;
};
export const textOf = (line: Line) =>
  line.words.map((word) => word.text).join(" ");
export const lineEnd = (line: Line) => line.words[line.words.length - 1].end!;
// A line with one word has nothing left to do in the words step once it is timed.
export const done = (line: Line) =>
  line.start != null && (line.byWord || line.words.length === 1);
export const place = (time: number) =>
  view.lead * canvas.clientWidth + (time - audio.currentTime) * view.scale;
export const timeAt = (x: number) =>
  audio.currentTime + (x - view.lead * canvas.clientWidth) / view.scale;
