# -*- coding: utf-8 -*-
"""导入调度：根据来源（文件后缀 / 目录 / 字节流）选择对应 importer。"""
import os
from .common import ImportedNote
from . import enex, markdown, html as html_imp, txt, youdao

_EXT_MAP = {
    ".enex": "enex",
    ".md": "markdown", ".markdown": "markdown", ".mdx": "markdown",
    ".html": "html", ".htm": "html", ".mht": "html", ".mhtml": "html",
    ".txt": "txt", ".text": "txt",
    ".note": "youdao",
}


def import_path(src: str, notebook_hint: str = None, assets_dir: str = None) -> dict:
    """导入本地文件或目录。返回 {notes, notebooks, resources, ...统计} 在调用方 persist 之后补齐。"""
    if os.path.isdir(src):
        notes = markdown.parse_md_dir(src, default_nb=notebook_hint)
        # 目录里也可能有 enex/html/txt，统一扫描
        for dp, _, files in os.walk(src):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dp, fn)
                if ext in (".enex", ".html", ".htm", ".mht", ".mhtml", ".txt", ".text", ".note"):
                    notes.extend(_one_file(full, notebook_hint))
        return _wrap(notes, src, "directory")
    return _wrap(_one_file(src, notebook_hint), src, os.path.splitext(src)[1].lower())


def import_bytes(raw: bytes, filename: str, notebook_hint: str = None) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    kind = _EXT_MAP.get(ext, "txt")
    notes = _by_kind(kind, raw, filename, notebook_hint)
    return _wrap(notes, filename, ext)


def _one_file(path: str, notebook_hint: str = None):
    ext = os.path.splitext(path)[1].lower()
    kind = _EXT_MAP.get(ext, "txt")
    return _by_kind(kind, None, path, notebook_hint, path=path)


def _by_kind(kind, raw, name, notebook_hint, path=None):
    if kind == "enex":
        return enex.parse_enex_bytes(raw or open(path, "rb").read(), notebook_hint)
    if kind == "markdown":
        if raw is not None:
            return markdown.parse_md_bytes(raw, name, notebook_hint)
        return markdown.parse_md_file(path, notebook_hint)
    if kind == "html":
        if raw is not None:
            return html_imp.parse_html_bytes(raw, notebook_hint or os.path.splitext(name)[0], name)
        return html_imp.parse_html_file(path, notebook_hint)
    if kind == "txt":
        if raw is not None:
            return txt.parse_txt_bytes(raw, name, notebook_hint)
        return txt.parse_txt_file(path or name, notebook_hint)
    if kind == "youdao":
        return youdao.parse_youdao_file(path or name, notebook_hint)
    # 兜底：txt
    return txt.parse_txt_file(path or name, notebook_hint)


def _wrap(notes: list, src: str, kind: str) -> dict:
    nbs = {}
    for n in notes:
        nbs[n.notebook] = nbs.get(n.notebook, 0) + 1
    return {
        "source": src,
        "kind": kind,
        "notes": notes,
        "note_count": len(notes),
        "notebooks": nbs,
    }
