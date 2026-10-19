#!/usr/bin/env python3
"""A small always-on-top chat window for folder_assistant.

Paste a folder or file path, then chat with it. Report and Summarize post their results into the chat.
Run:  python sticky.py
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import folder_assistant as fa

NOTE_BG = "#FFF59D"
NOTE_DARK = "#5D4037"
CHAT_BG = "#FFFDE7"
YOU_BG = "#FFE082"
BOT_BG = "#FFFFFF"
FONT = ("Segoe UI", 10)
SMALL = ("Segoe UI", 8)
MONO = ("Consolas", 9)


def report_for_chat(markdown: str) -> str:
    """Turn the report's Markdown table into short per-folder lines that fit a narrow window."""
    lines, out = markdown.splitlines(), []
    for line in lines:
        if not line.startswith("|"):
            out.append(line.lstrip("# "))
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] in ("Folder", "---") or len(cells) < 7:
            continue
        folder, changed, age, based, size, files, why = cells[:7]
        out.append(f"\u2022 {folder}\n    {changed}, {age} days ({based}), {size}, {files} files"
                   + ("" if why == "-" else f"\n    {why}"))
    return "\n".join(out).strip()


class StickyChat:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.last_progress = ""
        self.last_answer = ""
        self.chat_root: Path | None = None
        self.chat_messages: list = []

        root.title("Folder assistant")
        root.geometry("480x640")
        root.minsize(380, 420)
        root.configure(bg=NOTE_BG)
        root.attributes("-topmost", True)

        top = tk.Frame(root, bg=NOTE_BG)
        top.pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(top, text="Folder or file", bg=NOTE_BG, fg=NOTE_DARK, font=FONT).pack(side="left")
        self.pin = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="Keep on top", variable=self.pin, command=self.toggle_pin,
                       bg=NOTE_BG, activebackground=NOTE_BG, font=SMALL).pack(side="right")

        path_row = tk.Frame(root, bg=NOTE_BG)
        path_row.pack(fill="x", padx=10)
        self.path = tk.Entry(path_row, width=1, font=FONT)
        self.path.pack(side="left", fill="x", expand=True)
        tk.Button(path_row, text="Browse", command=self.browse, font=SMALL).pack(side="left", padx=(6, 0))

        tools = tk.Frame(root, bg=NOTE_BG)
        tools.pack(fill="x", padx=10, pady=(6, 0))
        self.report_btn = tk.Button(tools, text="Report", command=self.run_report, font=SMALL)
        self.summarize_btn = tk.Button(tools, text="Summarize", command=self.run_summarize, font=SMALL)
        self.report_btn.pack(side="left")
        self.summarize_btn.pack(side="left", padx=(4, 10))
        tk.Label(tools, text="days", bg=NOTE_BG, font=SMALL).pack(side="left")
        self.days = self.spinbox(tools, 30, 3650, 30, "365", 5)
        tk.Label(tools, text="depth", bg=NOTE_BG, font=SMALL).pack(side="left", padx=(6, 0))
        self.depth = self.spinbox(tools, 0, 6, 1, "2", 2)
        self.show_all = tk.BooleanVar(value=False)
        tk.Checkbutton(tools, text="all", variable=self.show_all, bg=NOTE_BG,
                       activebackground=NOTE_BG, font=SMALL).pack(side="left", padx=(4, 0))

        chat_frame = tk.Frame(root, bg=NOTE_BG)
        chat_frame.pack(fill="both", expand=True, padx=10, pady=(8, 2))
        self.chat = tk.Text(chat_frame, width=1, height=1, wrap="word", bg=CHAT_BG, relief="flat", font=FONT,
                            padx=6, pady=6, state="disabled", cursor="arrow")
        scroll = tk.Scrollbar(chat_frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("you_name", justify="right", font=SMALL, foreground=NOTE_DARK, spacing1=10)
        self.chat.tag_configure("you", justify="right", background=YOU_BG, lmargin1=90, lmargin2=90,
                                rmargin=4, spacing1=2, spacing3=2)
        self.chat.tag_configure("bot_name", font=SMALL, foreground=NOTE_DARK, spacing1=10)
        self.chat.tag_configure("bot", background=BOT_BG, lmargin1=4, lmargin2=4, rmargin=50,
                                spacing1=2, spacing3=2)
        self.chat.tag_configure("mono", font=MONO)
        self.chat.tag_configure("note", justify="center", font=SMALL, foreground="#8D6E63", spacing1=8)

        status_row = tk.Frame(root, bg=NOTE_BG)
        status_row.pack(fill="x", padx=10)
        tk.Button(status_row, text="New chat", command=self.new_chat, font=SMALL).pack(side="right")
        tk.Button(status_row, text="Copy last", command=self.copy_last, font=SMALL).pack(side="right", padx=(0, 4))
        self.status = tk.Label(status_row, text="", bg=NOTE_BG, fg="#8D6E63", font=SMALL, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        bottom = tk.Frame(root, bg=NOTE_BG)
        bottom.pack(fill="x", padx=10, pady=(2, 10))
        self.input = tk.Text(bottom, height=3, width=1, wrap="word", font=FONT, relief="flat")
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Return>", self.on_enter)
        self.send_btn = tk.Button(bottom, text="Send", command=self.run_ask, font=FONT, width=6)
        self.send_btn.pack(side="left", padx=(6, 0), fill="y")

        self.note("Paste a folder or file path above, then ask a question.\n"
                  "Enter sends, Shift+Enter adds a new line.")
        root.after(100, self.poll_events)

    def spinbox(self, parent, low, high, step, value, width) -> tk.Spinbox:
        box = tk.Spinbox(parent, from_=low, to=high, increment=step, width=width, font=SMALL)
        box.delete(0, "end")
        box.insert(0, value)
        box.pack(side="left", padx=(2, 0))
        return box

    # ---- chat display -------------------------------------------------

    def append(self, chunks: list[tuple[str, tuple]]):
        self.chat.configure(state="normal")
        for text, tags in chunks:
            self.chat.insert("end", text, tags)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def note(self, text: str):
        self.append([(text + "\n", ("note",))])

    def say_you(self, text: str):
        self.append([("You\n", ("you_name",)), (text + "\n", ("you",))])

    def say_bot(self, text: str, mono: bool = False):
        tags = ("bot", "mono") if mono else ("bot",)
        self.append([("Assistant\n", ("bot_name",)), (text.strip() + "\n", tags)])
        self.last_answer = text.strip()

    # ---- small actions ------------------------------------------------

    def toggle_pin(self):
        self.root.attributes("-topmost", self.pin.get())

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path.delete(0, "end")
            self.path.insert(0, folder)

    def copy_last(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_answer)
        self.status.config(text="Copied the last answer.")

    def new_chat(self):
        self.chat_root = None
        self.chat_messages = []
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self.note("New chat.")

    def on_enter(self, event):
        if event.state & 0x0001:
            return None
        self.run_ask()
        return "break"

    def target(self) -> Path | None:
        raw = self.path.get().strip().strip('"').strip("'")
        if not raw:
            messagebox.showinfo("Folder assistant", "Paste a folder or file path first.")
            return None
        path = Path(raw).expanduser()
        if not path.exists():
            messagebox.showerror("Folder assistant", f"Not found:\n{path}")
            return None
        return path.resolve()

    def number(self, spinbox: tk.Spinbox, default: int) -> int:
        try:
            return int(spinbox.get())
        except ValueError:
            return default

    # ---- background work ----------------------------------------------

    def start(self, label: str, work, mono: bool = False):
        if self.busy:
            return
        self.busy = True
        self.last_progress = ""
        for b in (self.send_btn, self.report_btn, self.summarize_btn):
            b.config(state="disabled")
        self.status.config(text=f"{label}...")

        def runner():
            try:
                self.events.put(("done", (work(), mono)))
            except SystemExit as e:
                self.events.put(("error", str(e)))
            except Exception as e:
                self.events.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=runner, daemon=True).start()

    def progress(self, message: str):
        self.events.put(("progress", message))

    def poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.last_progress = payload
                    self.status.config(text=payload[:110])
                    continue
                self.busy = False
                for b in (self.send_btn, self.report_btn, self.summarize_btn):
                    b.config(state="normal")
                if kind == "done":
                    text, mono = payload
                    self.say_bot(text, mono)
                    done = self.last_progress if "done" in self.last_progress.lower() else ""
                    self.status.config(text=done)
                else:
                    self.say_bot(f"Something went wrong:\n{payload}")
                    self.status.config(text="Failed.")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    # ---- commands -----------------------------------------------------

    def run_ask(self):
        question = self.input.get("1.0", "end").strip()
        if not question or self.busy:
            return
        path = self.target()
        if not path:
            return
        folder = path.parent if path.is_file() else path
        if folder != self.chat_root:
            self.chat_root = folder
            self.chat_messages = [{"role": "system", "content": fa.system_prompt(folder)}]
            has_map = (folder / fa.SUMMARY_FILE).is_file()
            self.note(f"Chatting about {folder}" + (" (using readsummary.md as a map)" if has_map else ""))
        self.input.delete("1.0", "end")
        self.say_you(question)
        if path.is_file():
            question = f"About the file {path.name}: {question}"
        tools = fa.FolderTools(folder)
        messages = self.chat_messages

        def work():
            answer = fa.ask(question, messages, tools, on_step=lambda step: self.progress(f"Looking: {step}"))
            self.progress("Answer done.")
            return answer

        self.start("Thinking", work)

    def run_report(self):
        path = self.target()
        if not path:
            return
        if path.is_file():
            path = path.parent
        days, depth, show_all = self.number(self.days, 365), self.number(self.depth, 2), self.show_all.get()
        self.say_you(f"Report on {path.name} (older than {days} days, depth {max(depth, 1)})")
        self.start("Scanning",
                   lambda: report_for_chat(fa.cleanup_report(path, days, max(depth, 1), show_all, self.progress)))

    def run_summarize(self):
        path = self.target()
        if not path:
            return
        if path.is_file():
            self.say_you(f"Summarize {path.name}")
            self.start("Summarizing file", lambda: fa.summarize_file(path))
            return
        depth = self.number(self.depth, 2)
        count = len(fa.folders_to_summarize(path, depth))
        if not messagebox.askyesno(
            "Folder assistant",
            f"This writes {fa.SUMMARY_FILE} into {count} folder(s) and makes {count} model call(s).\nContinue?",
        ):
            return
        self.say_you(f"Summarize {path.name} (depth {depth})")

        def work():
            written = fa.summarize_folders(path, depth, self.progress)
            top = path / fa.SUMMARY_FILE
            preview = top.read_text(encoding="utf-8") if top.exists() else ""
            if self.chat_root == path:
                self.chat_root = None
            return f"Wrote {len(written)} summaries. Top-level summary:\n\n{preview}"

        self.start("Summarizing", work)


def main():
    root = tk.Tk()
    StickyChat(root)
    root.mainloop()


if __name__ == "__main__":
    main()
