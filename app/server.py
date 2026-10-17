#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线笔记 App · 本地服务器（仅本机访问，无任何网络同步）
------------------------------------------------------------
功能：浏览笔记本/笔记、查看内容、全文搜索、导入 .enex 文件。
数据全部落在本地 SQLite + assets 目录，零外部依赖、零联网。
启动: python3 server.py  (默认 http://127.0.0.1:8731)
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import html as _html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
# 让 importers 在两种布局下都能被找到：
#  - 开发态：app/ 与 importers/ 同级，需把父目录加入（dirname(dirname)）
#  - 打包态：importers/ 被拷进 Resources/app/importers，需把 app 自身目录加入（ROOT）
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))
import importers
from importers.common import (persist_notes, ensure_schema, md_to_html, mime_for_ext,
                              safe_name, sort_time_sql)
from sanitize import sanitize_html
from exporter import (note_markdown, note_enex, notebook_markdown, notebook_enex,
                      html_to_markdown)
# 数据目录：优先用环境变量（打包 App 时由外壳注入），否则落到用户支持目录，
# 这样 App 包被移动也不会丢数据（见 macos-native-webapp-package 坑 5）。
# 1.3 起 App 改名 GreenNotes，目录也跟着改；外壳会把旧的 LocalNotes 目录复制过来，
# 这里的 LOCALNOTES_* 只作兜底兼容（老外壳注入的旧变量名 / 老文档里的调试命令）。
def _default_data_dir():
    support = os.path.expanduser("~/Library/Application Support")
    new = os.path.join(support, "GreenNotes")
    old = os.path.join(support, "LocalNotes")
    if not os.path.isdir(new) and os.path.isdir(old):
        return old          # 还没迁移过（例如直接 python3 app/server.py 调试）
    return new

DATA_DIR = (os.environ.get("GREENNOTES_DATA")
            or os.environ.get("LOCALNOTES_DATA")
            or _default_data_dir())
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "notes.db")
ASSETS = os.path.join(DATA_DIR, "assets")
SAMPLE = os.path.join(ROOT, "sample.enex")   # 打包时一并拷进 Resources/app/
INDEX = os.path.join(ROOT, "static", "index.html")
PORT = int(os.environ.get("GREENNOTES_PORT") or os.environ.get("LOCALNOTES_PORT") or "8731")

ensure_schema(DB)
os.makedirs(ASSETS, exist_ok=True)

_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "bmp": "image/bmp", "svg": "image/svg+xml", "heic": "image/heic",
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown",
    "csv": "text/csv", "json": "application/json", "html": "text/html",
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "mp4": "video/mp4", "mov": "video/quicktime",
    "zip": "application/zip", "gz": "application/gzip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "rtf": "application/rtf",
}

def _con():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con

def list_notebooks():
    con = _con(); cur = con.cursor()
    rows = cur.execute("SELECT id,name,(SELECT COUNT(*) FROM notes n WHERE n.notebook_id=notebooks.id) FROM notebooks ORDER BY name").fetchall()
    con.close()
    return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]

def _sort_expr(p=""):
    return sort_time_sql(p)


def list_notes(notebook_id=None, tag=None):
    con = _con(); cur = con.cursor()
    cols = "SELECT id,notebook_id,title,created,updated FROM notes"
    order = _sort_expr() + " DESC, id DESC"
    if notebook_id:
        notes = cur.execute(cols + " WHERE notebook_id=? ORDER BY " + order, (notebook_id,)).fetchall()
    elif tag:
        notes = cur.execute(
            "SELECT n.id,n.notebook_id,n.title,n.created,n.updated FROM notes n "
            "JOIN tags t ON t.note_id=n.id WHERE t.name=? ORDER BY " + _sort_expr("n.") + " DESC, n.id DESC",
            (tag,)).fetchall()
    else:
        notes = cur.execute(cols + " ORDER BY " + order).fetchall()
    out = []
    for nid, nbid, title, created, updated in notes:
        tags = [t[0] for t in cur.execute("SELECT name FROM tags WHERE note_id=?", (nid,)).fetchall()]
        snip = cur.execute("SELECT substr(content_text,1,120) FROM notes WHERE id=?", (nid,)).fetchone()[0] or ""
        out.append({"id": nid, "notebook_id": nbid, "title": title, "created": created, "updated": updated,
                    "tags": tags, "snippet": snip.replace("\n", " ")})
    con.close()
    return out

