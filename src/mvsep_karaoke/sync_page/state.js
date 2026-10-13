// The lyrics editor's constants, its state and the small helpers the other scripts share. These are
// plain scripts, not modules: everything in them is global, and index.html loads this one first.

const base = location.pathname.endsWith("/")
  ? location.pathname
  : location.pathname + "/";
const SECONDS_PER_CHAR = 0.06; // the same sung-length estimate the renderer uses
const MIN = 0.02; // shortest a word may be, in seconds
const JOIN = 0.005; // a word that starts this close to the end of the one before shares an edge with it
const PEAKS = 200; // waveform points per second
const LANE = 34,
  RULER = 16; // heights of the timeline's row of blocks and of its time ruler
const STEPS = {
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
const $ = (id) => document.getElementById(id);
const audio = new Audio();
const canvas = $("timeline"),
  pen = canvas.getContext("2d");
// A line: `start` is null until it is timed; `end` is where it is known to end (Enter, or a dragged
// edge), null when that is worked out from its length; `byWord` says its word times were set by
// hand instead of being spread by length.
let lines = []; // [{start, end, byWord, words: [{text, start, end, cut}]}]
let cursor = 0; // line the next tap times
let wordCursor = 0; // word within it, in the words step
let step = "line"; // 'line', 'word' or 'review'
let undo = [];
let dirty = false;
let closeArmed = false;
let closed = false;
let duration = 0;
let peaks = null; // loudness of the song, PEAKS values per second, for the waveform
let view = { scale: 140, lead: 0.3 }; // timeline: pixels per second, and how far across the playhead sits
let rows = []; // the lyric column's elements: [{row, words: [span]}]
let marked = null; // row that carries the highlight
let cache = null; // spans(), until something changes
let drag = null; // a pointer held down on the timeline

const clock = (t) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
const weight = (text) => text.replace(/[^\p{L}\p{N}]/gu, "").length + 2;
const sum = (values) => values.reduce((a, b) => a + b, 0);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const say = (message) => {
  $("status").textContent = message;
};
const textOf = (line) => line.words.map((word) => word.text).join(" ");
const lineEnd = (line) => line.words[line.words.length - 1].end;
// A line with one word has nothing left to do in the words step once it is timed.
const done = (line) =>
  line.start != null && (line.byWord || line.words.length === 1);
const place = (time) =>
  view.lead * canvas.clientWidth + (time - audio.currentTime) * view.scale;
const timeAt = (x) =>
  audio.currentTime + (x - view.lead * canvas.clientWidth) / view.scale;
