# -*- coding: utf-8 -*-
"""Markdown 导入器 —— 覆盖 Obsidian vault、通用 .md 文件，以及整目录递归导入。"""
import datetime
import os
import re
from .common import ImportedNote, Resource, md_to_html, parse_md_frontmatter, mime_for_ext, ext_for_mime


def _note_from_text(text: str, base_dir: str, title_hint: str, notebook: str) -> ImportedNote:
    meta, body = parse_md_frontmatter(text)
    title = None
    if isinstance(meta.get("title"), str):
        title = meta["title"].strip()
    else:
        m = re.search(r"^#\s+(.+)$", body, re.M)
        if m:
            title = m.group(1).strip()
    if not title:
        title = title_hint or "无标题笔记"
    tags = []
    if "tags" in meta:
        t = meta["tags"]
        tags = t if isinstance(t, list) else [x.strip() for x in str(t).split() if x.strip()]
    tags += re.findall(r"(?<![\w])#([\w一-鿿\-]+)", body)
    tags = list(dict.fromkeys(tags))[:30]
    html = md_to_html(body)
    res = []
    def repl(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith(("http://", "https://", "data:", "assets:")):
            return m.group(0)
        cand = os.path.normpath(os.path.join(base_dir, src))
        if os.path.isfile(cand):
            ext = os.path.splitext(cand)[1].lstrip(".")
            data = open(cand, "rb").read()
            fn = os.path.basename(cand)
            res.append(Resource(filename=fn, mime=mime_for_ext(ext), data=data, ext=ext))
            return f'<img alt="{alt}" src="{fn}"/>'
        return m.group(0)
    html = re.sub(r'<img alt="([^"]*)" src="([^"]+)"/>', repl, html)
    ctime = meta.get("created") if isinstance(meta.get("created"), str) else ""
    mtime = meta.get("updated") if isinstance(meta.get("updated"), str) else ""
    n = ImportedNote(notebook=notebook, title=title, content_html=html, tags=tags,
                     created=ctime, updated=mtime, source="markdown")
    n.resources = res
    n._base = base_dir
    return n


def _note_from_md_file(path: str, notebook: str) -> ImportedNote:
    raw = open(path, encoding="utf-8", errors="replace").read()
    st = os.stat(path)
    ctime = datetime.datetime.fromtimestamp(st.st_ctime).strftime("%Y%m%dT%H%M%SZ")
    mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y%m%dT%H%M%SZ")
    n = _note_from_text(raw, os.path.dirname(path), os.path.splitext(os.path.basename(path))[0], notebook)
    if not n.created:
        n.created = ctime
    if not n.updated:
        n.updated = mtime
    return n


def parse_md_file(path: str, notebook: str = None) -> list:
    nb = notebook or os.path.splitext(os.path.basename(path))[0]
    return [_note_from_md_file(path, nb)]


def parse_md_bytes(raw: bytes, filename: str, notebook: str = None) -> list:
    text = raw.decode("utf-8", "replace")
    nb = notebook or os.path.splitext(filename)[0]
    return [_note_from_text(text, "", os.path.splitext(filename)[0], nb)]


def parse_md_dir(root: str, default_nb: str = None) -> list:
    """目录递归：每层子目录作为笔记本名；根目录下的散文件归到一个默认笔记本。"""
    notes = []
    root = os.path.abspath(root)
    default_nb = default_nb or os.path.basename(root) + "(笔记)"
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            nb = default_nb
        else:
            nb = rel.replace(os.sep, " / ")
        for fn in filenames:
            if fn.lower().endswith((".md", ".markdown", ".mdx")):
                notes.append(_note_from_md_file(os.path.join(dirpath, fn), nb))
    return notes
