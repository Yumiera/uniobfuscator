# -*- coding: utf-8 -*-
"""标识符重命名 Pass：函数参数 + 局部变量改为随机短名。

安全策略（保证混淆后整项目可运行）：
1. 全文件共享一张改名映射（original -> new）。同一名字在任何函数里
   都改成一模一样的新名，从而保证 global/nonlocal/闭包跨函数一致。
2. 只重命名"在本函数作用域内声明的名字"（参数 + 局部变量 + nonlocal），
   其它位置的同名标识符一律不动（例如引用模块级常量、global 声明名）。
3. 处理外层函数时跳过嵌套函数/类的区间，避免与外层逻辑互相污染。
4. 生成的新名不会与模块级名字冲突。
"""
from __future__ import annotations

from ..editable import EditableSource, nodes_of_type, walk
from ...languages.base import LanguageAdapter
from .base import ObfuscationPass


def _module_scope_names(root) -> set[str]:
    """收集模块级出现的所有标识符（不含函数/类内部），用于规避新名冲突。"""
    names: set[str] = set()

    def scan(node) -> None:
        if node.type in {"function_definition", "class_definition"}:
            # 只取声明名本身，不深入函数/类体
            ids = [c for c in node.children if c.type == "identifier"]
            if ids:
                names.add(ids[0].text.decode("utf-8", "replace"))
            return
        if node.type == "identifier":
            names.add(node.text.decode("utf-8", "replace"))
            return
        for c in node.children:
            scan(c)

    for stmt in root.children:
        scan(stmt)
    return names


class RenamePass(ObfuscationPass):
    name = "rename"
    description = "函数参数与局部变量重命名"

    def _new_name(self, used: set[str], reserved: set[str]) -> str:
        while True:
            name = f"_{self.rng.randint(0, 0xFFFFFF):06x}"
            if name not in used and name not in reserved:
                return name

    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        # 全文件共享：同一个原名 -> 同一个新名，保证闭包/nonlocal/global 一致
        name_map: dict[str, str] = {}
        used: set[str] = set()
        reserved = _module_scope_names(src.root)

        # 所有函数/类区间（用于处理外层函数时跳过嵌套作用域）
        scope_nodes = nodes_of_type(src.root, {"function_definition", "class_definition"})

        # ---- 第一遍：收集每个函数的作用域信息 ----
        fns = adapter.function_nodes(src.root)
        fn_info: list[tuple[list[str], set[str], set[int], int]] = []
        for fn in fns:
            params = adapter.function_parameters(fn)
            locals_ = adapter.local_declarations(fn)

            # 本函数内 global 声明的名字属于模块作用域，不是局部变量，绝不能改名
            globals_f: set[str] = set()
            for n in walk(fn):
                if n.type == "global_statement":
                    globals_f.update(
                        c.text.decode("utf-8", "replace")
                        for c in n.children if c.type == "identifier"
                    )

            candidates: list[str] = []
            seen: set[str] = set()
            for d in params + locals_:
                if d.type != "identifier":
                    continue
                key = d.text.decode("utf-8", "replace")
                if not key.isidentifier() or key in seen:
                    continue
                seen.add(key)
                if key in globals_f:
                    continue
                candidates.append(key)
            decl_ids = {id(n) for n in params} | {id(n) for n in locals_}
            block_start, _ = adapter.function_body_range(fn)
            fn_info.append((candidates, globals_f, decl_ids, block_start))

        # ---- 第二遍：生成改名映射（按文档顺序，保证种子可复现）----
        for candidates, _g, _d, _b in fn_info:
            for key in candidates:
                if key not in name_map:
                    new = self._new_name(used, reserved)
                    name_map[key] = new
                    used.add(new)

        # ---- 第三遍：按作用域规则替换 ----
        for i, fn in enumerate(fns):
            candidates, globals_f, decl_ids, block_start = fn_info[i]
            candidates_set = set(candidates)
            # 外层函数绑定的名字：闭包自由变量可安全跟随外层改名
            ancestor_bounds: set[str] = set()
            for j, g in enumerate(fns):
                if j == i:
                    continue
                if g.start_byte <= fn.start_byte and fn.end_byte <= g.end_byte:
                    ancestor_bounds.update(fn_info[j][0])

            fstart, fend = fn.start_byte, fn.end_byte
            nested = [
                n for n in scope_nodes
                if fstart < n.start_byte and n.end_byte < fend
            ]

            for node in walk(fn):
                if node.type != "identifier":
                    continue
                if not (fstart <= node.start_byte < fend):
                    continue
                # 跳过嵌套作用域（交给各自的 pass）
                if any(a <= node.start_byte < b for a, b in
                       ((n.start_byte, n.end_byte) for n in nested)):
                    continue
                text = node.text.decode("utf-8", "replace")
                # 声明点（参数 / 局部声明）永远随映射改名
                if id(node) in decl_ids:
                    if text in name_map:
                        src.replace_node(node, name_map[text])
                    continue
                if not adapter.is_reference(node):
                    continue
                # 引用点：global 声明名指向模块作用域，绝不改名
                if text in globals_f:
                    continue
                # 参数区（函数体之外，即默认值）求值于外层作用域：
                # 只有外层函数绑定该名字时才随外层改名；模块级名字（RATE 等）不动
                if node.start_byte < block_start:
                    if text not in ancestor_bounds:
                        continue
                else:
                    # 函数体内：本作用域声明的名字或外层函数绑定的自由变量
                    if text not in candidates_set and text not in ancestor_bounds:
                        continue
                if text in name_map:
                    src.replace_node(node, name_map[text])
