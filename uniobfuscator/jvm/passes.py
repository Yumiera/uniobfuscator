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

def _build_decrypt_helper(class_file: ClassFile, seed: int) -> MethodInfo:
    """构造静态解密方法：static String d_xxx(String s)。

    实现：把 s 的每个 char 与 key 异或后组装为新 String 返回。
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
        0x2A, 0xB6, length_mref >> 8, length_mref & 0xFF, 0xBC, 0x07,   # buf=new char[s.length()]
        0x4C, 0x03, 0x3D,                                              # buf, i=0
        0x1C, 0x2B, 0xBE, 0xA2, 0x00, 0x15,                            # while(i<buf.length)
        0x2B, 0x1C, 0x2A, 0x1C,                                        # buf[i]=s.charAt(i)
        0xB6, charat_mref >> 8, charat_mref & 0xFF, 0x10, key,         #   ^ key
        0x82, 0x92, 0x55, 0x84, 0x02, 0x01, 0xA7, 0xFF, 0xEB,          #   ; i++
        0xBB, str_class >> 8, str_class & 0xFF, 0x59, 0x2B,            # new String(buf)
        0xB7, init_mref >> 8, init_mref & 0xFF, 0xB0,                  # <init>; return
    ])
    helper_code = CodeAttribute(4, 3, parse_code(code_bytes), [], [], code_utf)
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
                    new_insns.append(Instruction(0x13, struct.pack(">H", encryptable[idx])))
                    new_insns.append(Instruction(0xB8, struct.pack(">H", helper_mref)))
                    continue
            new_insns.append(insn)
        code.instructions = new_insns
