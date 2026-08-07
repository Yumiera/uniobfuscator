# -*- coding: utf-8 -*-
"""JVM 字节码混淆 pass。

- remove_debug_info：删除 LineNumberTable / LocalVariableTable /
  LocalVariableTypeTable / SourceFile 等调试属性（无副作用，安全）。
- encrypt_strings：把 ldc/ldc_w 加载的字符串常量替换为"密文 +
  调用本类注入的静态解密方法"，并做指令重定位。
"""
from __future__ import annotations

import random
import struct

from .classfile import (
    CONSTANT_Class,
    CONSTANT_Integer,
    CONSTANT_Methodref,
    CONSTANT_NameAndType,
    CONSTANT_String,
    Attribute,
    ClassFile,
    CodeAttribute,
    MethodInfo,
)
from .code import (
    Instruction,
    _descriptor_effect,
    layout,
    parse_code,
    stack_depths,
    writes_local,
)

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


def _cipher(s: str, key: int) -> str:
    return "".join(chr(ord(c) ^ key) for c in s)


# ---------------------------------------------------------------------------
# 字符串加密
# ---------------------------------------------------------------------------

def _build_stack_map_table(cp, instructions,
                           append_types: tuple[bytes, ...] = ()) -> Attribute:
    """为带分支的指令流生成 StackMapTable 属性（JVMS 4.7.4）。

    class 版本 >= 51（Java 7+）采用 SplitVerifier：任何带分支/跳转的方法
    都必须携带 StackMapTable，否则 JVM 抛 VerifyError。

    append_types: 分支目标处相对隐式初始帧多出的局部变量类型
    （verification_type_info 原始字节）。为空时各帧 locals 与隐式帧相同
    （用 SAME 帧）；非空时首帧用 APPEND(k)，后续帧用 SAME。

    帧偏移计算（此前硬编码 delta 导致 ClassFormatError 的根因）：
      首帧 offset = offset_delta；
      后续帧 offset = 上一帧 offset + offset_delta + 1。
    """
    targets = sorted({t for insn in instructions for t in insn.jumps})
    if not targets:
        raise ValueError("无跳转目标，无法生成 StackMapTable")
    entries: list[bytes] = []
    prev = None
    first = True
    for off in targets:
        if prev is None:
            delta = off
        else:
            delta = off - prev - 1  # 规范：非首帧 offset = prev + delta + 1
        if first and append_types:
            if len(append_types) > 3:
                raise ValueError("append_types 最多 3 个")
            # APPEND(k)：frame_type = 251 + k（252..254）
            entries.append(bytes([251 + len(append_types)])
                           + struct.pack(">H", delta) + b"".join(append_types))
        elif delta <= 63:
            entries.append(bytes([delta]))  # SAME
        else:
            entries.append(bytes([251]) + struct.pack(">H", delta))  # SAME_EXTENDED
        prev = off
        first = False
    payload = struct.pack(">H", len(entries)) + b"".join(entries)
    return Attribute(cp.add_utf8("StackMapTable"), payload)


