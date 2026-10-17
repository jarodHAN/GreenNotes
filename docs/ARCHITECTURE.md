# 架构与关键决策

这份文档说明「为什么是现在这个形状」，以及改造时最容易踩坏的地方。

---

## 1. 整体形态

```
┌──────────────────────── LocalNotes.app ────────────────────────┐
│  Main Thread            Swift 外壳 (WKWebView)                  │
│    ├─ 选端口 → 拉起子进程                                        │
│    ├─ NSMenu（复制/粘贴/导出 走 responder chain）                │
│    └─ 窗口 ← http://127.0.0.1:<随机端口>                        │
│                          │                                      │
│                          ▼ HTTP（本机回环）                      │
│  Python 子进程  app/server.py                                   │
│    ├─ ThreadingHTTPServer(127.0.0.1, port)                      │
│    ├─ 路由 /api/*                                               │
│    ├─ SQLite: ~/Library/Application Support/LocalNotes/notes.db │
│    └─ assets/ 附件落盘                                           │
└─────────────────────────────────────────────────────────────────┘
```

外壳只做四件事：起服务、给窗口、修菜单、把「另存为」接到系统面板。
**业务逻辑一行都不在 Swift 里**，全部在 Python + 前端，改功能不需要碰原生代码。

### 为什么不用 Electron / Tauri
- Electron 带约 100 MB 运行时，且要 Node 工具链；
- 本机是 Intel Mac，Tauri 需要 Rust 工具链 + 交叉编译，收益不抵成本；
- `swiftc` 是系统自带的（Command Line Tools），一个文件编译出通用二进制，dmg 只有 18 MB。

代价是壳要自己写，`WKUIDelegate`、`NSMenu`、下载这些都得手接（见第 5 节）。

---

## 2. 后端：只用标准库

`app/` 与 `importers/` 的全部依赖是 `sqlite3 / json / http.server / html.parser / re / zlib`，
**没有 requirements.txt**。好处是内嵌运行时可以裁到 44 MB，且十年后仍然跑得起来。

### 数据模型（`importers/common.py: SCHEMA`）

```sql
notebooks(id, name)
notes(id, notebook_id, title, content_html, content_text,
      created, updated, source, author, latitude, longitude, reminder_time)
tags(id, note_id, name)
resources(id, note_id, filename, mime, ext, hash, size, local_path)
```

- 同时存 `content_html`（渲染用）和 `content_text`（搜索用、列表摘要用）；
  `sanitize.py` 在入库时把 HTML 洗成白名单标签，并顺手产出纯文本。
- 时间是 Evernote 的 `YYYYMMDDTHHMMSSZ` 字符串，排序即字典序，省掉时区换算。
- 附件两份信息：DB 里记元数据，实体文件在 `assets/<md5>.<ext>`。

### 搜索：FTS5 trigram + LIKE 兜底

每次查询时临时建索引：

```sql
DROP TABLE IF EXISTS notes_fts;
CREATE VIRTUAL TABLE notes_fts USING fts5(title, content_text, rowid, tokenize='trigram');
INSERT INTO notes_fts(rowid, title, content_text) SELECT id, title, content_text FROM notes;
```

- `trigram` 分词器才能对中文做子串匹配（默认 `unicode61` 按空格切词，中文整句算一个 token，搜「国电」搜不到）。
- 但 trigram 要求 **至少 3 个字符**；两字中文（很常见）会退回 `LIKE '%q%' ESCAPE '\'`。
- 为什么不用触发器维护索引：几百到几千篇的规模，重建索引 + 查询在 10 ms 量级，
  省掉了「导入/编辑/删除路径都要同步 FTS」这类一致性 bug。**笔记数上到十万级需要改成触发器或外部内容表。**

### 导入（`importers/`）

`dispatch.py` 按扩展名 → 具体导入器 → 统一产出 `ImportedNote` → `persist_notes()` 落库。
`.enex` 的 `en-media hash=` 与 `<resource>` 用 MD5 对上，图片改写成 `assets/` 下的真实路径，
所以导入后正文里的图片能直接显示。

### 导出（`exporter.py`）

- **Markdown**：HTML → 文本，保留标题层级、列表、图片（转成相对路径或跳过）、标签、来源时间。
- **ENEX**：段落级重组为 ENML，附件转 base64 `<resource>`，`en-media` 的 hash 与 resource 的 MD5 对齐。
  ENML 是严格校验的，所以正文必须干净——`<img>`、`<br>`、`<hr>` 这类空元素不能带闭合标签。

---

## 3. 前端：一个文件，零构建

`app/static/index.html`（约 690 行）里 JS/CSS/HTML 全在一起，没有 npm、没有打包、没有框架。
改完刷新即生效，这对「一个人维护的小工具」比工程化更重要。

