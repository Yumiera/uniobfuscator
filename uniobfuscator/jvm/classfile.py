# -*- coding: utf-8 -*-
"""JVM class 文件解析与序列化（纯 Python，大端字节序）。

实现 JVM 规范 (JVMS 4) 中 class 文件的核心结构：常量池、
access_flags、this/super、interfaces、fields、methods、attributes。
Code 属性内的字节码指令流由 code.py 负责。

结构在内存中是可修改的：混淆 pass 直接改对象，最后 serialize() 写回。
"""
from __future__ import annotations

import struct

# 常量池 tag
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_InterfaceMethodref = 11
CONSTANT_NameAndType = 12
CONSTANT_MethodHandle = 15
CONSTANT_MethodType = 16
CONSTANT_Dynamic = 17
CONSTANT_InvokeDynamic = 18
CONSTANT_Module = 19
CONSTANT_Package = 20

# 多槽常量（Long/Double 各占两个常量池槽位）
_TWO_SLOT = {CONSTANT_Long, CONSTANT_Double}


class ConstantPool:
    """常量池。索引从 1 开始；Long/Double 占用两个槽位（后一个槽为 None）。"""

    def __init__(self, entries: list | None = None):
        # entries[0] 占位不用；entries[i] = (tag, data) 或 None（Long/Double 的后续槽）
        self.entries = entries if entries is not None else [None]

    # ---- 查询 ----
    def __len__(self) -> int:
        return len(self.entries) - 1

    def __getitem__(self, index: int) -> tuple | None:
        return self.entries[index]

    def tag(self, index: int) -> int:
        return self.entries[index][0]

    def utf8(self, index: int) -> str:
        return self.entries[index][1]

    def find_utf8(self, value: str) -> int:
        """返回内容为 value 的 Utf8 常量索引，不存在返回 0。"""
        for i in range(1, len(self.entries)):
            entry = self.entries[i]
            if entry and entry[0] == CONSTANT_Utf8 and entry[1] == value:
                return i
        return 0

    # ---- 追加 ----
    def add_utf8(self, value: str) -> int:
        idx = self.find_utf8(value)
        if idx:
            return idx
        self.entries.append((CONSTANT_Utf8, value))
        return len(self.entries) - 1

    def add(self, tag: int, *data) -> int:
        """追加常量，返回其索引。Long/Double 自动补占位槽。"""
        self.entries.append((tag, *data))
        index = len(self.entries) - 1
        if tag in _TWO_SLOT:
            self.entries.append(None)
        return index

    # ---- 引用统计 ----
    def string_utf8_refs(self) -> dict[int, int]:
        """统计每个 Utf8 被哪些常量引用。

        返回 {utf8_index: 引用它的常量索引列表}（仅统计指向 Utf8 的常量）。
        """
        refs: dict[int, list[int]] = {}
        for i in range(1, len(self.entries)):
            entry = self.entries[i]
            if not entry:
                continue
            tag = entry[0]
            if tag == CONSTANT_Utf8:
                continue  # Utf8 不指向 Utf8
            if tag == CONSTANT_String:
                target = entry[1]
            elif tag in (CONSTANT_Class, CONSTANT_Module, CONSTANT_Package):
                target = entry[1]
            elif tag in (CONSTANT_Fieldref, CONSTANT_Methodref, CONSTANT_InterfaceMethodref):
                continue  # 指向 NameAndType
            elif tag == CONSTANT_NameAndType:
                continue  # 指向两个 Utf8，单独处理
            elif tag == CONSTANT_MethodHandle:
                continue
            elif tag == CONSTANT_MethodType:
                target = entry[1]
            elif tag in (CONSTANT_Dynamic, CONSTANT_InvokeDynamic):
                continue  # 指向 NameAndType
            else:
                continue
            refs.setdefault(target, []).append(i)
        return refs

    def name_and_type_refs(self) -> dict[int, tuple[int, int]]:
        """{name_and_type_index: (name_utf8_index, descriptor_utf8_index)}。"""
        out = {}
        for i in range(1, len(self.entries)):
            entry = self.entries[i]
            if entry and entry[0] == CONSTANT_NameAndType:
                out[i] = (entry[1], entry[2])
        return out


class Attribute:
    """原始字节属性（非 Code）。name_index 指向 Utf8 常量。"""

    def __init__(self, name_index: int, payload: bytes):
        self.name_index = name_index
        self.payload = payload

    def serialize(self) -> bytes:
        return struct.pack(">HI", self.name_index, len(self.payload)) + self.payload


