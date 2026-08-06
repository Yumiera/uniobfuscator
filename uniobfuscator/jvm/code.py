# -*- coding: utf-8 -*-
"""JVM 字节码指令流解析、序列化与跳转重定位。

指令以 Instruction 对象表示；跳转指令与 tableswitch/lookupswitch
记录跳转目标（旧偏移），重写（serialize）时按新的指令布局重新
计算相对偏移 / 绝对偏移，从而支持插入/替换指令。
"""
from __future__ import annotations

import struct

from .classfile import Attribute, CodeAttribute

#: 普通指令操作数长度（字节）。未列出的指令操作数为 0。
INSN_OPERAND_LEN = {
    0x10: 1, 0x11: 2, 0x12: 1, 0x13: 2, 0x14: 2,          # bipush/sipush/ldc/ldc_w/ldc2_w
    0x15: 1, 0x16: 1, 0x17: 1, 0x18: 1, 0x19: 1,          # *load
    0x36: 1, 0x37: 1, 0x38: 1, 0x39: 1, 0x3A: 1,          # *store
    0x84: 2,                                               # iinc
    0x99: 2, 0x9A: 2, 0x9B: 2, 0x9C: 2, 0x9D: 2, 0x9E: 2, # if*
    0x9F: 2, 0xA0: 2, 0xA1: 2, 0xA2: 2, 0xA3: 2, 0xA4: 2, 0xA5: 2, 0xA6: 2,
    0xA7: 2, 0xA8: 2,                                      # goto / jsr
    0xA9: 1,                                               # ret
    0xB2: 2, 0xB3: 2, 0xB4: 2, 0xB5: 2,                   # getstatic/putstatic/getfield/putfield
    0xB6: 2, 0xB7: 2, 0xB8: 2,                            # invoke*
    0xB9: 4, 0xBA: 4,                                     # invokeinterface / invokedynamic
    0xBB: 2, 0xBC: 1, 0xBD: 2,                            # new / newarray / anewarray
    0xC0: 2, 0xC1: 2,                                     # checkcast / instanceof
    0xC5: 3,                                               # multianewarray
    0xC6: 2, 0xC7: 2,                                     # ifnull / ifnonnull
    0xC8: 4, 0xC9: 4,                                     # goto_w / jsr_w
}

TABLESWITCH = 0xAA
LOOKUPSWITCH = 0xAB

#: 相对跳转指令（2 字节偏移）：目标 = 指令起始 + signed16
REL16_JUMP = frozenset(range(0x99, 0xA9)) | {0xC6, 0xC7}
#: 相对跳转指令（4 字节偏移）
REL32_JUMP = {0xC8, 0xC9}
#: switch 指令
SWITCH_OPS = {TABLESWITCH, LOOKUPSWITCH}


class Instruction:
    __slots__ = ("opcode", "operand", "old_offset", "new_offset", "jumps", "padding")

    def __init__(self, opcode: int, operand: bytes = b"", old_offset: int = 0,
                 jumps: tuple = ()):
        self.opcode = opcode
        self.operand = operand          # 普通指令：原始操作数字节；switch：结构化数据
        self.old_offset = old_offset
        self.new_offset = 0             # 由 layout() 计算
        self.jumps = tuple(jumps)       # 跳转目标（旧偏移）；switch 含 default
        self.padding = 0                # switch 指令的重写 padding

    def operand_len(self) -> int:
        if self.opcode == TABLESWITCH:
            low, high, offsets = self.operand
            return 12 + 4 * len(offsets)
        if self.opcode == LOOKUPSWITCH:
            (pairs,) = self.operand
            return 8 + 8 * len(pairs)
        return len(self.operand)


