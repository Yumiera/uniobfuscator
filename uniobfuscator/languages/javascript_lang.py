# -*- coding: utf-8 -*-
"""JavaScript 语言适配器（基于 tree-sitter-javascript）。"""
from __future__ import annotations

from tree_sitter import Language, Node

import tree_sitter_javascript

from ..core.editable import EditableSource, nodes_of_type, walk
from .base import LanguageAdapter


class JavaScriptAdapter(LanguageAdapter):
    name = "javascript"
    display_name = "JavaScript"
    extensions = (".js", ".mjs", ".cjs", ".jsx")

    def language_func(self):
        return tree_sitter_javascript.language()

    # ---------------------------------------------------------------- 作用域
    def function_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(
            root,
            {"function_declaration", "function_expression", "arrow_function", "method_definition"},
        )

    def function_parameters(self, fn: Node) -> list[Node]:
        params = [c for c in fn.children if c.type == "formal_parameters"]
        if not params:
            return []
        out: list[Node] = []
        for n in params[0].named_children:
            if n.type == "identifier":
                out.append(n)
            elif n.type == "assignment_pattern" and n.named_children:
                out.append(n.named_children[0])  # 默认值参数 b = 2 的 b
            elif n.type == "rest_pattern":
                ids = [c for c in walk(n) if c.type == "identifier"]
                out.extend(ids)  # ...args 的 args
            elif n.type == "required_parameter":
                ids = [c for c in walk(n) if c.type == "identifier"]
                if ids:
                    out.append(ids[0])
        return out

    def local_declarations(self, fn: Node) -> list[Node]:
        decls: list[Node] = []
        for n in walk(fn):
            if n.type not in {"lexical_declaration", "variable_declaration"}:
                continue
            for d in n.children:
                if d.type == "variable_declarator" and d.named_children:
                    target = d.named_children[0]
                    if target.type == "identifier":
                        decls.append(target)
        return decls

    def function_body_range(self, fn: Node) -> tuple[int, int]:
        body = next((c for c in fn.children if c.type == "statement_block"), None)
        if body is None:
            return fn.start_byte, fn.end_byte  # 箭头函数单表达式：退回函数整体
        return body.start_byte, body.end_byte

    def is_reference(self, node: Node) -> bool:
        if node.type == "shorthand_property_identifier":
            return True  # 对象简写 {a} 等价于 {a: a}，是变量引用
        if node.type != "identifier":
            return False
        p = node.parent
        if p is None:
            return False
        pt = p.type
        # import / export 绑定与模块名
        if pt in {
            "import_statement", "import_clause", "import_specifier",
            "export_statement", "export_clause", "namespace_import",
            "export_specifier", "import_attribute",
        }:
            return False
        # 函数/类声明名
        if pt in {"function_declaration", "class_declaration", "generator_function_declaration"}:
            return False
        # 函数表达式命名（function_expression 的标识名）由声明集合覆盖，这里排除防误伤
        if pt == "function_expression" and p.children and node == p.children[1]:
            return False
        return True

    # ---------------------------------------------------------------- 字符串
    def string_nodes(self, root: Node) -> list[Node]:
        out = []
        for n in nodes_of_type(root, {"string"}):
            ancestor = n.parent
            skip = False
            while ancestor is not None:
                if ancestor.type in {"import_statement", "export_statement", "import_clause"}:
                    skip = True
                    break
                if ancestor.type == "call_expression":
                    func = ancestor.children[0] if ancestor.children else None
                    if func is not None and func.type == "identifier" and func.text in (b"require", b"import"):
                        skip = True
                        break
                ancestor = ancestor.parent
            if not skip:
                out.append(n)
        return out

    def string_helper(self, helper_name: str) -> str:
        return (
            f"function {helper_name}(__s) {{\n"
            "  const __b = atob(__s);\n"
            "  const __u8 = new Uint8Array(__b.length);\n"
            "  for (let __i = 0; __i < __b.length; __i++) __u8[__i] = __b.charCodeAt(__i);\n"
            "  return new TextDecoder().decode(__u8);\n"
            "}\n"
        )

    def inject_string_helper(self, src: EditableSource, helper_code: str) -> bool:
        src.replace(0, 0, helper_code)
        return True

    # ---------------------------------------------------------------- 数字
    def number_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(root, {"number"})

    # ---------------------------------------------------------------- 死代码
    def dead_code_snippet(self, indent: str) -> str:
        return f'{indent}if (2 === 1) {{ console.log("__dead_code"); }}\n{indent}'

    def insert_at_function_head(self, src: EditableSource, fn: Node) -> None:
        body = next((c for c in fn.children if c.type == "statement_block"), None)
        if body is None:
            return
        pos = body.start_byte + 1  # '{' 之后
        indent = self._body_indent(src, body)
        src.replace(pos, pos, "\n" + self.dead_code_snippet(indent))

    @staticmethod
    def _body_indent(src: EditableSource, body: Node) -> str:
        """取函数体内第一个以空白开头的非空行的缩进作为基准（字节切片）。"""
        for line in src.bytes[body.start_byte : body.end_byte].split(b"\n"):
            if line and line[0] in b" \t":
                return line[: len(line) - len(line.lstrip(b" \t"))].decode("utf-8")
        return "  "