三栏用 flex 布局，**每个纵向 flex 容器都必须 `min-height: 0`**——
否则子元素内容一长，`min-height:auto` 会把整页撑高，右栏内部滚动条就变成了整页滚动
（左侧笔记本列表也会跟着滚走，这正是早期被报过的 bug）。

---

## 4. 打包链

```
app/ + importers/  ──rsync(排除 pyc/db/assets)──┐
desktop_app/AppIcon.png ──sips/iconutil──► AppIcon.icns │
shell/main.swift ──swiftc ×2 (x86_64/arm64)──lipo──► LocalNotes │
runtime/python (可选) ─────────────────────────────┘
                                    │
                              ../LocalNotes.app
                                    │
                          make_dmg.sh ──► dist/LocalNotes-x.y.dmg
```

收尾自检（`build.sh` 末尾）是硬门禁，任一条不满足就 `exit 1`：

1. `Contents/Resources/python/bin/python3` 存在
2. `Contents/Resources/app/importers/` 存在
3. `Contents/Resources/app/static/index.html` 存在
4. `codesign --verify --deep --strict` 通过
5. 包内**没有** `notes.db*` / `assets/`

### 内嵌运行时（`build_runtime.py`）

python-build-standalone 的 `install_only_stripped` 两个架构解包后：

1. 以 arm64 树为骨架（`ditto` 保留符号链接）
2. 遍历所有 Mach-O（`bin/python3.13`、`lib-dynload/*.so` 等），`lipo -create` 合成胖二进制
3. 裁剪：`test/`、`idlelib`、`tkinter`、`ensurepip`、`lib2to3`、`pydoc_data`、`site-packages`、
   Tcl/Tk 整套（含 `libtcl*.dylib`，目录名带版本号，必须用通配）、`__pycache__`
4. 确认无人引用后删掉 `libpython3.13.dylib`（34 MB，解释器是静态链接的）

结果：单架构 32 MB → 双架构 44 MB，dmg 18 MB。

---

## 5. 原生外壳：五处必须接的手

| 位置 | 不接会怎样 |
|---|---|
| `WKUIDelegate` | 不实现 `runJavaScriptAlertPanel` / `ConfirmPanel` / `TextInputPanel`，页面里 `alert/confirm/prompt` **静默失效**，按钮点了没反应。现在前端还额外做了页内弹窗，双保险 |
| `NSApp.mainMenu` | 没有菜单，⌘C/⌘V/⌘A 完全不触发（复制粘贴走 responder chain 的 `copy:`/`paste:`）。菜单项的 `target` 必须是 `nil` |
| 下载 | WKWebView 里 `<a download>` 不会下载，还会把页面导航成源码。导出改走 `WKScriptMessageHandler` + `NSSavePanel` + `URLSession` 回源取数据 |
| `PYTHONDONTWRITEBYTECODE=1` + `-B` | Python 往包内写 `__pycache__`，代码签名封条立刻失效，别的机器二次打开可能提示「已损坏」 |
| 数据目录放 `Application Support` | 放 App 包内的话，App 一移动/升级，笔记就没了 |

另外：`NSAllowsLocalNetworking` 要在 Info.plist 里打开，否则将来收紧 ATS 时本机回环请求会被拦。

---

## 6. 脚本层面的两个坑

这两条纯粹是环境/工具行为导致的，但都造成过「构建显示成功、产物其实是坏的」：

1. **批量删除保护**：环境会拦截一次删除大量文件的操作。脚本里 `rm -rf` 一个含几百张 png 的
   `iconset` 目录 → 被拦截 → `set -e` 中断 → 后续「内嵌运行时 + 签名」整段跳过 → 打出未签名包。
   对策：临时目录一律 `mktemp -d /tmp/...` 且**不删**（系统会清），
   并且外层不要用 `bash build.sh | tail` 这种把退出码吃掉的方式调用。
2. **`rsync -a app/` 会把开发者本地的 `notes.db`、`assets/` 一起打进安装包**。
   分发给别人就等于把自己的笔记发出去了。对策：显式 `--exclude`，并在自检里当门禁查。

---

## 7. 分发的现实约束

App 用 **ad-hoc 签名**（`codesign --sign -`），没有 Apple 开发者证书、没有公证：

- `spctl --assess` 会判 `rejected: no usable signature`，首次打开需右键「打开」；
- 若被加了隔离属性并提示「已损坏」，`xattr -dr com.apple.quarantine` 即可；
- 解决方式只有买 Apple Developer Program（$99/年）→ Developer ID 证书 → `codesign` → `notarytool` 公证。
  这不是代码问题，是签名基础设施问题。

另外 App 未启用 App Sandbox：ad-hoc 签名 + `com.apple.security.app-sandbox` 的组合启动即被系统终止。
要上架 App Store 才需要沙盒，本地分发不需要。
