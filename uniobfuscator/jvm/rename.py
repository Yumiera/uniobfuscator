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


def build_repackage_map(internal_names, seed: int = 0, excluded=(),
                        prefix: str = "a") -> dict[str, str]:
    """把可重命名类平铺到单一短包（repackage），类名全局唯一短名。

    - 所有非排除类都移到前缀包（默认 "a"）下，包路径信息被抹平，
      分析者无法从包名推断模块/业务结构（对应 ProGuard -repackageclasses）；
    - excluded 的类保留原包路径（反射/框架加载的关键类）；
    - 前缀包若与 jar 内现有顶层包冲突，自动追加短名避让；
    - 简单名在全部可重命名类范围内唯一（seed 洗牌，可复现）。

    注意：不同包之间的 package-private（默认访问）访问在源码层已被
    编译器禁止，因此 repackage 到同包不会破坏既有访问语义。
    """
    excluded = set(excluded)
    renameable = [n for n in internal_names if n not in excluded]
    if not renameable:
        return {}
    top_pkgs = {n.split("/")[0] for n in internal_names if "/" in n}
    pkg = prefix
    k = 0
    while pkg in top_pkgs:  # 前缀包与现有包冲突则避让
        k += 1
        pkg = prefix + _short_name(k)
    rng = random.Random(seed)
    order = list(range(len(renameable)))
    rng.shuffle(order)
    name_map = {}
    for name, pos in zip(renameable, order):
        name_map[name] = f"{pkg}/{_short_name(pos)}"
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
