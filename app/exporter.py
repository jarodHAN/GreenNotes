# -*- coding: utf-8 -*-
"""笔记导出：HTML→Markdown 与 本地库→Evernote ENEX。

函数均接收已打开的 sqlite 连接（复用 server.py 的 _con()），避免重复开关库。
- note_markdown(con, nid) / notebook_markdown(con, nbid)
- note_enex(con, assets_dir, nid) / notebook_enex(con, assets_dir, nbid)
"""
import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# HTML -> Markdown
# ---------------------------------------------------------------------------
class _MDConverter(HTMLParser):
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "ul", "ol", "table", "tr"}
    VOID = {"br", "hr", "img"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # 已完成的块（字符串）
        self.cur = []          # 当前块内的行内缓冲
        self.skip = 0
        self.pre = False
        self.pre_buf = []
        self.list_stack = []   # 'ul' / 'ol'
        self.li_num = 0
        self.in_table = False
        self.rows = []
        self.rowbuf = []
        self.cellbuf = []
        self.in_cell = False
        self.link_href = ""
        self.h_level = 0
        self.quote = False

    def _flush_block(self, prefix=""):
        txt = "".join(self.cur).strip()
        if txt:
            self.out.append(prefix + txt)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if self.pre:
            attrs_s = "".join(f' {k}="{v}"' for k, v in attrs if v is not None)
            self.pre_buf.append(f"<{tag}{attrs_s}>")
            return
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag == "br":
            self.cur.append("\n")
            return
        if tag == "hr":
            self._flush_block()
            self.out.append("---")
            return
        if tag in ("p", "div", "blockquote"):
            self._flush_block()
            self.quote = (tag == "blockquote")
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_block()
            self.h_level = int(tag[1])
            return
        if tag in ("ul", "ol"):
            self._flush_block()
            self.list_stack.append(tag)
            self.li_num = 0
            return
        if tag == "li":
            self._flush_block()
            indent = "  " * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1] == "ol":
                self.li_num += 1
                self.cur.append(f"{indent}{self.li_num}. ")
            else:
                self.cur.append(f"{indent}- ")
            return
        if tag == "pre":
            self.pre = True
            self.pre_buf = []
            return
        if tag == "code" and not self.pre:
            self.cur.append("`")
            return
        if tag == "a":
            self.link_href = dict(attrs).get("href", "")
            self.cur.append("[")
            return
        if tag == "img":
            a = dict(attrs)
            self.cur.append(f"![{a.get('alt', '')}]({a.get('src', '')})")
            return
        if tag == "table":
            self._flush_block()
            self.in_table = True
            self.rows = []
            return
        if tag == "tr":
            self.rowbuf = []
            return
        if tag in ("td", "th"):
            self.in_cell = True
            self.cellbuf = []
            return
        if tag in ("em", "i"):
            self.cur.append("*")
            return
        if tag in ("strong", "b"):
            self.cur.append("**")
            return
        if tag == "u":
            self.cur.append("<u>")
            return
        # span 等其余标签忽略，仅保留其文本

    def handle_endtag(self, tag):
        if self.pre:
            if tag == "pre":
                self.pre = False
                code = "".join(self.pre_buf).rstrip("\n")
                self._flush_block()
                self.out.append(f"```\n{code}\n```")
            else:
                self.pre_buf.append(f"</{tag}>")
            return
        if tag in ("script", "style"):
            if self.skip > 0:
                self.skip -= 1
            return
        if tag in ("p", "div"):
            self._flush_block()
            return
        if tag == "blockquote":
            self._flush_block("> ")
            self.quote = False
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            txt = "".join(self.cur).strip()
            if txt:
                self.out.append("#" * self.h_level + " " + txt)
            self.cur = []
            return
        if tag in ("ul", "ol"):
            self._flush_block()
            if self.list_stack:
                self.list_stack.pop()
            self.li_num = 0
            return
        if tag == "li":
            self._flush_block()
            return
        if tag == "code" and not self.pre:
            self.cur.append("`")
            return
        if tag == "a":
            self.cur.append(f"]({self.link_href or ''})")
            return
        if tag == "table":
            self._flush_block()
            if self.rows:
                self.out.append(_render_table(self.rows))
            self.in_table = False
            return
        if tag == "tr":
            self.rows.append(self.rowbuf)
            self.rowbuf = []
            return
        if tag in ("td", "th"):
            self.rowbuf.append("".join(self.cellbuf).strip())
            self.in_cell = False
            self.cellbuf = []
            return
        if tag in ("em", "i"):
            self.cur.append("*")
            return
        if tag in ("strong", "b"):
            self.cur.append("**")
            return
        if tag == "u":
            self.cur.append("</u>")
            return

    def handle_data(self, data):
        if self.pre:
            self.pre_buf.append(data)
            return
        if self.skip > 0:
            return
        if self.in_cell:
            self.cellbuf.append(data)
            return
        self.cur.append(data)

    def get_markdown(self):
        self._flush_block()
        blocks = [b for b in self.out if b.strip()]
        merged = []
        li_re = re.compile(r"^\s*(?:[-*]|\d+\.)\s")
        for b in blocks:
            if li_re.match(b) and merged and li_re.match(merged[-1]):
                merged[-1] += "\n" + b
            else:
                merged.append(b)
        return "\n\n".join(merged)


