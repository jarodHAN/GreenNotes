# Architecture and Key Decisions

This document explains *why the app looks the way it does*, and which parts are easiest to break
when you change something.

*[中文 →](ARCHITECTURE.md)*

---

## 1. Overall shape

```
┌──────────────────────── GreenNotes.app ────────────────────────┐
│  Main thread            Swift shell (WKWebView)                 │
│    ├─ pick a port → spawn the child process                     │
│    ├─ NSMenu (copy / paste / export via the responder chain)    │
│    └─ window ← http://127.0.0.1:<random port>                   │
│                          │                                      │
│                          ▼ HTTP (loopback only)                  │
│  Python child process   app/server.py                           │
│    ├─ ThreadingHTTPServer(127.0.0.1, port)                      │
│    ├─ routes /api/*                                             │
│    ├─ SQLite: ~/Library/Application Support/GreenNotes/notes.db │
│    └─ assets/  attachments on disk                              │
└─────────────────────────────────────────────────────────────────┘
```

The shell does exactly four things: start the server, provide the window, fix the menus, and wire
"Save as…" to the system panel. **Not a single line of business logic lives in Swift** — it is all
Python plus the front end, so features can be changed without touching native code.

### Why not Electron or Tauri
- Electron drags in a ~100 MB runtime and a Node toolchain.
- This machine is an Intel Mac; Tauri would need a Rust toolchain plus cross-compilation. The payoff
  does not justify the cost.
- `swiftc` ships with the Command Line Tools. One file compiles into a universal binary and the dmg
  is only 18 MB.

The price is that the shell has to be written by hand — `WKUIDelegate`, `NSMenu`, downloads and so on
all need explicit handling (see section 5).

---

## 2. Backend: standard library only

The entire dependency set of `app/` and `importers/` is
`sqlite3 / json / http.server / html.parser / re / zlib`. **There is no requirements.txt.** That is
what lets the bundled runtime be trimmed to 44 MB and what will keep the app runnable ten years from
now.

### Data model (`importers/common.py: SCHEMA`)

```sql
notebooks(id, name)
notes(id, notebook_id, title, content_html, content_text,
      created, updated, source, author, latitude, longitude, reminder_time)
tags(id, note_id, name)
resources(id, note_id, filename, mime, ext, hash, size, local_path)
```

- Both `content_html` (for rendering) and `content_text` (for search and list snippets) are stored.
  `sanitize.py` scrubs incoming HTML against a tag allow-list on write and produces the plain text
  along the way.
- Timestamps are Evernote's `YYYYMMDDTHHMMSSZ` strings, so sorting is plain lexicographic order and
  timezone conversion is unnecessary.
- Attachments carry two pieces of information: metadata in the DB, bytes in `assets/<md5>.<ext>`.

### Search: FTS5 trigram with a LIKE fallback

The index is rebuilt per query:

```sql
DROP TABLE IF EXISTS notes_fts;
CREATE VIRTUAL TABLE notes_fts USING fts5(title, content_text, rowid, tokenize='trigram');
INSERT INTO notes_fts(rowid, title, content_text) SELECT id, title, content_text FROM notes;
```

- Only the `trigram` tokenizer can do substring matching on Chinese. The default `unicode61`
  tokenizer splits on whitespace, so a whole Chinese sentence becomes a single token and searching
  for 「国电」 finds nothing.
- Trigram requires **at least three characters**, though, and two-character Chinese queries are very
  common — those fall back to `LIKE '%q%' ESCAPE '\'`.
- Why no triggers to maintain the index: at the scale of hundreds to a few thousand notes, rebuild
  plus query is on the order of 10 ms, and it removes an entire class of consistency bugs (every
  import / edit / delete path having to update FTS). **Above ~100k notes this has to become triggers
  or an external-content table.**

### Import (`importers/`)

`dispatch.py` maps an extension to a concrete importer, which produces `ImportedNote` objects, and
`persist_notes()` writes them. For `.enex`, `en-media hash=` is matched against `<resource>` by MD5
and images are rewritten to real paths under `assets/`, so inline images render immediately after
import.

### Export (`exporter.py`)

- **Markdown**: HTML → text, keeping heading levels, lists, images (as relative paths or skipped),
  tags, and source timestamps.