def get_note(nid):
    con = _con(); cur = con.cursor()
    r = cur.execute("SELECT title,content_html,created,updated,author,source,notebook_id FROM notes WHERE id=?", (nid,)).fetchone()
    if not r:
        con.close(); return None
    tags = [t[0] for t in cur.execute("SELECT name FROM tags WHERE note_id=?", (nid,)).fetchall()]
    res = cur.execute("SELECT filename,mime,size FROM resources WHERE note_id=?", (nid,)).fetchall()
    con.close()
    return {"id": nid, "title": r[0], "content_html": r[1], "created": r[2], "updated": r[3],
            "author": r[4], "source": r[5], "notebook_id": r[6], "tags": tags,
            "resources": [{"filename": x[0], "mime": x[1], "size": x[2]} for x in res]}

def search(q):
    q2 = (q or "").strip()
    con = _con(); cur = con.cursor()
    rows = []
    if q2:
        # 中英文子串搜索：FTS5 trigram 支持 ≥3 字子串（含中文）；
        # 短词或异常时退回 LIKE 兜底，保证中文也能搜到。
        try:
            cur.execute("DROP TABLE IF EXISTS notes_fts")
            cur.execute("CREATE VIRTUAL TABLE notes_fts USING fts5("
                        "title, content_text, rowid, tokenize='trigram')")
            cur.execute("INSERT INTO notes_fts(rowid,title,content_text) "
                        "SELECT id,title,content_text FROM notes")
            if len(q2) >= 3:
                try:
                    cur.execute("SELECT n.id,n.title FROM notes n "
                                "JOIN notes_fts f ON n.id=f.rowid "
                                "WHERE notes_fts MATCH ? ORDER BY " + _sort_expr("n.") + " DESC", (q2,))
                    rows = cur.fetchall()
                except sqlite3.OperationalError:
                    rows = []
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            esc = q2.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            rows = cur.execute(
                "SELECT id,title FROM notes WHERE title LIKE ? ESCAPE '\\' "
                "OR content_text LIKE ? ESCAPE '\\' ORDER BY " + _sort_expr() + " DESC",
                (like, like)).fetchall()
    con.close()
    seen = set(); out = []
    for nid, title in rows:
        if nid in seen:
            continue
        seen.add(nid); out.append({"id": nid, "title": title})
    return out

def get_or_create_notebook(name, con=None):
    own = con is None
    c = con or _con()
    cur = c.cursor()
    r = cur.execute("SELECT id FROM notebooks WHERE name=?", (name,)).fetchone()
    if r:
        nid = r[0]
    else:
        cur.execute("INSERT INTO notebooks(name) VALUES(?)", (name,))
        nid = cur.lastrowid
        c.commit()
    if own:
        c.close()
    return nid


def insert_imported(wrapped, assets_dir):
    """把 dispatch 返回的笔记写入本地库，并返回统计。"""
    notes = wrapped["notes"]
    # 先落盘资源并改写引用
    stat = persist_notes(notes, assets_dir)
    con = _con(); cur = con.cursor()
    inserted = 0
    for n in notes:
        nb_id = get_or_create_notebook(n.notebook, con)
        clean = sanitize_html(n.content_html)
        txt = strip_text(clean)
        cur.execute(
            "INSERT INTO notes(notebook_id,title,content_html,content_text,created,updated,author,source) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (nb_id, n.title or "无标题", clean, txt,
             n.created or None, n.updated or None, n.author or None, n.source or None))
        nid = cur.lastrowid
        for t in (n.tags or []):
            if t:
                cur.execute("INSERT INTO tags(note_id,name) VALUES(?,?)", (nid, t))
        # 资源元数据落库（按对象去重，避免同一资源被 resources 与 enmedia 重复计入）
        seen = set()
        all_res = []
        for r in (list(n.resources) + list(n.enmedia.values())):
            if id(r) in seen:
                continue
            seen.add(id(r))
            all_res.append(r)
        for r in all_res:
            fn = getattr(r, "saved_as", None) or r.filename
            cur.execute(
                "INSERT INTO resources(note_id,filename,mime,ext,hash,size,local_path) VALUES(?,?,?,?,?,?,?)",
                (nid, fn, r.mime, (r.ext or os.path.splitext(fn)[1].lstrip(".")),
                 r.hash or None, len(r.data), os.path.join("assets", fn)))
        inserted += 1
    con.commit(); con.close()
    return {
        "source": wrapped["source"], "kind": wrapped["kind"],
        "notes": inserted,
        "notebooks": wrapped["notebooks"],
        "resources": stat["resources"],
        "resources_total_bytes": stat["resources_total_bytes"],
    }


