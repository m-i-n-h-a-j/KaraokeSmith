"""Local browser editor for typing in lyrics and tapping their timing by hand."""
import json
import secrets
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import lyrics
from .media import run

PAGE = Path(__file__).with_name("sync.html")


def to_rows(drafts: list[lyrics.Draft]) -> list[dict]:
    """Lyrics as the editor holds them: rows of words whose times may still be unknown."""
    rows = []
    for draft in drafts:
        if draft.words:
            words = [{"text": word.text, "start": word.start, "end": word.end} for word in draft.words]
            rows.append({"start": draft.words[0].start, "end": None, "words": words})
        else:
            # A line with only a start time: the editor spreads its words by length.
            words = [{"text": text, "start": None, "end": None} for text in draft.text.split()]
            rows.append({"start": draft.start, "end": None, "words": words})
    return rows


def to_lrc(rows: list[dict]) -> str:
    """LRC text for the editor's rows. Rows that are not fully timed are written without a
    timestamp, which keeps their text for the next editing session."""
    out = []
    for row in rows:
        words = [word for word in row["words"] if word["text"].strip()]
        if not words:
            continue
        if all(word["start"] is not None and word["end"] is not None for word in words):
            out.append(lyrics.format_line([lyrics.Word(word["text"].strip(), word["start"], word["end"]) for word in words]))
        else:
            out.append(" ".join(word["text"].strip() for word in words))
    return "\n".join(out) + "\n"


def edit(song: Path, lrc: Path, drafts: list[lyrics.Draft], duration: float, title: str,
         open_browser: bool = True) -> None:
    """Serve the editor on localhost until the user closes it. Saving in the editor writes `lrc`."""
    # The page address carries a random token, so no other site open in the browser can reach
    # the editor's save endpoint by guessing its port.
    token = secrets.token_urlsafe(16)
    with tempfile.TemporaryDirectory() as folder:
        # WAV plays in every browser and seeks exactly, whatever format the song is in.
        audio = Path(folder) / "song.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(song), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(audio)])
        state = {"title": title, "duration": duration, "file": lrc.name, "lines": to_rows(drafts)}

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status: int, body: bytes = b"", kind: str = "text/plain; charset=utf-8") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == f"/{token}/":
                    self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif self.path == f"/{token}/state":
                    self.reply(200, json.dumps(state).encode(), "application/json")
                elif self.path == f"/{token}/audio":
                    self.reply(200, audio.read_bytes(), "audio/wav")
                else:
                    self.reply(404)

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == f"/{token}/save":
                    rows = json.loads(body)
                    text = to_lrc(rows)
                    if not text.strip():
                        self.reply(400, b"There are no lyrics to save.")
                        return
                    lrc.write_text(text, encoding="utf-8")
                    state["lines"] = rows
                    print(f"saved {lrc}", flush=True)
                    self.reply(200, f"Saved {lrc.name}".encode())
                elif self.path == f"/{token}/close":
                    self.reply(200, b"closed")
                    threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    self.reply(404)

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        url = f"http://127.0.0.1:{server.server_address[1]}/{token}/"
        print(f"lyrics editor: {url}\nSave there, then press Close in the page (or Ctrl+C here) to go on.", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
