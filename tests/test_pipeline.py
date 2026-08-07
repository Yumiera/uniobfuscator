# -*- coding: utf-8 -*-
"""端到端测试：混淆后语法正确，且运行输出与原始一致。"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from uniobfuscator.core.pipeline import obfuscate
from uniobfuscator.languages import get_adapter

SAMPLES = os.path.join(os.path.dirname(__file__), "samples")

LANG_ALIAS = {"python": sys.executable, "javascript": "node"}

OPTIONS = {"rename": True, "strings": True, "dead_code": True, "arithmetic": True, "seed": 42}


def load_sample(name: str) -> str:
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return f.read()


def run_output(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"命令失败: {' '.join(cmd)}\n{r.stderr}"
    return r.stdout


@pytest.mark.parametrize("lang,ext,runner", [
    ("python", "py", sys.executable),
    ("javascript", "js", "node"),
])
def test_runtime_output_unchanged(tmp_path, lang, ext, runner):
    """混淆后程序运行输出应与原始一致。"""
    adapter = get_adapter(lang)
    source = load_sample(f"sample.{ext}")
    obf = obfuscate(source, adapter, OPTIONS)

    original = tmp_path / f"orig.{ext}"
    obfuscated = tmp_path / f"obf.{ext}"
    original.write_text(source, encoding="utf-8")
    obfuscated.write_text(obf, encoding="utf-8")

    out1 = run_output([runner, str(original)])
    out2 = run_output([runner, str(obfuscated)])
    assert out1 == out2


@pytest.mark.parametrize("lang,ext", [("python", "py"), ("javascript", "js"), ("java", "java")])
def test_obfuscated_output_is_valid_syntax(lang, ext):
    """混淆输出能再次被 tree-sitter 解析（无语法错误）。"""
    adapter = get_adapter(lang)
    source = load_sample(f"sample.{ext}")
    obf = obfuscate(source, adapter, OPTIONS)
    assert not adapter.parse(obf).has_error, "混淆输出存在语法错误"


@pytest.mark.parametrize("lang,ext,runner", [
    ("python", "py", sys.executable),
    ("javascript", "js", "node"),
])
def test_each_pass_individually(tmp_path, lang, ext, runner):
    """每个 Pass 单独开启时输出也正确。"""
    adapter = get_adapter(lang)
    source = load_sample(f"sample.{ext}")
    for pass_name in ("rename", "strings", "dead_code", "arithmetic",
                      "module_rename", "control_flow", "flatten"):
        opts = {"rename": False, "strings": False, "dead_code": False, "arithmetic": False,
                "module_rename": False, "control_flow": False, "flatten": False,
                "seed": 42, pass_name: True}
        obf = obfuscate(source, adapter, opts)
        assert not adapter.parse(obf).has_error, f"{pass_name} 产生语法错误"

        orig = tmp_path / f"o.{ext}"
        obf_path = tmp_path / f"b_{pass_name}.{ext}"
        orig.write_text(source, encoding="utf-8")
        obf_path.write_text(obf, encoding="utf-8")
        out1 = run_output([runner, str(orig)])
        out2 = run_output([runner, str(obf_path)])
        assert out1 == out2, f"{pass_name} 改变了程序行为"


def test_cli_end_to_end(tmp_path):
    """CLI 全流程：混淆文件并检查输出存在。"""
    src = tmp_path / "demo.py"
    src.write_text(load_sample("sample.py"), encoding="utf-8")
    out = tmp_path / "demo_obf.py"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", str(src), "-o", str(out), "--seed", "7"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    # 混淆输出可运行且与原始一致
    r2 = subprocess.run([sys.executable, str(out)], capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0, r2.stderr
    r3 = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'tests');exec(open('tests/samples/sample.py').read())"],
                        capture_output=True, text=True, timeout=60)
    assert r2.stdout == r3.stdout


def test_project_directory_python(tmp_path):
    """整个 Python 项目（多文件互引）目录混淆：镜像结构 + 运行输出一致。"""
    src_dir = os.path.join(SAMPLES, "project_py")
    out_dir = tmp_path / "obf_project"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", src_dir, "-o", str(out_dir), "--seed", "9"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    # 镜像结构与文件名保持
    assert (out_dir / "app.py").exists()
    assert (out_dir / "calc.py").exists()

    # 混淆后项目运行输出应与原始一致
    out1 = run_output([sys.executable, os.path.join(src_dir, "app.py")])
    out2 = run_output([sys.executable, str(out_dir / "app.py")])
    assert out1 == out2


def test_project_directory_java_syntax(tmp_path):
    """整个 Java 项目（多文件互引）目录混淆：文件名保持 + 语法正确。"""
    src_dir = os.path.join(SAMPLES, "project_java")
    out_dir = tmp_path / "obf_java"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", src_dir, "-o", str(out_dir), "--seed", "9"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    # Java 文件名必须保持（文件名 = public 类名），这是目录混淆的关键约定
    assert (out_dir / "Main.java").exists()
    assert (out_dir / "Util.java").exists()
    # 输出文件无 .obf 后缀（与单文件模式区分）
    assert not list(out_dir.glob("*.obf.*"))

    adapter = get_adapter("java")
    for name in ("Main.java", "Util.java"):
        content = (out_dir / name).read_text(encoding="utf-8")
        assert not adapter.parse(content).has_error, f"{name} 混淆后存在语法错误"


@pytest.mark.parametrize("seed", [0, 1, 2, 7, 42])
def test_scope_edge_cases_multi_seed(tmp_path, seed):
    """global/nonlocal/同名遮蔽/模块级同名：多 seed 混淆后运行输出与原始一致。"""
    src = os.path.join(SAMPLES, "edge_scopes.py")
    out1 = run_output([sys.executable, src])
    for s in (seed,):
        out = tmp_path / f"edge_{s}.py"
        r = subprocess.run(
            [sys.executable, "-m", "uniobfuscator.cli", src, "-o", str(out), "--seed", str(s)],
            capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        )
        assert r.returncode == 0, r.stderr
        out2 = run_output([sys.executable, str(out)])
        assert out1 == out2, f"seed={s} 混淆改变了程序行为"


@pytest.mark.parametrize("seed", [0, 3, 17])
def test_project_python_runs_multi_seed(tmp_path, seed):
    """整个 Python 项目多 seed 混淆后都能运行，且输出与原始一致。"""
    src_dir = os.path.join(SAMPLES, "project_py")
    out_dir = tmp_path / f"obf_{seed}"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", src_dir, "-o", str(out_dir), "--seed", str(seed)],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    out1 = run_output([sys.executable, os.path.join(src_dir, "app.py")])
    out2 = run_output([sys.executable, str(out_dir / "app.py")])
    assert out1 == out2, f"seed={seed} 项目混淆后运行不一致"


def test_project_directory_javascript(tmp_path):
    """整个 JS 项目（多文件 import/export 互引）目录混淆：
    镜像结构 + 文件名保持 + 运行输出与原始一致。"""
    src_dir = os.path.join(SAMPLES, "project_js")
    out_dir = tmp_path / "obf_js"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", src_dir, "-o", str(out_dir), "--seed", "9"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert (out_dir / "main.mjs").exists()
    assert (out_dir / "util.mjs").exists()

    adapter = get_adapter("javascript")
    for name in ("main.mjs", "util.mjs"):
        content = (out_dir / name).read_text(encoding="utf-8")
        assert not adapter.parse(content).has_error, f"{name} 混淆后存在语法错误"

    out1 = run_output(["node", os.path.join(src_dir, "main.mjs")])
    out2 = run_output(["node", str(out_dir / "main.mjs")])
    assert out1 == out2


def test_cli_jar_bad_zip_fails(tmp_path):
    """伪 JAR（不是合法 zip）应报错退出。"""
    jar = tmp_path / "app.jar"
    jar.write_bytes(b"PK\x03\x04fake")
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", str(jar)],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode != 0
    assert "JAR" in r.stderr


def test_cli_dir_warns_binary_artifacts(tmp_path):
    """目录模式：跳过 .jar/.class 等二进制产物并给出警告，不影响源码混淆。"""
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "Main.java").write_text(
        "public class Main {\n    public static void main(String[] args) {\n        System.out.println(\"hi\");\n    }\n}\n",
        encoding="utf-8",
    )
    (src_dir / "lib.jar").write_bytes(b"PK\x03\x04fake")
    out_dir = tmp_path / "obf"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", str(src_dir), "-o", str(out_dir)],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert "跳过 1 个非源码文件" in r.stderr
    assert (out_dir / "Main.java").exists()


# ---------------------------------------------------------------------------
# 按语言切换混淆选项（能力矩阵 + CLI 自动切换 + 提示）
# ---------------------------------------------------------------------------

TEXT_FEATURES = {"rename", "strings", "dead_code", "arithmetic"}
#: Python 额外声明的能力（其余语言在 features 中声明为 False 即不支持）
PYTHON_EXTRA_FEATURES = {"module_rename", "control_flow", "flatten"}


def test_features_capability_matrix():
    """能力矩阵：所有语言声明基础文本特性（Python 强化混淆默认开启，
    其余语言 module_rename/control_flow 声明为 False 即不支持，flatten 仅 Python）；
    JAR 另有 java_*。"""
    from uniobfuscator.jvm import JAR_FEATURES
    from uniobfuscator.languages import features_for, list_languages

    for lang in ("python", "javascript", "java"):
        feats = features_for(lang)
        assert TEXT_FEATURES <= set(feats)
        assert all(feats[k] for k in TEXT_FEATURES)  # 基础 4 项出厂默认开启
    # Python 强化混淆默认开启；其余语言默认关闭（不支持该能力）
    py = features_for("python")
    assert set(py) == TEXT_FEATURES | PYTHON_EXTRA_FEATURES
    assert py["module_rename"] is True and py["control_flow"] is True
    assert py["flatten"] is True
    for lang in ("javascript", "java"):
        feats = features_for(lang)
        assert feats["module_rename"] is False and feats["control_flow"] is False
        assert "flatten" not in feats  # flatten 仅 Python 声明

    assert set(JAR_FEATURES) == {
        "strings", "java_arithmetic", "java_dead_code", "java_scramble",
        "java_rename", "java_member_rename", "java_repackage",
        "java_strip_metadata",
    }
    # strings 为文本/JAR 共享；改名类/成员 pass 破坏性较强默认关闭
    assert JAR_FEATURES["strings"] is True
    assert JAR_FEATURES["java_rename"] is False
    assert JAR_FEATURES["java_member_rename"] is False
    assert JAR_FEATURES["java_repackage"] is False
    assert JAR_FEATURES["java_strip_metadata"] is True

    for item in list_languages():
        if item["name"] == "python":
            assert set(item["features"]) == TEXT_FEATURES | PYTHON_EXTRA_FEATURES
        else:
            assert set(item["features"]) == TEXT_FEATURES | {"module_rename", "control_flow"}


def test_build_options_language_features_defaults():
    """语言能力默认值参与选项合并：features 覆盖内置默认，CLI 显式参数优先。"""
    from uniobfuscator.cli import _build_options

    class _Args:  # 模拟 argparse.Namespace：所有开关未指定（None）
        seed = rename = strings = dead_code = arithmetic = None
        module_rename = control_flow = flatten = None
        exclude = java_arithmetic = java_dead_code = None
        java_scramble = java_rename = None
        java_member_rename = java_repackage = java_strip_metadata = None

    # 语言出厂默认只声明部分特性：声明的覆盖内置默认，未声明的保持默认
    opts = _build_options(_Args(), features={"rename": False, "strings": False})
    assert opts["rename"] is False and opts["strings"] is False
    assert opts["dead_code"] is True
    assert opts["module_rename"] is False  # 未声明则保持内置默认
    assert opts.get("java_rename") is None  # 未声明 JAR 特性则不包含

    # Python 语言能力声明强化混淆默认开启
    from uniobfuscator.languages import get_adapter
    py_opts = _build_options(_Args(), features=get_adapter("python").features)
    assert py_opts["module_rename"] is True
    assert py_opts["control_flow"] is True
    assert py_opts["flatten"] is True

    # 命令行显式参数 > 语言能力默认
    args = _Args()
    args.rename = True
    assert _build_options(args, features={"rename": False})["rename"] is True
    args.module_rename = False
    assert _build_options(args, features=get_adapter("python").features)["module_rename"] is False


def test_cli_text_warns_jar_flags(tmp_path):
    """文本源码混淆时传 JAR 专属开关：警告并忽略，不影响输出。"""
    src = tmp_path / "a.py"
    src.write_text("x = 1\nprint(x + 2)\n", encoding="utf-8")
    out = tmp_path / "a_obf.py"
    r = subprocess.run(
        [sys.executable, "-m", "uniobfuscator.cli", str(src), "-o", str(out),
         "--java-scramble"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert "仅适用于 JAR 字节码模式" in r.stderr
    assert out.exists()


# ---------------------------------------------------------------------------
# Python 强化混淆：模块级名称重命名 / 控制流混淆
# ---------------------------------------------------------------------------


def test_module_rename_renames_private_names(tmp_path):
    """module_rename：模块级私有名（_ 开头）被重命名且行为不变，public 名保留。"""
    adapter = get_adapter("python")
    src = (
        "_secret = 1\n"
        "PUBLIC = 2\n"
        "\n"
        "def _helper():\n"
        "    return _secret\n"
        "\n"
        "print(PUBLIC + _helper())\n"
    )
    opts = {"rename": False, "strings": False, "dead_code": False, "arithmetic": False,
            "module_rename": True, "control_flow": False, "seed": 42}
    obf = obfuscate(src, adapter, opts)
    assert not adapter.parse(obf).has_error
    assert "_secret" not in obf and "_helper" not in obf  # 私有名已改名
    assert "PUBLIC" in obf  # public 名保留（跨文件 import 安全）

    orig = tmp_path / "o.py"
    obf_path = tmp_path / "b.py"
    orig.write_text(src, encoding="utf-8")
    obf_path.write_text(obf, encoding="utf-8")
    assert run_output([sys.executable, str(orig)]) == \
        run_output([sys.executable, str(obf_path)])


def test_control_flow_injects_opaque_predicate(tmp_path):
    """control_flow：函数头注入算术恒假谓词 + 诱饵代码，且运行行为不变。"""
    adapter = get_adapter("python")
    src = "def f(x):\n    return x + 1\n\nprint(f(1))\n"
    opts = {"rename": False, "strings": False, "dead_code": False, "arithmetic": False,
            "module_rename": False, "control_flow": True, "seed": 42}
    obf = obfuscate(src, adapter, opts)
    assert not adapter.parse(obf).has_error
    assert "if (" in obf and "==" in obf  # 不透明谓词已注入

    orig = tmp_path / "o.py"
    obf_path = tmp_path / "b.py"
    orig.write_text(src, encoding="utf-8")
    obf_path.write_text(obf, encoding="utf-8")
    assert run_output([sys.executable, str(orig)]) == \
        run_output([sys.executable, str(obf_path)])


def test_flatten_rebuilds_function_body(tmp_path):
    """flatten：函数体被改造成 while+状态机，且运行行为不变。"""
    adapter = get_adapter("python")
    src = (
        "def calc(a, b):\n"
        "    total = a * b + 100\n"
        "    print('mid', total)\n"
        "    return total\n"
        "\n"
        "print(calc(3, 4))\n"
    )
    opts = {"rename": False, "strings": False, "dead_code": False, "arithmetic": False,
            "module_rename": False, "control_flow": False, "flatten": True, "seed": 42}
    obf = obfuscate(src, adapter, opts)
    assert not adapter.parse(obf).has_error
    assert "while True:" in obf          # 状态机已注入
    assert obf.index("def calc") < obf.index("while True:")
    # 状态变量与诱饵状态存在
    assert "elif" in obf and "break" in obf

    orig = tmp_path / "o.py"
    obf_path = tmp_path / "b.py"
    orig.write_text(src, encoding="utf-8")
    obf_path.write_text(obf, encoding="utf-8")
    assert run_output([sys.executable, str(orig)]) == \
        run_output([sys.executable, str(obf_path)])


def test_flatten_docstring_preserved(tmp_path):
    """flatten：docstring 保留为函数体第一条语句（__doc__ 语义不丢）。"""
    adapter = get_adapter("python")
    src = (
        "def f():\n"
        "    \"\"\"文档说明\"\"\"\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
        "\n"
        "print(f(), f.__doc__)\n"
    )
    opts = {"rename": False, "strings": False, "dead_code": False, "arithmetic": False,
            "module_rename": False, "control_flow": False, "flatten": True, "seed": 7}
    obf = obfuscate(src, adapter, opts)
    assert not adapter.parse(obf).has_error
    assert "while True:" in obf

    orig = tmp_path / "o.py"
    obf_path = tmp_path / "b.py"
    orig.write_text(src, encoding="utf-8")
    obf_path.write_text(obf, encoding="utf-8")
    assert run_output([sys.executable, str(orig)]) == \
        run_output([sys.executable, str(obf_path)])


def test_flatten_all_passes_compose(tmp_path):
    """flatten 与全部 Python pass 叠加：多 seed 运行输出与原始一致。"""
    adapter = get_adapter("python")
    src = load_sample("sample.py")
    out1 = run_output([sys.executable, os.path.join(SAMPLES, "sample.py")])
    for seed in (1, 42, 99):
        opts = {"rename": True, "strings": True, "dead_code": True, "arithmetic": True,
                "module_rename": True, "control_flow": True, "flatten": True,
                "seed": seed}
        obf = obfuscate(src, adapter, opts)
        assert not adapter.parse(obf).has_error, f"seed={seed} 产生语法错误"
        assert "while True:" in obf
        p = tmp_path / f"all_{seed}.py"
        p.write_text(obf, encoding="utf-8")
        assert run_output([sys.executable, str(p)]) == out1, f"seed={seed} 行为改变"


def test_string_encrypt_multi_algo_multi_seed(tmp_path):
    """字符串加密多算法：多个 seed 下（覆盖 xor/add/sub 三种算法）运行一致。"""
    adapter = get_adapter("python")
    src = "print('hello')\nprint('中文测试')\nprint('x' * 3)\n"
    for seed in (1, 5, 11, 23, 99):
        opts = {"rename": False, "strings": True, "dead_code": False, "arithmetic": False,
                "module_rename": False, "control_flow": False, "flatten": False,
                "seed": seed}
        obf = obfuscate(src, adapter, opts)
        assert not adapter.parse(obf).has_error, f"seed={seed} 产生语法错误"
        p = tmp_path / f"s_{seed}.py"
        p.write_text(obf, encoding="utf-8")
        assert run_output([sys.executable, str(p)]) == "hello\n中文测试\nxxx\n", \
            f"seed={seed} 解码结果不一致"
