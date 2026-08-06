# -*- coding: utf-8 -*-
"""字符串加密 Pass：字符串字面量 → 运行时解码函数调用，并在文件头部注入解码 helper。"""
from __future__ import annotations

import ast
import base64

from ..editable import EditableSource
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass

_CSTYLE = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
    "0": "\0", "\\": "\\", '"': '"', "'": "'", "/": "/",
}


def _decode_cstyle(inner: str) -> str:
    """解析 C/Java/JS 风格字符串转义，返回真实字符串值。"""
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = inner[i]
        if e in _CSTYLE:
            out.append(_CSTYLE[e])
            i += 1
        elif e == "x" and i + 2 < n + 1:
            out.append(chr(int(inner[i + 1 : i + 3], 16)))
            i += 3
        elif e == "u" and i + 4 < n + 1:
            out.append(chr(int(inner[i + 1 : i + 5], 16)))
            i += 5
        elif e in "01234567":  # 八进制
            j = i
            while j < n and j < i + 3 and inner[j] in "01234567":
                j += 1
            out.append(chr(int(inner[i:j], 8)))
            i = j
        else:  # 未知转义按字面保留
            out.append(e)
            i += 1
    return "".join(out)


def _string_inner(raw: str) -> str:
    """去掉字符串字面量的前缀与引号，返回内部原文（可能含转义序列）。"""
    body = raw
    while body and body[0] in "rRbBuUfF":
        body = body[1:]
    if body.startswith('"""') and body.endswith('"""') and len(body) >= 6:
        return body[3:-3]
    if body.startswith("'''") and body.endswith("'''") and len(body) >= 6:
        return body[3:-3]
    if len(body) >= 2 and body[0] in "\"'" and body[-1] == body[0]:
        return body[1:-1]
    return body


def _real_value(raw: str, lang: str) -> str:
    """把字符串字面量原文解析为真实运行时值。"""
    if lang == "python":
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return _string_inner(raw)
    return _decode_cstyle(_string_inner(raw))


class StringEncryptPass(ObfuscationPass):
    name = "strings"
    description = "字符串 base64 加密"

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        strings = adapter.string_nodes(src.root)
        if not strings:
            return
        # 注意：helper 名不能以双下划线开头（Python 类体内会发生名称改写 name mangling）
        helper = f"_u{self.rng.randint(0, 0xFFFFFF):06x}"
        if not adapter.inject_string_helper(src, adapter.string_helper(helper)):
            return  # 无法注入 helper（如无类声明的 Java 文件），跳过加密
        for node in strings:
            raw = node.text.decode("utf-8", "replace")
            value = _real_value(raw, adapter.name)
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            src.replace_node(node, f'{helper}("{encoded}")')
