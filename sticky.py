#!/usr/bin/env python3
"""A small always-on-top 'sticky note' window for folder_assistant.

Paste a folder or file path, then click Report, Ask or Summarize.
Run:  python sticky.py
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

import folder_assistant as fa

NOTE_BG = "#FFF59D"
NOTE_DARK = "#5D4037"
FONT = ("Segoe UI", 10)


class StickyNote:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.last_progress = ""
        self.chat_root: Path | None = None
        self.chat_messages: list = []

        root.title("Folder assistant")
        root.geometry("460x560")
        root.configure(bg=NOTE_BG)
        root.attributes("-topmost", True)

        self.pin = tk.BooleanVar(value=True)
        top = tk.Frame(root, bg=NOTE_BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="Folder or file path", bg=NOTE_BG, fg=NOTE_DARK, font=FONT).pack(side="left")
        tk.Checkbutton(top, text="Keep on top", variable=self.pin, command=self.toggle_pin,
                       bg=NOTE_BG, activebackground=NOTE_BG, font=FONT).pack(side="right")

        path_row = tk.Frame(root, bg=NOTE_BG)
        path_row.pack(fill="x", padx=10)
        self.path = tk.Entry(path_row, font=FONT)
        self.path.pack(side="left", fill="x", expand=True)
        tk.Button(path_row, text="Browse", command=self.browse, font=FONT).pack(side="left", padx=(6, 0))

        tk.Label(root, text="Question (for Ask)", bg=NOTE_BG, fg=NOTE_DARK, font=FONT).pack(anchor="w", padx=10, pady=(8, 0))
        self.question = tk.Entry(root, font=FONT)
        self.question.pack(fill="x", padx=10)
        self.question.bind("<Return>", lambda _: self.run_ask())

        options = tk.Frame(root, bg=NOTE_BG)
        options.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(options, text="Old after (days)", bg=NOTE_BG, font=FONT).pack(side="left")
        self.days = tk.Spinbox(options, from_=30, to=3650, increment=30, width=6, font=FONT)
        self.days.delete(0, "end")
        self.days.insert(0, "365")
        self.days.pack(side="left", padx=(4, 12))
        tk.Label(options, text="Depth", bg=NOTE_BG, font=FONT).pack(side="left")
        self.depth = tk.Spinbox(options, from_=0, to=6, width=3, font=FONT)
        self.depth.delete(0, "end")
        self.depth.insert(0, "2")
        self.depth.pack(side="left", padx=(4, 12))
        self.show_all = tk.BooleanVar(value=False)
        tk.Checkbutton(options, text="List all folders", variable=self.show_all,
                       bg=NOTE_BG, activebackground=NOTE_BG, font=FONT).pack(side="left")

        buttons = tk.Frame(root, bg=NOTE_BG)
        buttons.pack(fill="x", padx=10, pady=8)
        self.buttons = [
            tk.Button(buttons, text="Report", command=self.run_report, font=FONT, width=10),
            tk.Button(buttons, text="Ask", command=self.run_ask, font=FONT, width=10),
            tk.Button(buttons, text="Summarize", command=self.run_summarize, font=FONT, width=10),
        ]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="Copy", command=self.copy_output, font=FONT).pack(side="right")

        self.status = tk.Label(root, text="Ready.", bg=NOTE_BG, fg=NOTE_DARK, font=FONT, anchor="w")
        self.status.pack(fill="x", padx=10)

        self.output = scrolledtext.ScrolledText(root, wrap="word", font=("Consolas", 9), bg="#FFFDE7", relief="flat")
        self.output.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        root.after(100, self.poll_events)

    def toggle_pin(self):
        self.root.attributes("-topmost", self.pin.get())

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path.delete(0, "end")
            self.path.insert(0, folder)

    def copy_output(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.output.get("1.0", "end").strip())
        self.status.config(text="Copied to clipboard.")

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

    def show(self, text: str):
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)

    def start(self, label: str, work):
        if self.busy:
            return
        self.busy = True
        for b in self.buttons:
            b.config(state="disabled")
        self.status.config(text=f"{label}...")
        self.last_progress = ""

        def runner():
            try:
                self.events.put(("done", work()))
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
                    self.status.config(text=payload)
                    continue
                self.busy = False
                for b in self.buttons:
                    b.config(state="normal")
                if kind == "done":
                    self.show(payload)
                    done = self.last_progress if "done" in self.last_progress.lower() else ""
                    self.status.config(text=done or "Done.")
                else:
                    self.status.config(text="Failed.")
                    self.show(payload)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def run_report(self):
        path = self.target()
        if not path:
            return
        if path.is_file():
            path = path.parent
        days, depth, show_all = self.number(self.days, 365), self.number(self.depth, 2), self.show_all.get()
        self.start("Scanning", lambda: fa.cleanup_report(path, days, max(depth, 1), show_all, self.progress))

    def run_ask(self):
        path = self.target()
        question = self.question.get().strip()
        if not path:
            return
        if not question:
            messagebox.showinfo("Folder assistant", "Type a question first.")
            return
        folder = path.parent if path.is_file() else path
        if path.is_file():
            question = f"About the file {path.name}: {question}"
        if folder != self.chat_root:
            self.chat_root = folder
            self.chat_messages = [{"role": "system", "content": fa.SYSTEM_PROMPT.format(root=folder)}]
        tools = fa.FolderTools(folder)
        messages = self.chat_messages
        self.question.delete(0, "end")

        def work():
            answer = fa.ask(question, messages, tools, on_step=lambda step: self.progress(f"Searching: {step}"[:120]))
            self.progress("Answer done.")
            return f"Q: {question}\n\n{answer}"

        self.start("Thinking", work)

    def run_summarize(self):
        path = self.target()
        if not path:
            return
        if path.is_file():
            self.start("Summarizing file", lambda: fa.summarize_file(path))
            return
        depth = self.number(self.depth, 2)
        count = len(fa.folders_to_summarize(path, depth))
        if not messagebox.askyesno(
            "Folder assistant",
            f"This writes {fa.SUMMARY_FILE} into {count} folder(s) and makes {count} model call(s).\nContinue?",
        ):
            return

        def work():
            written = fa.summarize_folders(path, depth, self.progress)
            top = path / fa.SUMMARY_FILE
            preview = top.read_text(encoding="utf-8") if top.exists() else ""
            listing = "\n".join(str(p) for p in written)
            return f"Wrote {len(written)} summaries:\n{listing}\n\n--- {top} ---\n{preview}"

        self.start("Summarizing", work)


def main():
    root = tk.Tk()
    StickyNote(root)
    root.mainloop()


if __name__ == "__main__":
    main()
