# -*- coding: utf-8 -*-
"""多格式笔记导入包。统一产出 ImportedNote，由 server 落盘到本地库。"""
from .common import ImportedNote, Resource
from . import dispatch

__all__ = ["ImportedNote", "Resource", "dispatch"]