class MethodInfo:
    def __init__(self, access_flags: int, name_index: int, descriptor_index: int,
                 attributes: list[Attribute]):
        self.access_flags = access_flags
        self.name_index = name_index
        self.descriptor_index = descriptor_index
        self.attributes = attributes  # 含 Code（Code 由 code.py 解析为 CodeAttribute）

    def code(self):
        """返回 Code 属性对象（CodeAttribute），无则 None。"""
        for attr in self.attributes:
            if isinstance(attr, CodeAttribute):
                return attr
        return None

    def serialize(self, cp: ConstantPool) -> bytes:
        out = struct.pack(">HHH", self.access_flags, self.name_index, self.descriptor_index)
        out += struct.pack(">H", len(self.attributes))
        for attr in self.attributes:
            out += attr.serialize()
        return out


class FieldInfo:
    def __init__(self, access_flags: int, name_index: int, descriptor_index: int,
                 attributes: list[Attribute]):
        self.access_flags = access_flags
        self.name_index = name_index
        self.descriptor_index = descriptor_index
        self.attributes = attributes

    def serialize(self, cp: ConstantPool) -> bytes:
        out = struct.pack(">HHH", self.access_flags, self.name_index, self.descriptor_index)
        out += struct.pack(">H", len(self.attributes))
        for attr in self.attributes:
            out += attr.serialize()
        return out


class CodeAttribute:
    """Code 属性的结构化表示。instructions 由 code.py 解析。"""

    def __init__(self, max_stack: int, max_locals: int, instructions,
                 exception_table: list[tuple[int, int, int, int]],
                 attributes: list[Attribute], name_index: int):
        self.name_index = name_index
        self.max_stack = max_stack
        self.max_locals = max_locals
        self.instructions = instructions  # list[Instruction]
        self.exception_table = exception_table  # (start_pc, end_pc, handler_pc, catch_type)
        self.attributes = attributes

    def serialize(self) -> bytes:
        from .code import layout, nearest_new_offset, offset_mapping, serialize_instructions
        layout(self.instructions)
        mapping = offset_mapping(self.instructions)
        code = serialize_instructions(self.instructions)
        out = struct.pack(">HH", self.max_stack, self.max_locals)
        out += struct.pack(">I", len(code)) + code
        out += struct.pack(">H", len(self.exception_table))
        for start, end, handler, catch in self.exception_table:
            out += struct.pack(
                ">HHHH",
                nearest_new_offset(mapping, start),
                nearest_new_offset(mapping, end),
                nearest_new_offset(mapping, handler),
                catch,
            )
        out += struct.pack(">H", len(self.attributes))
        for attr in self.attributes:
            out += attr.serialize()
        return struct.pack(">H", self.name_index) + struct.pack(">I", len(out)) + out


class ClassFile:
    """JVM class 文件。"""

    def __init__(self, cp: ConstantPool, access_flags: int, this_class: int,
                 super_class: int, interfaces: list[int], fields: list[FieldInfo],
                 methods: list[MethodInfo], attributes: list[Attribute],
                 minor_version: int = 0, major_version: int = 52):
        self.cp = cp
        self.minor_version = minor_version
        self.major_version = major_version
        self.access_flags = access_flags
        self.this_class = this_class
        self.super_class = super_class
        self.interfaces = interfaces
        self.fields = fields
        self.methods = methods
        self.attributes = attributes

    # ---- 便捷 ----
    def this_name(self) -> str:
        return self.cp.utf8(self.cp[self.this_class][1])

    def serialize(self) -> bytes:
        out = struct.pack(">IHH", 0xCAFEBABE, self.minor_version, self.major_version)
        out += struct.pack(">H", len(self.cp.entries))
        for entry in self.cp.entries[1:]:
            if entry is None:
                continue  # Long/Double 占位槽
            tag, payload = entry[0], entry[1:]
            out += struct.pack(">B", tag)
            if tag == CONSTANT_Utf8:
                data = payload[0].encode("utf-8")
                out += struct.pack(">H", len(data)) + data
            elif tag in (CONSTANT_Integer, CONSTANT_Float):
                out += struct.pack(">I", payload[0])
            elif tag in (CONSTANT_Long, CONSTANT_Double):
                out += struct.pack(">Q", payload[0])
            elif tag in (CONSTANT_Class, CONSTANT_String, CONSTANT_Module, CONSTANT_Package,
                         CONSTANT_MethodType):
                out += struct.pack(">H", payload[0])
            elif tag in (CONSTANT_Fieldref, CONSTANT_Methodref, CONSTANT_InterfaceMethodref,
                         CONSTANT_NameAndType, CONSTANT_Dynamic, CONSTANT_InvokeDynamic):
                out += struct.pack(">HH", payload[0], payload[1])
            elif tag == CONSTANT_MethodHandle:
                out += struct.pack(">BH", payload[0], payload[1])
            else:
                raise ValueError(f"不支持的常量池 tag: {tag}")
        out += struct.pack(">HHH", self.access_flags, self.this_class, self.super_class)
        out += struct.pack(">H", len(self.interfaces))
        for i in self.interfaces:
            out += struct.pack(">H", i)
        out += struct.pack(">H", len(self.fields))
        for f in self.fields:
            out += f.serialize(self.cp)
        out += struct.pack(">H", len(self.methods))
        for m in self.methods:
            out += m.serialize(self.cp)
        out += struct.pack(">H", len(self.attributes))
        for a in self.attributes:
            out += a.serialize()
        return out