def _build_decrypt_helper(class_file: ClassFile, seed: int) -> MethodInfo:
    """构造静态解密方法：static String _uX(String s, int key)。

    实现：把 s 的每个 char 与 key 异或后组装为新 String 返回。
    key 由调用点以常量指令传入（随后会被算术混淆一并处理），
    实现每字符串独立密钥，避免单一固定密钥。class 版本 >= 51 时
    附带 StackMapTable（split verification 要求）。
    """
    cp = class_file.cp
    name = cp.add_utf8("_u" + format(seed, "x"))
    desc = cp.add_utf8("(Ljava/lang/String;I)Ljava/lang/String;")
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
    code_utf = cp.add_utf8("Code")
    # 局部变量：0=s, 1=key, 2=buf, 3=i
    # 各操作数与局部变量槽严格对应（此前 0x1C/0x2B 等把 s/key/buf/i
    # 的槽位写错，真实 JVM 报 VerifyError: Instruction type does not
    # match stack map）
    code_bytes = bytes([
        0x2A, 0xB6, length_mref >> 8, length_mref & 0xFF, 0xBC, 0x05,   # @0: buf=new char[s.length()]
        0x4D, 0x03, 0x3E,                                              # @6: astore_2(buf), iconst_0, istore_3(i=0)
        0x1D, 0x2C, 0xBE, 0xA2, 0x00, 0x14,                            # @9: while(i<buf.length)
        0x2C, 0x1D, 0x2A, 0x1D,                                        # @15: buf[i]=s.charAt(i)
        0xB6, charat_mref >> 8, charat_mref & 0xFF, 0x1B,              # @19:   ^ key
        0x82, 0x92, 0x55, 0x84, 0x03, 0x01,                            # @23: i2c; castore; i++
        0xA7, 0xFF, 0xEC,                                              # @29: goto 循环头（@9，delta=-20）
        0xBB, str_class >> 8, str_class & 0xFF, 0x59, 0x2C,            # @32: new String(buf)
        0xB7, init_mref >> 8, init_mref & 0xFF, 0xB0,                  # @37: <init>; return
    ])
    instructions = parse_code(code_bytes)
    code_attrs: list[Attribute] = []
    if class_file.major_version >= 51:
        char_arr_class = cp.add(CONSTANT_Class, cp.add_utf8("[C"))
        object_info = bytes([7]) + struct.pack(">H", char_arr_class)
        code_attrs.append(_build_stack_map_table(
            cp, instructions, append_types=(object_info, bytes([1]))))
    helper_code = CodeAttribute(4, 4, instructions, [], code_attrs, code_utf)
    return MethodInfo(0x000A, name, desc, [helper_code])


def encrypt_strings(class_file: ClassFile, seed: int) -> None:
    """加密所有能被 ldc 加载的字符串常量（跳过含非 BMP 字符或密文含 NUL 的）。

    每个字符串使用独立随机密钥（由 seed 派生的 rng 产生，可复现）；
    调用点改为 ldc_w 密文 + sipush key + invokestatic，密钥随后被
    算术混淆一并处理，不再存在单一固定密钥。
    """
    cp = class_file.cp
    rng = random.Random(seed ^ 0x5EED)

    # 1) 找出哪些 String 常量可以加密（其内容不超 BMP、密文无 NUL）
    encryptable: dict[int, tuple[int, int]] = {}  # 原 String idx -> (密文 idx, key)
    for i in range(1, len(cp.entries)):
        entry = cp.entries[i]
        if entry is None or entry[0] != CONSTANT_String:
            continue
        s = cp.utf8(entry[1])
        if not s:
            continue
        if any(ord(c) > 0xFFFF for c in s):
            continue
        key = rng.randrange(256)
        cipher = _cipher(s, key)
        if "\x00" in cipher:
            continue
        encryptable[i] = (cp.add(CONSTANT_String, cp.add_utf8(cipher)), key)

    if not encryptable:
        return

    # 2) 注入解密方法（其 Methodref 随后续指令引用）
    helper = _build_decrypt_helper(class_file, seed)
    helper_mref = cp.add(
        CONSTANT_Methodref, class_file.this_class,
        cp.add(CONSTANT_NameAndType, helper.name_index, helper.descriptor_index),
    )
    class_file.methods.append(helper)

    # 3) 替换所有方法里的 ldc/ldc_w：明文 -> 密文 + key + invokestatic
    #    用快照遍历，避免把刚注入的 helper 方法也纳入（无害但更清晰）
    for method in list(class_file.methods):
        code = method.code()
        if code is None:
            continue
        new_insns: list[Instruction] = []
        replaced = False
        for insn in code.instructions:
            if insn.opcode in (0x12, 0x13):  # ldc / ldc_w
                idx = int.from_bytes(insn.operand, "big")
                pair = encryptable.get(idx)
                if pair:
                    # old_offset=-1：占位哨兵，避免与原始指令（偏移>=0）在
                    # offset_mapping 中冲突，保证跳转/StackMapTable 重定位正确
                    cipher_idx, key = pair
                    new_insns.append(
                        Instruction(0x13, struct.pack(">H", cipher_idx), old_offset=-1))
                    new_insns.append(
                        Instruction(0x11, struct.pack(">h", key), old_offset=-1))
                    new_insns.append(
                        Instruction(0xB8, struct.pack(">H", helper_mref), old_offset=-1))
                    replaced = True
                    continue
            new_insns.append(insn)
        if replaced:
            code.instructions = new_insns
            # ldc 单次压栈 -> ldc_w + sipush + invokestatic：瞬时栈深 +1
            # （否则与算术混淆叠加后 max_stack 不足，真实 JVM 报
            #  VerifyError: Exceeded max stack size）
            code.max_stack += 1


