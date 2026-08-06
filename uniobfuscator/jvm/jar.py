# -*- coding: utf-8 -*-
"""JAR 打包/重打包：解包 -> 逐 .class 混淆 -> 重打包（保留元数据）。"""
from __future__ import annotations

import os
import zipfile

from .classfile import parse_class_file
from .passes import encrypt_strings, remove_debug_info

_SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")


def obfuscate_jar(src_jar: str, dst_jar: str, seed: int = 0,
                  strings: bool = True) -> dict:
    """混淆 JAR 文件。

    返回统计信息：{"class": 处理数, "skipped": 跳过的 class 数,
    "signature": 检测到签名文件数, "warnings": [警告列表]}。
    """
    if os.path.abspath(src_jar) == os.path.abspath(dst_jar):
        raise ValueError("输出 JAR 不能与输入 JAR 相同（避免覆盖）")
    try:
        zin = zipfile.ZipFile(src_jar, "r")
    except zipfile.BadZipFile as e:
        raise ValueError(f"不是合法的 JAR/ZIP 文件: {e}") from e

    stats: dict = {"class": 0, "skipped": 0, "signature": 0, "warnings": []}
    with zin, zipfile.ZipFile(dst_jar, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            data = zin.read(info.filename)
            if name.endswith(".class"):
                try:
                    cf = parse_class_file(data)
                    remove_debug_info(cf)
                    if strings:
                        encrypt_strings(cf, seed)
                    zout.writestr(info, cf.serialize())
                    stats["class"] += 1
                except ValueError as e:
                    stats["skipped"] += 1
                    stats["warnings"].append(f"跳过 {name}: {e}")
                    zout.writestr(info, data)
            else:
                base = name.upper()
                if any(base.endswith(suf) for suf in _SIGNATURE_SUFFIXES):
                    stats["signature"] += 1
                    if not any("签名" in w for w in stats["warnings"]):
                        stats["warnings"].append(
                            "检测到 JAR 签名文件（META-INF/*.SF 等），"
                            "混淆后签名将失效，需重新签名"
                        )
                zout.writestr(info, data)
    return stats


def is_jar_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".jar"
