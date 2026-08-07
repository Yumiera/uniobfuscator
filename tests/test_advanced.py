# -*- coding: utf-8 -*-
"""高级 Java 混淆 pass 测试：反射保护 / 私有成员重命名 / repackage / 元数据剥离。"""
from __future__ import annotations

import struct
import zipfile

import pytest

from uniobfuscator.jvm.classfile import (
    CONSTANT_Class,
    CONSTANT_Methodref,
    CONSTANT_NameAndType,
    CONSTANT_String,
    Attribute,
    ClassFile,
    CodeAttribute,
    ConstantPool,
    FieldInfo,
    MethodInfo,
    parse_class_file,
)
from uniobfuscator.jvm.code import Instruction
from uniobfuscator.jvm.jar import obfuscate_jar


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

def _build_pair_class(internal: str, super_internal: str) -> bytes:
    """构造 class，this=internal、super=super_internal（用于引用关系测试）。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8(internal))
    superc = cp.add(CONSTANT_Class, cp.add_utf8(super_internal))
    code_utf = cp.add_utf8("Code")
    code = CodeAttribute(0, 1, [Instruction(0xB1)], [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def _build_private_members_class(internal: str = "T") -> bytes:
    """构造带私有成员与自引用的类。

    class T {
        private int secret;                       // 私有字段
        private String hidden() { return "x"; }   // 私有方法
        public  String run() { return hidden(); } // 经 Methodref 自引用
    }
    """
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8(internal))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")

    field = FieldInfo(0x0002, cp.add_utf8("secret"), cp.add_utf8("I"), [])  # private

    hid_name = cp.add_utf8("hidden")
    hid_desc = cp.add_utf8("()Ljava/lang/String;")
    x = cp.add(CONSTANT_String, cp.add_utf8("x"))
    hid_code = CodeAttribute(1, 1,
                             [Instruction(0x12, bytes([x])), Instruction(0xB0)],
                             [], [], code_utf)
    hidden = MethodInfo(0x0002, hid_name, hid_desc, [hid_code])  # private

    nat = cp.add(CONSTANT_NameAndType, hid_name, hid_desc)
    mref = cp.add(CONSTANT_Methodref, this, nat)
    run_code = CodeAttribute(2, 1, [
        Instruction(0x2A),  # aload_0
        Instruction(0xB6, struct.pack(">H", mref)),  # invokevirtual T.hidden()
        Instruction(0xB0),  # areturn
    ], [], [], code_utf)
    run = MethodInfo(0x0001, cp.add_utf8("run"), hid_desc, [run_code])  # public

    return ClassFile(cp, 0x0021, this, superc, [], [field], [hidden, run], []).serialize()


def _build_serializable_class(internal: str = "S") -> bytes:
    """实现 java/io/Serializable 的类：private int secret + private String tag()。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8(internal))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    ser = cp.add(CONSTANT_Class, cp.add_utf8("java/io/Serializable"))
    code_utf = cp.add_utf8("Code")

    field = FieldInfo(0x0002, cp.add_utf8("secret"), cp.add_utf8("I"), [])
    tag_name = cp.add_utf8("tag")
    tag_desc = cp.add_utf8("()Ljava/lang/String;")
    tag_code = CodeAttribute(1, 1, [Instruction(0xB1)], [], [], code_utf)
    tag = MethodInfo(0x0002, tag_name, tag_desc, [tag_code])
    run = MethodInfo(0x0001, cp.add_utf8("run"), tag_desc,
                     [CodeAttribute(1, 1, [Instruction(0xB1)], [], [], code_utf)])
    return ClassFile(cp, 0x0021, this, superc, [ser], [field], [tag, run], []).serialize()


def _build_reflect_class(strings: list[str]) -> bytes:
    """常量池含指定 String 常量的类（模拟反射按名字符串）。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    for s in strings:
        cp.add(CONSTANT_String, cp.add_utf8(s))
    code = CodeAttribute(1, 1, [Instruction(0xB1)], [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def _build_strip_meta_class() -> bytes:
    """带 Signature/Exceptions/两种可见性注解的类。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    attrs = [
        Attribute(cp.add_utf8("Signature"), b"\x00\x01"),
        Attribute(cp.add_utf8("Exceptions"), b"\x00\x00"),
        Attribute(cp.add_utf8("RuntimeInvisibleAnnotations"), b"\x00\x00"),
        Attribute(cp.add_utf8("RuntimeVisibleAnnotations"), b"\x00\x00"),
    ]
    code_utf = cp.add_utf8("Code")
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"),
                        [CodeAttribute(1, 1, [Instruction(0xB1)], [], [], code_utf)])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], attrs).serialize()


# ---------------------------------------------------------------------------
# 反射保护 / Serializable 分析
# ---------------------------------------------------------------------------

