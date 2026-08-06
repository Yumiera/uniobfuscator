# -*- coding: utf-8 -*-
"""JVM 字节码混淆 pass。

- remove_debug_info：删除 LineNumberTable / LocalVariableTable /
  LocalVariableTypeTable / SourceFile 等调试属性（无副作用，安全）。
- encrypt_strings：把 ldc/ldc_w 加载的字符串常量替换为"密文 +
  调用本类注入的静态解密方法"，并做指令重定位。
"""
from __future__ import annotations

import struct

from .classfile import (
    CONSTANT_Class,
    CONSTANT_Methodref,
    CONSTANT_NameAndType,
    CONSTANT_String,
    Attribute,
    ClassFile,
    CodeAttribute,
    MethodInfo,
)
from .code import Instruction, parse_code

#: 删除的调试属性名
_DEBUG_ATTR_NAMES = ("SourceFile", "LineNumberTable",
                     "LocalVariableTable", "LocalVariableTypeTable")


def _debug_attr_indexes(class_file: ClassFile) -> frozenset[int]:
    return frozenset(
        class_file.cp.find_utf8(name) for name in _DEBUG_ATTR_NAMES
    ) - {0}


def remove_debug_info(class_file: ClassFile) -> None:
    """移除类级与 Code 级调试属性。"""
    bad = _debug_attr_indexes(class_file)
    if not bad:
        return
    class_file.attributes = [
        a for a in class_file.attributes if a.name_index not in bad
    ]
    for method in class_file.methods:
        code = method.code()
        if code is None:
            continue
        code.attributes = [
            a for a in code.attributes if a.name_index not in bad
        ]


def _key_for(seed: int) -> int:
    """由 seed 派生的单字节异或密钥（0-255）。"""
    return (seed * 31 + 7) & 0xFF


def _cipher(s: str, key: int) -> str:
    return "".join(chr(ord(c) ^ key) for c in s)


# ---------------------------------------------------------------------------
# 字符串加密
# ---------------------------------------------------------------------------

def _build_stack_map_table(class_file: ClassFile, char_arr_class_idx: int,
                           instructions) -> Attribute:
    """为解密 helper 生成 StackMapTable 属性（JVMS 4.7.4）。

    class 版本 >= 51（Java 7+）采用 SplitVerifier：任何带分支/跳转的方法
    都必须携带 StackMapTable，否则 JVM 抛 VerifyError。helper 的 2 个分支
    目标处 locals 均为 [String, char[], int]，相对隐式帧（由方法描述符推导，
    即 [String]）追加 2 个局部变量（[C 与 Integer），故首帧用 APPEND(2)，
    frame_type = 251 + 2 = 253；后续帧 locals 不变，用 SAME 帧。

    帧偏移计算（此前硬编码 delta 导致 ClassFormatError 的根因）：
      首帧 offset = offset_delta；
      后续帧 offset = 上一帧 offset + offset_delta + 1。
    """
    cp = class_file.cp
    object_info = bytes([7]) + struct.pack(">H", char_arr_class_idx)  # Object_variable_info("[C")
    int_info = bytes([1])                                             # Integer_variable_info
    # 分支目标（绝对偏移，升序）即需要 frame 的位置；不硬编码偏移
    targets = sorted({t for insn in instructions for t in insn.jumps})
    entries: list[bytes] = []
    prev = None
    for off in targets:
        if prev is None:
            delta = off
            entries.append(bytes([0xFD]) + struct.pack(">H", delta) + object_info + int_info)
        else:
            delta = off - prev - 1  # 规范：非首帧 offset = prev + delta + 1
            if delta <= 63:
                entries.append(bytes([delta]))  # SAME
            else:
                entries.append(bytes([251]) + struct.pack(">H", delta))  # SAME_EXTENDED
        prev = off
    payload = struct.pack(">H", len(entries)) + b"".join(entries)
    return Attribute(cp.add_utf8("StackMapTable"), payload)


