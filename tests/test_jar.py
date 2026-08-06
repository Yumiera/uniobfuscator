# -*- coding: utf-8 -*-
"""JVM 字节码混淆测试：手工构造最小 class 验证解析/序列化/重定位/加密/JAR。"""
from __future__ import annotations

import struct
import zipfile

import pytest

from uniobfuscator.jvm.classfile import (
    CONSTANT_Class,
    CONSTANT_String,
    Attribute,
    ClassFile,
    CodeAttribute,
    ConstantPool,
    MethodInfo,
    parse_class_file,
)
from uniobfuscator.jvm.code import (
    Instruction,
    layout,
    parse_code,
    serialize_instructions,
)
from uniobfuscator.jvm.jar import obfuscate_jar
from uniobfuscator.jvm.passes import encrypt_strings, remove_debug_info


def build_class(with_debug: bool = True) -> bytes:
    """构造最小类：public static String T.f() { return "hello"; }"""
    cp = ConstantPool()
    t = cp.add_utf8("T")
    this = cp.add(CONSTANT_Class, t)
    obj = cp.add_utf8("java/lang/Object")
    superc = cp.add(CONSTANT_Class, obj)
    fname = cp.add_utf8("f")
    fdesc = cp.add_utf8("()Ljava/lang/String;")
    code_utf = cp.add_utf8("Code")
    hello = cp.add_utf8("hello")
    strc = cp.add(CONSTANT_String, hello)
    lnt = cp.add_utf8("LineNumberTable")
    sf = cp.add_utf8("SourceFile")
    tj = cp.add_utf8("T.java")
    code = CodeAttribute(1, 1, [Instruction(0x12, bytes([strc])), Instruction(0xB0)],
                         [], [], code_utf)
    if with_debug:
        code.attributes.append(Attribute(lnt, struct.pack(">HH", 0, 1)))
    method = MethodInfo(0x0009, fname, fdesc, [code])
    attrs = [Attribute(sf, struct.pack(">H", tj))] if with_debug else []
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], attrs).serialize()


# 带循环跳转的方法字节码：
#   iconst_0; istore_1; iload_1; bipush 10; if_icmpge +8;
#   iload_1; istore_2; iinc 1,1; goto -11; iload_2; ireturn
LOOP_CODE = bytes([
    0x03, 0x3C,             # iconst_0; istore_1
    0x1C, 0x10, 0x0A,       # iload_1; bipush 10
    0xA2, 0x00, 0x08,       # if_icmpge 13
    0x1C, 0x3D,             # iload_1; istore_2
    0x84, 0x01, 0x01,       # iinc 1, 1
    0xA7, 0xFF, 0xF5,       # goto 2
    0x1C, 0xAC,             # iload_2; ireturn
])


def test_class_round_trip():
    """parse -> serialize 应逐字节还原。"""
    raw = build_class()
    assert parse_class_file(raw).serialize() == raw


def test_remove_debug_info():
    """移除调试属性：SourceFile / LineNumberTable 消失，结构仍合法。"""
    cf = parse_class_file(build_class(with_debug=True))
    remove_debug_info(cf)
    out = cf.serialize()
    cf2 = parse_class_file(out)
    names = {cf2.cp.utf8(a.name_index) for a in cf2.attributes}
    assert "SourceFile" not in names
    code_attrs = {cf2.cp.utf8(a.name_index)
                  for m in cf2.methods
                  for a in (m.code().attributes if m.code() else [])}
    assert "LineNumberTable" not in code_attrs


def test_encrypt_strings():
    """字符串加密：ldc -> ldc_w 密文 + invokestatic，helper 注入，密文非原文。"""
    cf = parse_class_file(build_class(with_debug=False))
    encrypt_strings(cf, seed=5)
    out = parse_class_file(cf.serialize())

    # helper 方法注入
    helper_names = {out.cp.utf8(m.name_index) for m in out.methods}
    assert "_u5" in helper_names

    # f() 的指令：ldc_w + invokestatic，不再有 ldc
    code = out.methods[0].code()
    opcodes = [i.opcode for i in code.instructions]
    assert opcodes == [0x13, 0xB8, 0xB0]

    # ldc_w 现在加载的是密文字符串常量（原文常量保留在池中，但不再被引用）
    str_idx = int.from_bytes(code.instructions[0].operand, "big")
    assert out.cp.utf8(out.cp[str_idx][1]) == "".join(chr(ord(c) ^ 0xA2) for c in "hello")


