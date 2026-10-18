// The timeline: the waveform under a fixed playhead, and the blocks along its top that can be dragged.
import { draw, marked, refresh } from "./column";
import {
  JOIN,
  LANE,
  MIN,
  PEAKS,
  RULER,
  audio,
  canvas,
  clamp,
  clock,
  done,
  lineEnd,
  pen,
  place,
  say,
  state,
  textOf,
  timeAt,
  view,
} from "./state";
import { remember, shift, spread } from "./timing";

// A block on the timeline: a whole line (`k` is null) or one of its words.
interface Span {
  index: number;
  k: number | null;
  start: number;
  end: number;
  text: string;
  soft: boolean; // the line's word times are still an estimate
  minEnd: number;
  maxStart: number;
}

interface Edge {
  at: number; // index into spans()
  edge: "start" | "end";
  time: number;
  joint: boolean; // shared with the end of the span before
  distance?: number;
}

// A pointer held down on the timeline.
interface Drag {
  x: number;
  edge: Edge | null;
  at: number;
  moved: boolean;
  from: number;
  playing: boolean;
  line: boolean;
  grab: number;
}

let cache: Span[] | null = null; // spans(), until something changes
let drag: Drag | null = null;

export function forget() {
  cache = null;
}

// What the timeline shows and lets you drag: whole lines in the lines step, single words after
// it. `minEnd` and `maxStart` are how far the edges may be pushed together. The outer edges of
// an estimated line stretch the whole estimate, so there they leave room for all of its words.
export function spans() {
  if (cache) return cache;
  const list: Span[] = [];
  state.lines.forEach((line, index) => {
    if (line.start == null) return;
    const words = line.words,
      last = words.length - 1,
      room = 2 * MIN * words.length,
      soft = !done(line);
    if (state.step === "line") {
      list.push({
        index,
        k: null,
        start: line.start,
        end: words[last].end!,
        text: textOf(line),
        soft,
        minEnd: line.byWord ? words[last].start! + MIN : line.start + room,
        maxStart: line.byWord ? words[0].end! - MIN : words[last].end! - room,
      });
    } else {
      words.forEach((word, k) =>
        list.push({
          index,
          k,
          start: word.start!,
          end: word.end!,
          text: word.text,
          soft,
          minEnd:
            !line.byWord && k === last ? line.start! + room : word.start! + MIN,
          maxStart:
            !line.byWord && k === 0 ? words[last].end! - room : word.end! - MIN,
        }),
      );
    }
  });
  cache = list;
  return list;
}