def parse_code(bytecode: bytes) -> list[Instruction]:
    """把 Code 属性字节码解析为指令列表。"""
    insns: list[Instruction] = []
    off = 0
    n = len(bytecode)
    while off < n:
        op = bytecode[off]
        insn = Instruction(op, old_offset=off)
        if op in SWITCH_OPS:
            pad = (4 - ((off + 1) % 4)) % 4
            p = off + 1 + pad
            if op == TABLESWITCH:
                default, low, high = struct.unpack_from(">iii", bytecode, p)
                cnt = high - low + 1
                if cnt < 0:
                    raise ValueError("tableswitch low > high")
                offsets = struct.unpack_from(f">{cnt}i", bytecode, p + 12)
                insn.operand = (low, high, list(offsets))
                insn.jumps = (default, *offsets)
                off = p + 12 + 4 * cnt
            else:  # LOOKUPSWITCH
                default, npairs = struct.unpack_from(">ii", bytecode, p)
                if npairs < 0:
                    raise ValueError("lookupswitch 负的 npairs")
                matches: list[int] = []
                jumps = [default]
                for i in range(npairs):
                    match, offv = struct.unpack_from(">ii", bytecode, p + 8 + 8 * i)
                    matches.append(match)
                    jumps.append(offv)
                insn.operand = (matches,)
                insn.jumps = tuple(jumps)
                off = p + 8 + 8 * npairs
        elif op == 0xC4:  # wide
            sub = bytecode[off + 1]
            if sub == 0x84:  # iinc 变体：2 字节 index + 2 字节 const
                insn.operand = bytes(bytecode[off + 1:off + 5])
                off += 5
            else:
                insn.operand = bytes(bytecode[off + 1:off + 4])
                off += 4
        else:
            length = INSN_OPERAND_LEN.get(op, 0)
            insn.operand = bytes(bytecode[off + 1:off + 1 + length])
            if op in REL16_JUMP:
                delta = struct.unpack_from(">h", insn.operand, 0)[0]
                insn.jumps = (off + delta,)
            elif op in REL32_JUMP:
                delta = struct.unpack_from(">i", insn.operand, 0)[0]
                insn.jumps = (off + delta,)
            off += 1 + length
        insns.append(insn)
    if off != n:
        raise ValueError("字节码长度与指令解析不一致")
    return insns


def layout(insns: list[Instruction]) -> None:
    """计算每条指令的新偏移与 switch padding。"""
    offset = 0
    for insn in insns:
        insn.new_offset = offset
        if insn.opcode in SWITCH_OPS:
            insn.padding = (4 - ((offset + 1) % 4)) % 4
            offset += 1 + insn.padding + insn.operand_len()
        else:
            offset += 1 + insn.operand_len()


def offset_mapping(insns: list[Instruction]) -> dict[int, int]:
    """旧偏移 -> 新偏移 映射（需先 layout）。"""
    return {insn.old_offset: insn.new_offset for insn in insns}


def nearest_new_offset(mapping: dict[int, int], old: int) -> int:
    """异常表/跳转目标所在旧偏移的新偏移；无精确匹配时取不大于它的最近指令。"""
    if old in mapping:
        return mapping[old]
    candidates = [o for o in mapping if o <= old]
    if not candidates:
        return 0
    return mapping[max(candidates)]


def _vt_len(buf: memoryview, pos: int) -> int:
    """verification_type_info 长度：Object(7)/Uninitialized(8) 3 字节，其余 1 字节。"""
    return 3 if buf[pos] in (7, 8) else 1