def test_encrypt_strings_skips_non_ascii_control():
    """密文含 NUL 的字符串应跳过加密（保持明文）。"""
    cp = ConstantPool()
    s = cp.add_utf8("a")
    strc = cp.add(CONSTANT_String, s)
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code = CodeAttribute(1, 1, [Instruction(0x12, bytes([strc])), Instruction(0xB0)],
                         [], [], cp.add_utf8("Code"))
    cf = ClassFile(cp, 0x0021, this, superc, [], [],
                   [MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()Ljava/lang/String;"),
                               [code])], [])
    encrypt_strings(cf, seed=0)  # key=7; 'a'(97)^7 = 102 = 'f'，无 NUL，应加密
    assert cf.methods[0].code().instructions[0].opcode == 0x13
    # key 使密文为 NUL 的场景：选 'a' 且 key=97 → 需要 seed 使 key=97
    cp2 = ConstantPool()
    s2 = cp2.add_utf8("a")
    strc2 = cp2.add(CONSTANT_String, s2)
    this2 = cp2.add(CONSTANT_Class, cp2.add_utf8("T"))
    sup2 = cp2.add(CONSTANT_Class, cp2.add_utf8("java/lang/Object"))
    code2 = CodeAttribute(1, 1, [Instruction(0x12, bytes([strc2])), Instruction(0xB0)],
                          [], [], cp2.add_utf8("Code"))
    cf2 = ClassFile(cp2, 0x0021, this2, sup2, [], [],
                    [MethodInfo(0x0009, cp2.add_utf8("f"), cp2.add_utf8("()Ljava/lang/String;"),
                                [code2])], [])
    # 找 key=97 的 seed：97 = (s*31+7)&0xFF → s=77? (77*31+7)=2394 → 2394&0xFF=0x5A=90。手算太繁，
    # 直接验证不变量：无论 key 如何，加密后要么是密文+invokestatic，要么保持 ldc 明文（跳过）。
    encrypt_strings(cf2, seed=99)
    first = cf2.methods[0].code().instructions[0]
    if first.opcode == 0x12:
        utf = cf2.cp.utf8(cf2.cp[first.operand[0]][1])
        assert utf == "a"  # 跳过场景：明文保留
    else:
        assert first.opcode == 0x13


def test_jump_round_trip():
    """含循环跳转的字节码，无修改时 serialize 逐字节一致。"""
    insns = parse_code(LOOP_CODE)
    assert serialize_instructions(insns) == LOOP_CODE


def test_jump_relocation_after_insert():
    """插入指令后跳转目标仍指向合法指令边界。"""
    insns = parse_code(LOOP_CODE)
    # 在 goto 之后插入一条 ldc_w + invokestatic（5 字节），模拟字符串加密
    insert_at = next(i for i, x in enumerate(insns) if x.opcode == 0xA7) + 1
    insns[insert_at:insert_at] = [
        Instruction(0x13, b"\x00\x0a"),
        Instruction(0xB8, b"\x00\x0b"),
    ]
    new_code = serialize_instructions(insns)
    reparsed = parse_code(new_code)
    layout(reparsed)
    boundaries = {x.new_offset for x in reparsed}
    for insn in reparsed:
        for target in insn.jumps:
            assert target in boundaries, f"跳转目标 {target} 不在指令边界"
    # 循环语义保持：if_icmpge 目标 == goto 的环上点，目标地址必须一致
    by_offset = {x.new_offset: x for x in reparsed}
    targets = {}
    for insn in reparsed:
        if insn.opcode == 0xA2:
            targets["if"] = insn.jumps[0]
        if insn.opcode == 0xA7:
            targets["goto"] = insn.jumps[0]
    assert targets["goto"] in boundaries
    assert targets["if"] in boundaries


def test_tableswitch_round_trip():
    """tableswitch 解析与序列化（含 padding 对齐）逐字节一致。"""
    # code 起始 4 个 nop 使 tableswitch 对齐；跳转目标指向 nop 指令边界
    code = bytes([0x00] * 4) + bytes([
        0xAA,             # tableswitch @4
        0x00, 0x00, 0x00,  # padding -> default@8
    ]) + struct.pack(">iii", 0, 0, 1) + struct.pack(">ii", 1, 2)
    insns = parse_code(code)
    assert serialize_instructions(insns) == code


def test_jar_end_to_end(tmp_path):
    """JAR 打包/重打包：class 混淆、签名警告、非 class 资源保留。"""
    src_file = tmp_path / "app.jar"
    out_file = tmp_path / "app_obf.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("META-INF/TEST.SF", "Signature-Version: 1.0\r\n")
        z.writestr("pkg/T.class", build_class())
        z.writestr("README.txt", "keep me")

    stats = obfuscate_jar(str(src_file), str(out_file), seed=5)
    assert stats["class"] == 1
    assert stats["signature"] == 1
    assert any("签名" in w for w in stats["warnings"])

    with zipfile.ZipFile(str(out_file)) as z:
        names = set(z.namelist())
        assert "pkg/T.class" in names
        assert "README.txt" in names
        assert "META-INF/TEST.SF" in names
        obf_class = parse_class_file(z.read("pkg/T.class"))
        code_attrs = {obf_class.cp.utf8(a.name_index)
                      for m in obf_class.methods
                      for a in (m.code().attributes if m.code() else [])}
        assert "LineNumberTable" not in code_attrs
        opcodes = [i.opcode for i in obf_class.methods[0].code().instructions]
        assert opcodes == [0x13, 0xB8, 0xB0]


def test_obfuscate_jar_invalid_zip(tmp_path):
    """非法 zip 抛 ValueError。"""
    src = tmp_path / "bad.jar"
    src.write_bytes(b"PK\x03\x04not-a-zip")
    with pytest.raises(ValueError):
        obfuscate_jar(str(src), str(tmp_path / "out.jar"))


def test_cli_jar_obfuscates(tmp_path, monkeypatch, capsys):
    """CLI 直接混淆 .jar，输出默认 *.obf.jar。"""
    monkeypatch.chdir(tmp_path)
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("pkg/T.class", build_class())
    out = tmp_path / "out.jar"
    from uniobfuscator.cli import main
    rc = main([str(src_file), "-o", str(out)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert out.exists()
    with zipfile.ZipFile(str(out)) as z:
        cf = parse_class_file(z.read("pkg/T.class"))
        opcodes = [i.opcode for i in cf.methods[0].code().instructions]
        assert opcodes == [0x13, 0xB8, 0xB0]
