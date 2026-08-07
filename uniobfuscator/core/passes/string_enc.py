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
    description = "字符串加密（多算法 helper + 独立密钥拆分 + base64 双层）"

    #: 各语言支持的编码算法（解码 helper 与之一一对应）
    ALGOS = {
        "python": ("xor", "add", "sub"),
        "javascript": ("xor",),
        "java": ("xor",),
    }

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        strings = adapter.string_nodes(src.root)
        if not strings:
            return
        algos = self.ALGOS.get(adapter.name, ("xor",))
        # 注意：helper 名不能以双下划线开头（Python 类体内会发生名称改写 name mangling）
        helpers = [f"_u{self.rng.randint(0, 0xFFFFFF):06x}" for _ in algos]
        if not adapter.inject_string_helper(src, adapter.string_helpers(helpers)):
            return  # 无法注入 helper（如无类声明的 Java 文件），跳过加密
        for node in strings:
            raw = node.text.decode("utf-8", "replace")
            value = _real_value(raw, adapter.name)
            if value == "":
                continue  # 空字符串无需加密
            # 每字符串独立随机密钥（1..255，避免 key=0 等同明文）
            key = self.rng.randrange(1, 256)
            algo = algos[self.rng.randrange(len(algos))]
            if algo == "add":
                data = bytes((b + key) % 256 for b in value.encode("utf-8"))
            elif algo == "sub":
                data = bytes((b - key) % 256 for b in value.encode("utf-8"))
            else:
                data = bytes(b ^ key for b in value.encode("utf-8"))
            encoded = base64.b64encode(data).decode("ascii")
            # 密钥拆分：字面量中不出现真实 key，运行时 (k1 + k2) % 256 还原
            k1 = self.rng.randrange(1, 256)
            k2 = (key - k1) % 256
            helper = helpers[algos.index(algo)]
            src.replace_node(node, f'{helper}("{encoded}", ({k1} + {k2}) % 256)')
