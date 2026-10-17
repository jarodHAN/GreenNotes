# GreenNotes

**A local-first, Evernote-style note app for macOS. Native window, double-click to run.**

No upload, no sync, no account, no network. Every note lives in a single SQLite file on your own
machine — unplug the network, reboot, uninstall and reinstall, nothing is lost. Copy one folder and
your whole notebook moves with you.

<p>
  <img alt="platform" src="https://img.shields.io/badge/macOS-11%2B-000000?logo=apple&logoColor=white">
  <img alt="arch" src="https://img.shields.io/badge/universal-x86__64%20%7C%20arm64-00a82d">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-00a82d">
  <img alt="version" src="https://img.shields.io/badge/version-1.2-00a82d">
  <img alt="deps" src="https://img.shields.io/badge/dependencies-none-00a82d">
</p>

![Main window](docs/images/main.png)

*[中文文档 →](README.md)*

---

## Download & install

**[⬇ Download LocalNotes-1.2.dmg](https://github.com/jarodHAN/GreenNotes/releases/latest)** (18 MB, runtime included)

Also committed in the repo: [`dist/LocalNotes-1.2.dmg`](dist/LocalNotes-1.2.dmg)

1. Open the dmg and drag `LocalNotes.app` into `Applications`
2. **First launch:** in Applications, **right-click LocalNotes → Open → Open** again.
   The app is ad-hoc signed (no paid Apple Developer certificate), so Gatekeeper blocks it once.
   That is expected — the file is not corrupted.
3. If macOS says the app "is damaged", run this once and open it again:

```bash
xattr -dr com.apple.quarantine /Applications/LocalNotes.app
```

**Requirements:** macOS 11 (Big Sur) or newer. Runs natively on both Intel and Apple Silicon
(universal binary). The target machine needs **no Python, no dependencies, no network** — a trimmed
Python 3.13.16 runtime ships inside the bundle.

Verify the download:

```bash
shasum -a 256 LocalNotes-1.2.dmg
# 4f44efe8b2d3e13c8e1a15d13c761bb11f006e25b3b77955ba015e69bc1eebc3
```

> The bundle is named `LocalNotes.app` (early codename); the project is GreenNotes, named after the
> UI accent colour `#00a82d`.

---

## Features

| Area | What you get |
|---|---|
| **Notebooks** | Create, delete, **rename by double-clicking the name**, via the hover pencil `✎`, or the context menu |
| **Notes** | Create, edit, save (⌘S), delete; separate title and body; list sorted by last update |
| **Rich text** | Bold / italic / underline / monospace / bullet list / numbered list / quote / code / clear formatting |
| **Markdown** | Switch the toolbar to **Markdown** for a source + live-preview split view; both modes edit the same note (HTML is what gets stored) |
| **Tables** | Insert a 2×2 – 5×4 table, type straight into cells, Tab moves on; exported Markdown becomes a GFM table |
| **Attachments** | Drop a file into the body: images render inline, PDF/Office/zip become attachment cards that open in the default app |
| **Full-text search** | Title + body, Chinese substring search works down to two characters (FTS5 `trigram` with a `LIKE` fallback) |
| **Import** | Evernote `.enex` (images included), Yinxiang/Youdao HTML exports, Markdown, plain text, Obsidian vaults, whole folders (recursive) |
| **Export** | One note or a whole notebook → Markdown / ENEX; UTF-8 filenames; saves pending edits first |
| **Drag to file** | Drag a note onto any notebook in the left pane; the target highlights |
| **Tags** | Preserved on import, shown in the editor |

### Markdown live preview

![Markdown split view](docs/images/markdown.png)

**Rich text** and **Markdown** in the toolbar are two views of one note — switch back and forth
without losing anything:

- Going to Markdown converts the current body with the server-side `html_to_markdown`
- The right pane is rendered entirely client-side (GFM subset: headings, lists, quotes, code blocks,
  tables, images, strikethrough) and updates as you type — no network
- Saving (⌘S) or switching back runs the source through `md_to_html`; **the database only ever holds
  HTML**, so search, ENEX export and every importer keep working unchanged

### Tables

**▦ 表格** in the toolbar inserts a table. Type directly into cells in rich-text mode, Tab to move on;
in Markdown mode you get a `| col 1 | col 2 |` template. Header styling, zebra rows and alignment
(`:-`, `:-:`, `-:`) are supported, Markdown export produces a standard GFM table, and ENEX gets a real
`<table>`.

### Attachments

Drop files from Finder anywhere in the body:

| Type | What happens |
|---|---|
| Images (png / jpg / gif / webp…) | Inserted inline and displayed |
| Anything else (pdf / docx / xlsx / zip…) | Becomes a `📎 name + size` attachment card |
| Screenshot on the clipboard | ⌘V uploads and inserts it the same way |

Files land in `assets/` inside the data folder; identical content in the same note is stored once
(MD5 de-duplication). Clicking a card does not navigate the window — it hands the file to the system
via a native `NSWorkspace` call. Limit: 25 MB per file.

### Export

![Export](docs/images/export.png)

Three entry points: the **Export** button in the editor, the notebook context menu, and
**File → Export Current Note… (⌘E)**.

Markdown is for other editors; ENEX is Evernote's interchange format and can be re-imported into
this app or into Evernote.

### Rename

![Rename](docs/images/rename.png)

Pre-filled with the current name and fully selected. Enter saves, Esc cancels. The backend rejects
empty names and duplicate notebook names.

### App icon

![Icon](docs/images/logo-showcase.png)

---

## Data & privacy

| | |
|---|---|
| Location | `~/Library/Application Support/LocalNotes/` (`notes.db` + `assets/`) |
| Network | **None outbound.** The app starts a local server on `127.0.0.1` at a random port just to render its own window. No public listener, no external requests, no telemetry |
| Migration | Copy that folder to the same path on the new machine |
| Uninstall | Deleting the app leaves your notes alone; delete the data folder to wipe |
| Backup | Copy `notes.db` (plain SQLite), or export single notes / whole notebooks |

> Notes are **not** encrypted at rest, and there is no cloud sync — by design, not by omission.

---

## Run from source

Only the system Python 3 is needed — standard library only, no `pip install`:

```bash
git clone https://github.com/jarodHAN/GreenNotes.git
cd GreenNotes
python3 app/server.py          # http://127.0.0.1:8731
```

Open that URL in a browser and you get the full UI (handy for frontend work).

```bash
LOCALNOTES_DATA=/tmp/mynotes   # alternate data dir (never touch real notes while debugging)
LOCALNOTES_PORT=8899           # fixed port
```

On first run against an empty database, `app/sample.enex` seeds three demo notes.

---

## Build the .app and the dmg

Requires **Xcode Command Line Tools** (`xcode-select --install`, provides `swiftc`). No Xcode
project, no npm.

```bash
bash desktop_app/build.sh        # builds .app: universal binary, embedded runtime, ad-hoc signing, self-checks
bash desktop_app/make_dmg.sh     # packages a distributable dmg
VERSION=1.2 bash desktop_app/make_dmg.sh   # different version
```

`build.sh` ends with a hard gate — it exits non-zero unless all of these hold: embedded runtime
present, `importers` present, frontend present, signature valid, and **no local data**
(`notes.db` / `assets/`) inside the bundle.

### Embedded Python runtime (needed for distribution, optional for local use)

`build.sh` picks up `desktop_app/runtime/python/` if it exists (~44 MB) and skips it otherwise
(the resulting app then needs a system `python3`). That directory is not in the repo; generate it once:

```bash
# download python-build-standalone for both architectures, e.g.
#   cpython-3.13.16+<date>-x86_64-apple-darwin-install_only_stripped.tar.gz
#   cpython-3.13.16+<date>-aarch64-apple-darwin-install_only_stripped.tar.gz
# unpack both, then:
python3 desktop_app/build_runtime.py /path/to/x86_64/python /path/to/arm64/python \
        desktop_app/runtime/python
```

It uses the arm64 tree as the skeleton, `lipo -create`s every Mach-O into a fat binary, then trims
`test/`, `tkinter`, `idlelib`, `ensurepip`, the whole Tcl/Tk stack, `__pycache__` and the unreferenced
`libpython3.13.dylib` — 90 MB down to 44 MB for two architectures.

Regenerating the icon needs Pillow (`pip install pillow`); without it the committed `AppIcon.png` is
reused. Switch styles with `ICON_VARIANT=A bash desktop_app/build.sh` (A folded note / **B hardcover
notebook** / C card).

---

## Layout

```
GreenNotes/
├── app/                     # backend + frontend (stdlib only)
│   ├── server.py            # HTTP server, routes, SQLite, FTS5 search
│   ├── exporter.py          # Markdown / ENEX export (single note + notebook)
│   ├── sanitize.py          # HTML allow-list sanitizer (XSS, void elements)
│   ├── sample.enex          # first-run demo data
│   ├── static/index.html    # single-file frontend (HTML+CSS+JS, no framework, no build)
│   └── static/icon-*.png
├── importers/               # multi-format importers
│   ├── dispatch.py          # pick importer by extension / content
│   ├── enex.py  html.py  markdown.py  txt.py  youdao.py
│   └── common.py            # persistence, attachments, title/date inference
├── desktop_app/             # macOS toolchain
│   ├── shell/main.swift     # WKWebView shell (spawns server, menus, export channel)
│   ├── Info.plist
│   ├── build.sh  make_dmg.sh  build_runtime.py
│   ├── make_icon.py  icon_lib.py  icon_variants.py  AppIcon.png
├── dist/LocalNotes-1.2.dmg
└── docs/images/
```

Design notes and the reasoning behind the stack: [`docs/ARCHITECTURE.en.md`](docs/ARCHITECTURE.en.md)
([中文](docs/ARCHITECTURE.md)) — why no Electron/Tauri, the FTS5 trigram trade-off, the five native
hooks the shell has to implement, and the two script-level traps that produce silently broken builds.

---

## Local API

The window is a WKWebView talking to a loopback HTTP server, so everything is scriptable:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | counts |
| GET | `/api/notebooks` / `/api/notes` | lists |
| GET | `/api/note/<id>` | one note (HTML body, tags, attachments) |
| GET | `/api/search?q=` | full-text search |
| GET | `/api/tags` | tags with counts |
| GET | `/api/note/<id>/export?fmt=md\|enex` | export a note |
| GET | `/api/notebook/<id>/export?fmt=md\|enex` | export a notebook |
| POST | `/api/note` / `/api/notebook` | create |
| POST | `/api/note/<id>` / `/api/notebook/<id>` | update / rename |
| POST | `/api/note/<id>/move` | move note to another notebook |
| POST | `/api/import` / `/api/import_path` | import upload / local path |
| POST | `/api/convert` | HTML ⇄ Markdown `{from:'html'\|'md', text}` |
| POST | `/api/note/<id>/attach` | upload an attachment `{filename, data(base64)}` |
| DELETE | `/api/note/<id>` / `/api/notebook/<id>[?force=1]` | delete |

---

## Known limitations

- **No cloud sync.** One machine, one dataset. Move it by copying the data folder or exporting.
- **First launch needs right-click → Open** (ad-hoc signature, no notarization). Removing that step
  requires an Apple Developer Program membership ($99/yr) plus signing and notarization.
- **macOS only.** The backend is portable stdlib Python, but the shell is WKWebView/Cocoa.
- Rich text is `contenteditable`; Markdown is a second editing view and does **not** support footnotes,
  math or Mermaid.
- Tables support insert / edit / alignment / export, but not column dragging or cell merging
  (`colspan`/`rowspan` survive import).
- Attachments are capped at 25 MB and only accept drag / paste, no file-menu picker.
- **Encrypted notes inside `.enex` are not supported** (`<en-crypt>` is dropped by the allow-list;
  title and tags survive, body does not).
- Single window, single process; search has no query syntax (`AND`, quoted phrases).

Planned: column resizing and cell merging, tag filtering, image resize, Linux/Windows shell, incremental backup.

## License

MIT — see [LICENSE](LICENSE).
