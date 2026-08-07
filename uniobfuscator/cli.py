# -*- coding: utf-8 -*-
"""uniobfuscator 命令行入口。

混淆开关按输入形态自动切换（同一份命令行/配置对多语言通用）：

- 文本源文件（.py/.js/.java）：
    --rename / --strings / --dead-code / --arithmetic
    --module-rename / --control-flow（Python 强化混淆，默认按语言能力开启）
    各语言可裁剪能力或调整默认值（见 languages 配置段）；
    传了 JAR 专属的 --java-* 开关会提示"仅适用于 JAR 字节码模式"并忽略。
- JAR 字节码（.jar）：
    --strings（共享，字符串加密）
    --java-arithmetic / --java-dead-code / --java-scramble / --java-rename
    传了文本专属的 --rename/--dead-code/--arithmetic 会提示并忽略。

用法示例：
  uniobfuscator app.py -o app_obf.py            # 单文件
  uniobfuscator app.js --no-rename --stdout     # 单文件输出到标准输出
  uniobfuscator src/ -o out/                    # 整个目录（镜像结构、保持文件名）
  uniobfuscator src/ -o out/ -l java            # 只混淆 Java 文件
  uniobfuscator -c conf.json                    # 全部参数由配置文件提供
  uniobfuscator -c conf.json src/ --seed 9      # 配置文件 + 命令行覆盖（命令行优先）
  uniobfuscator app.jar -o app_obf.jar          # JAR 字节码混淆
  uniobfuscator app.jar --java-rename -o app_obf.jar   # 开启类名重命名（需配合 --exclude）
  uniobfuscator app.jar --exclude com.foo.Secret --exclude com.foo.secret.* -o app_obf.jar
                                               # 排除指定类/包（原样保留，适合反射/资源类）
"""
from __future__ import annotations

import argparse
import os
import sys

try:  # 以模块方式运行（python -m uniobfuscator.cli）
    from .config import (
        DEFAULT_CONFIG_DIR,
        DEFAULT_CONFIG_FILE,
        LANGUAGES,
        ensure_default_config,
        load_config,
        split_config,
    )
    from .core.pipeline import DEFAULT_OPTIONS, obfuscate, obfuscate_file
    from .jvm import JAR_FEATURES, is_jar_file, obfuscate_jar
    from .languages import (
        SUPPORTED_EXTENSIONS,
        adapter_for_filename,
        get_adapter,
        list_languages,
    )
except ImportError:  # 直接运行脚本（python uniobfuscator/cli.py）时的兼容回退
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from uniobfuscator.config import (
        DEFAULT_CONFIG_DIR,
        DEFAULT_CONFIG_FILE,
        LANGUAGES,
        ensure_default_config,
        load_config,
        split_config,
    )
    from uniobfuscator.core.pipeline import DEFAULT_OPTIONS, obfuscate, obfuscate_file
    from uniobfuscator.jvm import JAR_FEATURES, is_jar_file, obfuscate_jar
    from uniobfuscator.languages import (
        SUPPORTED_EXTENSIONS,
        adapter_for_filename,
        get_adapter,
        list_languages,
    )

LANG_CHOICES = list(LANGUAGES)

# 编译/打包产物：目录混淆时会跳过并提醒；单个 .jar 可直接作为输入走字节码混淆
BINARY_ARTIFACT_EXTS = frozenset({
    ".jar", ".class", ".pyc", ".pyo", ".o", ".so", ".pyd", ".dll", ".dylib", ".exe",
})

#: 文本语言专属开关（JAR 模式不适用；strings 为两种形态共享）
TEXT_ONLY_KEYS = frozenset({"rename", "dead_code", "arithmetic",
                            "module_rename", "control_flow", "flatten"})