def test_collect_protected_class_names():
    """String 常量匹配类内部名/点分名 -> 对应类被保护。"""
    from uniobfuscator.jvm.protect import collect_protected
    class_files = {
        "com/demo/App": parse_class_file(_build_pair_class("com/demo/App", "java/lang/Object")),
        "com/demo/Bar": parse_class_file(_build_pair_class("com/demo/Bar", "java/lang/Object")),
        "com/demo/Baz": parse_class_file(_build_pair_class("com/demo/Baz", "java/lang/Object")),
    }
    class_files["com/demo/App"] = parse_class_file(
        _build_reflect_class(["com/demo/Bar", "com.demo.Baz"]))
    classes, _ = collect_protected(class_files)
    assert "com/demo/Bar" in classes
    assert "com/demo/Baz" in classes
    assert "com/demo/App" not in classes


def test_collect_protected_member_names():
    """String 常量匹配成员名 -> 该成员名被保护（不重命名）。"""
    from uniobfuscator.jvm.protect import collect_protected
    class_files = {
        "T": parse_class_file(_build_reflect_class(["hidden", "other"])),
        "U": parse_class_file(_build_private_members_class("U")),
    }
    _, members = collect_protected(class_files)
    assert "hidden" in members      # U 的方法名被 T 的字符串引用
    assert "secret" not in members  # 无字符串引用


def test_find_serializable_transitive():
    """Serializable 判定含传递闭包：子类继承可序列化父类也算。"""
    from uniobfuscator.jvm.protect import find_serializable
    class_files = {
        "pkg/S": parse_class_file(_build_serializable_class("pkg/S")),
        "pkg/SChild": parse_class_file(_build_pair_class("pkg/SChild", "pkg/S")),
        "pkg/Normal": parse_class_file(_build_pair_class("pkg/Normal", "java/lang/Object")),
    }
    serializable = find_serializable(class_files)
    assert "pkg/S" in serializable
    assert "pkg/SChild" in serializable  # 父类可序列化 -> 子类字段也参与协议
    assert "pkg/Normal" not in serializable


# ---------------------------------------------------------------------------
# 私有成员重命名
# ---------------------------------------------------------------------------

def test_member_rename_private_only():
    """只重命名 private 成员；public / <init> 不动。"""
    from uniobfuscator.jvm.member import build_member_map
    cf = parse_class_file(_build_private_members_class())
    m = build_member_map(cf, seed=1)
    assert "hidden" in m and "secret" in m
    assert "run" not in m


def test_member_rename_skips_protected_and_serializable():
    """反射保护的成员名不重命名；Serializable 类字段不重命名（方法可）。"""
    from uniobfuscator.jvm.member import build_member_map
    cf = parse_class_file(_build_private_members_class())
    m = build_member_map(cf, seed=1, protected=frozenset({"hidden"}))
    assert "hidden" not in m and "secret" in m

    sf = parse_class_file(_build_serializable_class())
    m2 = build_member_map(sf, seed=1, serializable=True)
    assert "secret" not in m2          # Serializable 字段保留（序列化按名）
    assert "tag" in m2                 # 私有方法仍可重命名


def test_member_rename_apply_updates_refs():
    """应用重命名后：成员定义改名，常量池 Methodref 引用同步更新。"""
    from uniobfuscator.jvm.member import apply_member_rename, build_member_map
    cf = parse_class_file(_build_private_members_class())
    m = build_member_map(cf, seed=1)
    assert "hidden" in m
    apply_member_rename(cf, m)

    out = parse_class_file(cf.serialize())
    names = {out.cp.utf8(f.name_index) for f in out.fields}
    assert "secret" not in names
    mnames = {out.cp.utf8(mth.name_index) for mth in out.methods}
    assert "hidden" not in mnames and "run" in mnames

    # run() 的 invokevirtual 引用已指向新名
    run = [mth for mth in out.methods if out.cp.utf8(mth.name_index) == "run"][0]
    inv = [i for i in run.code().instructions if i.opcode == 0xB6][0]
    mref = out.cp[int.from_bytes(inv.operand, "big")]
    nat = out.cp[mref[2]]
    assert out.cp.utf8(nat[1]) in m.values()


# ---------------------------------------------------------------------------
# repackage（包名混淆）
# ---------------------------------------------------------------------------

def test_repackage_map_flattens_packages():
    """repackage：所有非排除类平铺到单一短包，简单名全局唯一。"""
    from uniobfuscator.jvm.rename import build_repackage_map
    names = ["com/demo/App", "com/demo/Base", "com/other/Helper", "pkg/Solo"]
    m = build_repackage_map(names, seed=1)
    assert set(m) == set(names)
    assert all(v.startswith("a/") for v in m.values())
    simples = [v[2:] for v in m.values()]
    assert len(simples) == len(set(simples))  # 全局唯一

    # 排除类保留原名
    m2 = build_repackage_map(names, seed=1, excluded=["com/demo/App"])
    assert "com/demo/App" not in m2
    assert all(v.startswith("a/") for k, v in m2.items())


def test_repackage_map_avoids_existing_pkg():
    """前缀包与现有顶层包冲突时自动避让。"""
    from uniobfuscator.jvm.rename import build_repackage_map
    names = ["a/Exist", "b/Other"]
    m = build_repackage_map(names, seed=1)
    for v in m.values():
        assert not v.startswith("a/")  # 已存在 a/ 包，改用 a1/ a2/ …
        assert v.startswith("a")


