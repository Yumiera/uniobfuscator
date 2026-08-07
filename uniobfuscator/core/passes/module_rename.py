# -*- coding: utf-8 -*-
"""模块级名称重命名 Pass：重命名模块内自定义的私有名（类/函数/全局变量）。

安全策略（保证混淆后项目可运行）：
1. 只重命名模块级**私有名**（以 `_` 开头、非 dunder）——按 Python 约定
   私有名不会被外部模块 import，改名不会破坏跨文件引用；public 名保留。
2. import 绑定的名字（`from x import _y` 的 _y）是外来名字，绝不重命名，
   否则 `import` 语义被破坏。
3. 同步本文件内所有引用：函数体、类体、表达式、global 声明里的同名
   标识符全部替换，保证改名后文件内自洽。
4. 遮蔽保护：函数内若声明了同名局部变量/参数，函数内引用指向局部变量，
   跳过不替换（避免把局部引用误改成模块级新名）。
"""
from __future__ import annotations

from ..editable import EditableSource, walk
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass


class ModuleRenamePass(ObfuscationPass):
    name = "module_rename"
    description = "模块级私有名称重命名（类名/函数名/全局变量）"

    def _new_name(self, used: set[str]) -> str:
        while True:
            # _m 前缀：不与字符串 helper（_u）、RenamePass 局部名冲突
            name = f"_m{self.rng.randint(0, 0xFFFFFF):06x}"
            if name not in used:
                return name

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        decls = adapter.module_scope_declarations(src.root)
        if not decls:
            return
        # 只取私有名（_ 开头非 dunder）作为候选
        candidates: dict[str, object] = {}
        for d in decls:
            text = d.text.decode("utf-8", "replace")
            if not text.startswith("_") or text.startswith("__"):
                continue
            candidates.setdefault(text, d)
        if not candidates:
            return

        # 全部标识符集合：用于生成不冲突的新名
        used: set[str] = set()
        for n in walk(src.root):
            if n.type == "identifier":
                used.add(n.text.decode("utf-8", "replace"))

        # 生成改名映射（按声明顺序，种子可复现）
        name_map: dict[str, str] = {}
        for text in candidates:
            if text in used and text in name_map:
                continue
            name_map[text] = self._new_name(used)
            used.add(name_map[text])

        # 遮蔽集合：每个函数/类的局部声明名（含参数）
        scope_ranges: list[tuple[int, int, set[str]]] = []
        for fn in adapter.function_nodes(src.root):
            local_names = {
                n.text.decode("utf-8", "replace")
                for n in (adapter.function_parameters(fn)
                          + adapter.local_declarations(fn))
            }
            scope_ranges.append((fn.start_byte, fn.end_byte, local_names))
        # 类体作用域：类内赋值也是局部于类，屏蔽模块级同名
        for cls in adapter.class_nodes(src.root):
            local_names = {
                n.text.decode("utf-8", "replace")
                for n in adapter.class_body_declarations(cls)
            }
            scope_ranges.append((cls.start_byte, cls.end_byte, local_names))

        def shadowed(pos: int, text: str) -> bool:
            """pos 处若被某作用域的同名局部声明遮蔽，则该引用指向局部变量。"""
            for start, end, names in scope_ranges:
                if start <= pos < end and text in names:
                    return True
            return False

        # 声明点集合（用于区分"声明"与"引用"）。
        # 注意：不能用 id() 判重 —— tree-sitter 每次遍历都会创建新的包装对象，
        # 同一语法节点两次遍历的 id 不同；声明点已被声明循环替换过，
        # 引用扫描必须以"字节区间"为准跳过，否则同区间二次替换会截断文本。
        decl_ranges = {(n.start_byte, n.end_byte) for n in decls}
        decl_texts = {n.text.decode("utf-8", "replace") for n in decls}

        # 1) 声明点：直接替换（类名/函数名/全局变量定义处）
        for d in decls:
            text = d.text.decode("utf-8", "replace")
            if text in name_map:
                src.replace_node(d, name_map[text])

        # 2) 引用点：全文件扫描，避开局部遮蔽与 import 绑定
        for node in walk(src.root):
            if node.type != "identifier":
                continue
            if (node.start_byte, node.end_byte) in decl_ranges:
                continue  # 声明点已替换
            text = node.text.decode("utf-8", "replace")
            if text not in name_map:
                continue
            if text not in decl_texts:
                continue  # 模块级未定义该名字，跳过（防误改局部同名）
            p = node.parent
            if p is not None and p.type == "global_statement":
                src.replace_node(node, name_map[text])
                continue
            if shadowed(node.start_byte, text):
                continue
            if not adapter.is_reference(node):
                continue
            src.replace_node(node, name_map[text])
