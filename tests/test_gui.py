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


class _FakeEvent:
    """模拟 flet 事件对象：新值只存在于 e.control.value（控件属性未同步）。"""

    def __init__(self, value):
        self.control = type("C", (), {"value": value})()


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


def test_build_argv_jar_defaults(app):
    """JAR 输入：默认全开时只传基础参数，不传文本开关（避免 CLI 警告）。"""
    app.input_field.value = "app.jar"
    argv = app._build_argv()
    assert argv == ["app.jar", "--seed", "0"]


def test_build_argv_jar_full_options(app):
    """JAR 输入：JAR 专属开关 + 排除列表，文本开关不传。"""
    app.input_field.value = "app.jar"
    app.seed_field.value = "7"
    app.sw_java_arithmetic.value = False
    app.sw_java_strip_metadata.value = False
    app.sw_java_rename.value = True
    app.sw_java_repackage.value = True
    app.exclude_field.value = "com.foo.Secret, com.foo.secret.*"
    argv = app._build_argv()
    assert argv == [
        "app.jar", "--seed", "7",
        "--no-java-arithmetic", "--no-java-strip-metadata",
        "--java-rename", "--java-repackage",
        "--exclude", "com.foo.Secret", "--exclude", "com.foo.secret.*",
    ]


def test_build_argv_jar_ignores_text_switches(app):
    """JAR 输入：文本开关（--no-rename 等）绝不出现。"""
    app.input_field.value = "app.jar"
    app.sw_rename.value = False
    app.sw_dead_code.value = False
    app.sw_arithmetic.value = False
    argv = app._build_argv()
    assert argv == ["app.jar", "--seed", "0"]


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


def test_load_config_fills_jar_options(app, tmp_path):
    """加载配置时 JAR 开关与排除列表回填到界面。"""
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({
        "java_rename": True,
        "java_repackage": True,
        "java_strip_metadata": False,
        "exclude": ["com.foo.Secret", "com.foo.secret.*"],
    }), encoding="utf-8")
    app.config_field.value = str(conf)
    asyncio.run(app._on_load_config(None))
    assert app.sw_java_rename.value is True
    assert app.sw_java_repackage.value is True
    assert app.sw_java_strip_metadata.value is False
    assert app.sw_java_scramble.value is True  # 未配置项保持默认
    assert app.exclude_field.value == "com.foo.Secret, com.foo.secret.*"


def test_render_options_text_mode(app):
    """Java 语言（或 .java 输入）：显示 Java 配置组（字符串加密 + JAR 专属 + 排除框）。"""
    app.input_field.value = "Main.java"
    app._render_options()
    in_area = set()
    for c in app.options_area.controls:
        in_area.add(c)
        if hasattr(c, "controls"):
            in_area.update(c.controls)
    # Java 配置组：JAR 专属开关在，文本开关 rename 不在（整体替换，不叠加）
    assert app.sw_java_rename in in_area
    assert app.sw_rename not in in_area
    assert app.sw_java_repackage in in_area
    assert app.exclude_field.visible is True
    assert "Java 混淆" in app.mode_hint.value


def test_render_options_text_mode_python(app):
    """python 源码模式：只显示文本开关，不显示 JAR 专属开关。"""
    app.input_field.value = "app.py"
    app.lang_dropdown.value = "python"
    app._render_options()
    in_area = set()
    for c in app.options_area.controls:
        in_area.add(c)
        if hasattr(c, "controls"):
            in_area.update(c.controls)
    assert app.sw_rename in in_area
    assert app.sw_java_rename not in in_area
    assert app.sw_java_repackage not in in_area
    assert app.exclude_field.visible is False


def test_render_options_jar_mode(app):
    """JAR 输入：显示字符串 + JAR 开关 + 排除框，不显示文本开关。"""
    app.input_field.value = "app.jar"
    app._render_options()
    in_area = set()
    for c in app.options_area.controls:
        in_area.add(c)
        if hasattr(c, "controls"):
            in_area.update(c.controls)
    assert app.sw_java_rename in in_area
    assert app.sw_java_repackage in in_area
    assert app.sw_rename not in in_area
    assert app.exclude_field in in_area
    assert app.exclude_field.visible is True
    assert "Java 混淆" in app.mode_hint.value


def test_render_options_switch_by_language(app):
    """切换语言后 options_area 随之更新。"""
    app.input_field.value = "a.js"
    app.lang_dropdown.value = "javascript"
    app._on_lang_change(None)
    assert "javascript 源码混淆" in app.mode_hint.value
    assert app.exclude_field.visible is False
    # 切到 jar：输入改 .jar 后渲染出 Java 配置组
    app.input_field.value = "a.jar"
    app._render_options()
    assert app.exclude_field.visible is True


def test_on_lang_change_syncs_from_event(app):
    """flet 事件里新值在 e.control.value 上：必须同步到 lang_dropdown 再渲染。

    模拟真实 flet 回调：控件 .value 仍是旧值（auto），事件对象带新值 java，
    此时选项区应切换为 Java 配置组（而非停留在 auto 推断的 python 模式）。
    """
    app.input_field.value = "app.py"   # auto 推断为 python
    app.lang_dropdown.value = "auto"   # 控件属性未同步，仍是旧值
    app._on_lang_change(_FakeEvent("java"))
    assert app.lang_dropdown.value == "java"
    assert "Java 混淆" in app.mode_hint.value
    assert app.exclude_field.visible is True


def test_on_lang_change_jar_input_keeps_jar_mode(app):
    """jar 输入时切换语言不改变 Java 配置组（jar 模式与语言无关）。"""
    app.input_field.value = "app.jar"
    app._render_options()
    app.lang_dropdown.value = "auto"
    app._on_lang_change(_FakeEvent("java"))
    assert "Java 混淆" in app.mode_hint.value
    assert app.exclude_field.visible is True


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
