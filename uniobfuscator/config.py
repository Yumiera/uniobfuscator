# -*- coding: utf-8 -*-
"""混淆参数配置文件加载。

支持 .json 与 .toml（Python 3.11+ 内置 tomllib；3.10 需另装 tomli）。

配置分两级：

1. 全局配置（顶层，作用于整个程序）：
     path        : 待混淆的源文件或目录（等价于位置参数）
     language    : python / javascript / java（等价于 -l/--language）
     output      : 输出路径（等价于 -o/--output）
     seed        : 随机种子（等价于 --seed）
     stdout      : 输出到标准输出（等价于 --stdout）
     rename      : 是否启用标识符重命名（等价于 --rename/--no-rename）
     strings     : 是否启用字符串加密
     dead_code   : 是否注入死代码
     arithmetic  : 是否启用算术混淆

2. 按语言配置（languages 字段，覆盖对应语言的全局默认）：
     languages: {
       "python": { rename/strings/dead_code/arithmetic/seed },
       "java":   { ... },
     }

优先级：命令行参数 > 按语言配置 > 全局配置 > 内置默认值。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

#: 支持的语言
LANGUAGES = ("python", "javascript", "java")

#: 自动生成的默认配置目录名与文件名（相对工作目录）
DEFAULT_CONFIG_DIR = "config"
DEFAULT_CONFIG_FILE = "uniobfuscator.toml"

#: 顶层（全局）允许出现的字段
GLOBAL_KEYS = frozenset({
    "path", "language", "output", "seed", "stdout",
    "rename", "strings", "dead_code", "arithmetic",
    "module_rename", "control_flow", "flatten", "languages",
    "exclude",
    "java_arithmetic", "java_dead_code", "java_scramble", "java_rename",
    "java_member_rename", "java_repackage", "java_strip_metadata",
})

#: 按语言分组里允许出现的字段
LANGUAGE_KEYS = frozenset({
    "seed", "rename", "strings", "dead_code", "arithmetic",
    "module_rename", "control_flow", "flatten",
})

#: 默认配置模板（程序自动生成 config/uniobfuscator.toml 时写入）
DEFAULT_CONFIG_TEMPLATE = """\
# uniobfuscator 默认配置文件（程序自动生成）
#
# 修改本文件后，运行 uniobfuscator 时自动生效。
# 也可用 -c <其它配置> 指定其它配置文件（命令行参数优先于配置文件）。
#
# 优先级：命令行参数 > 按语言配置 > 全局配置 > 语言能力默认值。
# 混淆开关按输入形态自动切换：
#   .py/.js/.java 源文件  -> 下方 rename/strings/dead_code/arithmetic
#   .jar 字节码           -> 下方 strings（共享）+ java_* 系列
# 传了不适用的开关会在 stderr 提示并忽略。
# 注意：相对路径基于命令行工作目录解析，建议写绝对路径。

# ---------------- 全局配置（作用于整个程序） ----------------
# path = "src"              # 待混淆的源文件或目录（也可用命令行位置参数）
# language = "python"       # python / javascript / java（目录模式下用于过滤）
# output = "out"            # 输出路径：单文件=文件路径，目录=输出目录（目录模式必须指定）
# stdout = false            # 单文件结果输出到屏幕而非写文件
# seed = 0                  # 随机种子，保证可复现

# 文本源码混淆开关（.py / .js / .java 源文件；默认全部开启，设为 false 可关闭）
rename = true               # 标识符重命名（函数内局部变量/参数）
strings = true              # 字符串加密（JAR 模式同样适用）
dead_code = true            # 死代码注入
arithmetic = true           # 算术混淆
module_rename = true        # 模块级私有名称重命名（仅 Python：类/函数/全局变量）
control_flow = true         # 控制流混淆（仅 Python：不透明谓词 + 诱饵代码）
flatten = true              # 控制流扁平化（仅 Python：函数体改 while+状态机分派）

