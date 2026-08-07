# -*- coding: utf-8 -*-
"""控制流扁平化（CFG flattening）：把函数体改造成 while + 状态机分派。

作为**独立后处理阶段**运行：在其余 Pass 基于 EditableSource 的字节编辑全部
应用之后，重新解析中间文本，按函数体重建状态机。因此被扁平化的语句文本
已经包含字符串加密 / 标识符重命名等效果，不会丢失。

覆盖范围（保守策略，保证语义安全）：
- 只处理"首条语句之后的顶层语句全部为单行"的函数；
- 首条语句（docstring / 多行 if 等）原样保留在函数体开头，避免破坏
  文档字符串语义与多行结构；
- 其余单行语句每行分配一个随机状态编号，执行链为随机乱序 id，
  并追加一个永不匹配的诱饵状态块，静态分析需逐一分辨。

原理：Python 无块级作用域，状态机里的 if/while 不影响局部变量可见性，
闭包 / nonlocal / global 绑定保持不变，故行为与原顺序执行等价。
"""
from __future__ import annotations

import random

from ..languages.base import LanguageAdapter


def _line_indent(data: bytes, pos: int) -> str:
    """取字节偏移所在行的前导空白（作为函数体缩进基准）。"""
    line_start = data.rfind(b"\n", 0, pos) + 1
    return data[line_start:pos].decode("utf-8", "replace")


def _state_ids(rng: random.Random, count: int) -> list[int]:
    """生成 count 个互不相同的随机状态编号。"""
    ids: set[int] = set()
    while len(ids) < count:
        ids.add(rng.randrange(1000, 10 ** 6))
    return list(ids)


def _fresh_name(rng: random.Random, used: set[str]) -> str:
    """生成与文件已有标识符不冲突的随机变量名。"""
    while True:
        name = f"_f{rng.randint(0, 0xFFFFFF):06x}"
        if name not in used:
            used.add(name)
            return name


def _walk(node) -> list:
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(n.children)
    return out


def _flat_fn(data: bytes, fn, used: set[str], rng: random.Random) -> str | None:
    """把单个函数体重写为状态机文本；不满足条件返回 None。"""
    body = fn.child_by_field_name("body")
    if body is None:
        return None
    stmts = [c for c in body.children if c.type != "comment"]
    if len(stmts) < 2:
        return None
    head, tail = stmts[0], stmts[1:]
    # 跨行语句不参与扁平化（避免多行字符串/复杂结构在重排时被破坏）
    if any(s.start_point.row != s.end_point.row for s in tail):
        return None

    var = _fresh_name(rng, used)
    ids = _state_ids(rng, len(tail))
    decoy = rng.randrange(10 ** 6, 10 ** 7)
    while decoy in ids:
        decoy = rng.randrange(10 ** 6, 10 ** 7)

    indent = _line_indent(data, body.start_byte)
    i4 = indent + "    "
    i8 = indent + "        "
    i12 = indent + "            "

    lines: list[str] = []
    # 首条语句原样保留（docstring / 多行 if 等），不进状态机
    if head.end_byte > head.start_byte:
        head_text = data[head.start_byte:head.end_byte].decode("utf-8", "replace")
        lines.append(indent + head_text)

    lines.append(f"{indent}{var} = {ids[0]}")
    lines.append(f"{indent}while True:")
    for i, stmt in enumerate(tail):
        kw = "if" if i == 0 else "elif"
        nxt = ids[i + 1] if i + 1 < len(tail) else -1
        lines.append(f"{i4}{kw} {var} == {ids[i]}:")
        text = data[stmt.start_byte:stmt.end_byte].decode("utf-8", "replace")
        lines.append(f"{i8}{text}")
        lines.append(f"{i8}{var} = {nxt}")
    # 诱饵状态：id 永不匹配，代码永不执行
    a, b = rng.randrange(2, 99), rng.randrange(2, 99)
    dname = _fresh_name(rng, used)
    lines.append(f"{i4}elif {var} == {decoy}:")
    lines.append(f"{i8}{dname} = ({a} * {b}) + 1")
    lines.append(f"{i8}if {dname} == ({a} * {b}):")
    lines.append(f"{i12}{var} = str({var}).join(['__', '__'])")
    lines.append(f"{i4}else:")
    lines.append(f"{i8}break")
    return "\n".join(lines) + "\n"


def flatten_module(source: str, adapter: LanguageAdapter, seed: int) -> str:
    """对整份源码应用控制流扁平化（仅处理可安全重建的函数）。"""
    src = adapter.parse(source)
    if src.has_error:
        return source
    data = source.encode("utf-8")
    used = {n.text.decode("utf-8", "replace")
            for n in _walk(src.root) if n.type == "identifier"}
    rng = random.Random(seed)

    edits: list[tuple[int, int, str]] = []
    for fn in adapter.function_nodes(src.root):
        new_text = _flat_fn(data, fn, used, rng)
        if new_text is None:
            continue
        body = fn.child_by_field_name("body")
        # 替换区间向左扩展到 body 首行的行首：data[:line_start] 以
        # "def f(...):\n" 结尾，而 new_text 每行自带缩进，避免重复缩进。
        line_start = data.rfind(b"\n", 0, body.start_byte) + 1
        edits.append((line_start, body.end_byte, new_text))

    # 从后往前应用，保证字节偏移有效
    for start, end, text in sorted(edits, key=lambda x: -x[0]):
        data = data[:start] + text.encode("utf-8") + data[end:]
    return data.decode("utf-8", "replace")