- **ENEX**: paragraphs are reassembled into ENML, attachments become base64 `<resource>` blocks, and
  the `en-media` hash is aligned with the resource MD5. ENML is strictly validated, so the body must
  be clean — void elements such as `<img>`, `<br>`, `<hr>` must not carry closing tags.

### Attachments (drag & drop / paste)

`POST /api/note/<id>/attach` takes base64 and writes `assets/<name>-<first 8 of md5>.<ext>`, recording
it in `resources`; identical content inside the same note is reused instead of duplicated. In the body,
images become `<img src="assets/…">` and everything else becomes
`<a class="attach" href="assets/…">📎 name<span class="sz">size</span></a>` — the `sz` span is pure UI
and is stripped before Markdown export, otherwise you would get `[📎 report.pdf12.4 KB](…)`.

`resources` was already the ENEX resource list, so attachments need no second storage: export maps them
back to `<en-media>` (images render as pictures, other types become attachments in Evernote). Import
does the reverse — previously non-images were also rewritten as `<img>`, so PDFs showed up as broken
images; now the mime type decides.

---

## 3. Front end: one file, zero build

`app/static/index.html` (~690 lines) holds JS, CSS and HTML together. No npm, no bundler, no
framework. Edit and refresh — for a tool one person maintains, that beats engineering ceremony.

The three panes are flex layouts, and **every vertical flex container needs `min-height: 0`**.
Otherwise a long child makes `min-height:auto` grow the page, and the right pane's internal scrollbar
turns into whole-page scrolling — which drags the notebook list along with it (exactly the bug
reported early on).

### Dual-mode editor: HTML is the single source of truth

Markdown is not a second store, it is another view of the HTML:

```
Rich text (contenteditable)  ──save──►  sanitize ──► notes.content_html
        ▲                                  │
        │ switch back: md_to_html          │ switch away: html_to_markdown
        │                                  ▼
   Markdown source + client preview  ◄──── /api/convert
```

- Switching to Markdown sends `edContent.innerHTML` to `/api/convert {from:'html'}` and puts the result
  in the textarea
- Saving or switching back converts `{from:'md'}` into HTML and follows the normal save path
  (sanitize, plain text, database)
- The right pane is `mdRender()` running in the browser (GFM subset, zero dependencies) purely for
  instant feedback; what gets stored is always the server's conversion, so the two renderers cannot
  drift apart silently

The payoff: search (`content_text`), ENEX export and every importer keep working untouched.

### Tables

The sanitizer allow-list already permitted `table/thead/tbody/tr/td/th` (with `colspan`/`rowspan`/`style`),
so tables only needed two things: an insert UI (`insertTable()` uses a Range to insert real DOM at the
caret — `execCommand('insertHTML')` is unreliable in WKWebView) and styling (borders, header tint,
zebra rows). On the way out, `_MDConverter` already emits GFM table syntax.

---

---

## 4. Packaging chain

```
app/ + importers/  ──rsync(exclude pyc/db/assets)──┐
desktop_app/AppIcon.png ──sips/iconutil──► AppIcon.icns │
shell/main.swift ──swiftc ×2 (x86_64/arm64)──lipo──► GreenNotes │
runtime/python (optional) ─────────────────────────────┘
                                    │
                              ../GreenNotes.app
                                    │
                          make_dmg.sh ──► dist/GreenNotes-x.y.dmg
```

The self-check at the end of `build.sh` is a hard gate: any failed condition means `exit 1`.

1. `Contents/Resources/python/bin/python3` exists
2. `Contents/Resources/app/importers/` exists
3. `Contents/Resources/app/static/index.html` exists
4. `codesign --verify --deep --strict` passes
5. the bundle contains **no** `notes.db*` and no `assets/`
6. the bundle contains **no** `__pycache__` / `*.pyc` (they break the signing seal)
7. the bundle contains **no** nested `importers/importers` copy (a stale leftover of older builds)

### Bundled runtime (`build_runtime.py`)

Starting from the two `install_only_stripped` architectures of python-build-standalone:

1. Use the arm64 tree as the skeleton (`ditto`, preserving symlinks).
2. Walk every Mach-O (`bin/python3.13`, `lib-dynload/*.so`, …) and `lipo -create` it into a fat binary.
3. Trim: `test/`, `idlelib`, `tkinter`, `ensurepip`, `lib2to3`, `pydoc_data`, `site-packages`, the
   whole Tcl/Tk stack (including `libtcl*.dylib` — those directory names carry version numbers, so
   globs are mandatory), and `__pycache__`.