#: JAR 字节码专属开关（文本模式不适用）
JAR_ONLY_KEYS = frozenset({
    "java_arithmetic", "java_dead_code", "java_scramble", "java_rename",
    "java_member_rename", "java_repackage", "java_strip_metadata",
})


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="uniobfuscator",
        description="多语言代码混淆工具 (Python / JavaScript / Java)",
    )
    p.add_argument("path", nargs="?", help="待混淆的源文件或目录")
    p.add_argument(
        "-l", "--language", choices=LANG_CHOICES,
        help="语言（默认按扩展名自动识别；目录模式下用于过滤）",
    )
    p.add_argument(
        "-o", "--output",
        help="输出路径：单文件为文件路径，目录模式为输出目录（必须指定）",
    )
    p.add_argument("--stdout", action="store_true", help="单文件输出到标准输出而非写文件")
    p.add_argument(
        "--seed", type=int, default=None,
        help="随机种子，保证可复现（默认 0）",
    )
    p.add_argument(
        "--exclude", action="append", metavar="CLASS", default=None,
        help="JAR 模式下跳过混淆的类/包（可重复；如 com.foo.Secret 或 com.foo.*）",
    )
    for flag, default, desc in (
        ("java-arithmetic", True, "JAR 模式整型常量算术混淆"),
        ("java-dead-code", True, "JAR 模式死代码注入（不透明谓词）"),
        ("java-scramble", True, "JAR 模式控制流打散"),
        ("java-rename", False, "JAR 模式类名重命名（改常量池引用，需配合 --exclude）"),
        ("java-member-rename", False, "JAR 模式私有成员重命名（private 方法/字段）"),
        ("java-repackage", False, "JAR 模式包名混淆（平铺到单一短包 + 全局短类名）"),
        ("java-strip-metadata", True, "JAR 模式剥离元数据（泛型签名/throws/不可见注解）"),
    ):
        p.add_argument(
            f"--{flag}", dest=flag.replace("-", "_"),
            action=argparse.BooleanOptionalAction, default=None,
            help=desc + f"（默认{'开启' if default else '关闭'}）",
        )
    p.add_argument(
        "-c", "--config",
        help="配置文件路径（.json / .toml），命令行参数优先于配置文件",
    )
    for flag in ("rename", "strings", "dead-code", "arithmetic"):
        p.add_argument(
            f"--{flag}", dest=flag.replace("-", "_"),
            action=argparse.BooleanOptionalAction, default=None,
            help=f"启用{flag.replace('-', '_')}混淆（默认开启；用 --no-{flag} 关闭）",
        )
    p.add_argument(
        "--module-rename", dest="module_rename",
        action=argparse.BooleanOptionalAction, default=None,
        help="模块级私有名称重命名（类/函数/全局变量，Python 默认开启；"
             "用 --no-module-rename 关闭）",
    )
    p.add_argument(
        "--control-flow", dest="control_flow",
        action=argparse.BooleanOptionalAction, default=None,
        help="控制流混淆（不透明谓词 + 诱饵代码，Python 默认开启；"
             "用 --no-control-flow 关闭）",
    )
    p.add_argument(
        "--flatten", dest="flatten",
        action=argparse.BooleanOptionalAction, default=None,
        help="控制流扁平化（函数体改 while+状态机分派，Python 默认开启；"
             "用 --no-flatten 关闭）",
    )
    p.add_argument("--list-languages", action="store_true", help="列出支持的语言")
    return p


def _build_options(
    args: argparse.Namespace,
    global_cfg: dict | None = None,
    per_language: dict | None = None,
    lang_name: str | None = None,
    features: dict[str, bool] | None = None,
) -> dict:
    """把解析结果转成流水线 options。

    优先级：命令行显式参数 > 按语言配置 > 全局配置 > 语言能力默认 > 内置默认值。
    features 为目标语言/形态的混淆特性出厂默认（adapter.features 或 JAR_FEATURES）；
    布尔开关与 seed 的 default=None 表示"命令行未指定"，
    此时回落到按语言配置 -> 全局配置 -> 语言能力默认 -> 内置默认值。
    """
    options = dict(DEFAULT_OPTIONS)  # 内置默认
    if features:
        options.update(features)  # 语言/形态出厂默认（覆盖内置默认）
    for key in ("seed", "rename", "strings", "dead_code", "arithmetic",
                "module_rename", "control_flow", "flatten", "exclude",
                "java_arithmetic", "java_dead_code", "java_scramble", "java_rename",
                "java_member_rename", "java_repackage", "java_strip_metadata"):
        if key in (global_cfg or {}):
            options[key] = global_cfg[key]  # 全局配置
    if per_language and lang_name:
        options.update(per_language.get(lang_name, {}))  # 按语言配置
    for key in ("seed", "rename", "strings", "dead_code", "arithmetic",
                "module_rename", "control_flow", "flatten", "exclude",
                "java_arithmetic", "java_dead_code", "java_scramble", "java_rename",
                "java_member_rename", "java_repackage", "java_strip_metadata"):
        value = getattr(args, key)
        if value is not None:
            options[key] = value  # 命令行显式参数
    return options


