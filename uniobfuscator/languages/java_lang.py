# -*- coding: utf-8 -*-
"""Java 语言适配器（基于 tree-sitter-java）。"""
from __future__ import annotations

from tree_sitter import Language, Node

import tree_sitter_java

from ..core.editable import EditableSource, nodes_of_type, walk
from .base import LanguageAdapter


class JavaAdapter(LanguageAdapter):
    name = "java"
    display_name = "Java"
    extensions = (".java",)

    def language_func(self):
        return tree_sitter_java.language()

    # ---------------------------------------------------------------- 作用域
    def function_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(root, {"method_declaration", "constructor_declaration"})

    def function_parameters(self, fn: Node) -> list[Node]:
        fp = [c for c in fn.children if c.type == "formal_parameters"]
        if not fp:
            return []
        return [n for n in walk(fp[0]) if n.type == "identifier"]

    def local_declarations(self, fn: Node) -> list[Node]:
        decls: list[Node] = []
        for n in walk(fn):
            if n.type == "variable_declarator" and n.named_children:
                target = n.named_children[0]
                if target.type == "identifier":
                    decls.append(target)
            elif n.type in {"formal_parameter", "catch_formal_parameter"}:
                ids = [c for c in n.named_children if c.type == "identifier"]
                decls.extend(ids)
        return decls

    def function_body_range(self, fn: Node) -> tuple[int, int]:
        body = next((c for c in fn.children if c.type == "block"), None)
        if body is None:
            return fn.start_byte, fn.end_byte  # 抽象方法等无方法体
        return body.start_byte, body.end_byte

    def is_reference(self, node: Node) -> bool:
        if node.type != "identifier":
            return False
        p = node.parent
        if p is None:
            return False
        pt = p.type
        # 字段访问：最后一个子节点是字段名（排除），object 部分可重命名
        if pt == "field_access":
            return node != p.children[-1]
        # 限定名（import/包名/类型限定）整体排除
        if pt == "scoped_identifier":
            return False
        # 无对象方法调用：identifier 即方法名
        if pt == "method_invocation":
            return False
        # 类型声明/方法/构造器/注解/import 中的名字
        if pt in {
            "class_declaration", "interface_declaration", "enum_declaration",
            "annotation_type_declaration", "record_declaration",
            "method_declaration", "constructor_declaration",
            "import_declaration", "package_declaration", "annotation",
        }:
            return False
        return True

    # ---------------------------------------------------------------- 字符串
    def string_nodes(self, root: Node) -> list[Node]:
        out = []
        for n in nodes_of_type(root, {"string_literal"}):
            ancestor = n.parent
            skip = False
            while ancestor is not None:
                if ancestor.type in {
                    "annotation", "import_declaration", "package_declaration",
                    "module_declaration",
                }:
                    skip = True
                    break
                ancestor = ancestor.parent
            if not skip:
                out.append(n)
        return out

    def string_helper(self, helper_name: str) -> str:
        return (
            f"private static String {helper_name}(String __s, int __k) {{\n"
            "    byte[] __b = java.util.Base64.getDecoder().decode(__s);\n"
            "    for (int __i = 0; __i < __b.length; __i++) __b[__i] ^= (byte) __k;\n"
            "    return new String(__b, java.nio.charset.StandardCharsets.UTF_8);\n"
            "}\n"
        )

    def inject_string_helper(self, src: EditableSource, helper_code: str) -> bool:
        classes = nodes_of_type(src.root, {"class_declaration", "record_declaration"})
        if not classes:
            return False
        body = next((c for c in classes[0].children if c.type == "class_body"), None)
        if body is None:
            return False
        src.replace(body.start_byte + 1, body.start_byte + 1, "\n    " + helper_code)
        return True

    # ---------------------------------------------------------------- 数字
    def number_nodes(self, root: Node) -> list[Node]:
        return nodes_of_type(
            root,
            {
                "decimal_integer_literal",
                "decimal_floating_point_literal",
                "hex_integer_literal",
                "octal_integer_literal",
                "binary_integer_literal",
                "hex_floating_point_literal",
            },
        )

    # ---------------------------------------------------------------- 死代码
    def dead_code_snippet(self, indent: str) -> str:
        return f'{indent}if (2 == 1) {{ System.out.println("__dead_code"); }}\n{indent}'

    def insert_at_function_head(self, src: EditableSource, fn: Node) -> None:
        body = next((c for c in fn.children if c.type == "block"), None)
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
        return "    "
