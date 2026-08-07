# -*- coding: utf-8 -*-
"""反射/序列化保护分析（JAR 级，Python 静态分析）。

混淆（类名重命名 / repackage / 成员重命名）会改写常量池里的名字，
但 `Class.forName("com.foo.Bar")`、`getMethod("doIt")` 这类按名字符串
是运行时字符串，混淆器无法改写其内容。本模块通过扫描所有类的常量池
String 常量，识别"可能被反射引用的类名 / 成员名"，把对应目标排除在
重命名之外，保证混淆后产物仍可用。

- collect_protected：返回 (受保护类名集合, 受保护成员名集合)。
- find_serializable：返回直接/间接实现 java/io/Serializable 的类
  （其字段参与序列化协议，不可重命名）。
"""
from __future__ import annotations

from .classfile import CONSTANT_String, ClassFile

_SERIALIZABLE = "java/io/Serializable"

#: 序列化协议的特殊私有方法名（readObject/writeObject 等按名反射调用）
_SERIAL_SPECIAL_METHODS = frozenset({
    "readObject", "writeObject", "readObjectNoData", "readResolve", "writeReplace",
})


def _string_constants(cf: ClassFile) -> set[str]:
    """类中所有 String 常量的值（ldc 可加载的字符串字面量）。"""
    cp = cf.cp
    return {
        cp.utf8(entry[1])
        for entry in cp.entries
        if entry and entry[0] == CONSTANT_String
    }


def collect_protected(
    class_files: dict[str, ClassFile],
) -> tuple[frozenset[str], frozenset[str]]:
    """扫描反射引用，返回 (受保护类名, 受保护成员名) 集合。

    类名：String 常量 == 某个类的内部名（com/foo/Bar）或点分名
    （com.foo.Bar）时，该类不参与类名重命名 / repackage。
    成员名：String 常量 == 某个类的成员（方法/字段）名时，该成员
    不参与私有成员重命名。

    保守策略：可能过度保护（如配置 key 与字段同名），但优先保证
    混淆产物的可使用性；真正要精确控制可用 --exclude。
    """
    internals = set(class_files)
    # 点分名 -> 内部名 反向映射（Class.forName 字符串常写点分形式）
    by_dotted = {n.replace("/", "."): n for n in internals}
    member_names = set()
    strings: set[str] = set()
    for cf in class_files.values():
        strings |= _string_constants(cf)
        cp = cf.cp
        member_names |= {cp.utf8(m.name_index) for m in cf.methods}
        member_names |= {cp.utf8(f.name_index) for f in cf.fields}
    protected_classes: set[str] = set()
    for s in strings:
        if s in internals:
            protected_classes.add(s)
        elif s in by_dotted:
            protected_classes.add(by_dotted[s])
    protected_members = strings & member_names
    return frozenset(protected_classes), frozenset(protected_members)


def find_serializable(class_files: dict[str, ClassFile]) -> frozenset[str]:
    """返回直接或间接实现 java/io/Serializable 的类内部名集合。

    遍历 superclass / interfaces 做传递闭包：父类可序列化则子类
    也可序列化（子类字段同样参与序列化协议）。
    """
    serializable = {_SERIALIZABLE}
    changed = True
    while changed:
        changed = False
        for internal, cf in class_files.items():
            if internal in serializable:
                continue
            cp = cf.cp
            if any(cp.utf8(cp[i][1]) in serializable for i in cf.interfaces):
                serializable.add(internal)
                changed = True
                continue
            if cf.super_class:
                sup = cp.utf8(cp[cf.super_class][1])
                if sup in serializable:
                    serializable.add(internal)
                    changed = True
    serializable.discard(_SERIALIZABLE)
    return frozenset(serializable)


def serial_special_methods() -> frozenset[str]:
    """序列化协议按名反射调用的私有方法名（不可重命名）。"""
    return _SERIAL_SPECIAL_METHODS
