"""A line in the terminal that says how far a long step has got, rewritten in place."""
import sys
import time


def clock(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Progress:
    """Shows "label: 1:23 of 3:03 (45%), 2.1x real time, about 0:40 left", at most twice a
    second. When the output is not a terminal, it prints a plain line every 10% instead."""

    def __init__(self, label: str, total: float):
        self.label, self.total = label, total
        self.start = time.monotonic()
        self.shown, self.step, self.width = 0.0, -1, 0
        self.live = sys.stdout.isatty()

    def update(self, done: float) -> None:
        now = time.monotonic()
        fraction = min(1.0, done / self.total) if self.total > 0 else 0.0
        if self.live:
            if now - self.shown < 0.5:
                return
        elif int(fraction * 10) == self.step:
            return
        self.shown, self.step = now, int(fraction * 10)
        speed = done / (now - self.start) if now > self.start else 0.0
        left = f", about {clock((self.total - done) / speed)} left" if speed > 0 else ""
        self.say(f"{self.label}: {clock(done)} of {clock(self.total)} ({fraction:.0%}), {speed:.1f}x real time{left}",
                 end="")

    def elapsed(self) -> str:
        return clock(time.monotonic() - self.start)

    def close(self, message: str = "") -> None:
        """End the line with `message`, or by default with how long the step took."""
        self.say(message or f"{self.label}: done in {self.elapsed()}")

    def say(self, text: str, end: str = "\n") -> None:
        if self.live:  # padded to cover a longer line before it
            print(f"\r{text:<{self.width}}", end=end, flush=True)
            self.width = 0 if end else max(self.width, len(text))
        else:
            print(text, flush=True)