// Draw the timeline: the song's waveform scrolling under a fixed playhead, with the timed lines
// or words as blocks along the top.
export function sketch() {
  const width = canvas.clientWidth,
    height = canvas.clientHeight,
    ratio = devicePixelRatio || 1;
  if (!width) return;
  if (
    canvas.width !== Math.round(width * ratio) ||
    canvas.height !== Math.round(height * ratio)
  ) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  pen.setTransform(ratio, 0, 0, ratio, 0, 0);
  pen.clearRect(0, 0, width, height);
  const { peaks, duration } = state;
  const t = audio.currentTime,
    head = view.lead * width;
  const x = (time: number) => head + (time - t) * view.scale;
  const from = t - head / view.scale,
    to = from + width / view.scale;
  const bottom = height - RULER,
    middle = (LANE + bottom) / 2,
    reach = (bottom - LANE) / 2 - 3;

  if (peaks) {
    // At low zoom several points share a pixel; they are merged on a fixed grid so the wave does not shimmer.
    const stride = Math.max(1, Math.floor(PEAKS / view.scale));
    const first = Math.max(0, Math.floor((from * PEAKS) / stride) * stride),
      last = Math.min(peaks.length - 1, Math.ceil(to * PEAKS));
    const outline: [number, number][] = [];
    for (let i = first; i <= last; i += stride) {
      let level = 0;
      for (let j = i; j < i + stride && j < peaks.length; j++)
        level = Math.max(level, peaks[j]);
      outline.push([x(i / PEAKS), Math.max(0.5, level * reach)]);
    }
    pen.beginPath();
    for (const [at, size] of outline) pen.lineTo(at, middle - size);
    for (const [at, size] of outline.reverse()) pen.lineTo(at, middle + size);
    pen.fillStyle = "#3b3b54";
    pen.fill();
  }

  const every =
    [1, 2, 5, 10, 15, 30].find((seconds) => seconds * view.scale >= 70) ?? 60;
  pen.fillStyle = "#8a8a96";
  pen.font = '11px "Segoe UI", system-ui, sans-serif';
  pen.textBaseline = "middle";
  for (
    let second = Math.max(0, Math.ceil(from));
    second <= Math.min(to, duration);
    second++
  ) {
    pen.fillRect(x(second), bottom, 1, second % every ? 3 : 6);
    if (second % every === 0)
      pen.fillText(clock(second).slice(0, 5), x(second) + 4, bottom + 9);
  }

  const list = spans();
  pen.font = '600 13px "Segoe UI", system-ui, sans-serif';
  list.forEach((span, at) => {
    if (span.end < from || span.start > to) return;
    const left = x(span.start),
      wide = Math.max(1, x(span.end) - left);
    const mine = span.index === marked,
      next =
        state.step === "word" &&
        span.index === state.cursor &&
        span.k === state.wordCursor;
    // Each edge runs down through the waveform, to line it up with what is heard there.
    pen.fillStyle = mine
      ? "rgba(255, 215, 0, 0.5)"
      : "rgba(255, 255, 255, 0.16)";
    pen.fillRect(left, LANE, 1, bottom - LANE);
    if (!list[at + 1] || Math.abs(list[at + 1].start - span.end) > JOIN)
      pen.fillRect(left + wide, LANE, 1, bottom - LANE);
    pen.fillStyle = next
      ? "rgba(255, 215, 0, 0.3)"
      : span.soft
        ? "rgba(255, 255, 255, 0.05)"
        : "rgba(255, 255, 255, 0.13)";
    pen.strokeStyle = mine ? "#ffd700" : span.soft ? "#6a6a7c" : "#b4b4c2";
    pen.setLineDash(span.soft ? [4, 3] : []);
    pen.beginPath();
    pen.roundRect(left + 0.5, 4.5, wide - 1, LANE - 9, 4);
    pen.fill();
    pen.stroke();
    if (left < head) {
      pen.fillStyle = "rgba(255, 215, 0, 0.28)";
      pen.fillRect(
        left + 1,
        5,
        Math.max(0, Math.min(wide, head - left) - 1.5),
        LANE - 10,
      );
    }
    if (wide > 16) {
      pen.save();
      pen.beginPath();
      pen.rect(left + 4, 0, wide - 8, LANE);
      pen.clip();
      pen.fillStyle = span.soft ? "#b4b4c2" : "#fff";
      pen.fillText(span.text, Math.max(left, 0) + 6, LANE / 2);
      pen.restore();
    }
  });
  pen.setLineDash([]);

  pen.fillStyle = "rgba(0, 0, 0, 0.45)"; // outside the song
  if (from < 0) pen.fillRect(0, 0, x(0), height);
  if (to > duration) pen.fillRect(x(duration), 0, width - x(duration), height);
  pen.fillStyle = "#ffd700";
  pen.fillRect(head - 1, 0, 2, height);
}

// The draggable edge under the pointer, if any, within `reach` pixels. Words that touch share one
// edge, held at the start of the later one; where an end and a start sit in the same spot without
// touching, the side the pointer is on tells them apart.
function edgeAt(x: number, y: number, reach = 6) {
  if (y > LANE) return null;
  const list = spans();
  let best: Edge | null = null;
  for (const [at, span] of list.entries()) {
    const before = list[at - 1],
      after = list[at + 1];
    const edges: Edge[] = [
      {
        at,
        edge: "start",
        time: span.start,
        joint: before != null && Math.abs(span.start - before.end) <= JOIN,
      },
    ];
    if (!after || Math.abs(after.start - span.end) > JOIN)
      edges.push({ at, edge: "end", time: span.end, joint: false });
    for (const edge of edges) {
      const off = x - place(edge.time);
      const distance =
        Math.abs(off) + ((edge.edge === "start" ? off < 0 : off > 0) ? 0.5 : 0);
      if (Math.abs(off) <= reach && (!best || distance < best.distance!))
        best = { ...edge, distance };
    }
  }
  return best;
}

function spanAt(x: number, y: number) {
  return y > LANE
    ? -1
    : spans().findIndex(
        (span) => place(span.start) <= x && x <= place(span.end),
      );
}

// The outer edge of an estimated line stretches the estimate; any other edge pins a word by hand.
function setStart({ index, k }: Span, t: number) {
  const line = state.lines[index];
  if (!line.byWord && !k) {
    line.end = lineEnd(line);
    line.start = t;
    return spread(index);
  }
  line.byWord = true;
  line.words[k ?? 0].start = t;
  if (!k) line.start = t;
}

function setEnd({ index, k }: Span, t: number) {
  const line = state.lines[index],
    last = line.words.length - 1;
  if (!line.byWord && (k ?? last) === last) {
    line.end = t;
    return spread(index);
  }
  line.byWord = true;
  Object.assign(line.words[k ?? last], { end: t, cut: true });
  if ((k ?? last) === last) line.end = t;
}

