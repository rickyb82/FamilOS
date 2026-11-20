# GridCraft MCP server

`gridcraft-cli mcp` exposes GridCraft to AI agents over the [Model Context Protocol](https://modelcontextprotocol.io):
newline-delimited JSON-RPC 2.0 on stdin/stdout (protocol `2025-06-18`; `2025-03-26` and `2024-11-05` clients are
answered in their own version). Logs go to stderr.

Two backends:

| Mode | Command | What it drives |
|---|---|---|
| Headless (default) | `gridcraft-cli mcp [--in book.xlsx \| --sample budget] [--automation-read-root DIR] [--automation-write-root DIR]` | An in-process engine session — no window; files only inside the roots ([below](#files-the-agent-may-read-and-write)). |
| Remote | `gridcraft-cli mcp --connect 7979` | A running app started with `gridcraft --control 7979` (TCP JSON lines on `127.0.0.1`, authenticated with the app's token: it is read from the app's token file or `GRIDCRAFT_CONTROL_TOKEN`, see [`control-protocol.md`](control-protocol.md)). |

## Files the agent may read and write

A workbook the agent opens can carry instructions aimed at it (prompt injection). So the headless
server reaches the file system only inside two folders given at start:

```sh
gridcraft-cli mcp --automation-read-root /work/project --automation-write-root /work/project/out
```

- Every path a tool or command reads (`open_workbook`, `file.open`, `data.fromTextCsv`,
  `insert.picture`) must lie inside the read root; every path it writes (`save_workbook`,
  `file.save`/`saveAs`, `file.exportCsv`/`exportHtml`/`exportPdf`, `file.print`) inside the write
  root. Paths are made absolute against the working directory and their links are followed
  before the comparison: `../` out of a root, an absolute path elsewhere and a link that leads out
  are refused (`automation path rejected: …`), and nothing is written.
- The two roots are separate: a write root is not readable unless it is also inside the read root
  (`/work/project/out` above is both).
- **Without the flags the headless server reads and writes no file by path** (`automation file
  access refused: no read root …`). Workbooks can still come and go as data:
  `execute_command` `file.open {"name": "book.xlsx", "base64": "…"}` and `file.saveBytes`.
  To give an agent your whole disk, say so: `--automation-read-root / --automation-write-root /`.
- `--in FILE` is yours, not the agent's: it is opened before the roots apply (saving it back
  still needs it inside the write root).
- The roots confine the headless server only: with `--connect` the agent drives your running app
  with your rights, and the flags are refused.

## Claude Code

```sh
claude mcp add gridcraft -- gridcraft-cli mcp
# or drive the desktop app you are looking at:
claude mcp add gridcraft-app -- gridcraft-cli mcp --connect 7979
```

JSON config (`.mcp.json`, Claude Desktop, other clients):

```json
{
  "mcpServers": {
    "gridcraft": {
      "command": "gridcraft-cli",
      "args": ["mcp"]
    }
  }
}
```

Use `"args": ["mcp", "--connect", "7979"]` for the desktop app, `["mcp", "--automation-read-root", "/path/work",
"--automation-write-root", "/path/work"]` to let the agent open and save files there, or add `"--in", "/path/book.xlsx"`
to start with a file open.

## Tools

| Tool | Arguments | Does |
|---|---|---|
| `execute_command` | `command`, `params?` | Runs **any** engine command (`cell.set`, `home.bold`, `data.removeDuplicates`, …). |
| `list_commands` | `search?` | Command ids, labels, ribbon paths, shortcuts and parameter docs. |
| `list_windows` | — | Open windows: window id, workbook (`doc`) id, title ("Book1", or "Book1:2" for a workbook's second window), unsaved changes, active, hidden. |
| `inspect_workbook` | — | Sheets, used ranges, tables, charts, names, selection, history. |
| `read_range` | `range?`, `sheet?`, `formulas?`, `formatted?` | Rows of values (default: the used range). |
| `write_range` | `range`, `values` (2-D), `parse?` | `range.setValues`; strings starting with `=` are formulas, other strings are read like typed input unless `parse: false` stores them as text. `converted` in the result lists the strings read as a number, date, time, boolean or error. |
| `set_cell` / `get_cell` | `cell`, `input` / `cell` | Enter one cell / read value, formula, display text, style. |
| `evaluate_formula` | `formula`, `cell?` | Evaluates without changing the sheet. A formula that couldn't be entered in a cell (it doesn't parse, or a function gets the wrong number of arguments) is an error saying why. |
| `select` | `range`, `active?` | Sets the selection. |
| `format_range` | `range`, `bold?`, `italic?`, `underline?`, `fontName?`, `fontSize?`, `fontColor?`, `fillColor?`, `numberFormat?`, `horizontalAlign?`, `wrapText?`, `borders?` | Composes the `home.*` formatting commands. |
| `insert_chart` | `range`, `type`, `title?`, `subtype?`, `at?` | `insert.chart`. |
| `create_table` | `range`, `style?`, `header?`, `name?` | `insert.table`. |
| `sort_range` | `keys`, `range?`, `header?` | `data.sort`; keys are `"B"`, `"B desc"` or `{column, order}`. |
| `filter` | `column`, `range?`, `values?`, `custom?`, `top?`, `blanks?`, `clear?` | `data.filterBy` (turns AutoFilter on when needed). |
| `open_workbook` / `save_workbook` / `new_workbook` | `path` / `path?` / `sample?` | Files (`.xlsx`, `.csv`, `.tsv`, `.json`, `.html`). |
| `undo` / `redo` | `steps?` | History. |
| `screenshot` | `path` | PNG of the window (Remote only). |
| `list_functions` | `search?`, `category?` | Worksheet functions with signatures. |

Every tool that acts on a workbook (all but `list_commands`, `list_functions`, `list_windows`, `open_workbook`,
`new_workbook` and `screenshot`) also takes `window` or `doc`, an id from `list_windows`, and acts in that window
(for `doc`, the workbook's most recent window) without bringing it forward. Without them it acts in the active window.

Results are one text block holding pretty JSON; failures set `isError: true` with a readable message. Unknown tools,
missing required arguments and wrong argument types are JSON-RPC `-32602` errors; malformed JSON is `-32700`; unknown
methods `-32601`.

## Resources

- `gridcraft://workbook` — `document.inspect` of the active workbook.
- `gridcraft://commands` — every command with its parameter docs.
- `gridcraft://functions` — every worksheet function.

`prompts/list` returns an empty list.

## Example session

```text
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
→ {"jsonrpc":"2.0","method":"notifications/initialized"}
→ {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"write_range","arguments":{"range":"A1","values":[["Item","Cost"],["Rent",1200],["Food",400],["Total","=SUM(B2:B3)"]]}}}
→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read_range","arguments":{"range":"B4"}}}
← {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\n  \"range\": \"B4\", ... \"values\": [[1600.0]]\n}"}],"isError":false}}
```

## Embedding

`gridcraft-mcp` is a library: `Server::new(Box::new(Headless::new()))` or
`Server::new(Box::new(Remote::connect(&control_addr("7979"))?))`, then `Server::serve(stdin, stdout)` or
`Server::handle_line(line) -> Option<String>`. Implement the `Backend` trait (`call(method, params)`) to drive another
host.
