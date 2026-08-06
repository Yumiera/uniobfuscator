# -*- coding: utf-8 -*-
"""生成可运行演示 jar 并混淆，验证修复后的字节码结构。

用法: python _demo_jar.py
产物: out/demo.jar (原始) 与 out/demo_obf.jar (混淆后)
验证: 有 JDK 的机器上执行 `java -jar demo.jar` / `java -jar demo_obf.jar`
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uniobfuscator.jvm.classfile import (
    CONSTANT_Class, CONSTANT_Fieldref, CONSTANT_Methodref,
    CONSTANT_NameAndType, CONSTANT_String, Attribute, ClassFile,
    CodeAttribute, ConstantPool, MethodInfo, parse_class_file,
)
from uniobfuscator.jvm.code import Instruction
from uniobfuscator.jvm.passes import encrypt_strings
from uniobfuscator.jvm.jar import obfuscate_jar


def build_hello_class() -> bytes:
    """构造 public class Hello { public static void main(String[] a){ System.out.println("hello uniobfuscator"); } }"""
    cp = [
        None,
        (1, "Hello"), (7, 1),
        (1, "java/lang/Object"), (7, 3),
        (1, "java/lang/System"), (7, 5),
        (1, "java/io/PrintStream"), (7, 7),
        (1, "out"), (1, "Ljava/io/PrintStream;"), (12, 9, 10), (9, 6, 11),
        (1, "main"), (1, "([Ljava/lang/String;)V"),
        (1, "println"), (1, "(Ljava/lang/String;)V"), (12, 15, 16), (10, 8, 17),
        (1, "hello uniobfuscator"), (8, 19),
        (1, "Code"), (1, "StackMapTable"),
    ]
    code = bytes([
        0xB2, 0x0C >> 8, 0x0C & 0xFF,   # getstatic System.out
        0x12, 0x13,                      # ldc "hello uniobfuscator" (String cp idx 19)
        0xB6, 0x11 >> 8, 0x11 & 0xFF,   # invokevirtual println
        0xB1,                            # return
    ])
    code_attr = CodeAttribute(2, 1, [Instruction(0xB2, bytes([0x0C >> 8, 0x0C & 0xFF])),
                                     Instruction(0x12, bytes([0x14])),
                                     Instruction(0xB6, bytes([0x12 >> 8, 0x12 & 0xFF])),
                                     Instruction(0xB1, b"")], [], [], 21)
    main = MethodInfo(0x0009, 13, 14, [code_attr])
    cf = ClassFile(ConstantPool(cp), 0x0021, 2, 4, [], [], [main], [], major_version=52)
    return cf.serialize()


def main() -> None:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out_dir, exist_ok=True)
    jar = os.path.join(out_dir, "demo.jar")
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nMain-Class: Hello\r\n\r\n")
        z.writestr("Hello.class", build_hello_class())

    obf = os.path.join(out_dir, "demo_obf.jar")
    obfuscate_jar(jar, obf, seed=7)

    # 结构验证：解密方法 + StackMapTable + 指令已改写
    with zipfile.ZipFile(obf) as z:
        data = z.read("Hello.class")
    cf = parse_class_file(data)
    helper = [m for m in cf.methods if cf.cp.utf8(m.name_index).startswith("_u")]
    print("混淆后 Hello.class:")
    print("  解密方法:", [cf.cp.utf8(m.name_index) for m in helper])
    code = helper[0].code()
    smt = [a for a in code.attributes if cf.cp.utf8(a.name_index) == "StackMapTable"]
    print("  helper 带 StackMapTable:", bool(smt), "| frames:", int.from_bytes(smt[0].payload[:2], "big") if smt else 0)
    main_code = cf.methods[0].code()
    opcodes = [i.opcode for i in main_code.instructions]
    print("  main 指令:", [hex(o) for o in opcodes], "（0x13=ldc_w, 0xB8=invokestatic, 0xB1=return）")
    # 原始与混淆后字节均无 parse 错误即结构合法
    print("产物: out/demo.jar / out/demo_obf.jar")
    print("在有 JDK 的机器上运行: java -jar out/demo_obf.jar  →  应输出 hello uniobfuscator")


if __name__ == "__main__":
    main()