# ---------------------------------------------------------------------------
# 元数据剥离
# ---------------------------------------------------------------------------

#: 可安全剥离的属性：泛型签名 / throws 异常表 / 运行期不可见注解。
#: 保留 RuntimeVisible*（RUNTIME 注解，反射与框架依赖）与
#: Code 内的 StackMapTable 等结构性属性。
_METADATA_STRIP_NAMES = frozenset({
    "Signature", "Exceptions",
    "RuntimeInvisibleAnnotations", "RuntimeInvisibleParameterAnnotations",
})


def strip_metadata(class_file: ClassFile) -> int:
    """剥离泛型签名、throws 声明与运行期不可见注解。返回剥离的属性数。

    泛型签名（Signature）暴露泛型结构；throws（Exceptions）暴露异常设计；
    不可见注解（CLASS 可见性）运行时反射不可见，仅被字节码处理工具使用。
    """
    cp = class_file.cp
    stripped = 0
    for attrs in (
        class_file.attributes,
        *[m.attributes for m in class_file.methods],
        *[f.attributes for f in class_file.fields],
    ):
        keep: list[Attribute] = []
        for a in attrs:
            if cp.utf8(a.name_index) in _METADATA_STRIP_NAMES:
                stripped += 1
            else:
                keep.append(a)
        attrs[:] = keep
    return stripped


# ---------------------------------------------------------------------------
# 整型常量算术混淆
# ---------------------------------------------------------------------------

def _integer_index(cp, value: int) -> int:
    """返回值为 value（有符号 32 位）的 Integer 常量索引；不存在则追加。"""
    unsigned = value & 0xFFFFFFFF
    for i in range(1, len(cp.entries)):
        entry = cp.entries[i]
        if entry and entry[0] == CONSTANT_Integer and entry[1] == unsigned:
            return i
    return cp.add(CONSTANT_Integer, unsigned)


def _push_int_insn(cp, value: int) -> Instruction:
    """生成压入 int 的指令：值在 sipush 范围用 sipush，否则 ldc/ldc_w。"""
    if -32768 <= value <= 32767:
        return Instruction(0x11, struct.pack(">h", value), old_offset=-1)
    idx = _integer_index(cp, value)
    if idx <= 0xFF:
        return Instruction(0x12, bytes([idx]), old_offset=-1)
    return Instruction(0x13, struct.pack(">H", idx), old_offset=-1)


def _const_int_value(insn: Instruction, cp) -> int | None:
    """bipush/sipush/ldc(Integer) 的常量值；其它指令返回 None。"""
    if insn.opcode == 0x10:  # bipush（有符号字节）
        return struct.unpack(">b", insn.operand)[0]
    if insn.opcode == 0x11:  # sipush
        return struct.unpack(">h", insn.operand)[0]
    if insn.opcode in (0x12, 0x13):  # ldc / ldc_w 且为 Integer 常量
        idx = int.from_bytes(insn.operand, "big")
        entry = cp[idx]
        if entry and entry[0] == CONSTANT_Integer:
            v = entry[1]  # 常量池按 u4 存储
            return v if v < 0x80000000 else v - 0x100000000
    return None


