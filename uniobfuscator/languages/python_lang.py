# -*- coding: utf-8 -*-
"""Python 语言适配器（基于 tree-sitter-python）。"""
from __future__ import annotations

import base64

from tree_sitter import Language, Node

import tree_sitter_python

from ..core.editable import EditableSource, nodes_of_type, walk
from .base import LanguageAdapter


class PythonAdapter(LanguageAdapter):
    name = "python"
    display_name = "Python"
    extensions = (".py",)

    # Python 强化混淆：模块级名称重命名（仅私有名，public 保留）+ 控制流混淆
    # 出厂默认开启（用户显式选择的能力）。
    features: dict[str, bool] = {
        "rename": True, "strings": True, "dead_code": True, "arithmetic": True,
        "module_rename": True, "control_flow": True, "flatten": True,
    }

    def language_func(self):
        return tree_sitter_python.language()

    # ---------------------------------------------------------------- 作用域
    def function_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(root, {"function_definition"})

    def function_parameters(self, fn: Node) -> list[Node]:
        """只收集参数声明点（默认值里的引用不算参数，避免误改名）。"""
        params = [c for c in fn.children if c.type == "parameters"]
        if not params:
            return []
        out: list[Node] = []
        for n in walk(params[0]):
            if n.type != "identifier":
                continue
            p = n.parent
            if p.type == "parameters":
                out.append(n)
            elif p.type in {"list_splat_pattern", "dictionary_splat_pattern"}:
                out.append(n)
            elif p.type == "default_parameter" and n == p.named_children[0]:
                out.append(n)
            elif p.type == "typed_parameter" and n == p.named_children[0]:
                out.append(n)  # def f(x: int)
            elif p.type == "typed_default_parameter" and n == p.named_children[0]:
                out.append(n)  # def f(x: int = 1)
        return out

    def local_declarations(self, fn: Node) -> list[Node]:
        """函数体内局部变量声明点：赋值左侧、for 循环变量、with/except 绑定。

        排除 global 声明的名字（属于模块作用域）与嵌套函数/类区间，
        只收集真正属于本函数作用域的局部声明。
        """
        decls: list[Node] = []
        body = fn.child_by_field_name("body") or next(
            (c for c in fn.children if c.type == "block"), None
        )
        if body is None:
            return decls

        # global 声明的名字指向模块作用域，绝不当局部变量收集
        global_names: set[str] = set()
        for n in walk(fn):
            if n.type == "global_statement":
                global_names.update(
                    c.text.decode("utf-8", "replace")
                    for c in n.children if c.type == "identifier"
                )

        # 嵌套函数/类的声明属于它们自己的作用域，跳过
        nested_ranges = [
            (n.start_byte, n.end_byte)
            for n in nodes_of_type(fn, {"function_definition", "class_definition"})
            if (n.start_byte, n.end_byte) != (fn.start_byte, fn.end_byte)
        ]

        def in_nested(node) -> bool:
            return any(a <= node.start_byte < b for a, b in nested_ranges)

        def add_decl(node) -> None:
            if node is None or node.type != "identifier":
                return
            text = node.text.decode("utf-8", "replace")
            if text in global_names or in_nested(node):
                return
            decls.append(node)

        def left_identifiers(left: Node) -> list[Node]:
            out = []
            if left.type == "identifier":
                out.append(left)
            elif left.type in {"tuple_pattern", "list_pattern", "pattern_list"}:
                for c in left.children:
                    out.extend(left_identifiers(c))
            return out

        for stmt in body.children:
            if stmt.type == "global_statement":
                # global 声明的名字属于模块作用域，绝不能当局部变量重命名
                continue
            if stmt.type == "nonlocal_statement":
                # nonlocal 绑定的名字由外层函数持有，必须在局部作用域内保持改名一致
                for c in stmt.children:
                    if c.type == "identifier":
                        add_decl(c)
                continue
            if stmt.type in {"expression_statement", "assignment"}:
                assign = next(
                    (c for c in stmt.children if c.type == "assignment"), None
                )
                if assign is not None:
                    for n in left_identifiers(assign.children[0]):
                        add_decl(n)
                    continue
                # expression_statement 也可能包裹 augmented_assignment: x += 1
                aug = next(
                    (c for c in stmt.children if c.type == "augmented_assignment"), None
                )
                if aug is not None:
                    add_decl(aug.children[0])
                continue
            elif stmt.type == "augmented_assignment":
                add_decl(stmt.children[0])
            elif stmt.type == "for_statement":
                # for <var> in ...:  var 是第 2 个子节点
                if len(stmt.children) > 1:
                    for n in left_identifiers(stmt.children[1]):
                        add_decl(n)
            elif stmt.type == "with_statement":
                for c in walk(stmt):
                    if c.type == "as_pattern":
                        # as_pattern: [as, identifier]
                        for x in c.children:
                            if x.type == "identifier":
                                add_decl(x)
            elif stmt.type == "try_statement":
                for c in walk(stmt):
                    if c.type == "except_clause":
                        # except [Type] as e: 最后一个 identifier 是绑定名
                        ids = [x for x in c.children if x.type == "identifier"]
                        if ids:
                            add_decl(ids[-1])
        return decls

    def function_body_range(self, fn: Node) -> tuple[int, int]:
        body = next(c for c in fn.children if c.type == "block")
        return body.start_byte, body.end_byte

    def is_reference(self, node: Node) -> bool:
        if node.type != "identifier":
            return False
        p = node.parent
        if p is None:
            return False
        pt = p.type
        if pt in {"import_statement", "import_from_statement", "dotted_name"}:
            return False
        # global 声明的名字指向模块作用域，绝不能重命名
        if pt == "global_statement":
            return False
        # 属性字段名：attribute -> [obj, ., 字段]
        if pt == "attribute" and len(p.children) >= 3 and node == p.children[2]:
            return False
        # 关键字参数名：keyword -> [名字, =, 值]
        if pt == "keyword" and p.children and node == p.children[0]:
            return False
        # 函数/类声明名
        if pt in {"function_definition", "class_definition"} and len(p.children) > 1 and node == p.children[1]:
            return False
        # 异常绑定名
        if pt == "except_clause":
            return False
        return True

    # ---------------------------------------------------------------- 字符串
    def string_nodes(self, root: Node) -> list[Node]:
        out = []
        for n in nodes_of_type(root, {"string"}):
            text = n.text.decode("utf-8", "replace")
            if text[:1] in ("b", "B"):  # bytes 字面量跳过
                continue
            if any(c.type == "interpolation" for c in n.children):  # f-string 跳过
                continue
            p = n.parent
            if p and p.type == "expression_statement" and len(p.children) == 1:
                continue  # docstring 跳过
            out.append(n)
        return out

    def string_helper(self, helper_name: str) -> str:
        return (
            "import base64 as __b64\n"
            f"def {helper_name}(__s, __k):\n"
            "    __b = bytes(__c ^ __k for __c in __b64.b64decode(__s))\n"
            "    return __b.decode('utf-8')\n"
        )

    def string_helpers(self, helper_names: list[str]) -> str:
        """生成多算法解码 helper：XOR / 加偏移 / 减偏移。

        每条字符串随机选用一个算法，静态分析必须逐一尝试三种算法。
        """
        templates = [
            # XOR
            "def {n}(__s, __k):\n"
            "    __b = bytes(__c ^ __k for __c in __b64.b64decode(__s))\n"
            "    return __b.decode('utf-8')",
            # 加偏移编码 -> 解码减偏移
            "def {n}(__s, __k):\n"
            "    __b = bytes((__c - __k) % 256 for __c in __b64.b64decode(__s))\n"
            "    return __b.decode('utf-8')",
            # 减偏移编码 -> 解码加偏移
            "def {n}(__s, __k):\n"
            "    __b = bytes((__c + __k) % 256 for __c in __b64.b64decode(__s))\n"
            "    return __b.decode('utf-8')",
        ]
        parts = ["import base64 as __b64\n"]
        for name, tmpl in zip(helper_names, templates):
            parts.append(tmpl.format(n=name) + "\n")
        return "".join(parts)

    def inject_string_helper(self, src: EditableSource, helper_code: str) -> bool:
        # 若文件带 BOM（utf-8-sig），注入到 BOM 之后
        offset = 3 if src.source.startswith("\ufeff") else 0
        src.replace(offset, offset, helper_code)
        return True

    # ---------------------------------------------------------------- 数字
    def number_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(root, {"integer", "float"})

    # ---------------------------------------------------------------- 死代码
    def dead_code_snippet(self, indent: str) -> str:
        # 插入位置前已含函数体缩进，首行不带缩进；后续行自带缩进
        inner = indent + "    "
        return f'if 2 == 1:\n{inner}print("__dead_code")\n{indent}'

    def insert_at_function_head(self, src: EditableSource, fn: Node) -> None:
        start, _ = self.function_body_range(fn)
        # block 从第一个语句开始，其所在行行首空白即函数体缩进。
        # 注意：start_byte 是字节偏移，必须用字节切片推断缩进。
        line_prefix = src.bytes[:start].rsplit(b"\n", 1)[-1].decode("utf-8")
        indent = line_prefix if line_prefix.strip() == "" else "    "
        src.replace(start, start, self.dead_code_snippet(indent))

    # ---------------------------------------------------------------- 模块级
    def module_scope_declarations(self, root: Node) -> list[Node]:
        """模块级自定义声明：类名 / 函数名 / 全局变量赋值左侧的 identifier。

        只收集"模块内自定义"的名字：
        - class_definition / function_definition 声明名
        - 模块级 assignment / augmented_assignment 左侧的 identifier
        - 排除 import 绑定（import 名是外来名字，改它不影响本模块可读性
          且会破坏跨文件引用）与 dunder（__name__/__all__ 等）。
        """
        decls: list[Node] = []
        for stmt in root.children:
            if stmt.type in ("class_definition", "function_definition"):
                ids = [c for c in stmt.children if c.type == "identifier"]
                if ids:
                    decls.append(ids[0])
                continue
            if stmt.type == "import_statement":
                continue
            if stmt.type == "import_from_statement":
                continue
            if stmt.type in {"expression_statement", "assignment",
                             "augmented_assignment"}:
                assign = stmt
                if stmt.type == "expression_statement":
                    assign = next(
                        (c for c in stmt.children if c.type == "assignment"), None)
                    if assign is None:
                        continue
                left = assign.children[0]
                for n in self._left_identifiers(left):
                    decls.append(n)
        # 排除 dunder 与 import 绑定名（防误伤）
        out: list[Node] = []
        for n in decls:
            text = n.text.decode("utf-8", "replace")
            if text.startswith("__") and text.endswith("__"):
                continue
            if not text.isidentifier():
                continue
            out.append(n)
        return out

    @staticmethod
    def _left_identifiers(left: Node) -> list[Node]:
        out = []
        if left.type == "identifier":
            out.append(left)
        elif left.type in {"tuple_pattern", "list_pattern", "pattern_list"}:
            for c in left.children:
                out.extend(PythonAdapter._left_identifiers(c))
        return out

    def class_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(root, {"class_definition"})

    def class_body_declarations(self, cls: Node) -> list[Node]:
        """类体内声明的 identifier：赋值左侧、方法/嵌套类声明名。

        类属性与模块级同名时会遮蔽模块级引用（类体内 _x 指向类属性），
        模块级重命名必须跳过这些位置。
        """
        body = next((c for c in cls.children if c.type == "block"), None)
        if body is None:
            return []
        out: list[Node] = []
        for stmt in body.children:
            if stmt.type in ("function_definition", "class_definition"):
                ids = [c for c in stmt.children if c.type == "identifier"]
                if ids:
                    out.append(ids[0])
                continue
            if stmt.type in {"expression_statement", "assignment",
                             "augmented_assignment"}:
                assign = stmt
                if stmt.type == "expression_statement":
                    assign = next(
                        (c for c in stmt.children if c.type == "assignment"), None)
                    if assign is None:
                        continue
                left = assign.children[0]
                for n in self._left_identifiers(left):
                    out.append(n)
        return out

    # ---------------------------------------------------------------- 控制流
    def control_flow_snippet(self, indent: str, a: int, b: int) -> str:
        """复杂不透明谓词：随机算术恒假条件 + 嵌套诱饵代码。

        例如：if (a * b) + 1 == a * b:  恒假（a*b+1 必不等于 a*b）
        a/b 由 pass 的 rng 生成（可复现）；块内放看似真实的局部变量
        声明与字符串操作，增加静态分析难度。
        """
        inner = indent + "    "
        return (
            f"if ({a} * {b}) + 1 == {a} * {b}:\n"
            f"{inner}_dc = ({a} + {b}) * 2\n"
            f"{inner}_dc = str(_dc).join(['x', 'y'])\n"
            f"{inner}if len(_dc) < 0:\n"
            f"{inner}    _dc = _dc[::-1]\n"
            f"{indent}"
        )

    def insert_control_flow_at_function_head(self, src: EditableSource, fn: Node,
                                             a: int, b: int) -> None:
        start, _ = self.function_body_range(fn)
        line_prefix = src.bytes[:start].rsplit(b"\n", 1)[-1].decode("utf-8")
        indent = line_prefix if line_prefix.strip() == "" else "    "
        src.replace(start, start, self.control_flow_snippet(indent, a, b))