# ---------------------------------------------------------------------------
# 元数据剥离
# ---------------------------------------------------------------------------

def test_strip_metadata():
    """剥离 Signature/Exceptions/不可见注解，保留 RUNTIME 注解。"""
    from uniobfuscator.jvm.passes import strip_metadata
    cf = parse_class_file(_build_strip_meta_class())
    assert strip_metadata(cf) == 3
    out = parse_class_file(cf.serialize())
    names = {out.cp.utf8(a.name_index) for a in out.attributes}
    assert names == {"RuntimeVisibleAnnotations"}


# ---------------------------------------------------------------------------
# JAR 集成
# ---------------------------------------------------------------------------

def test_jar_member_rename(tmp_path):
    """JAR 级私有成员重命名：字段与方法改名，调用点同步。"""
    src = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src), "w") as z:
        z.writestr("pkg/T.class", _build_private_members_class("pkg/T"))
        z.writestr("pkg/U.class", _build_pair_class("pkg/U", "java/lang/Object"))
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src), str(out), seed=1, member_rename=True,
                          arithmetic=False, dead_code=False, scramble=False)
    assert stats["class"] == 2 and stats["members"] >= 2
    with zipfile.ZipFile(str(out)) as z:
        cf = parse_class_file(z.read("pkg/T.class"))
        names = {cf.cp.utf8(f.name_index) for f in cf.fields}
        assert "secret" not in names
        mnames = {cf.cp.utf8(mth.name_index) for mth in cf.methods}
        assert "hidden" not in mnames and "run" in mnames


def test_jar_repackage(tmp_path):
    """JAR 级 repackage：zip 路径、常量池引用、MANIFEST Main-Class 全部同步。"""
    src = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src), "w") as z:
        z.writestr("META-INF/MANIFEST.MF",
                   "Manifest-Version: 1.0\r\nMain-Class: com.demo.App\r\n")
        z.writestr("com/demo/App.class",
                   _build_pair_class("com/demo/App", "com/demo/Base"))
        z.writestr("com/demo/Base.class",
                   _build_pair_class("com/demo/Base", "java/lang/Object"))
        z.writestr("com/other/Helper.class",
                   _build_pair_class("com/other/Helper", "com/demo/App"))
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src), str(out), seed=1, repackage=True)
    assert stats["renamed"] == 3
    with zipfile.ZipFile(str(out)) as z:
        names = set(z.namelist())
        assert "com/demo/App.class" not in names
        assert "com/demo/Base.class" not in names
        assert "com/other/Helper.class" not in names
        class_names = sorted(n for n in names if n.endswith(".class"))
        assert len(class_names) == 3
        assert all(n.startswith("a/") for n in class_names)
        # 引用一致性：Helper.super 指向 App 的新名，App.super 指向 Base 的新名
        by_this = {}
        for n in class_names:
            cf = parse_class_file(z.read(n))
            by_this[cf.this_name()] = cf
        new_supers = {cf.cp.utf8(cf.cp[cf.super_class][1]) for cf in by_this.values()}
        assert all(s == "java/lang/Object" or s in by_this for s in new_supers)
        # Main-Class 更新为 a/ 下的新类
        manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8").replace("\r", "")
        assert "Main-Class: com.demo.App" not in manifest
        assert "Main-Class: a." in manifest


def test_jar_repackage_protects_reflected(tmp_path):
    """repackage + 反射：被 Class.forName 引用的类自动保留原名。"""
    src = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src), "w") as z:
        # Main 的字符串常量引用 "com/demo/Secret"（模拟 Class.forName）
        z.writestr("com/demo/Main.class",
                   _build_reflect_class(["com/demo/Secret"]))
        z.writestr("com/demo/Secret.class",
                   _build_pair_class("com/demo/Secret", "java/lang/Object"))
        z.writestr("com/demo/Plain.class",
                   _build_pair_class("com/demo/Plain", "java/lang/Object"))
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src), str(out), seed=1, repackage=True)
    assert stats["renamed"] == 2  # Main、Plain 平铺；Secret 被反射保护
    with zipfile.ZipFile(str(out)) as z:
        names = set(z.namelist())
        assert "com/demo/Secret.class" in names  # 反射目标保留原包名
        moved = [n for n in names if n != "com/demo/Secret.class"
                 and n.endswith(".class")]
        assert len(moved) == 2 and all(n.startswith("a/") for n in moved)


def test_jar_strip_metadata(tmp_path):
    """JAR 级元数据剥离：默认开启（java_strip_metadata）。"""
    src = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src), "w") as z:
        z.writestr("pkg/T.class", _build_strip_meta_class())
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src), str(out), seed=1,
                          arithmetic=False, dead_code=False, scramble=False)
    assert stats["metadata"] == 3
    with zipfile.ZipFile(str(out)) as z:
        cf = parse_class_file(z.read("pkg/T.class"))
        names = {cf.cp.utf8(a.name_index) for a in cf.attributes}
        assert names == {"RuntimeVisibleAnnotations"}
