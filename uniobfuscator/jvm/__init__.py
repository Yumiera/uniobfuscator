# -*- coding: utf-8 -*-
"""JVM 字节码模块：JAR 容器 + .class 文件解析与字节码混淆。"""
from .jar import is_jar_file, obfuscate_jar
from .classfile import ClassFile, parse_class_file

__all__ = ["is_jar_file", "obfuscate_jar", "ClassFile", "parse_class_file"]
