# -*- coding: utf-8 -*-
"""JAR 级类名重命名：保留包路径，把简单类名替换为随机短名。

通过重写常量池中所有 CONSTANT_Class 条目指向的 Utf8 名称，同时覆盖
this_class / super_class / interfaces / 异常表 catch_type / new、checkcast、
anewarray 以及 invoke*/field 引用里的类，保证 JAR 内部引用一致。

注意：
- 只改类名，不改方法名/字段名（避免破坏 override、接口实现与 JNI）。
- Class.forName("...") 等运行期按名字符串无法跟踪，需用 --exclude
  排除对应类（字符串加密是运行期透明解密，不影响其内容）。
- MANIFEST 的 Main-Class 会同步更新；其它资源文件（Spring 配置等）
  里的类名引用不在处理范围内。
"""
from __future__ import annotations

import random

from .classfile import CONSTANT_Class, ClassFile

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _short_name(pos: int) -> str:
    """把序号编码为 base36 短名（一一对应，天然不冲突）。"""
    s = ""
    while True:
        s = _ALPHABET[pos % 36] + s
        pos //= 36
        if pos == 0:
            break
    return s


def build_rename_map(internal_names, seed: int = 0, excluded=()) -> dict[str, str]:
    """为可重命名的类生成 旧内部名 -> 新内部名 映射。

    - 保留包路径，只替换简单类名（同包内唯一）；
    - 排除（excluded）的类不重命名；
    - 简单类名按 seed 洗牌后的 base36 短名分配，同 seed 可复现。
    """
    excluded = set(excluded)
    groups: dict[str, list[str]] = {}
    for name in internal_names:
        if name in excluded:
            continue
        pkg, _, simple = name.rpartition("/")
        groups.setdefault(pkg, []).append(simple)
    rng = random.Random(seed)
    name_map: dict[str, str] = {}
    for pkg, simples in groups.items():
        order = list(range(len(simples)))
        rng.shuffle(order)
        for simple, pos in zip(simples, order):
            new = _short_name(pos)
            old = f"{pkg}/{simple}" if pkg else simple
            name_map[old] = f"{pkg}/{new}" if pkg else new
    return name_map


def apply_rename(class_file: ClassFile, name_map: dict[str, str]) -> int:
    """把常量池里指向被重命名类的 Class 常量更新为新名。返回更新的常量数。"""
    cp = class_file.cp
    renamed = 0
    for i in range(1, len(cp.entries)):
        entry = cp.entries[i]
        if entry and entry[0] == CONSTANT_Class:
            new_name = name_map.get(cp.utf8(entry[1]))
            if new_name:
                cp.entries[i] = (CONSTANT_Class, cp.add_utf8(new_name))
                renamed += 1
    return renamed
