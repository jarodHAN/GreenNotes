# -*- coding: utf-8 -*-
"""HTML / MHT 导入器 —— 覆盖 OneNote 导出(.htm/.mht)、有道云笔记 HTML 导出、通用网页。"""
import base64
import binascii
import os
import re
import html as _html
from html.parser import HTMLParser
from .common import ImportedNote, Resource, html_to_text, mime_for_ext, ext_for_mime


class _TitleExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""
    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
    def handle_data(self, data):
        if self.in_title:
            self.title += data


def _decode_part(body: bytes, encoding: str) -> bytes:
    enc = (encoding or "7bit").lower()
    if enc == "base64":
        return base64.b64decode(b"".join(body.split(b"\n")))
    if enc in ("quoted-printable", "quopri"):
        return binascii.a2b_qp(b"".join(body.split(b"\n")))
    return body


def _parse_mht(raw: bytes):
    """返回 (html_text, {location/cid: Resource})"""
    text = raw.decode("utf-8", "replace")
    m = re.search(r"boundary=\"?([^\"\r\n;]+)\"?", text)
    if not m:
        # 退化：当作普通 HTML
        return text, {}
    boundary = m.group(1)
    parts = re.split(r"(?m)^--" + re.escape(boundary) + r"(?:--)?\s*$", text)
    html_part = None
    cid_map = {}
    loc_map = {}
    for part in parts:
        if not part.strip():
            continue
        head_end = part.find("\n\n")
        if head_end == -1:
            continue
        head = part[:head_end]
        body = part[head_end + 2:]
        ctype_m = re.search(r"Content-Type:\s*([^\r\n;]+)", head, re.I)
        ctype = ctype_m.group(1).strip().lower() if ctype_m else "text/plain"
        enc_m = re.search(r"Content-Transfer-Encoding:\s*([^\r\n]+)", head, re.I)
        enc = enc_m.group(1).strip() if enc_m else "7bit"
        cid_m = re.search(r"Content-ID:\s*<([^>]+)>", head, re.I)
        loc_m = re.search(r"Content-Location:\s*(\S+)", head, re.I)
        data = _decode_part(body.encode("utf-8", "replace"), enc)
        if ctype.startswith("multipart/"):
            continue
        res_obj = None
        if ctype.startswith("text/html"):
            html_part = data.decode("utf-8", "replace")
            continue
        # 资源
        ext = mime_for_ext(ctype)
        fn = "res." + ext
        res_obj = Resource(filename=fn, mime=ctype, data=data, ext=ext)
        if cid_m:
            cid_map["cid:" + cid_m.group(1)] = res_obj
        if loc_m:
            loc_map[loc_m.group(1)] = res_obj
    # 用 loc_map 兜底（以文件名匹配）
    if html_part is None:
        return "", {}
    return html_part, {**loc_map, **cid_map}


def parse_html_bytes(raw: bytes, notebook: str, source_path: str = "") -> list:
    text = raw.decode("utf-8", "replace")
    resources = {}
    # 先尝试 MHT
    if "Content-Type: multipart/related" in text or "boundary=" in text[:2000]:
        try:
            html_text, resources = _parse_mht(raw)
            if html_text:
                text = html_text
        except Exception:
            pass
    return [_build_note(text, resources, notebook, source_path)]


def parse_html_file(path: str, notebook: str = None) -> list:
    nb = notebook or os.path.splitext(os.path.basename(path))[0]
    raw = open(path, "rb").read()
    return parse_html_bytes(raw, nb, path)


def _build_note(html_text: str, resources: dict, notebook: str, source_path: str) -> ImportedNote:
    te = _TitleExtractor()
    te.feed(html_text)
    title = te.title.strip() or (os.path.splitext(os.path.basename(source_path))[0] if source_path else "网页笔记")
    # 提取 body
    bm = re.search(r"<body[^>]*>(.*)</body>", html_text, re.I | re.S)
    body = bm.group(1) if bm else html_text
    # 去掉 script/style
    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.I)
    body = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.I)
    # 把 data: URI 内联图片提取为资源
    def data_uri(m):
        tag = m.group(0)
        src = m.group(1)
        if src.startswith("data:"):
            try:
                hdr, b64 = src.split(",", 1)
                mime = re.search(r"data:([^;]+)", hdr).group(1)
                data = base64.b64decode(b64)
                ext = mime_for_ext(mime)
                fn = "embed.%s" % ext
                resources[fn] = Resource(filename=fn, mime=mime, data=data, ext=ext)
                return tag.replace(src, fn)
            except Exception:
                return m.group(0)
        # 本地 cid/location 引用
        if src in resources:
            return tag  # 由 persist 改写
        return m.group(0)
    body = re.sub(r'<img\s+[^>]*src="([^"]+)"[^>]*/?>', lambda m: data_uri(m), body)
    # 把资源引用（按文件名/cid）改名为 assets 占位
    for key, res in resources.items():
        if key.startswith("cid:") or key.startswith("http") or os.path.isabs(key):
            continue
        body = re.sub(r'src="([^"]*)' + re.escape(os.path.basename(key)) + r'"',
                      f'src="{key}"', body)
    n = ImportedNote(notebook=notebook, title=title, content_html=body,
                     source="html", tags=[])
    # 把 resources 转为持久化所需结构：以文件名作为 note.resources
    n.resources = list(resources.values())
    n._base = os.path.dirname(source_path) if source_path else ""
    return n
