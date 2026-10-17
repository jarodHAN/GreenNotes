# -*- coding: utf-8 -*-
"""极简 HTML 净化器（标准库实现，无第三方依赖）。

用于：①导入外部笔记（Evernote/OneNote/有道/HTML）时去除危险内容；
②用户编辑保存时防止 contenteditable 把网页复制来的 <script>/on* 注入进来。
策略：白名单标签 + 白名单属性 + 安全 URL 方案；其余一律丢弃。
"""
import re
from html.parser import HTMLParser

# 允许保留的标签
_ALLOWED_TAGS = {
    "a", "p", "br", "div", "span", "b", "strong", "i", "em", "u", "s", "strike",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
    "code", "table", "thead", "tbody", "tr", "td", "th", "img", "hr",
}
# 每个标签允许的属性
_ALLOWED_ATTRS = {
    "a": {"href", "title", "name", "class"},
    "img": {"src", "alt", "title", "width", "height", "class"},
    "table": {"class", "style"},
    "td": {"colspan", "rowspan", "style"},
    "th": {"colspan", "rowspan", "style"},
    "span": {"style", "class"},
    "p": {"style"},
    "div": {"style", "class"},
}
# class 只留字母数字、下划线、连字符与空格，避免被拿去做奇怪的选择器
_CLASS_SAFE = re.compile(r"[^A-Za-z0-9_\-\s]")
# 完全丢弃的标签（含其全部内容）
_BAD_TAGS = {
    "script", "style", "iframe", "object", "embed", "link", "meta", "base",
    "form", "input", "button", "textarea", "select", "noscript", "template",
    "frame", "frameset",
}
# 空元素（void）：HTML 里没有闭合标签，不能压栈、更不能补 </img>
# 以前把它们当容器处理，于是每篇含图片的笔记都被写成 `...<img ...></img>`，
# 导出的 ENEX（ENML 严格校验）里就带了个非法闭合标签。
_VOID_TAGS = {"img", "br", "hr"}
_INLINE_STYLE_DANGER = re.compile(
    r"(expression|javascript|vbscript|behavior\s*:)\s*\(?", re.I)
_URL_DANGER = re.compile(r"^(javascript|vbscript|data:text/html)", re.I)


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _safe_url(val):
    if not val:
        return None
    v = val.strip()
    if _URL_DANGER.match(v):
        return None
    # data: 只允许图片
    if v.lower().startswith("data:") and not v.lower().startswith("data:image/"):
        return None
    return v


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _BAD_TAGS:
            self.skip += 1
            return
        if tag not in _ALLOWED_TAGS:
            return
        kept = []
        for k, v in attrs:
            if v is None:
                v = ""
            k = k.lower()
            if k not in _ALLOWED_ATTRS.get(tag, set()):
                continue
            if k in ("href", "src"):
                sv = _safe_url(v)
                if sv is None:
                    continue
                v = sv
            if k == "style":
                v = _INLINE_STYLE_DANGER.sub("", v)
            if k == "class":
                v = _CLASS_SAFE.sub("", v).strip()[:80]
                if not v:
                    continue
            kept.append((k, v))
        self.stack.append(tag)
        attr_str = "".join(f' {k}="{_esc_attr(v)}"' for k, v in kept)
        self.out.append(f"<{tag}{attr_str}>")
        if tag in _VOID_TAGS:
            self.stack.pop()          # 空元素立即出栈，稍后不会补闭合标签

    def handle_endtag(self, tag):
        if tag in _BAD_TAGS:
            if self.skip > 0:
                self.skip -= 1
            return
        if tag in _VOID_TAGS:
            return                    # 网上抄来的 </img> / </br> 一律丢掉
        if tag not in _ALLOWED_TAGS:
            return
        # 仅当栈顶匹配时闭合（简单容错，避免错位）
        if tag in self.stack:
            self.out.append(f"</{tag}>")
            if self.stack[-1] == tag:
                self.stack.pop()
            else:
                # 弹到匹配项
                while self.stack and self.stack[-1] != tag:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()

    def handle_data(self, data):
        if self.skip > 0:
            return
        self.out.append(_esc_text(data))

    def handle_entityref(self, name):
        if self.skip == 0:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if self.skip == 0:
            self.out.append(f"&#{name};")


def sanitize_html(html):
    """净化一段 HTML，返回安全的 HTML 字符串。失败时原样返回，避免丢数据。"""
    if not html:
        return ""
    p = _Sanitizer()
    try:
        p.feed(html)
    except Exception:
        return html
    # 关闭未闭合标签（按打开顺序反序）
    for tag in reversed(p.stack):
        p.out.append(f"</{tag}>")
    return "".join(p.out).strip()
