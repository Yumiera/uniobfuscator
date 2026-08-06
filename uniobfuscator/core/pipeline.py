# -*- coding: utf-8 -*-
"""流水线编排：parse -> 依次执行 Pass -> 应用编辑 -> 输出。"""
from __future__ import annotations

import os

from .passes import (
    ArithmeticPass,
    DeadCodePass,
    ObfuscationPass,
    RenamePass,
    StringEncryptPass,
)
from ..languages.base import LanguageAdapter

DEFAULT_OPTIONS = {
    "rename": True,
    "strings": True,
    "dead_code": True,
    "arithmetic": True,
    "seed": 0,
}


def build_passes(options: dict) -> list[ObfuscationPass]:
    seed = int(options.get("seed", DEFAULT_OPTIONS["seed"]))
    passes: list[ObfuscationPass] = []
    if options.get("rename", DEFAULT_OPTIONS["rename"]):
        passes.append(RenamePass(seed))
    if options.get("strings", DEFAULT_OPTIONS["strings"]):
        passes.append(StringEncryptPass(seed))
    if options.get("dead_code", DEFAULT_OPTIONS["dead_code"]):
        passes.append(DeadCodePass(seed))
    if options.get("arithmetic", DEFAULT_OPTIONS["arithmetic"]):
        passes.append(ArithmeticPass(seed))
    return passes


def obfuscate(source: str, adapter: LanguageAdapter, options: dict | None = None) -> str:
    """对 source 执行混淆，返回混淆后源码。"""
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    src = adapter.parse(source)
    if src.has_error:
        raise ValueError(f"[{adapter.name}] 源码解析失败，存在语法错误")
    for p in build_passes(opts):
        p.run(src, adapter)
    return src.apply()


def obfuscate_file(
    src_path: str, dst_path: str, adapter: LanguageAdapter,
    options: dict | None = None,
) -> str:
    """混淆单个文件并写入 dst_path（自动创建父目录），返回混淆后源码。"""
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    result = obfuscate(source, adapter, options)
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w", encoding="utf-8", newline="") as f:
        f.write(result)
    return result
