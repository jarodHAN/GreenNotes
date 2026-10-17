# -*- coding: utf-8 -*-
"""有道云笔记 .note 导入器 —— .note 本质是 zip，内含 JSON(笔记内容)+资源。
若无法确定格式，退回当作文本/HTML 解析。"""
import io
import json
import os
import re
import zipfile
from .common import ImportedNote


def parse_youdao_file(path: str, notebook: str = None) -> list:
    try:
        zf = zipfile.ZipFile(path)
    except Exception:
        return []
    entries = zf.namelist()
    meta = None
    for name in entries:
        if name.lower().endswith(".json"):
            try:
                meta = json.loads(zf.read(name).decode("utf-8", "replace"))
                break
            except Exception:
                continue
    if meta is None:
        return []
    nb = notebook or (meta.get("notebook") or meta.get("notebookName") or "有道云笔记")
    title = meta.get("title") or meta.get("name") or os.path.splitext(os.path.basename(path))[0]
    # 内容：优先 HTML，其次纯文本
    content = meta.get("content") or meta.get("contentHtml") or meta.get("contentText") or ""
    if isinstance(content, list):  # 有些结构是块数组
        content = "\n".join(str(c) for c in content)
    ctype = (meta.get("contentType") or "").lower()
    if ctype in ("text", "plain", "") and "<" not in content:
        content = "<pre>" + content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    n = ImportedNote(notebook=nb, title=title, content_html=content,
                     tags=list(tags)[:20], source="youdao")
    # 资源
    for name in entries:
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")):
            ext = name.rsplit(".", 1)[-1].lower()
            data = zf.read(name)
            fn = os.path.basename(name)
            from .common import Resource, mime_for_ext
            n.resources.append(Resource(filename=fn, mime=mime_for_ext(ext), data=data, ext=ext))
            n.content_html = n.content_html.replace(name, fn)
    return [n]
