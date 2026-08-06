# -*- coding: utf-8 -*-
"""语言注册表：按名称/扩展名查找适配器。"""
from __future__ import annotations

from .base import LanguageAdapter
from .java_lang import JavaAdapter
from .javascript_lang import JavaScriptAdapter
from .python_lang import PythonAdapter

ADAPTERS: list[LanguageAdapter] = [
    PythonAdapter(),
    JavaScriptAdapter(),
    JavaAdapter(),
]

_BY_NAME = {a.name: a for a in ADAPTERS}
_BY_EXT = {ext: a for a in ADAPTERS for ext in a.extensions}

# 全部支持的扩展名（目录模式按此收集源文件）
SUPPORTED_EXTENSIONS = frozenset(_BY_EXT)


def get_adapter(name: str) -> LanguageAdapter:
    if name in _BY_NAME:
        return _BY_NAME[name]
    raise KeyError(f"不支持的语言: {name}，可选: {', '.join(_BY_NAME)}")


def adapter_for_filename(filename: str) -> LanguageAdapter:
    import os

    ext = os.path.splitext(filename)[1].lower()
    if ext in _BY_EXT:
        return _BY_EXT[ext]
    raise KeyError(f"无法根据扩展名识别语言: {ext}，支持的扩展名: {', '.join(_BY_EXT)}")


def list_languages() -> list[dict]:
    return [
        {
            "name": a.name,
            "display": a.display_name,
            "extensions": list(a.extensions),
            "features": dict(a.features),
        }
        for a in ADAPTERS
    ]


def features_for(name: str) -> dict[str, bool]:
    """返回语言支持的混淆特性（pass 名 -> 出厂默认开关）。

    未声明（未来扩展能力）时视为全支持。用于 CLI 按语言
    自动选择可用开关、对不适用的开关给出提示。
    """
    return dict(get_adapter(name).features)
