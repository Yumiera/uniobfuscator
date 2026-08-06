# -*- coding: utf-8 -*-
"""混淆 Pass 集合。"""
from .arithmetic import ArithmeticPass
from .base import ObfuscationPass
from .dead_code import DeadCodePass
from .rename import RenamePass
from .string_enc import StringEncryptPass

ALL_PASSES: list[type[ObfuscationPass]] = [
    RenamePass,
    StringEncryptPass,
    DeadCodePass,
    ArithmeticPass,
]

__all__ = [
    "ObfuscationPass",
    "RenamePass",
    "StringEncryptPass",
    "DeadCodePass",
    "ArithmeticPass",
    "ALL_PASSES",
]
