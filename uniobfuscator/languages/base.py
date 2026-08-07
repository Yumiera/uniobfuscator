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

    #: 该语言支持的文本混淆特性及出厂默认开关。
    #: key 与 CLI/配置文件中的 pass 名一致（rename/strings/dead_code/arithmetic/
    #: module_rename/control_flow）。子类可覆盖以裁剪能力或调整默认值
    #: （例如某些语言重命名风险高默认关闭）。
    features: dict[str, bool] = {
        "rename": True,
        "strings": True,
        "dead_code": True,
        "arithmetic": True,
        "module_rename": False,  # 模块级名称重命名：默认关闭（跨文件 import 风险）
        "control_flow": False,   # 控制流混淆：默认关闭（破坏性较强）
    }

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

    def string_helpers(self, helper_names: list[str]) -> str:
        """返回一组字符串解码 helper 的源码（多算法变体）。

        默认实现退化为单 helper（仅用第一个名字），多算法语言（如 Python）
        重载以生成多个不同解码算法的 helper，静态分析需逐一尝试。
        """
        return self.string_helper(helper_names[0])

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

    # ---------------------------------------------------------------- 模块级作用域
    def module_scope_declarations(self, root: Node) -> list[Node]:
        """返回模块级可重命名的声明 identifier 节点（类名/函数名/全局变量）。

        只重命名"模块内自定义"的名字；import 绑定的名字由实现方排除。
        默认返回空（不支持模块级重命名的语言）。
        """
        return []

    def class_nodes(self, root: Node) -> list[Node]:
        """返回所有类定义节点（供模块级重命名的遮蔽分析使用）。"""
        return []

    def class_body_declarations(self, cls: Node) -> list[Node]:
        """返回类体内声明的 identifier 节点（类属性名），用于遮蔽分析。"""
        return []

    # ---------------------------------------------------------------- 控制流
    def control_flow_snippet(self, indent: str, a: int, b: int) -> str:
        """生成不透明谓词（恒假条件）+ 诱饵代码的控制流混淆片段。

        a/b 为 pass 的 rng 生成的两个随机数（保证可复现）。
        与死代码的区别：谓词是随机算术恒假表达式（而非字面 2==1），
        且块内是"看似真实"的嵌套诱饵代码。默认返回空（不支持的语言）。
        """
        return ""

    def insert_control_flow_at_function_head(self, src: EditableSource, fn: Node,
                                             a: int, b: int) -> None:
        """在函数体开头插入控制流混淆片段（保持缩进正确）。

        默认无操作（仅实现该能力的语言生效）。
        """
        return
