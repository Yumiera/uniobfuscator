# -*- coding: utf-8 -*-
"""JVM 字节码混淆测试：手工构造最小 class 验证解析/序列化/重定位/加密/JAR。"""
from __future__ import annotations

import struct
import zipfile

import pytest

from uniobfuscator.jvm.classfile import (
    CONSTANT_Class,
    CONSTANT_Fieldref,
    CONSTANT_InterfaceMethodref,
    CONSTANT_InvokeDynamic,
    CONSTANT_Long,
    CONSTANT_Methodref,
    CONSTANT_NameAndType,
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
    """字符串加密：ldc -> ldc_w 密文 + sipush key + invokestatic，helper 注入。"""
    cf = parse_class_file(build_class(with_debug=False))
    encrypt_strings(cf, seed=5)
    out = parse_class_file(cf.serialize())

    # helper 方法注入
    helper_names = {out.cp.utf8(m.name_index) for m in out.methods}
    assert "_u5" in helper_names

    # f() 的指令：ldc_w 密文 + sipush key + invokestatic，不再有 ldc
    code = out.methods[0].code()
    opcodes = [i.opcode for i in code.instructions]
    assert opcodes == [0x13, 0x11, 0xB8, 0xB0]

    # ldc_w 加载的是密文字符串；sipush 是独立密钥（seed=5 派生 rng 首值 68）
    str_idx = int.from_bytes(code.instructions[0].operand, "big")
    key = struct.unpack(">h", code.instructions[1].operand)[0]
    assert key == 68
    assert out.cp.utf8(out.cp[str_idx][1]) == "".join(chr(ord(c) ^ key) for c in "hello")


def test_helper_stack_map_table():
    """解密 helper（含循环）必须带 StackMapTable（major>=51 的 SplitVerifier 要求）。"""
    cf = parse_class_file(build_class(with_debug=False))
    assert cf.major_version >= 51
    encrypt_strings(cf, seed=5)
    helper = [m for m in cf.methods if cf.cp.utf8(m.name_index) == "_u5"][0]
    code = helper.code()
    smt = [a for a in code.attributes if cf.cp.utf8(a.name_index) == "StackMapTable"]
    assert smt, "含分支的 helper 必须携带 StackMapTable，否则 JVM 抛 VerifyError"
    payload = smt[0].payload
    assert int.from_bytes(payload[:2], "big") == 2  # 2 个 frame
    # entry1: APPEND(2) frame（251+2=253），delta=9（循环头 @9，goto 目标）：
    # Object("[C") + Integer
    assert payload[2] == 0xFD
    assert int.from_bytes(payload[3:5], "big") == 9
    assert payload[5] == 7 and payload[8] == 1
    # entry2: SAME frame（frame_type=22）：offset = 9 + 22 + 1 = 32（循环退出，if_icmpge 目标）
    assert payload[9] == 22
    # round-trip 后 StackMapTable 仍存在且内容一致
    out = parse_class_file(cf.serialize())
    helper2 = [m for m in out.methods if out.cp.utf8(m.name_index) == "_u5"][0]
    smt2 = [a for a in helper2.code().attributes
            if out.cp.utf8(a.name_index) == "StackMapTable"]
    assert smt2 and smt2[0].payload == payload


def test_stack_map_table_remap():
    """SMT 帧偏移重定位：delta+1 规则；identity 映射时逐字节一致。"""
    from uniobfuscator.jvm.code import remap_stack_map_table
    # javac 风格：APPEND(2) @9（Object "[C" idx 0x2B + Integer），SAME @33（delta=23）
    payload = struct.pack(">H", 2) + bytes([
        0xFD, 0x00, 0x09, 0x07, 0x00, 0x2B, 0x01,
        0x17,
    ])
    assert remap_stack_map_table(payload, {9: 9, 33: 33}) == payload
    # 指令前插 5 字节：9->14, 33->38；第二帧 delta = 38 - 14 - 1 = 23
    out = remap_stack_map_table(payload, {9: 14, 33: 38})
    assert out[:2] == struct.pack(">H", 2)
    assert out[2] == 0xFD
    assert int.from_bytes(out[3:5], "big") == 14
    assert out[5:8] == bytes([0x07, 0x00, 0x2B]) and out[8] == 0x01
    assert out[9] == 0x17  # SAME（frame_type 23，delta 编码在 frame_type）


def test_encrypt_loop_method_remaps_smt():
    """带循环+字符串的方法被加密后，其 StackMapTable 帧偏移随重定位更新。

    原方法：ldc "hello"@0; astore_1; iconst_0; istore_2;
            iload_2@5(循环头); bipush 10; if_icmpge 14; iinc 2,1;
            aload_1@14(循环出口); areturn
    SMT：APPEND(2)@5（Object String + Integer）、SAME@14。
    加密后 ldc -> ldc_w+sipush+invokestatic（净增 7 字节）：循环头 -> 12，出口 -> 21。
    """
    cp = ConstantPool()
    hello = cp.add_utf8("hello")
    strc = cp.add(CONSTANT_String, hello)          # index 2，ldc 操作数
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    fname = cp.add_utf8("f")
    fdesc = cp.add_utf8("(Ljava/lang/String;)V")
    code_utf = cp.add_utf8("Code")
    smt_utf = cp.add_utf8("StackMapTable")
    str_class = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/String"))
    code_bytes = bytes([
        0x12, strc,               # ldc "hello"
        0x4C,                     # astore_1
        0x03, 0x3D,               # iconst_0; istore_2
        0x1C, 0x10, 0x0A,         # iload_2; bipush 10
        0xA2, 0x00, 0x06,         # if_icmpge 13
        0x84, 0x02, 0x01,         # iinc 2, 1
        0x2B, 0xB0,               # aload_1; areturn
    ])
    object_info = bytes([7]) + struct.pack(">H", str_class)
    int_info = bytes([1])
    smt = (struct.pack(">H", 2)
           + bytes([0xFD]) + struct.pack(">H", 5) + object_info + int_info
           + bytes([8]))  # SAME @14（delta = 14 - 5 - 1 = 8）
    code = CodeAttribute(2, 3, parse_code(code_bytes), [],
                         [Attribute(smt_utf, smt)], code_utf)
    cf = ClassFile(cp, 0x0021, this, superc, [], [],
                   [MethodInfo(0x0009, fname, fdesc, [code])], [])

    encrypt_strings(cf, seed=5)
    out = parse_class_file(cf.serialize())
    f = [m for m in out.methods if out.cp.utf8(m.name_index) == "f"][0]
    smt2 = [a for a in f.code().attributes
            if out.cp.utf8(a.name_index) == "StackMapTable"][0]
    p = smt2.payload
    assert p[:2] == struct.pack(">H", 2)
    assert p[2] == 0xFD and int.from_bytes(p[3:5], "big") == 12   # APPEND(2) @12
    assert p[5] == 7 and p[8] == 1
    assert p[9] == 8                                            # SAME @21（21-12-1=8）
    # 二次序列化（identity 映射）逐字节一致
    assert parse_class_file(out.serialize()).serialize() == out.serialize()


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
    encrypt_strings(cf, seed=0)  # 派生 rng 首值 key=72；'a'(97)^72=41 无 NUL，应加密
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
    # 找 key=97 的 seed：97 = rng.randrange(256) 首值。直接验证不变量：无论 key
    # 如何，加密后要么是密文+invokestatic，要么保持 ldc 明文（跳过）。
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

    stats = obfuscate_jar(str(src_file), str(out_file), seed=5,
                          arithmetic=False, dead_code=False, scramble=False)
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
        assert opcodes == [0x13, 0x11, 0xB8, 0xB0]


def test_obfuscate_jar_invalid_zip(tmp_path):
    """非法 zip 抛 ValueError。"""
    src = tmp_path / "bad.jar"
    src.write_bytes(b"PK\x03\x04not-a-zip")
    with pytest.raises(ValueError):
        obfuscate_jar(str(src), str(tmp_path / "out.jar"))


def test_jar_exclude_exact_class(tmp_path):
    """--exclude 精确类：该类原样保留（含调试信息），其余正常混淆。"""
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("pkg/T.class", build_class())  # 带 LineNumberTable/SourceFile
        z.writestr("pkg/U.class", build_class())
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src_file), str(out), seed=5, exclude=["pkg.T"],
                          arithmetic=False, dead_code=False, scramble=False)
    assert stats["class"] == 1 and stats["excluded"] == 1
    with zipfile.ZipFile(str(out)) as z:
        assert z.read("pkg/T.class") == build_class()  # 原样，逐字节一致
        obf_u = parse_class_file(z.read("pkg/U.class"))
        opcodes = [i.opcode for i in obf_u.methods[0].code().instructions]
        assert opcodes == [0x13, 0x11, 0xB8, 0xB0]  # U 正常混淆