def remap_stack_map_table(payload: bytes, mapping: dict[int, int]) -> bytes:
    """重定位 StackMapTable 各帧的偏移（旧偏移 -> 新偏移，JVMS 4.7.4）。

    在指令流插入/删除后调用。帧的验证类型信息不变（插入的 ldc_w +
    invokestatic 栈效果为零，不改变分支目标处 locals），只需按新布局
    重新编码各帧 offset_delta：
      首帧 offset = offset_delta；
      后续帧 offset = 上一帧 offset + offset_delta + 1。
    偏移无变化（identity mapping）时输出与输入逐字节一致。
    """
    def new_of(old: int) -> int:
        return mapping.get(old, nearest_new_offset(mapping, old))

    def skip_types(pos: int, count: int) -> int:
        for _ in range(count):
            pos += _vt_len(buf, pos)
        return pos

    buf = memoryview(payload)
    (count,) = struct.unpack_from(">H", buf, 0)
    pos = 2
    out = bytearray(struct.pack(">H", count))
    old_prev = None  # 上一帧绝对偏移（旧布局）
    new_prev = None  # 上一帧绝对偏移（新布局）
    for _ in range(count):
        ft = buf[pos]
        pos += 1
        if ft <= 63:  # SAME：delta 编码在 frame_type
            delta, ntypes = ft, 0
        elif ft <= 127:  # SAME_LOCALS_1_STACK_ITEM
            delta, ntypes = ft - 64, 1
        elif ft == 247:  # SAME_LOCALS_1_STACK_ITEM_EXTENDED
            delta = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            ntypes = 1
        elif 248 <= ft <= 250:  # CHOP
            delta = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            ntypes = 0
        elif ft == 251:  # SAME_EXTENDED
            delta = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            ntypes = 0
        elif 252 <= ft <= 254:  # APPEND
            delta = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            ntypes = ft - 251
        elif ft == 255:  # FULL：u2 delta, u2 nlocals, locals, u2 nstack, stack
            delta = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            nlocals = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            lstart = pos
            pos = skip_types(pos, nlocals)
            local_types = bytes(buf[lstart:pos])
            nstack = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            sstart = pos
            pos = skip_types(pos, nstack)
            stack_types = bytes(buf[sstart:pos])
            ntypes = 0  # FULL 的 types 已单独捕获
        else:
            raise ValueError(f"无效的 StackMapTable frame_type: {ft}")
        if ft != 255:
            tstart = pos
            pos = skip_types(pos, ntypes)
            types_blob = bytes(buf[tstart:pos])

        old_abs = delta if old_prev is None else old_prev + delta + 1
        old_prev = old_abs
        new_abs = new_of(old_abs)
        nd = new_abs if new_prev is None else new_abs - new_prev - 1
        new_prev = new_abs

        if ft <= 63:  # SAME
            out += bytes([nd]) if nd <= 63 else b"\xfb" + struct.pack(">H", nd)
        elif ft <= 127:  # SAME_LOCALS_1_STACK_ITEM
            if nd <= 63:
                out += bytes([64 + nd]) + types_blob
            else:
                out += b"\xf7" + struct.pack(">H", nd) + types_blob
        elif ft == 247:
            out += b"\xf7" + struct.pack(">H", nd) + types_blob
        elif 248 <= ft <= 250:  # CHOP
            out += bytes([ft]) + struct.pack(">H", nd)
        elif ft == 251:  # SAME_EXTENDED
            out += b"\xfb" + struct.pack(">H", nd)
        elif 252 <= ft <= 254:  # APPEND
            out += bytes([ft]) + struct.pack(">H", nd) + types_blob
        else:  # FULL
            out += (b"\xff" + struct.pack(">H", nd) + struct.pack(">H", nlocals)
                    + local_types + struct.pack(">H", nstack) + stack_types)
    return bytes(out)


def serialize_instructions(insns: list[Instruction]) -> bytes:
    """按新布局序列化指令流（重写所有跳转偏移）。"""
    layout(insns)
    mapping = offset_mapping(insns)

    def target_new(old: int) -> int:
        return nearest_new_offset(mapping, old)

    out = bytearray()
    for insn in insns:
        out.append(insn.opcode)
        if insn.opcode == TABLESWITCH:
            low, high, offsets = insn.operand
            out += b"\x00" * insn.padding
            out += struct.pack(">iii", target_new(insn.jumps[0]), low, high)
            for j in insn.jumps[1:]:
                out += struct.pack(">i", target_new(j))
        elif insn.opcode == LOOKUPSWITCH:
            (pairs,) = insn.operand
            out += b"\x00" * insn.padding
            out += struct.pack(">ii", target_new(insn.jumps[0]), len(pairs))
            for (match, _off), j in zip(pairs, insn.jumps[1:]):
                out += struct.pack(">ii", match, target_new(j))
        elif insn.opcode in REL16_JUMP:
            delta = target_new(insn.jumps[0]) - insn.new_offset
            out += struct.pack(">h", delta)
        elif insn.opcode in REL32_JUMP:
            delta = target_new(insn.jumps[0]) - insn.new_offset
            out += struct.pack(">i", delta)
        else:
            out += insn.operand
    return bytes(out)


def parse_code_attr(name_index: int, payload: bytes) -> CodeAttribute:
    """解析 Code 属性的 payload（max_stack 之后的部分）。"""
    max_stack, max_locals = struct.unpack_from(">HH", payload, 0)
    (code_len,) = struct.unpack_from(">I", payload, 4)
    code = payload[8:8 + code_len]
    instructions = parse_code(code)
    pos = 8 + code_len
    (exc_len,) = struct.unpack_from(">H", payload, pos)
    pos += 2
    exc_table = []
    for _ in range(exc_len):
        start, end, handler, catch = struct.unpack_from(">HHHH", payload, pos)
        pos += 8
        exc_table.append((start, end, handler, catch))
    (attr_count,) = struct.unpack_from(">H", payload, pos)
    pos += 2
    attributes = []
    for _ in range(attr_count):
        a_name, a_len = struct.unpack_from(">HI", payload, pos)
        pos += 6
        attributes.append(Attribute(a_name, bytes(payload[pos:pos + a_len])))
        pos += a_len
    return CodeAttribute(max_stack, max_locals, instructions, exc_table,
                         attributes, name_index)
