# -*- coding: utf-8 -*-
"""混淆 Pass 集合。"""
from .arithmetic import ArithmeticPass
from .base import ObfuscationPass
from .control_flow import ControlFlowPass
from .dead_code import DeadCodePass
from .module_rename import ModuleRenamePass
from .rename import RenamePass
from .string_enc import StringEncryptPass

ALL_PASSES: list[type[ObfuscationPass]] = [
    RenamePass,
    StringEncryptPass,
    DeadCodePass,
    ArithmeticPass,
    ModuleRenamePass,
    ControlFlowPass,
]

__all__ = [
    "ObfuscationPass",
    "RenamePass",
    "StringEncryptPass",
    "DeadCodePass",
    "ArithmeticPass",
    "ModuleRenamePass",
    "ControlFlowPass",
    "ALL_PASSES",
]