function moveEdge({ at, edge, joint }: Edge, t: number) {
  const list = spans(),
    span = list[at],
    before = list[at - 1],
    after = list[at + 1];
  if (edge === "start") {
    t = clamp(
      t,
      Math.min(span.start, joint ? before.minEnd : before ? before.end : 0),
      span.maxStart,
    );
    setStart(span, t);
    if (joint) setEnd(before, t);
  } else {
    t = clamp(
      t,
      span.minEnd,
      Math.max(span.end, after ? after.start : state.duration),
    );
    setEnd(span, t);
  }
  say(clock(t));
}

// Slide a whole line along the song, between the lines around it.
function moveLine(at: number, start: number) {
  const list = spans(),
    span = list[at],
    before = list[at - 1],
    after = list[at + 1];
  const low = Math.min(span.start, before ? before.end : 0);
  const high = Math.max(
    span.start,
    (after ? after.start : state.duration) - (span.end - span.start),
  );
  const line = state.lines[span.index];
  line.end = span.end;
  shift(line, clamp(start, low, high) - span.start);
  say(clock(line.start!));
}

const spot = (event: PointerEvent) => {
  const box = canvas.getBoundingClientRect();
  return [event.clientX - box.left, event.clientY - box.top];
};

const fingers = new Map<number, number>(); // pointers held down on the timeline: their x, by pointer id
let pinch: { apart: number; scale: number } | null = null; // two fingers zoom: how far apart they started, and the scale then
const apart = () => {
  const [a, b] = fingers.values();
  return Math.max(20, Math.abs(a - b));
};

canvas.onpointerdown = (event) => {
  const [x, y] = spot(event);
  fingers.set(event.pointerId, x);
  canvas.setPointerCapture(event.pointerId);
  if (fingers.size > 1) {
    // A second finger zooms, and whatever the first one was dragging stays where it got to.
    if (drag?.moved && (drag.edge || drag.line)) draw();
    drag = null;
    pinch = fingers.size === 2 ? { apart: apart(), scale: view.scale } : null;
    return;
  }
  // A fingertip is wider than a mouse pointer, so edges can be caught from further away.
  const edge = edgeAt(x, y, event.pointerType === "touch" ? 16 : 6),
    at = edge ? -1 : spanAt(x, y);
  if (edge && event.shiftKey) edge.joint = false;
  drag = {
    x,
    edge,
    at,
    moved: false,
    from: audio.currentTime,
    playing: !audio.paused,
    line: at >= 0 && state.step === "line",
    // Where the pointer is on what it holds, so an edge caught from a few pixels away does not jump.
    grab: edge
      ? timeAt(x) - edge.time
      : at >= 0
        ? timeAt(x) - spans()[at].start
        : 0,
  };
};

canvas.onpointermove = (event) => {
  const [x, y] = spot(event);
  if (fingers.has(event.pointerId)) fingers.set(event.pointerId, x);
  if (pinch) {
    view.scale = clamp((pinch.scale * apart()) / pinch.apart, 40, 800);
    return sketch();
  }
  if (fingers.size > 1) return;
  if (!drag) {
    canvas.style.cursor = edgeAt(x, y)
      ? "ew-resize"
      : spanAt(x, y) < 0
        ? "default"
        : state.step === "line"
          ? "grab"
          : "pointer";
    return;
  }
  if (!drag.moved && Math.abs(x - drag.x) < 3) return;
  if (!drag.moved) {
    drag.moved = true;
    audio.pause();
    if (drag.edge || drag.line) remember();
  }
  if (drag.edge) moveEdge(drag.edge, timeAt(x) - drag.grab);
  else if (drag.line) moveLine(drag.at, timeAt(x) - drag.grab);
  else
    audio.currentTime = clamp(
      drag.from - (x - drag.x) / view.scale,
      0,
      state.duration,
    ); // the wave follows the pointer
  refresh();
};

canvas.onpointerup = (event) => {
  fingers.delete(event.pointerId);
  if (fingers.size < 2) pinch = null;
  if (!drag) return;
  const { moved, edge, at, line, playing } = drag;
  drag = null;
  if (!moved)
    audio.currentTime = clamp(
      at >= 0 ? spans()[at].start : timeAt(spot(event)[0]),
      0,
      state.duration,
    );
  else if (edge || line) draw();
  else if (playing) audio.play();
};

canvas.onpointercancel = (event) => {
  fingers.delete(event.pointerId);
  if (fingers.size < 2) pinch = null;
  if (drag?.moved) draw();
  drag = null;
};

canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    view.scale = clamp(view.scale * (event.deltaY < 0 ? 1.25 : 0.8), 40, 800);
    sketch();
  },
  { passive: false },
);

new ResizeObserver(sketch).observe(canvas);
