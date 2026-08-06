# -*- coding: utf-8 -*-
"""配置文件加载与 CLI 集成测试。"""
import json

from uniobfuscator.cli import main


def _write_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("RATE = 10\nprint(RATE)\n", encoding="utf-8")
    return src


def _write_func_project(tmp_path):
    """带函数局部变量的工程：局部变量才会被 RenamePass 重命名。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(
        "def calc():\n    local_value = 1\n    return local_value\nprint(calc())\n",
        encoding="utf-8",
    )
    return src


def test_config_file_supplies_options(tmp_path, capsys):
    src = _write_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "path": str(src),
        "language": "python",
        "output": str(out),
        "seed": 5,
    }), encoding="utf-8")

    rc = main(["-c", str(cfg)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert (out / "a.py").exists()
    assert "目录混淆完成" in captured.out


def test_cli_overrides_config(tmp_path, capsys):
    src = _write_project(tmp_path)
    cfg_out = tmp_path / "cfg_out"
    real_out = tmp_path / "real_out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "path": str(src),
        "output": str(cfg_out),
    }), encoding="utf-8")

    rc = main(["-c", str(cfg), str(src), "-o", str(real_out)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert (real_out / "a.py").exists()
    assert not (cfg_out / "a.py").exists()


def test_config_unknown_key_warns(tmp_path, capsys):
    src = _write_project(tmp_path)
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(tmp_path / "o"),
        "typo_key": 1,
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "未知字段 'typo_key'" in captured.err


def test_config_disables_rename(tmp_path):
    src = _write_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(out),
        "rename": False,
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    assert rc == 0
    content = (out / "a.py").read_text(encoding="utf-8")
    assert "RATE" in content  # 关闭重命名后标识符保持不变


def test_config_missing_file(tmp_path, capsys):
    rc = main(["-c", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "无法读取配置文件" in captured.err


def test_per_language_overrides_global(tmp_path):
    """按语言配置覆盖全局配置：全局关重命名，python 段单独打开。"""
    src = _write_func_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(out),
        "rename": False,
        "languages": {"python": {"rename": True}},
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    assert rc == 0
    content = (out / "a.py").read_text(encoding="utf-8")
    assert "local_value" not in content  # python 段重新开启了重命名


def test_global_default_used_without_language_section(tmp_path):
    """没有 languages 段时，全局配置对所有语言生效。"""
    src = _write_func_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(out),
        "rename": False,
        "seed": 7,
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    assert rc == 0
    assert "local_value" in (out / "a.py").read_text(encoding="utf-8")


def test_cli_overrides_per_language(tmp_path):
    """命令行参数优先于按语言配置。"""
    src = _write_func_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(out),
        "languages": {"python": {"rename": True}},
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg), "--no-rename"])
    assert rc == 0
    assert "local_value" in (out / "a.py").read_text(encoding="utf-8")


def test_config_unknown_language_warns(tmp_path, capsys):
    src = _write_project(tmp_path)
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(tmp_path / "o"),
        "languages": {"ruby": {"rename": False}},
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "未知语言 'ruby'" in captured.err


def test_per_language_unknown_key_warns(tmp_path, capsys):
    src = _write_project(tmp_path)
    cfg = tmp_path / "conf.json"
    cfg.write_text(json.dumps({
        "output": str(tmp_path / "o"),
        "languages": {"python": {"rename": False, "typo": 1}},
    }), encoding="utf-8")

    rc = main([str(src), "-c", str(cfg)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "languages.python 含未知字段 'typo'" in captured.err


def test_auto_generate_default_config(tmp_path, capsys, monkeypatch):
    """未指定 -c 且工作目录无默认配置时，自动生成 config/uniobfuscator.toml。"""
    monkeypatch.chdir(tmp_path)
    src = _write_func_project(tmp_path)
    out = tmp_path / "out"

    rc = main([str(src), "-o", str(out)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert (tmp_path / "config" / "uniobfuscator.toml").exists()
    assert "已生成默认配置文件" in captured.err
    assert (out / "a.py").exists()


def test_auto_load_default_config(tmp_path, capsys, monkeypatch):
    """存在默认配置时自动加载（无需 -c），且不重复生成。"""
    monkeypatch.chdir(tmp_path)
    src = _write_func_project(tmp_path)
    out = tmp_path / "out"
    cfg = tmp_path / "config" / "uniobfuscator.toml"
    cfg.parent.mkdir()
    cfg.write_text(f"output = '{out.as_posix()}'\nrename = false\n", encoding="utf-8")

    rc = main([str(src)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "已生成" not in captured.err
    # 默认配置 rename=false 生效：局部变量保留原名
    assert "local_value" in (out / "a.py").read_text(encoding="utf-8")


def test_auto_generate_does_not_overwrite(tmp_path, monkeypatch):
    """默认配置已存在时不覆盖用户修改。"""
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config" / "uniobfuscator.toml"
    cfg.parent.mkdir()
    cfg.write_text("seed = 123\n", encoding="utf-8")

    src = _write_func_project(tmp_path)
    out = tmp_path / "out"
    rc = main([str(src), "-o", str(out)])
    assert rc == 0
    assert "seed = 123" in cfg.read_text(encoding="utf-8")