def _build_decrypt_helper(class_file: ClassFile, seed: int) -> MethodInfo:
    """构造静态解密方法：static String d_xxx(String s)。

    实现：把 s 的每个 char 与 key 异或后组装为新 String 返回。
    class 版本 >= 51 时附带 StackMapTable（split verification 要求）。
    """
    cp = class_file.cp
    name = cp.add_utf8("_u" + format(seed, "x"))
    desc = cp.add_utf8("(Ljava/lang/String;)Ljava/lang/String;")
    str_utf = cp.add_utf8("java/lang/String")
    str_class = cp.add(CONSTANT_Class, str_utf)
    length_mref = cp.add(
        CONSTANT_Methodref, str_class,
        cp.add(CONSTANT_NameAndType, cp.add_utf8("length"), cp.add_utf8("()I")),
    )
    charat_mref = cp.add(
        CONSTANT_Methodref, str_class,
        cp.add(CONSTANT_NameAndType, cp.add_utf8("charAt"), cp.add_utf8("(I)C")),
    )
    init_mref = cp.add(
        CONSTANT_Methodref, str_class,
        cp.add(CONSTANT_NameAndType, cp.add_utf8("<init>"), cp.add_utf8("([C)V")),
    )
    key = _key_for(seed)
    code_utf = cp.add_utf8("Code")
    code_bytes = bytes([
        0x2A, 0xB6, length_mref >> 8, length_mref & 0xFF, 0xBC, 0x05,   # buf=new char[s.length()]（atype 5=char）
        0x4C, 0x03, 0x3D,                                              # buf, i=0
        0x1C, 0x2B, 0xBE, 0xA2, 0x00, 0x16,                            # while(i<buf.length)
        0x2B, 0x1C, 0x2A, 0x1C,                                        # buf[i]=s.charAt(i)
        0xB6, charat_mref >> 8, charat_mref & 0xFF, 0x11, 0x00, key,   #   ^ key（sipush 无符号，避免 bipush 符号扩展）
        0x82, 0x92, 0x55, 0x84, 0x02, 0x01, 0xA7, 0xFF, 0xEA,          #   ; i++
        0xBB, str_class >> 8, str_class & 0xFF, 0x59, 0x2B,            # new String(buf)
        0xB7, init_mref >> 8, init_mref & 0xFF, 0xB0,                  # <init>; return
    ])
    instructions = parse_code(code_bytes)
    code_attrs: list[Attribute] = []
    if class_file.major_version >= 51:
        char_arr_class = cp.add(CONSTANT_Class, cp.add_utf8("[C"))
        code_attrs.append(_build_stack_map_table(class_file, char_arr_class, instructions))
    helper_code = CodeAttribute(4, 3, instructions, [], code_attrs, code_utf)
    return MethodInfo(0x000A, name, desc, [helper_code])


def encrypt_strings(class_file: ClassFile, seed: int) -> None:
    """加密所有能被 ldc 加载的字符串常量（跳过含非 BMP 字符或密文含 NUL 的）。"""
    cp = class_file.cp
    key = _key_for(seed)

    # 1) 找出哪些 String 常量可以加密（其内容不超 BMP、密文无 NUL）
    encryptable: dict[int, int] = {}  # 原 String 常量索引 -> 密文 String 常量索引
    for i in range(1, len(cp.entries)):
        entry = cp.entries[i]
        if entry is None or entry[0] != CONSTANT_String:
            continue
        s = cp.utf8(entry[1])
        if not s:
            continue
        if any(ord(c) > 0xFFFF for c in s):
            continue
        cipher = _cipher(s, key)
        if "\x00" in cipher:
            continue
        encryptable[i] = cp.add(CONSTANT_String, cp.add_utf8(cipher))

    if not encryptable:
        return

    # 2) 注入解密方法（其 Methodref 随后续指令引用）
    helper = _build_decrypt_helper(class_file, seed)
    helper_mref = cp.add(
        CONSTANT_Methodref, class_file.this_class,
        cp.add(CONSTANT_NameAndType, helper.name_index, helper.descriptor_index),
    )
    class_file.methods.append(helper)

    # 3) 替换所有方法里的 ldc/ldc_w：明文 -> 密文 + invokestatic
    #    用快照遍历，避免把刚注入的 helper 方法也纳入（无害但更清晰）
    for method in list(class_file.methods):
        code = method.code()
        if code is None:
            continue
        new_insns: list[Instruction] = []
        for insn in code.instructions:
            if insn.opcode in (0x12, 0x13):  # ldc / ldc_w
                idx = int.from_bytes(insn.operand, "big")
                if idx in encryptable:
                    # old_offset=-1：占位哨兵，避免与原始指令（偏移>=0）在
                    # offset_mapping 中冲突，保证跳转/StackMapTable 重定位正确
                    new_insns.append(Instruction(0x13, struct.pack(">H", encryptable[idx]), old_offset=-1))
                    new_insns.append(Instruction(0xB8, struct.pack(">H", helper_mref), old_offset=-1))
                    continue
            new_insns.append(insn)
        code.instructions = new_insns
