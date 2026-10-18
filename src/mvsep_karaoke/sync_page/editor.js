//#region src/state.ts
var base = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
var SECONDS_PER_CHAR = .06;
var MIN = .02;
var LANE = matchMedia("(pointer: coarse)").matches ? 44 : 34;
var STEPS = {
	line: {
		button: "byLine",
		space: "line starts",
		enter: "line ends here",
		hint: "Press Space as each line starts. Its words are spread over the line by their length."
	},
	word: {
		button: "byWord",
		space: "word starts",
		enter: "word ends here",
		hint: "Press Space as each word starts. Dotted lines still have estimated word times."
	},
	review: {
		button: "review",
		space: "play / pause",
		hint: "Watch the fill as it will be in the video. Drag the edge of a word in the timeline to move it."
	}
};
var $ = (id) => document.getElementById(id);
var audio = new Audio();
var canvas = $("timeline");
var pen = canvas.getContext("2d");
var state = {
	lines: [],
	cursor: 0,
	wordCursor: 0,
	step: "line",
	undo: [],
	dirty: false,
	closeArmed: false,
	closed: false,
	duration: 0,
	peaks: null
};
var view = {
	scale: 140,
	lead: .3
};
var clock = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
var weight = (text) => text.replace(/[^\p{L}\p{N}]/gu, "").length + 2;
var sum = (values) => values.reduce((a, b) => a + b, 0);
var clamp = (value, low, high) => Math.max(low, Math.min(high, value));
var say = (message) => {
	$("status").textContent = message;
};
var textOf = (line) => line.words.map((word) => word.text).join(" ");
var lineEnd = (line) => line.words[line.words.length - 1].end;
var done = (line) => line.start != null && (line.byWord || line.words.length === 1);
var place = (time) => view.lead * canvas.clientWidth + (time - audio.currentTime) * view.scale;
var timeAt = (x) => audio.currentTime + (x - view.lead * canvas.clientWidth) / view.scale;
//#endregion
//#region src/lyrics.ts
var box = () => $("text");
function openEditor() {
	box().value = state.lines.map(textOf).join("\n");
	document.body.classList.add("editing");
	audio.pause();
	box().focus();
}
function reword(old, text) {
	const texts = text.split(" ");
	if (old?.byWord && old.words.length === texts.length) return {
		...old,
		words: old.words.map((word, k) => ({
			...word,
			text: texts[k]
		}))
	};
	const words = texts.map((word) => ({
		text: word,
		start: null,
		end: null,
		cut: false
	}));
	return {
		start: old?.start ?? null,
		end: old?.start != null ? lineEnd(old) : null,
		byWord: false,
		words
	};
}
function applyLyrics() {
	const texts = box().value.split("\n").map((row) => row.trim().replace(/\s+/g, " ")).filter(Boolean);
	const old = state.lines, was = old.map(textOf), n = was.length, m = texts.length;
	remember();
	const common = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
	for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) common[i][j] = was[i] === texts[j] ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
	const kept = [];
	for (let i = 0, j = 0; i < n && j < m;) if (was[i] === texts[j]) kept.push([i++, j++]);
	else if (common[i + 1][j] >= common[i][j + 1]) i++;
	else j++;
	kept.push([n, m]);
	const lines = [];
	let i = 0, j = 0;
	for (const [keptOld, keptNew] of kept) {
		const reworded = keptOld - i === keptNew - j;
		for (; j < keptNew; i++, j++) lines.push(reword(reworded ? old[i] : null, texts[j]));
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
//#endregion
//#region src/controls.ts
function setStep(next) {
	state.step = next;
	state.cursor = todo();
	state.wordCursor = 0;
	document.body.dataset.step = next;
	for (const [name, { button }] of Object.entries(STEPS)) $(button).classList.toggle("on", name === next);
	$("hint").textContent = STEPS[next].hint;
	const { space, enter } = STEPS[next];
	const keys = [
		["Space", space],
		...enter ? [["Enter", enter]] : [],
		["Backspace", "undo"],
		["P", "play / pause"],
		["← →", "3 s"],
		["↑ ↓", "line"],
		["1 2 3", "speed"]
	];
	$("keys").replaceChildren(...keys.map(([names, what]) => {
		const item = document.createElement("span");
		for (const name of names.split(" ")) {
			const key = document.createElement("kbd");
			key.textContent = name;
			item.append(key, " ");
		}
		item.append(what);
		return item;
	}));
	labelPad();
	const line = state.lines[state.cursor];
	if (next !== "review" && line?.start != null) audio.currentTime = Math.max(0, line.start - 2);
	draw();
}
function labelPad() {
	const { space, enter } = STEPS[state.step];
	$("toggle").textContent = $("play").textContent = audio.paused ? "Play" : "Pause";
	$("tapWhat").textContent = audio.paused && state.step !== "review" ? "plays the song" : space;
	$("endWhat").textContent = enter ?? "";
	$("end").disabled = !enter;
}
async function save() {
	const round = (t) => t == null ? null : Math.round(t * 1e3) / 1e3;
	const rows = state.lines.map((line) => ({
		start: round(line.start),
		end: round(line.end),
		words: line.words.map((word) => ({
			text: word.text,
			start: round(word.start),
			end: round(word.end)
		}))
	}));
	const response = await fetch(base + "save", {
		method: "POST",
		body: JSON.stringify(rows)
	});
	const untimed = state.lines.filter((line) => line.start == null).length;
	say(await response.text() + (response.ok && untimed ? `; ${untimed} lines still untimed` : ""));
	if (response.ok) {
		state.dirty = false;
		$("save").classList.remove("unsaved");
	}
}
async function closeEditor() {
	if (state.dirty && !state.closeArmed) {
		state.closeArmed = true;
		return say("Unsaved changes. Press Close again to discard them, or Save first.");
	}
	await fetch(base + "close", { method: "POST" });
	audio.pause();
	state.closed = true;
	state.dirty = false;
	document.body.innerHTML = "<p style=\"margin:auto;font-size:20px\">Editor closed. Go back to the terminal.</p>";
}
function toggle() {
	if (audio.paused) audio.play();
	else audio.pause();
}
function setSpeed(value) {
	$("speed").value = value;
	audio.playbackRate = Number(value);
}
$("play").onclick = toggle;
audio.onplay = audio.onpause = () => {
	labelPad();
	paint();
};
audio.onseeked = paint;
$("seek").oninput = (event) => {
	audio.currentTime = Number(event.target.value);
};
$("speed").onchange = (event) => {
	const speed = event.target;
	audio.playbackRate = Number(speed.value);
	speed.blur();
};
$("byLine").onclick = () => setStep("line");
$("byWord").onclick = () => setStep("word");
$("review").onclick = () => setStep("review");
$("earlier").onclick = () => shiftAll(-.05);
$("later").onclick = () => shiftAll(.05);
$("clear").onclick = clearFromHere;
$("edit").onclick = openEditor;
$("apply").onclick = applyLyrics;
$("cancel").onclick = () => document.body.classList.remove("editing");
$("save").onclick = save;
$("close").onclick = closeEditor;
$("more").onclick = () => document.body.classList.toggle("more");
document.addEventListener("click", (event) => {
	if (!event.target.closest("#more")) document.body.classList.remove("more");
});
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
	const action = {
		" ": () => audio.paused ? audio.play() : tap(),
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
		3: () => setSpeed("1")
	}[key];
	if (!action) return;
	event.preventDefault();
	if (event.repeat && (key === " " || key === "Enter")) return;
	const target = event.target;
	if (target.matches("button, select, input")) target.blur();
	action();
});
//#endregion
//#region src/timing.ts
function nextStart(index) {
	for (const line of state.lines.slice(index + 1)) if (line.start != null) return line.start;
	return null;
}
function previousTimed(index) {
	for (let i = index - 1; i >= 0; i--) if (state.lines[i].start != null) return i;
	return -1;
}
function autoLength(index, begin, guess) {
	return Math.max(.5 * guess, Math.min(1.5 * guess, (nextStart(index) ?? Infinity) - begin));
}
function spread(index, from = 0) {
	const line = state.lines[index];
	if (line.start == null) return;
	const rest = line.words.slice(from);
	const begin = from === 0 ? line.start : rest[0].start;
	const weights = rest.map((word) => weight(word.text));
	const total = sum(weights);
	if (line.end != null && line.end - begin < .02 * rest.length) line.end = null;
	const length = line.end != null ? line.end - begin : autoLength(index, begin, SECONDS_PER_CHAR * total);
	let used = 0;
	rest.forEach((word, k) => {
		word.start = begin + length * used / total;
		used += weights[k];
		word.end = begin + length * used / total;
		word.cut = false;
	});
}
function shift(line, delta) {
	const move = (t) => t == null ? t : Math.max(0, t + delta);
	line.start = move(line.start);
	line.end = move(line.end);
	for (const word of line.words) {
		word.start = move(word.start);
		word.end = move(word.end);
	}
}
function remember(rewind = false) {
	const { lines, cursor, wordCursor } = state;
	state.undo.push({
		snapshot: JSON.stringify({
			lines,
			cursor,
			wordCursor
		}),
		time: rewind ? audio.currentTime : null
	});
	if (state.undo.length > 1e3) state.undo.shift();
	state.dirty = true;
	state.closeArmed = false;
	$("save").classList.add("unsaved");
	say("");
}
function settleBefore(index) {
	const at = previousTimed(index);
	if (at < 0) return;
	const before = state.lines[at], t = state.lines[index].start;
	if (!before.byWord) {
		if (before.end != null && before.end > t) before.end = t;
		return spread(at);
	}
	const last = before.words[before.words.length - 1];
	if (last.end > t) {
		last.end = Math.max(last.start + MIN, t);
		if (before.end != null) before.end = last.end;
	}
}
function tap() {
	const { step, lines, cursor, wordCursor } = state;
	if (step === "review") return toggle();
	if (cursor >= lines.length) return say("Every line is timed. Click a line to time it again.");
	const t = audio.currentTime, line = lines[cursor], words = line.words;
	const floor = step === "word" && wordCursor > 0 ? words[wordCursor - 1].start : lines[previousTimed(cursor)]?.start;
	if (floor != null && t <= floor) return say("Not timed: this is earlier than the timing before it. Click the line you want to time again.");
	remember(true);
	if (step === "line") {
		if (line.byWord && line.start != null) shift(line, t - line.start);
		else {
			Object.assign(line, {
				start: t,
				end: null,
				byWord: false
			});
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
			before.end = before.cut ? Math.min(before.end, t) : t;
		}
		words[wordCursor].start = t;
		spread(cursor, wordCursor);
		if (++state.wordCursor >= words.length) {
			state.cursor++;
			state.wordCursor = 0;
		}
	}
	draw();
}
function cut() {
	const { step, lines, cursor, wordCursor } = state;
	if (step === "review") return;
	const mid = step === "word" && wordCursor > 0;
	const index = mid ? cursor : cursor - 1;
	const line = lines[index];
	if (!line || line.start == null) return;
	const words = line.words, word = mid ? words[wordCursor - 1] : words[words.length - 1];
	const single = mid || line.byWord;
	const t = Math.min(audio.currentTime, mid ? Infinity : nextStart(index) ?? Infinity);
	if (t < (single ? word.start : line.start) + .02) return;
	remember();
	if (!single) {
		line.end = t;
		spread(index);
	} else {
		line.byWord = true;
		Object.assign(word, {
			end: t,
			cut: true
		});
		if (mid) {
			words[wordCursor].start = t;
			spread(cursor, wordCursor);
		} else line.end = t;
	}
	draw();
}
function undoLast() {
	const last = state.undo.pop();
	if (!last) return say("Nothing to undo.");
	Object.assign(state, JSON.parse(last.snapshot));
	if (last.time != null) audio.currentTime = Math.max(0, last.time - 1.5);
	draw();
}
function select(index, k = 0) {
	const line = state.lines[index];
	if (state.step === "review") {
		if (line.start != null) audio.currentTime = Math.max(0, line.start - .5);
		return;
	}
	state.cursor = index;
	state.wordCursor = state.step === "word" && line.words[k].start != null ? k : 0;
	const at = state.wordCursor ? line.words[state.wordCursor].start : line.start;
	if (at != null) audio.currentTime = Math.max(0, at - 2);
	draw();
}
function move(delta) {
	const { lines, step, cursor, wordCursor } = state;
	if (!lines.length) return;
	if (step === "review") {
		const t = audio.currentTime, starts = lines.flatMap((line) => line.start == null ? [] : [line.start]);
		const start = delta > 0 ? starts.find((start) => start > t + .6) : starts.findLast((start) => start < t - .6);
		if (start != null) audio.currentTime = Math.max(0, start - .5);
		return;
	}
	if (delta < 0 && wordCursor > 0) return select(cursor);
	select(clamp(cursor + delta, 0, lines.length - 1));
}
function shiftAll(delta) {
	remember();
	for (const line of state.lines) shift(line, delta);
	draw();
}
function clearFromHere() {
	if (state.cursor >= state.lines.length) return;
	remember();
	for (const line of state.lines.slice(state.cursor)) {
		Object.assign(line, {
			start: null,
			end: null,
			byWord: false
		});
		for (const word of line.words) Object.assign(word, {
			start: null,
			end: null,
			cut: false
		});
	}
	state.wordCursor = 0;
	draw();
}
function todo() {
	const index = state.lines.findIndex((line) => state.step === "word" ? !done(line) : line.start == null);
	return index < 0 ? state.lines.length : index;
}
//#endregion
//#region src/timeline.ts
var cache = null;
var drag = null;
function forget() {
	cache = null;
}
function spans() {
	if (cache) return cache;
	const list = [];
	state.lines.forEach((line, index) => {
		if (line.start == null) return;
		const words = line.words, last = words.length - 1, room = 2 * MIN * words.length, soft = !done(line);
		if (state.step === "line") list.push({
			index,
			k: null,
			start: line.start,
			end: words[last].end,
			text: textOf(line),
			soft,
			minEnd: line.byWord ? words[last].start + MIN : line.start + room,
			maxStart: line.byWord ? words[0].end - MIN : words[last].end - room
		});
		else words.forEach((word, k) => list.push({
			index,
			k,
			start: word.start,
			end: word.end,
			text: word.text,
			soft,
			minEnd: !line.byWord && k === last ? line.start + room : word.start + MIN,
			maxStart: !line.byWord && k === 0 ? words[last].end - room : word.end - MIN
		}));
	});
	cache = list;
	return list;
}
function sketch() {
	const width = canvas.clientWidth, height = canvas.clientHeight, ratio = devicePixelRatio || 1;
	if (!width) return;
	if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
		canvas.width = Math.round(width * ratio);
		canvas.height = Math.round(height * ratio);
	}
	pen.setTransform(ratio, 0, 0, ratio, 0, 0);
	pen.clearRect(0, 0, width, height);
	const { peaks, duration } = state;
	const t = audio.currentTime, head = view.lead * width;
	const x = (time) => head + (time - t) * view.scale;
	const from = t - head / view.scale, to = from + width / view.scale;
	const bottom = height - 16, middle = (LANE + bottom) / 2, reach = (bottom - LANE) / 2 - 3;
	if (peaks) {
		const stride = Math.max(1, Math.floor(200 / view.scale));
		const first = Math.max(0, Math.floor(from * 200 / stride) * stride), last = Math.min(peaks.length - 1, Math.ceil(to * 200));
		const outline = [];
		for (let i = first; i <= last; i += stride) {
			let level = 0;
			for (let j = i; j < i + stride && j < peaks.length; j++) level = Math.max(level, peaks[j]);
			outline.push([x(i / 200), Math.max(.5, level * reach)]);
		}
		pen.beginPath();
		for (const [at, size] of outline) pen.lineTo(at, middle - size);
		for (const [at, size] of outline.reverse()) pen.lineTo(at, middle + size);
		pen.fillStyle = "#3b3b54";
		pen.fill();
	}
	const every = [
		1,
		2,
		5,
		10,
		15,
		30
	].find((seconds) => seconds * view.scale >= 70) ?? 60;
	pen.fillStyle = "#8a8a96";
	pen.font = "11px \"Segoe UI\", system-ui, sans-serif";
	pen.textBaseline = "middle";
	for (let second = Math.max(0, Math.ceil(from)); second <= Math.min(to, duration); second++) {
		pen.fillRect(x(second), bottom, 1, second % every ? 3 : 6);
		if (second % every === 0) pen.fillText(clock(second).slice(0, 5), x(second) + 4, bottom + 9);
	}
	const list = spans();
	pen.font = "600 13px \"Segoe UI\", system-ui, sans-serif";
	list.forEach((span, at) => {
		if (span.end < from || span.start > to) return;
		const left = x(span.start), wide = Math.max(1, x(span.end) - left);
		const mine = span.index === marked, next = state.step === "word" && span.index === state.cursor && span.k === state.wordCursor;
		pen.fillStyle = mine ? "rgba(255, 215, 0, 0.5)" : "rgba(255, 255, 255, 0.16)";
		pen.fillRect(left, LANE, 1, bottom - LANE);
		if (!list[at + 1] || Math.abs(list[at + 1].start - span.end) > .005) pen.fillRect(left + wide, LANE, 1, bottom - LANE);
		pen.fillStyle = next ? "rgba(255, 215, 0, 0.3)" : span.soft ? "rgba(255, 255, 255, 0.05)" : "rgba(255, 255, 255, 0.13)";
		pen.strokeStyle = mine ? "#ffd700" : span.soft ? "#6a6a7c" : "#b4b4c2";
		pen.setLineDash(span.soft ? [4, 3] : []);
		pen.beginPath();
		pen.roundRect(left + .5, 4.5, wide - 1, LANE - 9, 4);
		pen.fill();
		pen.stroke();
		if (left < head) {
			pen.fillStyle = "rgba(255, 215, 0, 0.28)";
			pen.fillRect(left + 1, 5, Math.max(0, Math.min(wide, head - left) - 1.5), LANE - 10);
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
	pen.fillStyle = "rgba(0, 0, 0, 0.45)";
	if (from < 0) pen.fillRect(0, 0, x(0), height);
	if (to > duration) pen.fillRect(x(duration), 0, width - x(duration), height);
	pen.fillStyle = "#ffd700";
	pen.fillRect(head - 1, 0, 2, height);
}
function edgeAt(x, y, reach = 6) {
	if (y > LANE) return null;
	const list = spans();
	let best = null;
	for (const [at, span] of list.entries()) {
		const before = list[at - 1], after = list[at + 1];
		const edges = [{
			at,
			edge: "start",
			time: span.start,
			joint: before != null && Math.abs(span.start - before.end) <= .005
		}];
		if (!after || Math.abs(after.start - span.end) > .005) edges.push({
			at,
			edge: "end",
			time: span.end,
			joint: false
		});
		for (const edge of edges) {
			const off = x - place(edge.time);
			const distance = Math.abs(off) + ((edge.edge === "start" ? off < 0 : off > 0) ? .5 : 0);
			if (Math.abs(off) <= reach && (!best || distance < best.distance)) best = {
				...edge,
				distance
			};
		}
	}
	return best;
}
function spanAt(x, y) {
	return y > LANE ? -1 : spans().findIndex((span) => place(span.start) <= x && x <= place(span.end));
}
function setStart({ index, k }, t) {
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
function setEnd({ index, k }, t) {
	const line = state.lines[index], last = line.words.length - 1;
	if (!line.byWord && (k ?? last) === last) {
		line.end = t;
		return spread(index);
	}
	line.byWord = true;
	Object.assign(line.words[k ?? last], {
		end: t,
		cut: true
	});
	if ((k ?? last) === last) line.end = t;
}
function moveEdge({ at, edge, joint }, t) {
	const list = spans(), span = list[at], before = list[at - 1], after = list[at + 1];
	if (edge === "start") {
		t = clamp(t, Math.min(span.start, joint ? before.minEnd : before ? before.end : 0), span.maxStart);
		setStart(span, t);
		if (joint) setEnd(before, t);
	} else {
		t = clamp(t, span.minEnd, Math.max(span.end, after ? after.start : state.duration));
		setEnd(span, t);
	}
	say(clock(t));
}
function moveLine(at, start) {
	const list = spans(), span = list[at], before = list[at - 1], after = list[at + 1];
	const low = Math.min(span.start, before ? before.end : 0);
	const high = Math.max(span.start, (after ? after.start : state.duration) - (span.end - span.start));
	const line = state.lines[span.index];
	line.end = span.end;
	shift(line, clamp(start, low, high) - span.start);
	say(clock(line.start));
}
var spot = (event) => {
	const box = canvas.getBoundingClientRect();
	return [event.clientX - box.left, event.clientY - box.top];
};
var fingers = /* @__PURE__ */ new Map();
var pinch = null;
var apart = () => {
	const [a, b] = fingers.values();
	return Math.max(20, Math.abs(a - b));
};
canvas.onpointerdown = (event) => {
	const [x, y] = spot(event);
	fingers.set(event.pointerId, x);
	canvas.setPointerCapture(event.pointerId);
	if (fingers.size > 1) {
		if (drag?.moved && (drag.edge || drag.line)) draw();
		drag = null;
		pinch = fingers.size === 2 ? {
			apart: apart(),
			scale: view.scale
		} : null;
		return;
	}
	const edge = edgeAt(x, y, event.pointerType === "touch" ? 16 : 6), at = edge ? -1 : spanAt(x, y);
	if (edge && event.shiftKey) edge.joint = false;
	drag = {
		x,
		edge,
		at,
		moved: false,
		from: audio.currentTime,
		playing: !audio.paused,
		line: at >= 0 && state.step === "line",
		grab: edge ? timeAt(x) - edge.time : at >= 0 ? timeAt(x) - spans()[at].start : 0
	};
};
canvas.onpointermove = (event) => {
	const [x, y] = spot(event);
	if (fingers.has(event.pointerId)) fingers.set(event.pointerId, x);
	if (pinch) {
		view.scale = clamp(pinch.scale * apart() / pinch.apart, 40, 800);
		return sketch();
	}
	if (fingers.size > 1) return;
	if (!drag) {
		canvas.style.cursor = edgeAt(x, y) ? "ew-resize" : spanAt(x, y) < 0 ? "default" : state.step === "line" ? "grab" : "pointer";
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
	else audio.currentTime = clamp(drag.from - (x - drag.x) / view.scale, 0, state.duration);
	refresh();
};
canvas.onpointerup = (event) => {
	fingers.delete(event.pointerId);
	if (fingers.size < 2) pinch = null;
	if (!drag) return;
	const { moved, edge, at, line, playing } = drag;
	drag = null;
	if (!moved) audio.currentTime = clamp(at >= 0 ? spans()[at].start : timeAt(spot(event)[0]), 0, state.duration);
	else if (edge || line) draw();
	else if (playing) audio.play();
};
canvas.onpointercancel = (event) => {
	fingers.delete(event.pointerId);
	if (fingers.size < 2) pinch = null;
	if (drag?.moved) draw();
	drag = null;
};
canvas.addEventListener("wheel", (event) => {
	event.preventDefault();
	view.scale = clamp(view.scale * (event.deltaY < 0 ? 1.25 : .8), 40, 800);
	sketch();
}, { passive: false });
new ResizeObserver(sketch).observe(canvas);
//#endregion
//#region src/column.ts
var rows = [];
var marked = null;
function draw() {
	const { lines, step, cursor, wordCursor } = state;
	forget();
	marked = null;
	rows = lines.map((line, index) => {
		const row = document.createElement("div");
		row.className = "line " + (line.start == null ? "untimed" : done(line) ? "tapped" : "guess");
		row.onclick = () => select(index);
		const stamp = document.createElement("span");
		stamp.className = "stamp";
		stamp.textContent = line.start == null ? "--:--.--" : clock(line.start);
		const text = document.createElement("span");
		text.className = "words";
		const words = line.words.map((word, k) => {
			const span = document.createElement("span");
			span.className = "word" + (step === "word" && index === cursor && k === wordCursor ? " next" : "");
			span.textContent = word.text;
			span.onclick = (event) => {
				event.stopPropagation();
				select(index, k);
			};
			text.append(span, " ");
			return span;
		});
		row.append(stamp, text);
		return {
			row,
			words
		};
	});
	$("lines").replaceChildren(...rows.map((entry) => entry.row));
	$("lineCount").textContent = `${lines.filter((line) => line.start != null).length}/${lines.length}`;
	$("wordCount").textContent = `${lines.filter(done).length}/${lines.length}`;
	paint();
}
function lineAt(t) {
	let found = -1;
	state.lines.forEach((line, index) => {
		if (line.start != null && line.start <= t) found = index;
	});
	return found;
}
function paint() {
	const { lines, step, cursor, duration } = state;
	if (state.closed) return;
	const t = audio.currentTime;
	$("clock").textContent = `${clock(t)} / ${clock(duration)}`;
	const seek = $("seek");
	if (document.activeElement !== seek) seek.value = String(t);
	const want = step === "review" ? lineAt(t + .3) : cursor < lines.length ? cursor : -1;
	if (want !== marked) {
		if (marked != null) rows[marked]?.row.classList.remove("target");
		rows[want]?.row.classList.add("target");
		const row = (rows[want] ?? rows[step === "review" ? 0 : rows.length - 1])?.row, column = $("lines");
		if (row) column.scrollTo({ top: row.offsetTop + row.offsetHeight / 2 - column.clientHeight / 2 });
		marked = want;
	}
	lines.forEach((line, index) => line.words.forEach((word, k) => {
		const span = rows[index]?.words[k];
		if (!span) return;
		const { start, end } = word;
		const active = start != null && t >= start && t < end;
		span.classList.toggle("sung", start != null && t >= end);
		span.classList.toggle("active", active);
		if (active) span.style.setProperty("--fill", `${100 * (t - start) / (end - start)}%`);
	}));
	sketch();
}
function refresh() {
	forget();
	paint();
}
function frame() {
	if (!audio.paused) paint();
	requestAnimationFrame(frame);
}
//#endregion
//#region src/main.ts
function recognise(index) {
	const line = state.lines[index], words = line.words, begin = line.start, length = lineEnd(line) - begin;
	const weights = words.map((word) => weight(word.text)), total = sum(weights);
	let used = 0;
	const even = words.every((word, k) => {
		const from = begin + length * used / total;
		used += weights[k];
		return Math.abs(word.start - from) < .0125 && Math.abs(word.end - (begin + length * used / total)) < .0125;
	});
	line.byWord = !even;
	words.forEach((word, k) => {
		word.cut = k + 1 < words.length && words[k + 1].start - word.end > .005;
	});
	if (!even || Math.abs(length - autoLength(index, begin, .06 * total)) > .02) line.end = begin + length;
}
function loudness(buffer) {
	const data = new DataView(buffer);
	let channels = 2, rate = 44100;
	for (let at = 12; at + 8 <= data.byteLength;) {
		const name = String.fromCharCode(data.getUint8(at), data.getUint8(at + 1), data.getUint8(at + 2), data.getUint8(at + 3));
		const size = data.getUint32(at + 4, true);
		if (name === "fmt ") {
			channels = data.getUint16(at + 10, true);
			rate = data.getUint32(at + 12, true);
		}
		if (name === "data") {
			const samples = new Int16Array(buffer, at + 8, Math.min(size, data.byteLength - at - 8) >> 1);
			const per = rate * channels / 200;
			const levels = new Float64Array(Math.ceil(samples.length / per));
			for (let i = 0; i < samples.length; i++) levels[Math.floor(i / per)] += samples[i] * samples[i];
			const top = Math.sqrt(levels.reduce((a, b) => Math.max(a, b), 1));
			return levels.map((level) => Math.sqrt(level) / top);
		}
		at += 8 + size + (size & 1);
	}
	return null;
}
Object.assign(window, { editor: {
	state,
	view,
	audio,
	tap,
	cut,
	undoLast,
	select,
	setStep,
	clearFromHere,
	save,
	closeEditor,
	openEditor,
	paint,
	place,
	refresh,
	spans
} });
var saved = await (await fetch(base + "state")).json();
$("title").textContent = saved.title;
document.title = `${saved.title}: lyrics sync`;
state.duration = saved.duration;
$("seek").max = String(saved.duration);
var song = await (await fetch(base + "audio")).blob();
audio.src = URL.createObjectURL(song);
state.lines = saved.lines.map((line) => ({
	start: line.start,
	end: null,
	byWord: false,
	words: line.words.map((word) => ({
		...word,
		cut: false
	}))
}));
state.lines.forEach((line, index) => {
	if (line.start == null) return;
	if (line.words.some((word) => word.start == null)) spread(index);
	else recognise(index);
});
var { lines } = state, untimed = lines.filter((line) => line.start == null).length, guessed = lines.filter((line) => !done(line)).length - untimed;
setStep(untimed || !lines.length ? "line" : guessed ? "word" : "review");
if (!lines.length) openEditor();
say(!lines.length ? "Add the lyrics to begin" : untimed ? `${untimed} of ${lines.length} lines untimed` : guessed ? `${guessed} of ${lines.length} lines have estimated word times` : "Every line is timed");
frame();
try {
	state.peaks = loudness(await song.arrayBuffer());
} catch (problem) {
	console.warn("no waveform:", problem);
}
sketch();
//#endregion
