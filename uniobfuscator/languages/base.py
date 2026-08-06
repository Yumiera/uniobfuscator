# -*- coding: utf-8 -*-
"""语言适配器抽象基类。

每个语言实现一个 LanguageAdapter 子类，只负责回答"语言差异点"：
解析、函数/局部变量声明收集、标识符引用判定、字符串/数字节点类型、
注入 helper 与死代码的代码片段。混淆 Pass 全部基于此接口编写，
从而做到"写一次 Pass，跑所有语言"。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from tree_sitter import Language, Node, Parser

from ..core.editable import EditableSource, walk

# 各语言字符串字面量的节点类型
STRING_NODE_TYPES = {
    "python": {"string"},
    "javascript": {"string"},
    "java": {"string_literal"},
}


class LanguageAdapter(ABC):
    """语言适配器基类。子类必须实现 name/extensions/language_func 与各差异点。"""

    name: str = ""
    display_name: str = ""
    extensions: tuple[str, ...] = ()

    def __init__(self):
        self._language = Language(self.language_func())
        self._parser = Parser(self._language)

    # ---------------------------------------------------------------- 解析
    @abstractmethod
    def language_func(self):
        """返回 tree-sitter 语言对象（如 tree_sitter_python.language()）。"""

    def parse(self, source: str) -> EditableSource:
        return EditableSource(source, self._language, self._parser)

    # ---------------------------------------------------------------- 通用树工具
    def walk(self, node: Node):
        return walk(node)

    # ---------------------------------------------------------------- 作用域收集
    def function_nodes(self, root: Node) -> list[Node]:
        """返回所有可作为混淆作用域的函数节点。"""
        raise NotImplementedError

    def function_parameters(self, fn: Node) -> list[Node]:
        """返回函数参数声明中的 identifier 节点。"""
        raise NotImplementedError

    def local_declarations(self, fn: Node) -> list[Node]:
        """返回函数体内局部变量声明的 identifier 节点。"""
        raise NotImplementedError

    def function_body_range(self, fn: Node) -> tuple[int, int]:
        """返回函数体字节区间 (start, end)，用于限定重命名替换范围。"""
        raise NotImplementedError

    def is_reference(self, node: Node) -> bool:
        """node 是 identifier，判断它是否是可安全重命名的"引用"位置。

        需要排除：属性/方法字段名、关键字参数名、import 绑定名、
        嵌套函数/类声明名、标签、注解名等不可改名上下文。
        """
        raise NotImplementedError

    # ---------------------------------------------------------------- 字符串
    def string_nodes(self, root: Node) -> list[Node]:
        """返回可以加密的字符串字面量节点（已排除 f-string 等不安全位置）。"""
        raise NotImplementedError

    def string_helper(self, helper_name: str) -> str:
        """返回字符串解码 helper 的源码（语言相关）。"""
        raise NotImplementedError

    def inject_string_helper(self, src: EditableSource, helper_code: str) -> bool:
        """把解码 helper 注入到合适位置（文件顶部 / 类体内）。返回是否注入成功。"""
        raise NotImplementedError

    # ---------------------------------------------------------------- 数字
    def number_nodes(self, root: Node) -> list[Node]:
        """返回可混淆的数字字面量节点。"""
        raise NotImplementedError

    # ---------------------------------------------------------------- 死代码
    def dead_code_snippet(self, indent: str) -> str:
        """生成一段永不执行但语法合法的死代码（含换行与缩进）。"""
        raise NotImplementedError

    def insert_at_function_head(self, src: EditableSource, fn: Node) -> None:
        """在函数体开头插入一段死代码（保持缩进正确）。"""
        raise NotImplementedError
