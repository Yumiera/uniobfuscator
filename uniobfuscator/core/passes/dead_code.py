# -*- coding: utf-8 -*-
"""死代码注入 Pass：在每个函数体开头插入永不执行的分支诱饵。"""
from __future__ import annotations

from ..editable import EditableSource
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass


class DeadCodePass(ObfuscationPass):
    name = "dead_code"
    description = "注入不可达死代码"

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        for fn in adapter.function_nodes(src.root):
            adapter.insert_at_function_head(src, fn)