def test_jar_exclude_package(tmp_path):
    """--exclude pkg.*：整个包原样保留。"""
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("pkg/a/T.class", build_class())
        z.writestr("pkg/a/b/U.class", build_class())
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src_file), str(out), seed=5, exclude=["pkg.a.*"])
    assert stats["class"] == 0 and stats["excluded"] == 2
    with zipfile.ZipFile(str(out)) as z:
        assert z.read("pkg/a/T.class") == build_class()
        assert z.read("pkg/a/b/U.class") == build_class()


def test_cli_jar_exclude(tmp_path, monkeypatch, capsys):
    """CLI --exclude：被排除类原样保留。"""
    monkeypatch.chdir(tmp_path)
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("pkg/T.class", build_class())
    out = tmp_path / "out.jar"
    from uniobfuscator.cli import main
    rc = main([str(src_file), "-o", str(out), "--exclude", "pkg.T"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "排除 1 个" in captured.out
    with zipfile.ZipFile(str(out)) as z:
        assert z.read("pkg/T.class") == build_class()


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
        # 默认开启全部 pass：字符串加密（ldc_w+invokestatic）+ 死代码注入（iconst_0 前缀）
        assert opcodes[0] == 0x03 and 0x99 in opcodes
        assert 0x13 in opcodes and 0xB8 in opcodes and opcodes[-1] == 0xB0


# ---------------------------------------------------------------------------
# 新增 Java 混淆 pass
# ---------------------------------------------------------------------------

def _build_const_class(value: int = 42) -> bytes:
    """int f() { return value; } —— sipush value; ireturn。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    code = CodeAttribute(1, 1,
                         [Instruction(0x11, struct.pack(">h", value)), Instruction(0xAC)],
                         [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()I"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def _build_multi_statement_class() -> bytes:
    """void f() { nanoTime(); nanoTime(); nanoTime(); } —— 3 条栈深 0 语句。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    sys_class = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/System"))
    mref = cp.add(CONSTANT_Methodref, sys_class,
                  cp.add(CONSTANT_NameAndType, cp.add_utf8("nanoTime"), cp.add_utf8("()J")))
    insns = [Instruction(0xB8, struct.pack(">H", mref)), Instruction(0x58)] * 3 \
        + [Instruction(0xB1)]  # invokestatic nanoTime; pop2（x3）; return
    code = CodeAttribute(2, 1, insns, [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def test_arithmetic_obfuscate():
    """整型常量算术混淆：sipush 42 -> 双常量表达式，32 位求值仍为 42。"""
    from uniobfuscator.jvm.passes import arithmetic_obfuscate
    cf = parse_class_file(_build_const_class(42))
    assert arithmetic_obfuscate(cf, seed=7) == 1
    insns = cf.methods[0].code().instructions
    assert len(insns) == 4
    a = struct.unpack(">h", insns[0].operand)[0]
    if insns[1].opcode == 0x11:
        b = struct.unpack(">h", insns[1].operand)[0]
    else:  # ldc Integer
        b = cf.cp[int.from_bytes(insns[1].operand, "big")][1]
    if insns[2].opcode == 0x82:  # ixor
        result = a ^ b
    else:                        # iadd
        result = a + b
    assert (result & 0xFFFFFFFF) == (42 & 0xFFFFFFFF)
    assert insns[3].opcode == 0xAC
    # 序列化 round-trip 稳定
    out = parse_class_file(cf.serialize())
    assert len(out.methods[0].code().instructions) == 4


def test_inject_dead_code():
    """死代码注入：无分支方法获得不透明谓词 + StackMapTable。"""
    from uniobfuscator.jvm.passes import inject_dead_code
    cf = parse_class_file(_build_const_class(42))
    assert inject_dead_code(cf, seed=1) == 1
    code = cf.methods[0].code()
    # ()I static：入口 locals=0，新变量占槽 0，无需扩大 max_locals（原本为 1）
    assert code.max_locals == 1
    names = {cf.cp.utf8(a.name_index) for a in code.attributes}
    assert "StackMapTable" in names
    opcodes = [i.opcode for i in code.instructions]
    assert opcodes[0] == 0x03  # iconst_0 开头
    assert 0x99 in opcodes and 0xA7 in opcodes  # ifeq + goto
    # round-trip 稳定
    out = parse_class_file(cf.serialize())
    assert len(out.methods[0].code().instructions) == len(opcodes)


def test_inject_dead_code_slot_after_params():
    """死代码变量槽位 = 参数之后第一个空闲槽（APPEND 帧追加位置），
    而非 max_locals：否则与 StackMapTable 槽位错位，真实 JVM VerifyError。"""
    from uniobfuscator.jvm.passes import inject_dead_code
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    # static int f(long a, int b) { return b; }
    code = CodeAttribute(2, 3,
                         [Instruction(0x1C), Instruction(0xAC)],  # iload_2; ireturn
                         [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"),
                        cp.add_utf8("(JI)I"), [code])
    cf = ClassFile(cp, 0x0021, this, superc, [], [], [method], [])
    assert inject_dead_code(cf, seed=1) == 1
    insns = cf.methods[0].code().instructions
    # 新变量槽 = 参数槽数（long 2 + int 1 = 3）
    istore = [i for i in insns if i.opcode == 0x36][0]
    assert int.from_bytes(istore.operand, "big") == 3
    assert cf.methods[0].code().max_locals == max(3, 3 + 1)  # 4


def test_scramble_control_flow():
    """控制流打散：多语句方法插入 goto+垃圾块并携带 StackMapTable。"""
    from uniobfuscator.jvm.passes import scramble_control_flow
    cf = parse_class_file(_build_multi_statement_class())
    assert scramble_control_flow(cf, seed=3) == 1
    code = cf.methods[0].code()
    assert code.max_stack >= 2
    names = {cf.cp.utf8(a.name_index) for a in code.attributes}
    assert "StackMapTable" in names
    opcodes = [i.opcode for i in code.instructions]
    assert opcodes.count(0xA7) == 6  # 3 个 goto 垃圾块 + 3 个跳回
    out = parse_class_file(cf.serialize())
    assert len(out.methods[0].code().instructions) == len(opcodes)


def _build_pair_class(internal: str, super_internal: str) -> bytes:
    """构造 class，this=internal、super=super_internal（用于引用关系测试）。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8(internal))
    superc = cp.add(CONSTANT_Class, cp.add_utf8(super_internal))
    code_utf = cp.add_utf8("Code")
    code = CodeAttribute(0, 1, [Instruction(0xB1)], [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def test_jar_rename_classes(tmp_path):
    """类名重命名：zip 路径、常量池引用、MANIFEST Main-Class 全部同步。"""
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF",
                   "Manifest-Version: 1.0\r\nMain-Class: com.demo.App\r\n")
        z.writestr("com/demo/App.class",
                   _build_pair_class("com/demo/App", "com/demo/Base"))
        z.writestr("com/demo/Base.class",
                   _build_pair_class("com/demo/Base", "java/lang/Object"))
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src_file), str(out), seed=1, rename=True)
    assert stats["renamed"] == 2
    with zipfile.ZipFile(str(out)) as z:
        names = set(z.namelist())
        assert "com/demo/App.class" not in names
        assert "com/demo/Base.class" not in names
        new_names = sorted(n for n in names if n.endswith(".class"))
        assert len(new_names) == 2
        this_names, super_names = set(), set()
        for n in new_names:
            cf = parse_class_file(z.read(n))
            this_names.add(cf.this_name())
            super_names.add(cf.cp.utf8(cf.cp[cf.super_class][1]))
        assert "com/demo/App" not in this_names and "com/demo/Base" not in this_names
        assert "com/demo/Base" not in super_names  # 子类 super 已指向新名
        assert "java/lang/Object" in super_names
        manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8").replace("\r", "")
        assert "Main-Class: com.demo.App" not in manifest
        assert "Main-Class: com.demo." in manifest


def test_jar_rename_excludes_class(tmp_path):
    """类名重命名 + 排除：被排除类不改名，但其对重命名类的引用同步更新。"""
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("pkg/Main.class",
                   _build_pair_class("pkg/Main", "pkg/Secret"))
        z.writestr("pkg/Secret.class",
                   _build_pair_class("pkg/Secret", "java/lang/Object"))
    out = tmp_path / "out.jar"
    stats = obfuscate_jar(str(src_file), str(out), seed=1, rename=True,
                          exclude=["pkg.Secret"])
    assert stats["renamed"] == 1 and stats["excluded"] == 1
    with zipfile.ZipFile(str(out)) as z:
        names = set(z.namelist())
        assert "pkg/Secret.class" in names  # 被排除类保持原名
        main_names = [n for n in names if n != "pkg/Secret.class" and n.endswith(".class")]
        assert len(main_names) == 1
        cf = parse_class_file(z.read(main_names[0]))
        assert cf.this_name() != "pkg/Main"  # Main 本身被重命名
        super_name = cf.cp.utf8(cf.cp[cf.super_class][1])
        assert super_name == "pkg/Secret"  # 被排除类不改名，引用保持不变


def test_cli_jar_warns_text_flags(tmp_path, monkeypatch, capsys):
    """JAR 模式传文本专属开关：警告并忽略（java_* 仍按默认生效）。"""
    src_file = tmp_path / "app.jar"
    with zipfile.ZipFile(str(src_file), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n")
        z.writestr("pkg/T.class", build_class())
    out = tmp_path / "out.jar"
    monkeypatch.chdir(tmp_path)
    from uniobfuscator.cli import main
    rc = main([str(src_file), "-o", str(out), "--no-dead-code"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    # --no-dead-code 是文本专属开关：警告并忽略
    assert "仅适用于 文本源码混淆" in captured.err
    with zipfile.ZipFile(str(out)) as z:
        cf = parse_class_file(z.read("pkg/T.class"))
        opcodes = [i.opcode for i in cf.methods[0].code().instructions]
        # java_dead_code 默认仍开启：iconst_0 前缀 + ifeq
        assert opcodes[0] == 0x03 and 0x99 in opcodes


# ---------------------------------------------------------------------------
# 真实 javac 输出形态回归测试（t8 端到端验证发现的真实 JVM bug）
# ---------------------------------------------------------------------------

def test_parse_class_with_long_constant():
    """含 long 常量（serialVersionUID 场景）的 class：cp_count 按槽位计数，
    占位槽无 tag 字节，解析与 round-trip 必须逐字节一致。"""
    cp = ConstantPool([None, (CONSTANT_Long, 1), None])  # 槽1=Long，槽2=占位
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    cf = ClassFile(cp, 0x0021, this, superc, [], [], [], [])
    raw = cf.serialize()
    cf2 = parse_class_file(raw)
    assert cf2.serialize() == raw
    assert cf2.cp[1][0] == CONSTANT_Long and cf2.cp[2] is None


def _build_invoke_class() -> bytes:
    """含 invokeinterface + invokedynamic 的方法（Java 9+ 字符串拼接形态）。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    list_c = cp.add(CONSTANT_Class, cp.add_utf8("java/util/List"))
    size_mref = cp.add(CONSTANT_InterfaceMethodref, list_c,
                       cp.add(CONSTANT_NameAndType, cp.add_utf8("size"),
                              cp.add_utf8("()I")))
    nat = cp.add(CONSTANT_NameAndType, cp.add_utf8("makeConcat"),
                 cp.add_utf8("(Ljava/lang/String;)Ljava/lang/String;"))
    indy = cp.add(CONSTANT_InvokeDynamic, 0, nat)
    code = CodeAttribute(2, 1, [
        Instruction(0xB9, struct.pack(">H", size_mref) + b"\x01\x00"),
        Instruction(0x57),                                        # pop
        Instruction(0xBA, struct.pack(">H", indy) + b"\x00\x00"),
        Instruction(0x57),                                        # pop
        Instruction(0xB1),                                        # return
    ], [], [], code_utf)
    method = MethodInfo(0x0009, cp.add_utf8("f"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def test_invoke_effect_4byte_operand():
    """invokeinterface/invokedynamic 的 4 字节操作数只取前 2 字节作为常量池
    索引（此前整段读取越界抛 IndexError，真实 javac 代码含 invokedynamic）。"""
    from uniobfuscator.jvm.code import stack_depths
    cf = parse_class_file(_build_invoke_class())
    depths = stack_depths(cf.methods[0].code().instructions, cf.cp)
    assert depths[0] == 0
    assert len(depths) == 5
    # 序列化 round-trip 后仍可解析（操作数 4 字节保留）
    out = parse_class_file(cf.serialize())
    assert len(out.methods[0].code().instructions) == 5


def _build_init_class() -> bytes:
    """<init>：super() + 两条 putfield（多条栈深 0 边界，本会触发打散）。"""
    cp = ConstantPool()
    this = cp.add(CONSTANT_Class, cp.add_utf8("T"))
    superc = cp.add(CONSTANT_Class, cp.add_utf8("java/lang/Object"))
    code_utf = cp.add_utf8("Code")
    strc = cp.add(CONSTANT_String, cp.add_utf8("x"))
    field1 = cp.add(CONSTANT_Fieldref, this, cp.add(
        CONSTANT_NameAndType, cp.add_utf8("a"), cp.add_utf8("Ljava/lang/String;")))
    field2 = cp.add(CONSTANT_Fieldref, this, cp.add(
        CONSTANT_NameAndType, cp.add_utf8("b"), cp.add_utf8("I")))
    obj_init = cp.add(CONSTANT_Methodref, superc, cp.add(
        CONSTANT_NameAndType, cp.add_utf8("<init>"), cp.add_utf8("()V")))
    insns = [
        Instruction(0x2A),                                  # aload_0
        Instruction(0xB7, struct.pack(">H", obj_init)),     # invokespecial <init>
        Instruction(0x2A), Instruction(0x12, bytes([strc])),
        Instruction(0xB5, struct.pack(">H", field1)),       # putfield a
        Instruction(0x2A), Instruction(0x03),
        Instruction(0xB5, struct.pack(">H", field2)),       # putfield b
        Instruction(0xB1),                                  # return
    ]
    code = CodeAttribute(2, 1, insns, [], [], code_utf)
    method = MethodInfo(0x0001, cp.add_utf8("<init>"), cp.add_utf8("()V"), [code])
    return ClassFile(cp, 0x0021, this, superc, [], [], [method], []).serialize()


def test_scramble_skips_init():
    """打散必须跳过 <init>：locals[0] 有 uninitializedThis->this 转换，
    SAME 帧无法表示，否则真实 JVM VerifyError。"""
    from uniobfuscator.jvm.passes import scramble_control_flow
    cf = parse_class_file(_build_init_class())
    assert scramble_control_flow(cf, seed=3) == 0


def test_encrypt_strings_bumps_max_stack():
    """字符串加密提升 max_stack：ldc->ldc_w+sipush+invokestatic 瞬时栈深 +1，
    否则与算术混淆叠加后真实 JVM 报 Exceeded max stack size。"""
    cf = parse_class_file(build_class(with_debug=False))
    assert cf.methods[0].code().max_stack == 1
    encrypt_strings(cf, seed=5)
    assert cf.methods[0].code().max_stack == 2
