# -*- coding: utf-8 -*-
"""多格式导入层 · 公共基础
定义统一的 Note / Resource 数据结构，以及把导入结果落盘（资源写入 assets、改写引用）的函数。
所有 importer 都产出 ImportedNote，由 persist_notes 统一写文件并改写正文里的图片引用。
"""
from __future__ import annotations
import base64
import binascii
import hashlib
import html
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
    "image/bmp": "bmp", "image/svg+xml": "svg", "application/pdf": "pdf",
    "text/plain": "txt", "text/html": "html", "audio/wav": "wav",
    "audio/mpeg": "mp3", "audio/m4a": "m4a", "video/mp4": "mp4",
}


def ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), "bin")


def mime_for_ext(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    for k, v in _MIME_EXT.items():
        if v == ext:
            return k
    return "application/octet-stream"


def safe_name(name: str, fallback: str, idx: int) -> str:
    name = (name or "").strip()
    if not name:
        return f"{fallback}_{idx}"
    # 保留中文、字母数字、点、连字符、下划线
    return re.sub(r"[^\w.\-一-鿿]", "_", name)


def html_to_text(html_str: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", html_str or "")
    t = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


@dataclass
class Resource:
    filename: str
    mime: str
    data: bytes
    hash: str = ""          # 用于 en-media 匹配（Evernote）
    ext: str = ""
    saved_as: str = ""      # 落盘后的实际文件名（由 persist_notes 填充）


SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks(
    id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY, notebook_id INTEGER, title TEXT,
    content_html TEXT, content_text TEXT,
    created TEXT, updated TEXT,
    source TEXT, author TEXT, latitude REAL, longitude REAL,
    reminder_time TEXT);
CREATE TABLE IF NOT EXISTS tags(
    id INTEGER PRIMARY KEY, note_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS resources(
    id INTEGER PRIMARY KEY, note_id INTEGER,
    filename TEXT, mime TEXT, ext TEXT,
    hash TEXT, size INTEGER, local_path TEXT);
"""


def ensure_schema(db_path: str):
    """在指定数据库上创建笔记存储表（App 启动时调用）。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


@dataclass
class ImportedNote:
    notebook: str
    title: str
    content_html: str
    tags: List[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    author: str = ""
    source: str = ""
    resources: List[Resource] = field(default_factory=list)   # 显式附件
    enmedia: dict = field(default_factory=dict)               # hash -> Resource (en-media)
    _base: str = ""                                          # 源文件所在目录（解析相对图片用）


# ---------------------------------------------------------------------------
# Markdown -> HTML（轻量但够用，覆盖 Obsidian/通用 md）
# ---------------------------------------------------------------------------
def md_to_html(md: str) -> str:
    md = md.replace("\r\n", "\n")
    lines = md.split("\n")
    out: List[str] = []
    i = 0
    in_code = False
    code_buf: List[str] = []
    list_buf: List[str] = []
    list_type = None  # 'ul' / 'ol'

    def flush_list():
        nonlocal list_buf, list_type
        if list_buf:
            tag = list_type or "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(c)}</li>" for c in list_buf) + f"</{tag}>")
            list_buf = []
            list_type = None

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: f'<img alt="{m.group(1)}" src="{m.group(2)}"/>', s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
        s = re.sub(r"(?<![\w])#([\w一-鿿\-]+)", lambda m: f'<span class="md-tag">#{m.group(1)}</span>', s)  # #标签
        s = re.sub(r"\[\[([^\]]+)\]\]", lambda m: f'<a href="#" class="wikilink">{m.group(1)}</a>', s)       # [[wikilink]]
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    while i < len(lines):
        line = lines[i]
        # 代码块
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
                lang = line.strip()[3:].strip()
                if lang:
                    out.append(f'<pre class="code" data-lang="{html.escape(lang)}">')
                else:
                    out.append("<pre class=\"code\">")
            else:
                in_code = False
                out.append(html.escape("\n".join(code_buf)) + "</pre>")
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        # 分割线
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            flush_list()
            out.append("<hr/>")
            i += 1
            continue
        # 引用
        if re.match(r"^\s*>\s?", line):
            flush_list()
            quote = re.sub(r"^\s*>\s?", "", line)
            out.append(f"<blockquote>{inline(quote)}</blockquote>")
            i += 1
            continue
        # 列表
        lm = re.match(r"^\s*([-*])\s+(.*)$", line)
        om = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if lm:
            if list_type and list_type != "ul":
                flush_list()
            list_type = "ul"
            list_buf.append(lm.group(2))
            i += 1
            continue
        if om:
            if list_type and list_type != "ol":
                flush_list()
            list_type = "ol"
            list_buf.append(om.group(2))
            i += 1
            continue
        # 空行
        if not line.strip():
            flush_list()
            i += 1
            continue
        # 普通段落
        flush_list()
        out.append(f"<p>{inline(line)}</p>")
        i += 1
    flush_list()
    if in_code:  # 未闭合
        out.append(html.escape("\n".join(code_buf)) + "</pre>")
    return "\n".join(out)


def parse_md_frontmatter(md: str):
    """解析 YAML frontmatter，返回 (meta_dict, 剩余md)。仅支持简单 key: value / key: [a,b]。"""
    meta = {}
    body = md
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            block = md[3:end].strip("\n")
            body = md[end + 4:]
            for ln in block.split("\n"):
                if ":" not in ln:
                    continue
                k, v = ln.split(":", 1)
                k, v = k.strip(), v.strip()
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                meta[k] = v
    return meta, body


# ---------------------------------------------------------------------------
# 落盘：保存资源 + 改写正文引用
# ---------------------------------------------------------------------------
def _unique_filename(assets_dir: str, base: str, ext: str, used: set) -> str:
    fn = f"{base}.{ext}" if ext else base
    if fn in used and os.path.exists(os.path.join(assets_dir, fn)):
        n = 1
        stem = os.path.splitext(fn)[0]
        while True:
            cand = f"{stem}_{n}.{ext}" if ext else f"{stem}_{n}"
            if cand not in used:
                fn = cand
                break
            n += 1
    used.add(fn)
    return fn


def persist_notes(notes: List[ImportedNote], assets_dir: str) -> dict:
    """把导入的笔记资源写盘，并改写正文中的 <en-media> 与 <img> 引用。
    返回统计信息。"""
    os.makedirs(assets_dir, exist_ok=True)
    used = set(os.listdir(assets_dir))
    res_count = 0
    res_bytes = 0

    for note in notes:
        # 1) en-media 按 hash 改写
        if note.enmedia:
            for h, res in note.enmedia.items():
                if getattr(res, "saved_as", None):  # 同一对象已在 resources 中处理过则跳过
                    continue
                stem, ext = os.path.splitext(res.filename)
                ext = ext.lstrip(".")
                fn = _unique_filename(assets_dir, safe_name(stem, "res", res_count + 1), ext, used)
                with open(os.path.join(assets_dir, fn), "wb") as f:
                    f.write(res.data)
                res_count += 1
                res_bytes += len(res.data)
                res.saved_as = fn
                note.content_html = re.sub(
                    r'<en-media[^>]*hash="' + re.escape(h) + r'"[^>]*/?>',
                    f'<img src="assets/{fn}" alt="{html.escape(fn)}"/>',
                    note.content_html)

        # 2) 显式 resources（markdown/html 引用到的本地图片等）
        for res in note.resources:
            stem, ext = os.path.splitext(res.filename)
            ext = ext.lstrip(".")
            fn = _unique_filename(assets_dir, safe_name(stem, "res", res_count + 1), ext, used)
            with open(os.path.join(assets_dir, fn), "wb") as f:
                f.write(res.data)
            res_count += 1
            res_bytes += len(res.data)
            res.saved_as = fn
            # 把正文中对该原路径的引用改写为 assets/fn
            esc = re.escape(os.path.basename(res.filename))
            note.content_html = re.sub(
                r'src="([^"]*)' + esc + r'"',
                f'src="assets/{fn}"',
                note.content_html)

        # 3) 正文中仍残留的本地相对 <img src="xxx">（未变成 Resource 的）：尝试读取
        if note._base:
            def _resolve(m):
                src = m.group(1)
                if src.startswith(("http://", "https://", "data:", "assets/", "#")):
                    return m.group(0)
                cand = os.path.normpath(os.path.join(note._base, src))
                if os.path.isfile(cand):
                    ext = os.path.splitext(cand)[1].lstrip(".")
                    fn = _unique_filename(assets_dir, safe_name(os.path.basename(cand), "img", res_count + 1), ext, used)
                    with open(os.path.join(assets_dir, fn), "wb") as f:
                        f.write(open(cand, "rb").read())
                    global _rc, _rb
                    _rc += 1
                    _rb += os.path.getsize(cand)
                    return f'<img src="assets/{fn}" alt="{html.escape(os.path.basename(cand))}"/>'
                return m.group(0)
            # 用闭包麻烦，直接处理
            note.content_html = _resolve_imgs(note.content_html, note._base, assets_dir, used,
                                             lambda: None)
    return {"resources": res_count, "resources_total_bytes": res_bytes}


_rc = 0
_rb = 0


def _resolve_imgs(content: str, base: str, assets_dir: str, used: set, _counter):
    def repl(m):
        global _rc, _rb
        src = m.group(1)
        if src.startswith(("http://", "https://", "data:", "assets/", "#", "cid:")):
            return m.group(0)
        cand = os.path.normpath(os.path.join(base, src))
        if os.path.isfile(cand):
            ext = os.path.splitext(cand)[1].lstrip(".")
            data = open(cand, "rb").read()
            fn = _unique_filename(assets_dir, safe_name(os.path.basename(cand), "img", len(used) + 1), ext, used)
            with open(os.path.join(assets_dir, fn), "wb") as f:
                f.write(data)
            _rc += 1
            _rb += len(data)
            return f'<img src="assets/{fn}" alt="{html.escape(os.path.basename(cand))}"/>'
        return m.group(0)
    return re.sub(r'<img[^>]*src="([^"]+)"[^>]*/?>', repl, content)
