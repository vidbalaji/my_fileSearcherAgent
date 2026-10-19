#!/usr/bin/env python3
"""Folder assistant: ask questions, find old folders, and write a readsummary.md per folder.

Usage:
    python folder_assistant.py ask /path/to/folder
    python folder_assistant.py ask /path/to/folder -q "where do we validate the order quantity?"
    python folder_assistant.py report /path/to/folder --days 365
    python folder_assistant.py summarize /path/to/folder --depth 2
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MODEL = os.getenv("MODEL", "openai/gpt-4.1")
MAX_TOOL_ROUNDS = 15
MAX_SEARCH_HITS = 60
MAX_READ_LINES = 300
MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build", "target"}
REGENERABLE_DIRS = {
    "node_modules", "__pycache__", ".venv", "venv", "build", "dist", "target",
    ".pytest_cache", ".mypy_cache", ".gradle", ".next", "coverage", ".cache",
}
SUMMARY_FILE = "readsummary.md"
SUMMARY_INPUT_CHARS = 12_000
SUMMARY_PROMPT = """Write a short summary of this folder for someone opening it for the first time.
Use Markdown with these sections:
# <folder name>
**Purpose:** one or two sentences on what this folder is for.
## Contents
A bullet per important file or subfolder with a few words on what it holds.
## Notes
Anything notable (entry points, config, how pieces connect, things that look outdated). Omit if nothing.
Only describe what the listing and excerpts show. Do not guess beyond them.

Folder: {name}
Last changed: {last_changed}

{material}"""

SYSTEM_PROMPT = """You help the user find and understand things in a folder of files at: {root}

How to work:
- Always look in the files before answering. Start with search or list_files, then read_file the most relevant parts.
- Try a few search terms (synonyms, class/function names, config keys) if the first one finds nothing.
- Answer briefly, then list the references you used as `path:line` with one line on why each matters.
- Only state what the files show. If you could not find it, say so and say where you looked.
- If the question is too vague to search, ask one short clarifying question instead.
- You are read-only. Never suggest that you changed a file."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a folder, optionally filtered by a glob like '*.java' or '*config*'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder relative to the root. Use '.' for the root."},
                    "pattern": {"type": "string", "description": "Optional filename glob."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search file contents with a case-insensitive regular expression. Returns path:line: text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression, e.g. 'order.?qty|quantity'."},
                    "file_glob": {"type": "string", "description": "Optional filename glob to limit the search."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read part of a text file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the root."},
                    "start_line": {"type": "integer", "description": "First line, 1-based. Default 1."},
                    "end_line": {"type": "integer", "description": "Last line. Default start_line + 200."},
                },
                "required": ["path"],
            },
        },
    },
]


class FolderTools:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _resolve(self, relative: str) -> Path:
        target = (self.root / relative).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"'{relative}' is outside the folder")
        return target

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _walk_files(self, start: Path):
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    @staticmethod
    def _is_text(path: Path) -> bool:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return False
            with path.open("rb") as f:
                return b"\0" not in f.read(4096)
        except OSError:
            return False

    def list_files(self, path: str = ".", pattern: str | None = None) -> str:
        start = self._resolve(path)
        if not start.is_dir():
            return f"Not a folder: {path}"
        names = [
            self._relative(p)
            for p in self._walk_files(start)
            if not pattern or fnmatch.fnmatch(p.name.lower(), pattern.lower())
        ]
        if not names:
            return "No files found."
        shown = names[:200]
        more = f"\n... and {len(names) - len(shown)} more" if len(names) > len(shown) else ""
        return "\n".join(shown) + more

    def search(self, pattern: str, file_glob: str | None = None) -> str:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Invalid regex: {e}"
        hits = []
        for p in self._walk_files(self.root):
            if file_glob and not fnmatch.fnmatch(p.name.lower(), file_glob.lower()):
                continue
            if not self._is_text(p):
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if regex.search(line):
                    hits.append(f"{self._relative(p)}:{number}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + "\n... more matches; narrow the pattern or file_glob."
        return "\n".join(hits) if hits else "No matches."

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Not a file: {path}"
        if not self._is_text(target):
            return f"Not a readable text file: {path}"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line or start + 200, start + MAX_READ_LINES - 1)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}"

    def call(self, name: str, arguments: dict) -> str:
        handlers = {"list_files": self.list_files, "search": self.search, "read_file": self.read_file}
        if name not in handlers:
            return f"Unknown tool: {name}"
        try:
            return handlers[name](**arguments)
        except (TypeError, ValueError, OSError) as e:
            return f"Error: {e}"