4. After confirming nothing references it, delete `libpython3.13.dylib` (34 MB; the interpreter is
   statically linked).

Result: 32 MB for one architecture, 44 MB for both, 18 MB as a dmg.

---

## 5. The native shell: six things you must wire up

| Where | What breaks if you don't |
|---|---|
| `WKUIDelegate` | Without `runJavaScriptAlertPanel` / `ConfirmPanel` / `TextInputPanel`, `alert/confirm/prompt` **fail silently** and buttons appear dead. The front end also implements in-page dialogs as a second line of defence |
| `NSApp.mainMenu` | No menu means ⌘C/⌘V/⌘A never fire (copy and paste go through the responder chain's `copy:`/`paste:`). Menu item `target` must be `nil` |
| Downloads | `<a download>` does not download inside WKWebView; it navigates to the source instead. Export goes through `WKScriptMessageHandler` + `NSSavePanel` + `URLSession` back to the local server |
| Opening attachments | Attachment links are relative `assets/…` paths; clicking one **navigates the whole UI to the file** (with no way back). They go through the `openFile` channel → `NSWorkspace.shared.open`, which only accepts a plain filename and rejects `../` traversal |
| `PYTHONDONTWRITEBYTECODE=1` + `-B` | Python writes `__pycache__` into the bundle, which breaks the code-signing seal instantly; a second launch on another machine may then report the app as damaged |
| Data directory in `Application Support` | Inside the bundle, moving or upgrading the app would take all your notes with it |

> **Renamed in 1.3**: the bundle is `GreenNotes.app` and the data directory is
> `…/Application Support/GreenNotes/`. Users upgrading from 1.2 or earlier (which used `LocalNotes`)
> are migrated at launch: if the new directory is missing and the old one exists, the old folder is
> **copied** over and kept as a backup; if the copy fails the shell keeps using the old directory
> rather than refusing to start. Copy, not move — a failed move would leave the user with no second copy.

Also: `NSAllowsLocalNetworking` must be enabled in Info.plist, otherwise tighter ATS settings will
later block the loopback requests.

---

## 6. Two script-level traps

Both are artifacts of the environment or the tools, and both have produced "the build said success
but the artifact was broken":

1. **Bulk-delete protection.** The environment intercepts operations that delete a large number of
   files at once. `rm -rf` on an `iconset` directory with hundreds of PNGs → intercepted → `set -e`
   aborts → the whole "embed runtime + sign" section is skipped → an unsigned bundle is produced.
   The same trap bit again later with `rm -rf "$RES/python"` (549 files): the build died at "embed
   runtime" and never even copied it in.
   Countermeasure: temp dirs are always `mktemp -d /tmp/...` and are **never deleted** (the system
   cleans them); directories that must be wiped before copying are `mv`-ed to `/tmp` and then
   `ditto`-ed over; and never invoke the build as `bash build.sh | tail`, which swallows the exit
   code (the pipeline reports `tail`'s status).
2. **`rsync -a app/` happily copies the developer's `notes.db` and `assets/` into the installer.**
   Shipping that means shipping your own notes. Countermeasure: explicit `--exclude`, enforced as a
   gate in the self-check. It also has to run with `--delete --delete-excluded`: the `.app` is updated
   in place, so empty `__pycache__` dirs, a nested `importers/importers` copy and `.DS_Store` from
   earlier builds survive a plain copy and would ship inside the dmg.

---

## 7. The reality of distribution

The app is **ad-hoc signed** (`codesign --sign -`): no Apple developer certificate, no notarization.

- `spctl --assess` reports `rejected: no usable signature`; the first launch needs right-click → Open.
- If quarantine attributes are attached and macOS claims the app is damaged,
  `xattr -dr com.apple.quarantine` clears it.
- The only real fix is the Apple Developer Program ($99/yr) → Developer ID certificate → `codesign`
  → `notarytool`. That is a signing-infrastructure problem, not a code problem.

The app also does not enable App Sandbox: ad-hoc signing plus `com.apple.security.app-sandbox` is
terminated by the system on launch. Sandboxing is only needed for the App Store; local distribution
does not require it.