def _render_table(rows):
    if not rows:
        return ""
    maxc = max(len(r) for r in rows)
    lines = []
    for i, r in enumerate(rows):
        r = r + [""] * (maxc - len(r))
        lines.append("| " + " | ".join(c.replace("\n", " ") for c in r) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in r) + " |")
    return "\n".join(lines)


def html_to_markdown(html):
    if not html:
        return ""
    # 附件卡片末尾的「12.4 KB」（span.sz）和删除按钮「✕」（span.del）都是 UI 装饰，
    # 不进 Markdown，否则会变成 [📎 设计稿.pdf12.4 KB✕](assets/…)
    html = re.sub(r'<span[^>]*class="[^"]*\b(?:sz|del)\b[^"]*"[^>]*>.*?</span>', '',
                  html, flags=re.S | re.I)
    c = _MDConverter()
    try:
        c.feed(html)
        return c.get_markdown().strip()
    except Exception:
        return _strip_to_text(html)


def _strip_to_text(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _sort_sql():
    try:
        from importers.common import sort_time_sql
        return sort_time_sql()
    except Exception:
        return "updated"


# ---------------------------------------------------------------------------
# 本地库 -> ENEX
# ---------------------------------------------------------------------------
def _esc_xml(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _enex_time(s):
    if not s:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s = str(s).replace(" ", "T")
    m = re.search(r"(\d{4})\D?(\d{2})\D?(\d{2})\D?(\d{2})\D?(\d{2})\D?(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}T{m.group(4)}{m.group(5)}{m.group(6)}Z"
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resource_xml(filename, hhash, data, mime):
    b64 = base64.b64encode(data or b"").decode("ascii")
    return (
        "<resource>"
        f"<data encoding=\"base64\">{b64}</data>"
        f"<mime>{_esc_xml(mime)}</mime>"
        "<width>0</width><height>0</height>"
        "<resource-attributes>"
        f"<file-name>{_esc_xml(filename)}</file-name>"
        f"<source-url>{_esc_xml(filename)}</source-url>"
        "</resource-attributes>"
        f"<hash>{hhash}</hash>"
        "</resource>"
    )


def _note_inner(con, assets_dir, nid):
    """返回单篇笔记的 <note>...</note> XML 片段。"""
    cur = con.cursor()
    r = cur.execute(
        "SELECT title,content_html,created,updated,author,source FROM notes WHERE id=?", (nid,)
    ).fetchone()
    if not r:
        return ""
    title, chtml, created, updated, author, source = r
    tags = [t[0] for t in cur.execute(
        "SELECT name FROM tags WHERE note_id=?", (nid,)).fetchall()]
    res = cur.execute(
        "SELECT filename,hash,local_path,mime FROM resources WHERE note_id=?", (nid,)
    ).fetchall()

    body = chtml or ""
    # 历史数据兜底：旧版净化器把 <img>/<br> 当容器，补出了 </img>、</br>。
    # ENML/ENEX 是严格校验的，带上这些会让 Evernote 那边拒收，导出前先清掉。
    body = re.sub(r"</(?:img|br|hr)\s*>", "", body, flags=re.I)
    res_elems = []
    for filename, h, local_path, mime in res:
        base = os.path.basename(local_path or filename or "")
        fpath = os.path.join(assets_dir, base)
        data = b""
        if os.path.isfile(fpath):
            with open(fpath, "rb") as f:
                data = f.read()
        hhash = hashlib.md5(data).hexdigest() if data else (h or "")
        if base:
            # 图片：<img src="assets/x.png"> → <en-media>
            body = re.sub(
                r'<img[^>]+src=["\'][^"\']*%s["\'][^>]*>' % re.escape(base),
                f'<en-media hash="{hhash}" type="{mime or "application/octet-stream"}"/>',
                body,
            )
            # 非图片附件：正文里是 <a class="attach" href="assets/x.pdf">📎 …</a>
            # ENML 的 en-media 对非图片就是「附件」，需要同样替换，否则导出后文件就丢了
            body = re.sub(
                r'<a[^>]+href=["\'][^"\']*%s["\'][^>]*>.*?</a>' % re.escape(base),
                f'<en-media hash="{hhash}" type="{mime or "application/octet-stream"}"/>',
                body, flags=re.S | re.I,
            )
        res_elems.append(_resource_xml(filename or base, hhash, data, mime or "application/octet-stream"))

    tags_xml = "".join(f"<tag>{_esc_xml(t)}</tag>" for t in tags)
    author_xml = f"<author>{_esc_xml(author)}</author>" if author else ""
    return (
        "<note>"
        f"<title>{_esc_xml(title or '')}</title>"
        "<content><![CDATA[<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE en-note SYSTEM \"http://xml.evernote.com/pub/enml2.dtd\">\n"
        f"<en-note>{body}</en-note>]]></content>"
        f"<created>{_enex_time(created)}</created>"
        f"<updated>{_enex_time(updated)}</updated>"
        f"{tags_xml}"
        "<note-attributes>" + author_xml + "</note-attributes>"
        f"{''.join(res_elems)}"
        "</note>"
    )


def _wrap_enex(notes_xml):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE en-export SYSTEM \"http://xml.evernote.com/pub/evernote-export4.dtd\">\n"
        f"<en-export export-date=\"{stamp}\" application=\"GreenNotes\" version=\"1.0\">\n"
        f"{notes_xml}\n"
        "</en-export>"
    )


def note_markdown(con, nid):
    cur = con.cursor()
    r = cur.execute("SELECT title,content_html FROM notes WHERE id=?", (nid,)).fetchone()
    if not r:
        return ""
    title, chtml = r
    md = html_to_markdown(chtml)
    tags = [t[0] for t in cur.execute(
        "SELECT name FROM tags WHERE note_id=?", (nid,)).fetchall()]
    head = f"# {title}\n" if title else ""
    tagline = ("\n\n标签: " + " ".join("#" + t for t in tags)) if tags else ""
    return f"{head}\n{md}{tagline}\n"


def note_enex(con, assets_dir, nid):
    inner = _note_inner(con, assets_dir, nid)
    return _wrap_enex(inner) if inner else ""


def notebook_markdown(con, nbid):
    cur = con.cursor()
    name = cur.execute("SELECT name FROM notebooks WHERE id=?", (nbid,)).fetchone()
    name = name[0] if name else "笔记本"
    ids = [r[0] for r in cur.execute(
        "SELECT id FROM notes WHERE notebook_id=? ORDER BY " + _sort_sql() + " DESC",
        (nbid,)).fetchall()]
    parts = [f"# 笔记本：{name}\n"]
    for i in ids:
        md = note_markdown(con, i)
        if md:
            parts.append("---\n\n" + md)
    return "\n".join(parts) + "\n"


def notebook_enex(con, assets_dir, nbid):
    cur = con.cursor()
    ids = [r[0] for r in cur.execute(
        "SELECT id FROM notes WHERE notebook_id=? ORDER BY " + _sort_sql() + " DESC",
        (nbid,)).fetchall()]
    notes_xml = "\n".join(_note_inner(con, assets_dir, i) for i in ids)
    return _wrap_enex(notes_xml)