def do_import_bytes(raw, filename, notebook_name=None):
    wrapped = importers.dispatch.import_bytes(raw, filename, notebook_name)
    return insert_imported(wrapped, ASSETS)


def do_import_path(path, notebook_name=None):
    if not os.path.exists(path):
        return {"error": "not found", "path": path}
    wrapped = importers.dispatch.import_path(path, notebook_name, ASSETS)
    return insert_imported(wrapped, ASSETS)


def strip_text(html_str):
    t = re.sub(r"<br\s*/?>", "\n", html_str or "")
    t = re.sub(r"</(p|div|li|h[1-6]|tr|td|th)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return _html.unescape(t).strip()


MAX_ATTACH = 25 * 1024 * 1024      # 单个附件上限 25MB（base64 后约 33MB）


def attach_file(nid, filename, data):
    """把拖入的文件存进 assets/ 并登记到 resources，返回给前端插入用。
    同一篇笔记里内容相同的文件只存一份（按 md5 去重）。"""
    if not data:
        return {"error": "文件是空的"}, 400
    if len(data) > MAX_ATTACH:
        return {"error": f"文件超过 {MAX_ATTACH // 1024 // 1024}MB 上限"}, 413
    con = _con(); cur = con.cursor()
    if not cur.execute("SELECT id FROM notes WHERE id=?", (nid,)).fetchone():
        con.close()
        return {"error": "笔记不存在"}, 404
    h = hashlib.md5(data).hexdigest()
    dup = cur.execute(
        "SELECT filename,local_path,mime,size FROM resources WHERE note_id=? AND hash=?",
        (nid, h)).fetchone()
    if dup:
        con.close()
        fn = os.path.basename(dup[1] or dup[0])
        return {"ok": True, "filename": fn, "name": dup[0], "url": "assets/" + fn,
                "mime": dup[2] or "", "size": dup[3] or len(data),
                "image": (dup[2] or "").startswith("image/"), "dedup": True}, 200

    orig = os.path.basename((filename or "附件").replace("\\", "/"))
    stem, ext = os.path.splitext(orig)
    ext = re.sub(r"[^A-Za-z0-9]", "", ext.lstrip("."))[:10].lower()
    base = safe_name(stem, "file", 1)[:60]
    fn = f"{base}-{h[:8]}.{ext}" if ext else f"{base}-{h[:8]}"
    with open(os.path.join(ASSETS, fn), "wb") as f:
        f.write(data)
    mime = mime_for_ext(ext) or "application/octet-stream"
    cur.execute(
        "INSERT INTO resources(note_id,filename,mime,ext,hash,size,local_path) VALUES(?,?,?,?,?,?,?)",
        (nid, orig, mime, ext, h, len(data), os.path.join("assets", fn)))
    con.commit(); con.close()
    return {"ok": True, "filename": fn, "name": orig, "url": "assets/" + fn,
            "mime": mime, "size": len(data), "image": mime.startswith("image/")}, 200

def create_notebook(name):
    con = _con(); cur = con.cursor()
    cur.execute("INSERT INTO notebooks(name) VALUES(?)", (name,))
    nid = cur.lastrowid; con.commit(); con.close()
    return nid

def create_note(notebook_id, title, content_html, tags):
    con = _con(); cur = con.cursor()
    clean = sanitize_html(content_html)
    txt = strip_text(clean)
    cur.execute("INSERT INTO notes(notebook_id,title,content_html,content_text,created,updated) VALUES(?,?,?,?,datetime('now'),datetime('now'))",
                (notebook_id, title, clean, txt))
    nid = cur.lastrowid
    for t in (tags or []):
        cur.execute("INSERT INTO tags(note_id,name) VALUES(?,?)", (nid, t))
    con.commit(); con.close()
    return nid

def update_note(nid, title, content_html, tags):
    con = _con(); cur = con.cursor()
    clean = sanitize_html(content_html)
    txt = strip_text(clean)
    cur.execute("UPDATE notes SET title=?,content_html=?,content_text=?,updated=datetime('now') WHERE id=?",
                (title, clean, txt, nid))
    cur.execute("DELETE FROM tags WHERE note_id=?", (nid,))
    for t in (tags or []):
        cur.execute("INSERT INTO tags(note_id,name) VALUES(?,?)", (nid, t))
    con.commit(); con.close()

def move_note(nid, notebook_id):
    """把笔记归到另一个笔记本，返回原笔记本 id（不存在则 None）。"""
    con = _con(); cur = con.cursor()
    row = cur.execute("SELECT notebook_id FROM notes WHERE id=?", (nid,)).fetchone()
    if not row:
        con.close(); return None
    cur.execute("UPDATE notes SET notebook_id=?, updated=datetime('now') WHERE id=?",
                (notebook_id, nid))
    con.commit(); con.close()
    return row[0]

def delete_note(nid):
    con = _con(); cur = con.cursor()
    cur.execute("DELETE FROM tags WHERE note_id=?", (nid,))
    cur.execute("DELETE FROM resources WHERE note_id=?", (nid,))
    cur.execute("DELETE FROM notes WHERE id=?", (nid,))
    con.commit(); con.close()


def export_filename(name, ext, fallback="笔记"):
    """把标题变成安全的文件名：去掉路径分隔符与系统保留字符，限制长度。"""
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", (name or "").strip())
    s = s.strip(" .")
    if len(s) > 60:
        s = s[:60].rstrip(" .")
    return f"{s or fallback}.{ext}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, fpath):
        if not os.path.isfile(fpath):
            self._send(404, {"error": "not found"})
            return
        ext = fpath.rsplit(".", 1)[-1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_download(self, data, filename, ctype):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        # 中文文件名要按 RFC 5987 编码，否则 Safari/Chrome 会存成乱码或直接叫 download
        from urllib.parse import quote as _quote
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "export"
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{_quote(filename)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        if path in ("/", "/index.html"):
            self._send_file(INDEX)
        elif path in ("/icon-64.png", "/icon-128.png", "/favicon.ico"):
            fn = "icon-64.png" if path == "/icon-64.png" else "icon-128.png"
            self._send_file(os.path.join(ROOT, "static", fn))
        elif path == "/api/notebooks":
            self._send(200, list_notebooks())
        elif path == "/api/notes":
            qs = parse_qs(p.query)
            nb = qs.get("notebook_id", [None])[0]
            tg = qs.get("tag", [None])[0]
            self._send(200, list_notes(int(nb) if nb else None, tg))
        elif path.startswith("/api/note/") and path.endswith("/export"):
            nid = int(path.split("/")[-2])
            fmt = parse_qs(p.query).get("fmt", ["md"])[0]
            con = _con()
            row = con.execute("SELECT title FROM notes WHERE id=?", (nid,)).fetchone()
            title = (row[0] if row else "") or "笔记"
            if fmt == "enex":
                data = note_enex(con, ASSETS, nid) or ""
                self._send_download(data, export_filename(title, "enex"),
                                    "application/xml; charset=utf-8")
            else:
                data = note_markdown(con, nid) or ""
                self._send_download(data, export_filename(title, "md"),
                                    "text/markdown; charset=utf-8")
            con.close()
        elif path.startswith("/api/notebook/") and path.endswith("/export"):
            nbid = int(path.split("/")[-2])
            fmt = parse_qs(p.query).get("fmt", ["md"])[0]
            con = _con()
            row = con.execute("SELECT name FROM notebooks WHERE id=?", (nbid,)).fetchone()
            nb_name = (row[0] if row else "") or "笔记本"
            if fmt == "enex":
                data = notebook_enex(con, ASSETS, nbid) or ""
                self._send_download(data, export_filename(nb_name, "enex"),
                                    "application/xml; charset=utf-8")
            else:
                data = notebook_markdown(con, nbid) or ""
                self._send_download(data, export_filename(nb_name + "-全部笔记", "md"),
                                    "text/markdown; charset=utf-8")
            con.close()
        elif path.startswith("/api/note/"):
            nid = path.rsplit("/", 1)[-1]
            self._send(200, get_note(int(nid)) or {"error": "not found"})
        elif path == "/api/search":
            q = parse_qs(p.query).get("q", [""])[0]
            self._send(200, search(q) if q else [])
        elif path == "/api/tags":
            con = _con(); cur = con.cursor()
            tags = [r[0] for r in cur.execute(
                "SELECT DISTINCT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()]
            con.close()
            self._send(200, tags)
        elif path == "/api/state":
            con = _con(); n = con.execute("SELECT COUNT(*) FROM notebooks").fetchone()[0]; con.close()
            self._send(200, {"ok": True, "port": PORT, "notebooks": n})
        elif path.startswith("/assets/"):
            # 附件名可能带中文，浏览器会把 URL 百分号编码，这里要先解码再取 basename
            name = os.path.basename(unquote(path))
            self._send_file(os.path.join(ASSETS, name))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        if p.path == "/api/import":
            data = payload.get("data")
            if not data:
                self._send(400, {"error": "missing data"}); return
            try:
                b = base64.b64decode(data)
            except Exception:
                self._send(400, {"error": "bad base64"}); return
            filename = payload.get("filename") or "import.enex"
            res = do_import_bytes(b, filename, payload.get("notebook"))
            self._send(200, res)
        elif p.path == "/api/import_path":
            fp = payload.get("path")
            if not fp or not os.path.exists(fp):
                self._send(400, {"error": "file or dir not found"}); return
            res = do_import_path(fp, payload.get("notebook"))
            self._send(200, res)
        elif p.path == "/api/convert":
            # 编辑器两种模式之间互转：HTML ↔ Markdown（Markdown 模式实时预览的保存路径）
            src = (payload.get("from") or "").strip().lower()
            text = payload.get("text") or ""
            if src == "html":
                self._send(200, {"ok": True, "text": html_to_markdown(text)})
            elif src == "md":
                self._send(200, {"ok": True, "text": md_to_html(text)})
            else:
                self._send(400, {"error": "from 必须是 html 或 md"})
        elif p.path.startswith("/api/note/") and p.path.endswith("/attach"):
            # 拖入/粘贴附件：POST /api/note/<id>/attach  {filename, data(base64)}
            try:
                nid = int(p.path.split("/")[3])
            except (IndexError, ValueError):
                self._send(400, {"error": "bad note id"}); return
            b64 = payload.get("data")
            if not b64:
                self._send(400, {"error": "缺少文件内容"}); return
            try:
                blob = base64.b64decode(b64)
            except Exception:
                self._send(400, {"error": "base64 解析失败"}); return
            res, code = attach_file(nid, payload.get("filename"), blob)
            self._send(code, res)
        elif p.path == "/api/notebook":
            nid = create_notebook(payload.get("name", "未命名笔记本"))
            self._send(200, {"id": nid})
        elif p.path == "/api/note":
            nb = payload.get("notebook_id")
            if not nb:
                nbs = list_notebooks()
                nb = nbs[0]["id"] if nbs else create_notebook("我的笔记本")
            nid = create_note(int(nb), payload.get("title", ""), payload.get("content_html", ""), payload.get("tags", []))
            self._send(200, get_note(nid))
        elif p.path.startswith("/api/note/") and p.path.endswith("/move"):
            # 拖拽归类：POST /api/note/<id>/move  {"notebook_id": 3}
            try:
                nid = int(p.path.split("/")[3])
            except (IndexError, ValueError):
                self._send(400, {"error": "bad note id"}); return
            nb = payload.get("notebook_id")
            if nb is None:
                self._send(400, {"error": "missing notebook_id"}); return
            nb = int(nb)
            existed = any(n["id"] == nb for n in list_notebooks())
            if not existed:
                self._send(404, {"error": "notebook not found"}); return
            old = move_note(nid, nb)
            if old is None:
                self._send(404, {"error": "note not found"}); return
            note = get_note(nid)
            self._send(200, {"ok": True, "from": old, "note": note})
        elif p.path.startswith("/api/note/"):
            nid = int(p.path.rsplit("/", 1)[-1])
            update_note(nid, payload.get("title", ""), payload.get("content_html", ""), payload.get("tags", []))
            self._send(200, get_note(nid))
        elif p.path.startswith("/api/notebook/"):
            # 重命名笔记本：POST /api/notebook/<id>  {"name": "新名字"}
            try:
                nid = int(p.path.rsplit("/", 1)[-1])
            except ValueError:
                self._send(400, {"error": "笔记本 id 不合法"}); return
            new_name = (payload.get("name") or "").strip()
            if not new_name:
                self._send(400, {"error": "名称不能为空"}); return
            if len(new_name) > 100:
                self._send(400, {"error": "名称最多 100 个字符"}); return
            con = _con(); cur = con.cursor()
            row = cur.execute("SELECT name FROM notebooks WHERE id=?", (nid,)).fetchone()
            if not row:
                con.close(); self._send(404, {"error": "笔记本不存在"}); return
            dup = cur.execute("SELECT id FROM notebooks WHERE name=? AND id<>?",
                              (new_name, nid)).fetchone()
            if dup:
                con.close(); self._send(409, {"error": "已有同名笔记本"}); return
            old_name = row[0]
            cur.execute("UPDATE notebooks SET name=? WHERE id=?", (new_name, nid))
            con.commit(); con.close()
            self._send(200, {"ok": True, "id": nid, "name": new_name, "old": old_name})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        p = urlparse(self.path)
        if p.path.startswith("/api/note/"):
            nid = int(p.path.rsplit("/", 1)[-1])
            delete_note(nid)
            self._send(200, {"ok": True})
        elif p.path.startswith("/api/notebook/"):
            nid = int(p.path.rsplit("/", 1)[-1])
            con = _con(); cur = con.cursor()
            n = cur.execute("SELECT COUNT(*) FROM notes WHERE notebook_id=?", (nid,)).fetchone()[0]
            if n > 0 and not parse_qs(p.query).get("force"):
                con.close()
                self._send(409, {"error": "notebook not empty", "notes": n})
                return
            cur.execute("DELETE FROM notes WHERE notebook_id=?", (nid,))
            cur.execute("DELETE FROM notebooks WHERE id=?", (nid,))
            con.commit(); con.close()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})


def watch_parent(pid: int):
    """看门狗：父进程（App 外壳）挂了就自杀，避免孤儿服务占用端口。"""
    import threading
    def loop():
        while True:
            time.sleep(2)
            try:
                os.kill(pid, 0)
            except OSError:
                os._exit(0)
    threading.Thread(target=loop, daemon=True).start()


def seed():
    """首次启动用样例数据填充，便于直接体验。"""
    con = _con(); n = con.execute("SELECT COUNT(*) FROM notebooks").fetchone()[0]; con.close()
    if n == 0 and os.path.isfile(SAMPLE):
        do_import_path(SAMPLE, "示例笔记本")
        print("[seed] 已用 sample.enex 填充示例数据")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="离线笔记 App 本地服务")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--parent", type=int, default=None, help="父进程 PID，用于看门狗")
    args = ap.parse_args()
    PORT = args.port
    if args.parent:
        watch_parent(args.parent)
    seed()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"离线笔记 App 已启动: http://127.0.0.1:{PORT}  (Ctrl+C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