def arithmetic_obfuscate(class_file: ClassFile, seed: int) -> int:
    """整型常量算术混淆。

    把 bipush/sipush/ldc(Integer) 替换为两条常量压栈 + 一条 XOR/ADD
    表达式指令（32 位回绕求值与原值相等）。替换不改跳转目标与帧类型，
    指令偏移由 serialize 时自动重定位。返回替换的常量数。
    """
    cp = class_file.cp
    rng = random.Random(seed)
    total = 0
    for method in class_file.methods:
        code = method.code()
        if code is None:
            continue
        new_insns: list[Instruction] = []
        replaced = False
        for insn in code.instructions:
            value = _const_int_value(insn, cp)
            if value is None:
                new_insns.append(insn)
                continue
            a = rng.randrange(-32768, 32768)
            if rng.random() < 0.5:
                b, opcode = value ^ a, 0x82          # ixor
            else:
                b, opcode = (value - a) & 0xFFFFFFFF, 0x60  # iadd（回绕）
            new_insns.append(Instruction(0x11, struct.pack(">h", a), old_offset=-1))
            new_insns.append(_push_int_insn(cp, b))
            new_insns.append(Instruction(opcode, b"", old_offset=-1))
            replaced = True
            total += 1
        if replaced:
            code.instructions = new_insns
            code.max_stack += 1  # 表达式瞬时占 2 槽，比原常量指令多 1
    return total


# ---------------------------------------------------------------------------
# 死代码注入（不透明谓词）
# ---------------------------------------------------------------------------

def _has_smt(code: CodeAttribute, cp) -> bool:
    """Code 属性是否已携带 StackMapTable。"""
    smt = cp.find_utf8("StackMapTable")
    return any(a.name_index == smt for a in code.attributes)


def _random_junk(rng) -> list[Instruction]:
    """生成栈平衡的垃圾指令块（不写局部变量，verifier 可验证）。"""
    t = rng.randrange(3)
    if t == 0:
        x, y = rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)
        return [
            Instruction(0x11, struct.pack(">h", x), old_offset=-1),
            Instruction(0x11, struct.pack(">h", y), old_offset=-1),
            Instruction(0x82, b"", old_offset=-1),   # ixor
            Instruction(0x57, b"", old_offset=-1),   # pop
        ]
    if t == 1:
        x = rng.randrange(-32768, 32768)
        return [
            Instruction(0x11, struct.pack(">h", x), old_offset=-1),
            Instruction(0x57, b"", old_offset=-1),   # pop
        ]
    return [
        Instruction(0x04, b"", old_offset=-1),       # iconst_1
        Instruction(0x05, b"", old_offset=-1),       # iconst_2
        Instruction(0x68, b"", old_offset=-1),       # imul
        Instruction(0x57, b"", old_offset=-1),       # pop
    ]


def inject_dead_code(class_file: ClassFile, seed: int) -> int:
    """向"原本无分支"的方法注入不透明谓词 + 垃圾块。

    谓词 x*(x+1) 恒为偶数（x(x+1) mod 2 == 0），故 ifeq 恒跳真、垃圾块
    永不可达，但 verifier 仍会验证。注入块使用一个新增局部变量（Integer），
    major>=51 时生成对应 StackMapTable。返回注入的方法数。
    """
    cp = class_file.cp
    rng = random.Random(seed)
    injected = 0
    for method in class_file.methods:
        code = method.code()
        if code is None or code.exception_table:
            continue
        if _has_smt(code, cp):      # 原本有分支 -> 跳过
            continue
        # 新变量必须放在"方法入口帧 locals 末尾"，即参数（+this）之后第一个
        # 空闲槽：APPEND(k) 帧追加的位置正是入口 locals 末尾，若用 max_locals
        # 会与 APPEND 追加槽位错位，导致真实 JVM VerifyError（槽类型不一致）。
        is_static = bool(method.access_flags & 0x0008)
        param_slots = _descriptor_effect(cp.utf8(method.descriptor_index))[0]
        slot = (0 if is_static else 1) + param_slots
        if slot >= 255:             # 新增槽位需可普通寻址
            continue
        pred = [
            Instruction(0x03, old_offset=-1),                # iconst_0
            Instruction(0x36, bytes([slot]), old_offset=-1),  # istore slot
            Instruction(0x15, bytes([slot]), old_offset=-1),  # iload slot
            Instruction(0x15, bytes([slot]), old_offset=-1),  # iload slot
            Instruction(0x04, old_offset=-1),                # iconst_1
            Instruction(0x60, old_offset=-1),                # iadd
            Instruction(0x68, old_offset=-1),                # imul
            Instruction(0x05, old_offset=-1),                # iconst_2
            Instruction(0x70, old_offset=-1),                # irem
            Instruction(0x99, b"\x00\x00", old_offset=-1),   # ifeq -> 原方法体
        ]
        junk = _random_junk(rng) + [
            Instruction(0xA7, b"\x00\x00", old_offset=-1),   # goto -> 原方法体
        ]
        new_insns = pred + junk + list(code.instructions)
        layout(new_insns)
        target = new_insns[len(pred) + len(junk)].new_offset  # 原方法体首指令
        new_insns[len(pred) - 1].jumps = (target,)                       # ifeq
        new_insns[len(pred) + len(junk) - 1].jumps = (target,)           # goto
        for insn in new_insns:
            insn.old_offset = insn.new_offset  # identity 映射
        code.instructions = new_insns
        code.max_locals = max(code.max_locals, slot + 1)
        code.max_stack = max(code.max_stack, 3)
        if class_file.major_version >= 51:
            code.attributes.append(_build_stack_map_table(
                cp, new_insns, append_types=(bytes([1]),)))  # 追加 Integer
        injected += 1
    return injected


