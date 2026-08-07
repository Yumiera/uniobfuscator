# -*- coding: utf-8 -*-
"""控制流混淆 Pass：在函数体开头注入不透明谓词（恒假条件）+ 诱饵代码。

与死代码注入的区别：
- 谓词是随机算术恒假表达式（如 `(a*b) + 1 == a*b`），而非字面 `2 == 1`，
  静态分析者需要做常量折叠才能识破。
- 块内是"看似真实"的嵌套代码（局部变量声明、字符串操作、嵌套 if），
  提升反混淆成本。

安全性：恒假条件保证诱饵块永不执行，不改变任何运行时行为。
"""
from __future__ import annotations

from ..editable import EditableSource
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass


class ControlFlowPass(ObfuscationPass):
    name = "control_flow"
    description = "注入不透明谓词（复杂恒假条件 + 诱饵代码）"

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        if not adapter.control_flow_snippet("", 0, 0):
            return  # 该语言未实现控制流混淆
        for fn in adapter.function_nodes(src.root):
            a = self.rng.randint(2, 99)
            b = self.rng.randint(2, 99)
            adapter.insert_control_flow_at_function_head(src, fn, a, b)