def ask(question: str, messages: list, tools: FolderTools, verbose: bool) -> str:
    import litellm

    messages.append({"role": "user", "content": question})
    for _ in range(MAX_TOOL_ROUNDS):
        response = litellm.completion(model=MODEL, messages=messages, tools=TOOLS)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or ""

        for call in message.tool_calls:
            arguments = json.loads(call.function.arguments or "{}")
            if verbose:
                print(f"  > {call.function.name} {arguments}", file=sys.stderr)
            result = tools.call(call.function.name, arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return "Stopped after too many search steps. Try a more specific question."


def folder_stats(folder: Path) -> tuple[float, int, int]:
    """Return (newest file modification time, total bytes, file count) for everything under the folder."""
    newest, size, count = 0.0, 0, 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            if name == SUMMARY_FILE:
                continue
            try:
                st = (Path(dirpath) / name).stat()
            except OSError:
                continue
            newest = max(newest, st.st_mtime)
            size += st.st_size
            count += 1
    return newest or folder.stat().st_mtime, size, count


def git(folder: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def last_changed(folder: Path, file_mtime: float) -> tuple[float, str]:
    """Use the last git commit for folders inside a repo, since clone/pull resets file dates."""
    commit_time = git(folder, "log", "-1", "--format=%ct", "--", ".")
    if not commit_time:
        return file_mtime, "file dates"
    if git(folder, "status", "--porcelain", "--", "."):
        return file_mtime, "uncommitted changes"
    return float(commit_time), "last commit"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def cleanup_report(root: Path, days: int, max_depth: int, show_all: bool = False) -> str:
    now = datetime.now().timestamp()
    rows = []
    total_bytes = 0

    def visit(folder: Path, depth: int):
        nonlocal total_bytes
        for child in sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()):
            if child.name == ".git":
                continue
            file_mtime, size, count = folder_stats(child)
            newest, source = last_changed(child, file_mtime)
            age_days = int((now - newest) // 86400)
            reasons = []
            if child.name in REGENERABLE_DIRS:
                reasons.append("generated/cache folder, can be rebuilt")
            if count == 0:
                reasons.append("empty")
            elif age_days >= days:
                reasons.append(f"not changed in {age_days} days")
            if reasons:
                total_bytes += size
            if reasons or show_all:
                why = "; ".join(reasons) or "-"
                rows.append((child.relative_to(root).as_posix(), newest, age_days, size, count, source, why))
            if not reasons and depth < max_depth:
                visit(child, depth + 1)

    visit(root, 1)
    if not rows:
        return (f"No folders older than {days} days, empty, or regenerable found under {root} "
                f"(checked {max_depth} levels). Try --all to see every folder's age.")

    lines = [
        f"# Folders under {root}" if show_all else f"# Cleanup candidates under {root}",
        f"Review before deleting. Nothing has been deleted. Potential space: {human_size(total_bytes)}",
        "",
        "| Folder | Last changed | Age (days) | Based on | Size | Files | Why |",
        "|---|---|---|---|---|---|---|",
    ]
    for path, newest, age, size, count, source, why in sorted(rows, key=lambda r: (r[6] == "-", -r[3])):
        changed = datetime.fromtimestamp(newest).strftime("%Y-%m-%d")
        lines.append(f"| {path} | {changed} | {age} | {source} | {human_size(size)} | {count} | {why} |")
    return "\n".join(lines)


def folders_to_summarize(root: Path, max_depth: int) -> list[Path]:
    folders = []
    for dirpath, dirnames, _ in os.walk(root):
        folder = Path(dirpath)
        depth = len(folder.relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d not in REGENERABLE_DIRS)
        if depth >= max_depth:
            dirnames[:] = []
        folders.append(folder)
    return sorted(folders, key=lambda f: -len(f.parts))


def summary_material(folder: Path, tools: FolderTools) -> str:
    entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    listing = []
    for p in entries:
        if p.name == SUMMARY_FILE:
            continue
        if p.is_dir():
            listing.append(f"[folder] {p.name}/")
        else:
            listing.append(f"[file] {p.name} ({human_size(p.stat().st_size)})")
    parts = ["Listing:\n" + ("\n".join(listing) or "(empty)")]

    for child in (p for p in entries if p.is_dir() and (p / SUMMARY_FILE).is_file()):
        text = (child / SUMMARY_FILE).read_text(encoding="utf-8", errors="replace")[:800]
        parts.append(f"Summary of subfolder {child.name}/:\n{text}")

    files = [p for p in entries if p.is_file() and p.name != SUMMARY_FILE and tools._is_text(p)]
    files.sort(key=lambda p: (not p.name.lower().startswith("readme"), -p.stat().st_size))
    for p in files[:6]:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[:60]
        parts.append(f"Excerpt of {p.name}:\n" + "\n".join(lines))

    return "\n\n".join(parts)[:SUMMARY_INPUT_CHARS]


def summarize_folders(root: Path, max_depth: int) -> None:
    import litellm

    tools = FolderTools(root)
    folders = folders_to_summarize(tools.root, max_depth)
    print(f"Writing {SUMMARY_FILE} in {len(folders)} folders (deepest first)...")
    for folder in folders:
        newest, _, _ = folder_stats(folder)
        prompt = SUMMARY_PROMPT.format(
            name=folder.name or str(folder),
            last_changed=datetime.fromtimestamp(newest).strftime("%Y-%m-%d"),
            material=summary_material(folder, tools),
        )
        response = litellm.completion(model=MODEL, messages=[{"role": "user", "content": prompt}])
        (folder / SUMMARY_FILE).write_text(response.choices[0].message.content or "", encoding="utf-8")
        print(f"  wrote {(folder / SUMMARY_FILE).relative_to(tools.root.parent)}")


def main():
    parser = argparse.ArgumentParser(description="Ask questions about a folder, find old folders, write summaries.")
    commands = parser.add_subparsers(dest="command", required=True)

    ask_cmd = commands.add_parser("ask", help="Ask questions; answers cite file:line.")
    ask_cmd.add_argument("folder", type=Path)
    ask_cmd.add_argument("-q", "--question", help="Ask one question and exit.")
    ask_cmd.add_argument("-v", "--verbose", action="store_true", help="Show each search the model makes.")

    report_cmd = commands.add_parser("report", help="List old, empty or regenerable folders. Never deletes.")
    report_cmd.add_argument("folder", type=Path)
    report_cmd.add_argument("--days", type=int, default=365, help="Flag folders unchanged for this many days.")
    report_cmd.add_argument("--depth", type=int, default=3, help="How many folder levels to check.")
    report_cmd.add_argument("--out", type=Path, help="Also save the report to this Markdown file.")
    report_cmd.add_argument("--all", action="store_true", help="List every checked folder, not just candidates.")

    summarize_cmd = commands.add_parser("summarize", help=f"Write {SUMMARY_FILE} in each folder.")
    summarize_cmd.add_argument("folder", type=Path)
    summarize_cmd.add_argument("--depth", type=int, default=2, help="How many folder levels to summarize.")

    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"Not a folder: {args.folder}")

    if args.command == "report":
        report = cleanup_report(args.folder.resolve(), args.days, args.depth, args.all)
        print(report)
        if args.out:
            args.out.write_text(report + "\n", encoding="utf-8")
        return

    if args.command == "summarize":
        summarize_folders(args.folder, args.depth)
        return

    tools = FolderTools(args.folder)
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(root=tools.root)}]

    if args.question:
        print(ask(args.question, messages, tools, args.verbose))
        return

    print(f"Folder: {tools.root}\nAsk a question (empty line or Ctrl+C to quit).")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print("\n" + ask(question, messages, tools, args.verbose))


if __name__ == "__main__":
    main()