# ---------------------------------------------------------------------------
# 控制流打散（栈平衡垃圾块 + goto 绕行）
# ---------------------------------------------------------------------------

def scramble_control_flow(class_file: ClassFile, seed: int) -> int:
    """控制流打散：在方法体栈深为 0 的语句边界插入"goto 随机垃圾块 -> 跳回"。

    前提（保证 verifier 正确）：方法原本无分支/无 StackMapTable、无异常表、
    不写局部变量（帧可保持 SAME）。打散不改变原始指令的相对执行顺序，
    只是让代码路径被 goto 和随机垃圾块打断。返回处理的方法数。
    """
    cp = class_file.cp
    rng = random.Random(seed)
    processed = 0
    for method in class_file.methods:
        code = method.code()
        if code is None or code.exception_table:
            continue
        # <init> 的 locals[0] 在 super 构造调用前后由 uninitializedThis 变为
        # this；SAME 帧无法表示该变化，打散会触发真实 JVM VerifyError。
        if cp.utf8(method.name_index) == "<init>":
            continue
        if _has_smt(code, cp):
            continue
        insns = code.instructions
        if not insns:
            continue
        # 有分支 / 写局部变量 / 老式 jsr：SAME 帧前提被破坏，跳过
        if any(i.jumps for i in insns) or any(writes_local(i) for i in insns):
            continue
        if any(i.opcode in (0xA8, 0xC9, 0xA9) for i in insns):
            continue
        depths = stack_depths(insns, cp)
        # 切分点：执行前栈深为 0 的语句边界（跳过方法开头）
        cuts = [i for i in range(1, len(insns)) if depths[i] == 0]
        if len(cuts) < 2:
            continue
        new_insns = list(insns)
        blocks = []  # (goto_垃圾块, 垃圾块首指令, 跳回指令, 原切分点指令)
        for k, i in enumerate(cuts):
            g = Instruction(0xA7, b"\x00\x00", old_offset=-1)
            new_insns.insert(i + k, g)
            junk = _random_junk(rng)
            back = Instruction(0xA7, b"\x00\x00", old_offset=-1)
            blocks.append((g, junk[0], back, insns[i]))
            new_insns.extend(junk + [back])
        layout(new_insns)
        for g, junk_first, back, orig_insn in blocks:
            g.jumps = (junk_first.new_offset,)
            back.jumps = (orig_insn.new_offset,)
        for insn in new_insns:
            insn.old_offset = insn.new_offset  # identity 映射
        code.instructions = new_insns
        code.max_stack = max(code.max_stack, 2)
        if class_file.major_version >= 51:
            code.attributes.append(_build_stack_map_table(cp, new_insns))
        processed += 1
    return processed