# JAR 模式排除列表（不混淆的类/包，原样保留；用于反射加载、资源路径等类）
# 格式：精确类名 'com.foo.Secret' 或包前缀 'com.foo.secret.*'（含子包）
# exclude = ["com.foo.Secret", "com.foo.secret.*"]

# JAR 字节码混淆开关（.jar；默认除类名/包名/成员重命名外全部开启）
# java_arithmetic = true    # 整型常量算术混淆
# java_dead_code = true     # 死代码注入（不透明谓词，仅无分支方法）
# java_scramble = true      # 控制流打散（栈平衡垃圾块 + goto 绕行）
# java_strip_metadata = true  # 剥离泛型签名 / throws / 运行期不可见注解
# java_rename = false       # 类名重命名（保留包路径；反射/框架目标自动保护）
# java_repackage = false    # 包名混淆：平铺到单一短包 + 全局短类名（破坏性最强）
# java_member_rename = false  # 私有方法/字段重命名（Serializable 字段自动保护）

# ---------------- 按语言配置（覆盖对应语言的全局默认） ----------------
# 每个语言段可单独设置：seed / rename / strings / dead_code / arithmetic
# / module_rename / control_flow / flatten。
# 即"同一种输入，不同语言可用不同开关组合"。
#
# [languages.python]
# rename = false            # Python 不重命名
#
# [languages.javascript]
# seed = 42
#
# [languages.java]
# dead_code = false         # Java 源文件不注入死代码
"""


def ensure_default_config(work_dir: str | None = None) -> Path | None:
    """确保工作目录下存在默认配置文件。

    不存在时创建 config/uniobfuscator.toml 并写入默认模板，返回其路径；
    已存在则返回 None（不覆盖用户已修改的内容）。
    """
    base = Path(work_dir or os.getcwd())
    cfg_path = base / DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    if cfg_path.exists():
        return None
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return cfg_path


def load_config(path: str) -> dict:
    """从 JSON / TOML 文件加载配置，返回原始配置字典。

    Raises:
        ValueError: 文件不存在、格式不支持或内容不是键值对对象。
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        elif ext in (".toml", ".tml"):
            if tomllib is None:
                raise ValueError("TOML 配置需要 Python 3.11+（或安装 tomli 包）")
            with open(path, "rb") as f:
                data = tomllib.load(f)
        else:
            raise ValueError(f"不支持的配置文件格式 '{ext}'（支持 .json / .toml）")
    except OSError as e:
        raise ValueError(f"无法读取配置文件: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是键值对对象")
    return data


def split_config(cfg: dict) -> tuple[dict, dict]:
    """把配置拆成 (全局字段, 按语言字段)。

    未知字段 / 未知语言会打印警告到 stderr 并忽略；
    结构错误（如 languages 不是对象）抛 ValueError。
    """
    global_cfg: dict = {}
    per_language: dict = {}
    for key, value in cfg.items():
        if key == "languages":
            if not isinstance(value, dict):
                raise ValueError("'languages' 字段必须是键值对对象")
            for lang, opts in value.items():
                if lang not in LANGUAGES:
                    print(f"警告: 配置 'languages' 含未知语言 '{lang}'，已忽略", file=sys.stderr)
                    continue
                if not isinstance(opts, dict):
                    raise ValueError(f"languages.{lang} 必须是键值对对象")
                clean: dict = {}
                for k, v in opts.items():
                    if k not in LANGUAGE_KEYS:
                        print(
                            f"警告: languages.{lang} 含未知字段 '{k}'，已忽略",
                            file=sys.stderr,
                        )
                        continue
                    clean[k] = v
                if clean:
                    per_language[lang] = clean
        elif key not in GLOBAL_KEYS:
            print(f"警告: 配置文件包含未知字段 '{key}'，已忽略", file=sys.stderr)
        else:
            global_cfg[key] = value
    return global_cfg, per_language
