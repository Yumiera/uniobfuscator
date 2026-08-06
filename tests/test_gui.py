# -*- coding: utf-8 -*-
"""GUI（Flet）逻辑测试：不启动窗口，验证参数构造与配置填充。"""
from __future__ import annotations

import asyncio
import json

import pytest

from uniobfuscator.gui import UniObfuscatorApp


class _FakePage:
    """极简 Page 替身，只提供 UI 构造所需属性。"""

    def __init__(self):
        self.title = None
        self.theme_mode = None
        self.padding = 0
        self.spacing = 0
        self.overlay = []

    def add(self, *_controls):
        pass

    def update(self):
        pass


@pytest.fixture()
def app():
    return UniObfuscatorApp(_FakePage())


def test_gui_importable():
    import uniobfuscator.gui  # noqa: F401


def test_build_argv_defaults(app):
    app.input_field.value = "app.py"
    argv = app._build_argv()
    assert argv == ["app.py", "--seed", "0"]


def test_build_argv_full_options(app):
    app.input_field.value = "src"
    app.output_field.value = "out"
    app.lang_dropdown.value = "python"
    app.seed_field.value = "42"
    app.sw_rename.value = False
    app.sw_dead_code.value = False
    argv = app._build_argv()
    assert argv == [
        "src", "-o", "out", "-l", "python", "--seed", "42",
        "--no-rename", "--no-dead-code",
    ]


def test_build_argv_with_config(app):
    app.input_field.value = "a.py"
    app.config_field.value = "conf.toml"
    argv = app._build_argv()
    assert argv[-2:] == ["-c", "conf.toml"]


def test_build_argv_requires_input(app):
    app.input_field.value = "  "
    assert app._build_argv() is None


def test_build_argv_invalid_seed(app):
    app.input_field.value = "a.py"
    app.seed_field.value = "abc"
    assert app._build_argv() is None


def test_load_config_fills_form(app, tmp_path):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({
        "path": "src", "language": "java", "output": "out",
        "seed": 7, "rename": False,
    }), encoding="utf-8")
    app.config_field.value = str(conf)
    asyncio.run(app._on_load_config(None))
    assert app.input_field.value == "src"
    assert app.output_field.value == "out"
    assert app.seed_field.value == "7"
    assert app.lang_dropdown.value == "java"
    assert app.sw_rename.value is False


def test_gui_main_creates_default_config(tmp_path, capsys, monkeypatch):
    """GUI 启动路径：启动目录无 config 文件夹时创建并写入默认 toml。"""
    import uniobfuscator.gui as gui

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui.ft, "run", lambda app: None)
    gui.main()
    captured = capsys.readouterr()
    cfg = tmp_path / "config" / "uniobfuscator.toml"
    assert cfg.exists()
    assert "已生成默认配置文件" in captured.err
    assert "rename = true" in cfg.read_text(encoding="utf-8")


def test_gui_main_reuses_existing_config(tmp_path, capsys, monkeypatch):
    """已存在 config/uniobfuscator.toml 时不再覆盖用户修改。"""
    import uniobfuscator.gui as gui

    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config" / "uniobfuscator.toml"
    cfg.parent.mkdir()
    cfg.write_text("seed = 9\n", encoding="utf-8")
    monkeypatch.setattr(gui.ft, "run", lambda app: None)
    gui.main()
    captured = capsys.readouterr()
    assert "已生成默认配置文件" not in captured.err
    assert cfg.read_text(encoding="utf-8") == "seed = 9\n"
