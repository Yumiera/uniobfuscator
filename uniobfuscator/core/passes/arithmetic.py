# -*- coding: utf-8 -*-
"""算术混淆 Pass：数字字面量改写为等价表达式，如 5 → (5 * 1)。

使用 +0 / *1 / -0 这类在各语言中都类型安全、无精度/溢出风险的变换。
"""
from __future__ import annotations

from ..editable import EditableSource
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass


class ArithmeticPass(ObfuscationPass):
    name = "arithmetic"
    description = "数字字面量算术改写"

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        ops = ("+ 0", "* 1", "- 0")
        for node in adapter.number_nodes(src.root):
            text = node.text.decode("utf-8", "replace")
            if not text or text[0] in "-+":
                continue
            op = self.rng.choice(ops)
            src.replace_node(node, f"({text} {op})")
