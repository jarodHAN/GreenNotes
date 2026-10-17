# -*- coding: utf-8 -*-
"""纯文本导入器 —— Sublime Text、记事本等保存的 .txt / .text 文件。"""
import datetime
import os
from .common import ImportedNote


def _build_note(raw: str, title: str, notebook: str, path: str = None) -> ImportedNote:
    first = (raw.split("\n", 1)[0]).strip()
    if 0 < len(first) <= 60:
        title = first
    if path:
        st = os.stat(path)
        ctime = datetime.datetime.fromtimestamp(st.st_ctime).strftime("%Y%m%dT%H%M%SZ")
        mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y%m%dT%H%M%SZ")
    else:
        now = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")
        ctime = mtime = now
    escaped = _html_escape_para(raw)
    return ImportedNote(notebook=notebook or "导入的文本", title=title,
                        content_html=escaped, tags=[], created=ctime, updated=mtime, source="text")


def parse_txt_file(path: str, notebook: str = None) -> list:
    raw = open(path, encoding="utf-8", errors="replace").read()
    title = os.path.splitext(os.path.basename(path))[0]
    return [_build_note(raw, title, notebook or "导入的文本", path)]


def parse_txt_bytes(raw: bytes, filename: str, notebook: str = None) -> list:
    text = raw.decode("utf-8", "replace")
    title = os.path.splitext(filename)[0]
    return [_build_note(text, title, notebook or "导入的文本")]


def _html_escape_para(text: str) -> str:
    out = []
    for block in text.split("\n\n"):
        lines = [l for l in block.split("\n") if l.strip() != ""]
        if not lines:
            continue
        para = "<br/>".join(_esc(l) for l in lines)
        out.append(f"<p>{para}</p>")
    return "\n".join(out) if out else f"<p>{_esc(text.strip())}</p>"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
