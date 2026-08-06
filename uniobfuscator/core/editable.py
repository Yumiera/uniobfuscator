# -*- coding: utf-8 -*-
"""基于 tree-sitter 的统一编辑模型。

核心思路：对源码只解析一次，得到一个语法树；混淆 Pass 通过"字节区间替换"
记录编辑，全部编辑完成后再一次性应用（从后往前，避免区间位移）。
该模型对 Python / JavaScript / Java 完全一致，是"uni"架构的基石。
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Language, Node, Parser


@dataclass
class Edit:
    """一次文本替换：将 [start_byte, end_byte) 区间的源码替换为 text。"""

    start_byte: int
    end_byte: int
    text: str


class EditableSource:
    """持有源码 + 语法树的可编辑文档。"""

    def __init__(self, source: str, language: Language, parser: Parser | None = None):
        self.source = source
        self.parser = parser or Parser(language)
        self.tree = self.parser.parse(source.encode("utf-8"))
        self.edits: list[Edit] = []

    @property
    def root(self) -> Node:
        return self.tree.root_node

    @property
    def bytes(self) -> bytes:
        """源码的 UTF-8 字节表示，配合 tree-sitter 的字节偏移使用。"""
        return self.source.encode("utf-8")

    @property
    def has_error(self) -> bool:
        return self.tree.root_node.has_error

    def replace(self, start_byte: int, end_byte: int, text: str) -> None:
        """替换 [start_byte, end_byte) 区间的文本；start_byte == end_byte 即纯插入。"""
        self.edits.append(Edit(start_byte, end_byte, text))

    def replace_node(self, node: Node, text: str) -> None:
        self.replace(node.start_byte, node.end_byte, text)

    def apply(self) -> str:
        """应用全部编辑，返回最终源码。从后往前应用保证区间不位移。"""
        buf = self.source.encode("utf-8")
        for e in sorted(self.edits, key=lambda e: e.start_byte, reverse=True):
            buf = buf[: e.start_byte] + e.text.encode("utf-8") + buf[e.end_byte :]
        return buf.decode("utf-8")


def walk(node: Node):
    """深度优先遍历所有节点。"""
    yield node
    for child in node.children:
        yield from walk(child)


def nodes_of_type(root: Node, types: set[str]) -> list[Node]:
    """收集所有类型属于 types 的节点。"""
    return [n for n in walk(root) if n.type in types]
