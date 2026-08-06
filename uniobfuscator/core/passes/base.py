# -*- coding: utf-8 -*-
"""混淆 Pass 基类。所有 Pass 语言无关，只依赖 LanguageAdapter 接口。"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod

from ..editable import EditableSource
from ...languages.base import LanguageAdapter


class ObfuscationPass(ABC):
    name: str = "base"
    description: str = ""

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    @abstractmethod
    def run(self, src: EditableSource, adapter: LanguageAdapter) -> None:
        """在 src 上记录编辑（替换/插入），不做实际写回。"""
        raise NotImplementedError
