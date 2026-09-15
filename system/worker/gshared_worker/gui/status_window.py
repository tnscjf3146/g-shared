"""The status window. Tk only -- what it shows is the presenter's decision.

**Every Tk call happens on the thread that made the window.** The Worker runs
on another thread and never touches this module: it publishes values into a
:class:`~gshared_worker.status_events.StatusQueue`, and this window drains that
queue from a Tk ``after`` timer on its own thread. There is no other way in.

The [종료] button and the title bar's X call the same method, so there is one
shutdown decision whichever the supplier reaches for.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from .status_presenter import ShutdownAction, StatusPresenter, Tone

_TITLE = "G-shared"

_TONE_COLOUR = {
    Tone.BUSY: "#1a73e8",
    Tone.GOOD: "#188038",
    Tone.WORK: "#8430ce",
    Tone.WARN: "#e37400",
    Tone.BAD: "#d93025",
    Tone.DONE: "#5f6368",
}

_QUIET = "#5f6368"


class StatusWindow:
    """Draws the presenter, pumps the queue, and hands closing to the app."""

    #: How often the queue is drained. Fast enough that a line appears as it is
    #: said; slow enough to cost nothing while a Worker sits online for days.
    POLL_MS = 100

    def __init__(
        self,
        presenter: StatusPresenter,
        feed: Any,
        on_action: Callable[[ShutdownAction], None],
        on_event: Callable[[Any], None] | None = None,
    ) -> None:
        self._presenter = presenter
        self._feed = feed
        self._on_action = on_action
        self._on_event = on_event
        self._root: tk.Tk | None = None
        self._shown_lines = 0
        self.closed = False

    # -- life -------------------------------------------------------------

    def open(self) -> "StatusWindow":
        """Build the window and draw it once, before anything slow starts."""
        root = self._root = tk.Tk()
        root.title(_TITLE)
        root.minsize(520, 440)
        root.protocol("WM_DELETE_WINDOW", self.close_requested)

        body = ttk.Frame(root, padding=(16, 12, 16, 12))
        body.pack(fill="both", expand=True)

        header = ttk.Frame(body)
        header.pack(fill="x")
        self._dot = tk.Label(header, text="●", font=("Segoe UI", 14))
        self._dot.pack(side="left")
        self._headline = ttk.Label(header, font=("Segoe UI", 14, "bold"))
        self._headline.pack(side="left", padx=(6, 0))

        info = ttk.Frame(body)
        info.pack(fill="x", pady=(12, 10))
        info.columnconfigure(1, weight=1)
        self._fields: dict[str, ttk.Label] = {}
        for row, (key, label) in enumerate((
                ("gpu", "GPU"), ("worker", "Worker"),
                ("job", "현재 작업"), ("progress", "진행률"))):
            ttk.Label(info, text=label, foreground=_QUIET).grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=2)
            value = ttk.Label(info)
            value.grid(row=row, column=1, sticky="w", pady=2)
            self._fields[key] = value

        ttk.Label(body, text="최근 진행 내용", foreground=_QUIET).pack(anchor="w")
        log_frame = ttk.Frame(body)
        log_frame.pack(fill="both", expand=True, pady=(4, 12))
        self._log = tk.Text(
            log_frame, height=12, width=70, wrap="word", state="disabled",
            font=("Consolas", 9), relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=scroll.set)
        self._log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        footer = ttk.Frame(body)
        footer.pack(fill="x")
        self._button = ttk.Button(footer, command=self.close_requested)
        self._button.pack(side="right")

        self._render()
        root.update_idletasks()
        return self

    def run(self) -> None:
        """Pump the queue until the window is closed."""
        if self._root is None:
            self.open()
        self._root.after(self.POLL_MS, self._pump)
        self._root.mainloop()

    def close(self) -> None:
        """Destroy the window. Only once the Worker has finished."""
        if self.closed or self._root is None:
            return
        self.closed = True
        self._root.quit()
        self._root.destroy()

    def later(self, milliseconds: int, callback: Callable[[], None]) -> None:
        """Run ``callback`` on this window's thread, soon. Ignored once closed."""
        if not self.closed and self._root is not None:
            self._root.after(milliseconds, callback)

    # -- asking -------------------------------------------------------------

    def close_requested(self) -> None:
        """The [종료] button and the X. One path for both."""
        if self.closed:
            return
        action = self._presenter.request_close(self.confirm)
        self._on_action(action)
        if not self.closed:
            self._render()

    def confirm(self, title: str, question: str) -> bool:
        return bool(messagebox.askyesno(
            title, question, parent=self._root, icon="warning"))

    def show_error(self, text: str) -> None:
        if not self.closed:
            messagebox.showerror(_TITLE, text, parent=self._root)

    # -- drawing ------------------------------------------------------------

    def _pump(self) -> None:
        if self.closed:
            return
        for event in self._feed.drain():
            self._presenter.apply(event)
            if self._on_event is not None:
                self._on_event(event)
            if self.closed:
                return
        self._render()
        self.later(self.POLL_MS, self._pump)

    def _render(self) -> None:
        view = self._presenter.view()
        self._dot.configure(foreground=_TONE_COLOUR[view.tone])
        self._headline.configure(text=view.headline)
        for key in ("gpu", "worker", "job", "progress"):
            self._fields[key].configure(text=getattr(view, key))
        self._button.configure(
            text=view.button_text,
            state="normal" if view.button_enabled else "disabled")

        new, self._shown_lines = self._presenter.lines_since(self._shown_lines)
        if new:
            self._log.configure(state="normal")
            for line in new:
                self._log.insert("end", line + "\n")
            excess = int(self._log.index("end-1c").split(".")[0]) - 1 \
                - StatusPresenter.MAX_LINES
            if excess > 0:
                self._log.delete("1.0", f"{excess + 1}.0")
            self._log.configure(state="disabled")
            self._log.see("end")
