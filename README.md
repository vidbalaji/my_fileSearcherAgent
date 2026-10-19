# File searcher agent

A small, read-only assistant for a folder of files. It has three commands:

| Command | What it does | Uses the model |
|---|---|---|
| `ask` | Answers questions about the folder, citing `path:line` references | Yes |
| `report` | Lists folders that are old, empty, or rebuildable (`node_modules`, `.venv`, ...). Never deletes. | No |
| `summarize` | Writes a `readsummary.md` in each folder, deepest first | Yes |

## Setup

```bash
pip install -r requirements.txt
```

Give it your API key, either in a `.env` file next to `folder_assistant.py` (copy `.env.example`):

```
LITE_LLM_API_KEY=sk-...
```

or in the terminal (PowerShell: `$env:LITE_LLM_API_KEY="sk-..."`, bash: `export LITE_LLM_API_KEY=sk-...`).
Set `LITE_LLM_API_BASE` too if the key is for a LiteLLM proxy, and `MODEL` to use a model other than `openai/gpt-4.1`.
The provider's own variable (e.g. `OPENAI_API_KEY`) also works when `LITE_LLM_API_KEY` is not set.

## Usage

```bash
python folder_assistant.py ask "D:/work/project"
python folder_assistant.py ask ./project -q "where is the order quantity validated?" -v
python folder_assistant.py report ./project --days 365 --out cleanup.md
python folder_assistant.py report ./project --all        # every folder's age, not just candidates
python folder_assistant.py summarize ./project --depth 2
```

## Sticky-note chat window

```bash
python sticky.py
```

A small always-on-top chat window. Paste a folder or file path (quotes from Windows "Copy as path" are fine),
type a question and press Enter (Shift+Enter for a new line). Follow-up questions keep the conversation;
**New chat** starts over. **Report** and **Summarize** post their results into the chat; **Copy last** copies
the latest answer. For a single file, questions focus on that file and Summarize writes nothing.
For a folder, Summarize asks before writing `readsummary.md` files. Progress shows above the input box.
Uses Python's built-in tkinter (included with the python.org Windows installer).

## How `ask` uses readsummary.md

If the folder has a `readsummary.md` (from `summarize`), it is given to the model as a map so it knows where
to look first. Answers still come from the real files: keyword search skips `readsummary.md`, and the model
is told never to cite it, since summaries can be out of date.

## Notes

- Only text files are read (code, JSON, XML, Markdown, config). PDF, Word and Excel are skipped.
- `ask` searches by keyword, so it depends on the model picking good search terms.
- Folder age inside a git repo uses the last commit that touched it, because clone/pull resets file dates.
  Folders with uncommitted changes count as changed today. Outside git, file modification time is used,
  so copied or unzipped folders may look newer than they are.
- `report` needs no API key or `litellm`; only `ask` and `summarize` call the model.
- For an OpenRouter key (`sk-or-...`) set `MODEL=openrouter/<model id>`, e.g. `openrouter/openai/gpt-4.1`.
- `summarize` makes one model call per folder; start with a small `--depth`.
