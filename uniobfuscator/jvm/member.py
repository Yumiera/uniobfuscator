# -*- coding: utf-8 -*-
"""私有成员重命名（private 方法/字段）。

只重命名 private 成员（JVM 中 private 只能被本类引用），因此改写
本类常量池里的 Fieldref/Methodref 引用即可保证一致性，无 override /
接口实现 / JNI 破坏风险。结合 protect.py 的反射保护：被反射引用的
成员名、序列化协议特殊方法名、Serializable 类的字段均跳过。
"""
from __future__ import annotations

import random

from .classfile import (
    CONSTANT_Class,
    CONSTANT_Fieldref,
    CONSTANT_InterfaceMethodref,
    CONSTANT_Methodref,
    CONSTANT_NameAndType,
    ClassFile,
)

_ALPHABET = "xyz0123456789abcdefghijklmnopqrstuvwxyz"  # 与类名短名错开前缀

_PRIVATE = 0x0002
_STATIC = 0x0008
_FINAL = 0x0010
_SYNTHETIC = 0x1000
_NATIVE = 0x0100


def _short_name(pos: int) -> str:
    s = ""
    while True:
        s = _ALPHABET[pos % 36] + s
        pos //= 36
        if pos == 0:
            break
    return s


def build_member_map(
    cf: ClassFile,
    seed: int,
    protected: frozenset[str] = frozenset(),
    serializable: bool = False,
) -> dict[str, str]:
    """为可重命名的私有成员生成 旧名 -> 新名 映射。

    - 仅 private 且非 <init>/<clinit>/native/synthetic 的成员；
    - 排除 protected（反射引用）与 serial_special_methods；
    - serializable 类跳过全部字段（序列化协议按字段名）；
    - 新名避开类内所有现有成员名，seed 派生顺序可复现。
    """
    from .protect import serial_special_methods

    skip = set(protected) | serial_special_methods()
    rng = random.Random(seed)
    member_map: dict[str, str] = {}

    def can_rename(flags: int, name: str, is_field: bool) -> bool:
        if not (flags & _PRIVATE):
            return False
        if name in ("<init>", "<clinit>"):
            return False
        if flags & (_SYNTHETIC | _NATIVE):
            return False
        if name in skip:
            return False
        if is_field and serializable:
            return False  # Serializable 字段参与序列化，名不可变
        return True

    names_to_rename = []
    for m in cf.methods:
        name = cf.cp.utf8(m.name_index)
        if can_rename(m.access_flags, name, is_field=False):
            names_to_rename.append(name)
    if not serializable:  # Serializable 类字段已全部跳过
        for f in cf.fields:
            name = cf.cp.utf8(f.name_index)
            if can_rename(f.access_flags, name, is_field=True):
                names_to_rename.append(name)

    used = set(skip)
    for m in cf.methods:
        used.add(cf.cp.utf8(m.name_index))
    for f in cf.fields:
        used.add(cf.cp.utf8(f.name_index))

    order = list(range(len(names_to_rename)))
    rng.shuffle(order)
    pos = 0
    for name, _ in zip(names_to_rename, order):
        while _short_name(pos) in used:
            pos += 1
        new = _short_name(pos)
        used.add(new)
        pos += 1
        member_map[name] = new
    return member_map


def apply_member_rename(cf: ClassFile, member_map: dict[str, str]) -> int:
    """应用成员重命名：改写成员定义名 + 常量池中指向本类私有成员的引用。

    返回改写的引用数（Fieldref/Methodref/InterfaceMethodref）。
    """
    if not member_map:
        return 0
    cp = cf.cp
    # 1) 成员定义本身
    for m in cf.methods:
        name = cp.utf8(m.name_index)
        if name in member_map:
            m.name_index = cp.add_utf8(member_map[name])
    for f in cf.fields:
        name = cp.utf8(f.name_index)
        if name in member_map:
            f.name_index = cp.add_utf8(member_map[name])
    # 2) 常量池引用：class_index 指向本类 且 NameAndType.name 被重命名
    this_name = cf.this_name()
    renamed = 0
    # 本类 Class 常量索引（this_class 直接可用）
    ref_tags = (CONSTANT_Fieldref, CONSTANT_Methodref, CONSTANT_InterfaceMethodref)
    for i in range(1, len(cp.entries)):
        entry = cp.entries[i]
        if not entry or entry[0] not in ref_tags:
            continue
        class_idx, nat_idx = entry[1], entry[2]
        cls_entry = cp[class_idx]
        if not cls_entry or cls_entry[0] != CONSTANT_Class:
            continue
        if cp.utf8(cls_entry[1]) != this_name:
            continue
        nat = cp[nat_idx]
        if not nat or nat[0] != CONSTANT_NameAndType:
            continue
        old_name = cp.utf8(nat[1])
        new_name = member_map.get(old_name)
        if not new_name:
            continue
        # 新建独立 NameAndType（旧条目可能被其它类共享，不能原地改）
        new_nat = cp.add(
            CONSTANT_NameAndType, cp.add_utf8(new_name), nat[2])
        cp.entries[i] = (entry[0], class_idx, new_nat)
        renamed += 1
    return renamed