def _warn_inapplicable(
    args: argparse.Namespace,
    active_keys: frozenset[str],
    warned: set[str] | None = None,
) -> None:
    """用户显式传了不属于当前模式开关集合的开关时，警告并忽略。

    active_keys 为当前模式（文本语言 or JAR 字节码）支持的键集合；
    strings 为两种形态共享，不在提示范围。warned 用于目录模式
    （多语言文件循环）下对同一开关只提示一次。
    """
    for key in TEXT_ONLY_KEYS | JAR_ONLY_KEYS:
        value = getattr(args, key, None)
        if key not in active_keys and value is not None:
            if warned is not None and key in warned:
                continue
            mode = "文本源码混淆" if key in TEXT_ONLY_KEYS else "JAR 字节码模式"
            flag = "--" + key.replace("_", "-")
            print(f"警告: {flag} 仅适用于 {mode}，已忽略", file=sys.stderr)
            if warned is not None:
                warned.add(key)


def _apply_config(parser: argparse.ArgumentParser, global_cfg: dict) -> None:
    """把全局配置里的程序级参数（非混淆开关）作为默认值注入，命令行仍可覆盖。

    混淆开关（rename/strings/dead_code/arithmetic/seed）不注入 parser，
    由 _build_options 按“命令行 > 语言级 > 全局 > 默认”统一合并。
    """
    defaults = {
        k: v for k, v in global_cfg.items()
        if k in ("path", "language", "output", "stdout")
    }
    if defaults:
        parser.set_defaults(**defaults)


def _collect_source_files(
    root: str, out_root_abs: str, lang_name: str | None,
) -> tuple[list[str], list[str]]:
    """递归收集根目录下所有受支持扩展名的源文件（跳过输出目录）。

    同时返回非源码的二进制产物（.jar/.class/.pyc 等），用于提醒用户
    这类文件不会被混淆。
    """
    exts = (
        set(get_adapter(lang_name).extensions)
        if lang_name else SUPPORTED_EXTENSIONS
    )
    files: list[str] = []
    ignored: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if os.path.abspath(os.path.join(dirpath, d)) != out_root_abs
        ]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in exts:
                files.append(os.path.join(dirpath, name))
            elif ext in BINARY_ARTIFACT_EXTS:
                ignored.append(os.path.join(dirpath, name))
    return sorted(files), sorted(ignored)


def _obfuscate_jar_file(
    args: argparse.Namespace, global_cfg: dict, per_language: dict,
) -> int:
    """JAR 字节码混淆：解包 -> 逐 .class 混淆 -> 重打包。"""
    if args.stdout:
        print("错误: --stdout 不适用于 JAR 文件", file=sys.stderr)
        return 2
    # JAR 是字节码模式：文本语言的 rename/dead_code/arithmetic 开关不适用，
    # 自动切换到 java_* 系列（strings 为两种形态共享）
    options = _build_options(args, global_cfg, per_language,
                             lang_name=None, features=JAR_FEATURES)
    _warn_inapplicable(args, frozenset(JAR_FEATURES))
    out_path = args.output or f"{os.path.splitext(args.path)[0]}.obf.jar"
    try:
        stats = obfuscate_jar(
            args.path, out_path,
            seed=int(options.get("seed", 0)),
            strings=bool(options.get("strings", True)),
            exclude=options.get("exclude"),
            arithmetic=bool(options.get("java_arithmetic", True)),
            dead_code=bool(options.get("java_dead_code", True)),
            scramble=bool(options.get("java_scramble", True)),
            rename=bool(options.get("java_rename", False)),
            member_rename=bool(options.get("java_member_rename", False)),
            repackage=bool(options.get("java_repackage", False)),
            strip_meta=bool(options.get("java_strip_metadata", True)),
        )
    except (OSError, ValueError) as e:
        print(f"错误: JAR 混淆失败: {e}", file=sys.stderr)
        return 2
    for warning in stats["warnings"]:
        print(f"警告: {warning}", file=sys.stderr)
    print(
        f"JAR 混淆完成: {args.path} -> {out_path} "
        f"（处理 {stats['class']} 个 class，跳过 {stats['skipped']} 个，"
        f"排除 {stats['excluded']} 个，重命名 {stats['renamed']} 个类 / "
        f"{stats['members']} 个成员，剥离元数据 {stats['metadata']}；"
        f"算术 {stats['arithmetic']} / 死代码 {stats['dead_code']} / "
        f"打散 {stats['scramble']}）"
    )
    return 0