def _read_attributes(buf: memoryview, pos: int, count: int):
    """读取 count 个属性；Code 属性解析为 CodeAttribute。"""
    from .code import parse_code_attr
    attrs = []
    for _ in range(count):
        name_index, length = struct.unpack_from(">HI", buf, pos)
        pos += 6
        payload = bytes(buf[pos:pos + length])
        pos += length
        if name_index == _code_name_index_hint:
            attrs.append(parse_code_attr(name_index, payload))
        else:
            attrs.append(Attribute(name_index, payload))
    return attrs, pos


_code_name_index_hint = None  # 由 parse 设置：Code 属性的 Utf8 索引


def parse_class_file(data: bytes) -> ClassFile:
    """解析 class 文件字节。"""
    global _code_name_index_hint
    buf = memoryview(data)
    magic, minor, major = struct.unpack_from(">IHH", buf, 0)
    if magic != 0xCAFEBABE:
        raise ValueError("不是合法的 class 文件（magic 错误）")
    pos = 8

    cp_count = struct.unpack_from(">H", buf, pos)[0]
    pos += 2
    entries: list = [None]
    for _ in range(cp_count - 1):
        tag = buf[pos]
        pos += 1
        if tag == CONSTANT_Utf8:
            (length,) = struct.unpack_from(">H", buf, pos)
            pos += 2
            raw = bytes(buf[pos:pos + length])
            pos += length
            # modified UTF-8 近似处理：直接按 utf-8 解码；异常时按 latin-1 兜底
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError:
                value = raw.decode("latin-1")
            entries.append((tag, value))
        elif tag in (CONSTANT_Integer, CONSTANT_Float):
            entries.append((tag, struct.unpack_from(">I", buf, pos)[0]))
            pos += 4
        elif tag in (CONSTANT_Long, CONSTANT_Double):
            entries.append((tag, struct.unpack_from(">Q", buf, pos)[0]))
            entries.append(None)  # 占位槽
            pos += 8
        elif tag in (CONSTANT_Class, CONSTANT_String, CONSTANT_Module, CONSTANT_Package,
                     CONSTANT_MethodType):
            entries.append((tag, struct.unpack_from(">H", buf, pos)[0]))
            pos += 2
        elif tag in (CONSTANT_Fieldref, CONSTANT_Methodref, CONSTANT_InterfaceMethodref,
                     CONSTANT_NameAndType, CONSTANT_Dynamic, CONSTANT_InvokeDynamic):
            entries.append((tag, struct.unpack_from(">H", buf, pos)[0],
                            struct.unpack_from(">H", buf, pos + 2)[0]))
            pos += 4
        elif tag == CONSTANT_MethodHandle:
            entries.append((tag, buf[pos], struct.unpack_from(">H", buf, pos + 1)[0]))
            pos += 3
        else:
            raise ValueError(f"不支持的常量池 tag: {tag} @{pos}")
    cp = ConstantPool(entries)

    _code_name_index_hint = cp.find_utf8("Code")

    access_flags, this_class, super_class = struct.unpack_from(">HHH", buf, pos)
    pos += 6
    (ic,) = struct.unpack_from(">H", buf, pos)
    pos += 2
    interfaces = []
    for _ in range(ic):
        interfaces.append(struct.unpack_from(">H", buf, pos)[0])
        pos += 2

    (fc,) = struct.unpack_from(">H", buf, pos)
    pos += 2
    fields = []
    for _ in range(fc):
        af, ni, di, ac = struct.unpack_from(">HHHH", buf, pos)
        pos += 8
        attrs, pos = _read_attributes(buf, pos, ac)
        fields.append(FieldInfo(af, ni, di, attrs))

    (mc,) = struct.unpack_from(">H", buf, pos)
    pos += 2
    methods = []
    for _ in range(mc):
        af, ni, di, ac = struct.unpack_from(">HHHH", buf, pos)
        pos += 8
        attrs, pos = _read_attributes(buf, pos, ac)
        methods.append(MethodInfo(af, ni, di, attrs))

    (ac,) = struct.unpack_from(">H", buf, pos)
    pos += 2
    class_attrs, pos = _read_attributes(buf, pos, ac)

    return ClassFile(cp, access_flags, this_class, super_class,
                     interfaces, fields, methods, class_attrs,
                     minor_version=minor, major_version=major)
