# -*- coding: utf-8 -*-
"""Evernote .enex 导入器 —— 产出 ImportedNote 列表"""
import base64
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from .common import ImportedNote, Resource, html_to_text

_NS = re.compile(r"\{[^}]+\}")


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _children(elem):
    return {_local(c.tag): c for c in elem}


def _text(elem):
    return (elem.text or "").strip() if elem is not None else ""


def parse_enex_bytes(raw: bytes, notebook_hint: str = None) -> list:
    raw = re.sub(rb"<!DOCTYPE[^>]*>", b"", raw, flags=re.I)
    root = ET.fromstring(raw)
    nb_name = notebook_hint or _text(_children(root).get("export-date")) or "导入的笔记本"
    notes = []
    for note in root.iter():
        if _local(note.tag) != "note":
            continue
        notes.append(_parse_note(note, nb_name))
    return notes


def _parse_note(note, nb_name: str) -> ImportedNote:
    ch = _children(note)
    title = _text(ch.get("title")) or "无标题笔记"
    created = _text(ch.get("created"))
    updated = _text(ch.get("updated"))
    content_el = ch.get("content")
    content_html = (content_el.text or "") if content_el is not None else ""

    res_map = {}
    resources = []
    for res in note.findall("*"):
        if _local(res.tag) != "resource":
            continue
        rch = _children(res)
        data_el = rch.get("data")
        mime = _text(rch.get("mime"))
        data_b64 = (data_el.text or "").strip() if data_el is not None else ""
        data = base64.b64decode(data_b64) if data_b64 else b""
        h = (data_el.get("hash") if data_el is not None else None) or hashlib.md5(data).hexdigest()
        ra_el = rch.get("resource-attributes")
        rattr = _children(ra_el) if ra_el is not None else {}
        fname = _text(rattr.get("file-name")) or _text(rattr.get("source-url"))
        from .common import ext_for_mime
        ext = ext_for_mime(mime)
        # 文件已带合法扩展名则不重复追加
        if "." in (fname or "") and fname.rsplit(".", 1)[-1].lower() == ext:
            base = fname
        else:
            base = (fname or "resource") + "." + ext
        res_obj = Resource(filename=base, mime=mime, data=data, hash=h, ext=ext)
        res_map[h] = res_obj

    tags = [_text(t) for t in note.findall("*") if _local(t.tag) == "tag" and _text(t)]
    na_el = ch.get("note-attributes")
    attrs = _children(na_el) if na_el is not None else {}
    author = _text(attrs.get("author"))
    source = _text(attrs.get("source"))

    n = ImportedNote(
        notebook=nb_name, title=title, content_html=content_html,
        tags=tags, created=created, updated=updated, author=author,
        source=source or "evernote", resources=[], enmedia=res_map)
    return n