def _obfuscate_single_file(
    args: argparse.Namespace, global_cfg: dict, per_language: dict,
) -> int:
    """单文件混淆（保持原有行为）。"""
    if is_jar_file(args.path):
        return _obfuscate_jar_file(args, global_cfg, per_language)
    try:
        adapter = (
            get_adapter(args.language) if args.language
            else adapter_for_filename(args.path)
        )
    except KeyError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    options = _build_options(args, global_cfg, per_language,
                             adapter.name, features=adapter.features)
    _warn_inapplicable(args, frozenset(adapter.features))

    try:
        with open(args.path, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"错误: 无法读取文件 {args.path}: {e}", file=sys.stderr)
        return 2

    try:
        result = obfuscate(source, adapter, options)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    if args.output:
        out_path = args.output
    elif args.stdout:
        sys.stdout.write(result)
        return 0
    else:
        stem, ext = os.path.splitext(args.path)
        out_path = f"{stem}.obf{ext}"

    try:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            f.write(result)
    except OSError as e:
        print(f"错误: 无法写入 {out_path}: {e}", file=sys.stderr)
        return 2
    print(f"混淆完成: {args.path} -> {out_path} ({adapter.display_name})")
    return 0


def _obfuscate_directory(
    args: argparse.Namespace, global_cfg: dict, per_language: dict,
) -> int:
    """目录混淆：递归收集 -> 逐个混淆 -> 镜像输出（保持文件名）。"""
    if not args.output:
        print("错误: 混淆目录时必须指定 -o 输出目录", file=sys.stderr)
        return 2
    in_root = os.path.abspath(args.path)
    out_root = os.path.abspath(args.output)
    if in_root == out_root:
        print("错误: 输出目录不能与输入目录相同（避免覆盖源码）", file=sys.stderr)
        return 2

    files, ignored = _collect_source_files(in_root, out_root, args.language)
    if not files:
        print("警告: 未找到可混淆的源文件", file=sys.stderr)
        return 0
    if ignored:
        print(
            f"  注意: 跳过 {len(ignored)} 个非源码文件"
            "（.jar/.class/.pyc 等，需先反编译/解压为源码再混淆）",
            file=sys.stderr,
        )

    count, failed = 0, 0
    warned: set[str] = set()  # 目录模式：同一不适用开关只提示一次
    for src in files:
        rel = os.path.relpath(src, in_root)
        dst = os.path.join(out_root, rel)
        try:
            adapter = (
                get_adapter(args.language) if args.language
                else adapter_for_filename(src)
            )
            options = _build_options(args, global_cfg, per_language,
                                     adapter.name, features=adapter.features)
            _warn_inapplicable(args, frozenset(adapter.features), warned)
            obfuscate_file(src, dst, adapter, options)
            print(f"  混淆: {rel} ({adapter.display_name})")
            count += 1
        except ValueError as e:
            print(f"  跳过: {rel}: {e}", file=sys.stderr)
            failed += 1
    print(f"目录混淆完成: {count} 个文件 -> {out_root}" + (f"，跳过 {failed} 个" if failed else ""))
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    # 第一遍：先取出 --config 路径（命令行仍优先，见 _build_options 合并规则）
    pre, _ = parser.parse_known_args(argv)
    global_cfg: dict = {}
    per_language: dict = {}
    if pre.config:
        try:
            cfg = load_config(pre.config)
            global_cfg, per_language = split_config(cfg)
        except ValueError as e:
            print(f"错误: 配置文件 {pre.config}: {e}", file=sys.stderr)
            return 2
        _apply_config(parser, global_cfg)
    else:
        # 未指定 -c：若无默认配置则自动生成；存在默认配置则自动加载
        created = ensure_default_config()
        default_path = os.path.join(DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE)
        if os.path.exists(default_path):
            try:
                cfg = load_config(default_path)
                global_cfg, per_language = split_config(cfg)
            except ValueError as e:
                print(f"警告: 默认配置 {default_path} 解析失败，已忽略: {e}", file=sys.stderr)
            _apply_config(parser, global_cfg)
        if created:
            print(
                f"已生成默认配置文件: {default_path}（编辑后自动生效，或用 -c 指定其它配置）",
                file=sys.stderr,
            )
    # 第二遍：完整解析，命令行显式给出的参数会覆盖配置文件
    args = parser.parse_args(argv)

    if args.list_languages:
        for item in list_languages():
            print(
                f"{item['name']:<10} {item['display']:<12} "
                f"扩展名: {', '.join(item['extensions'])}"
            )
        return 0

    if not args.path:
        print("错误: 请指定源文件或目录（或使用 --list-languages）", file=sys.stderr)
        return 2

    if args.stdout and os.path.isdir(args.path):
        print("错误: --stdout 仅支持单文件", file=sys.stderr)
        return 2

    if os.path.isdir(args.path):
        return _obfuscate_directory(args, global_cfg, per_language)
    return _obfuscate_single_file(args, global_cfg, per_language)


if __name__ == "__main__":
    sys.exit(main())
