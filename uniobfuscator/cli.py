# -*- coding: utf-8 -*-
"""uniobfuscator 命令行入口。

用法示例：
  uniobfuscator app.py -o app_obf.py            # 单文件
  uniobfuscator app.js --no-rename --stdout     # 单文件输出到标准输出
  uniobfuscator src/ -o out/                    # 整个目录（镜像结构、保持文件名）
  uniobfuscator src/ -o out/ -l java            # 只混淆 Java 文件
  uniobfuscator -c conf.json                    # 全部参数由配置文件提供
  uniobfuscator -c conf.json src/ --seed 9      # 配置文件 + 命令行覆盖（命令行优先）
  uniobfuscator app.jar -o app_obf.jar          # JAR 字节码混淆（调试移除 + 字符串加密）
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
    from .jvm import is_jar_file, obfuscate_jar
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
    from uniobfuscator.jvm import is_jar_file, obfuscate_jar
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
        "-c", "--config",
        help="配置文件路径（.json / .toml），命令行参数优先于配置文件",
    )
    for flag in ("rename", "strings", "dead_code", "arithmetic"):
        p.add_argument(
            f"--{flag}", action=argparse.BooleanOptionalAction, default=None,
            help=f"启用{flag}混淆（默认开启；用 --no-{flag} 关闭）",
        )
    p.add_argument("--list-languages", action="store_true", help="列出支持的语言")
    return p


def _build_options(
    args: argparse.Namespace,
    global_cfg: dict | None = None,
    per_language: dict | None = None,
    lang_name: str | None = None,
) -> dict:
    """把解析结果转成流水线 options。

    优先级：命令行显式参数 > 按语言配置 > 全局配置 > 内置默认值。
    布尔开关与 seed 的 default=None 表示“命令行未指定”，
    此时回落到按语言配置 -> 全局配置 -> 内置默认值。
    """
    options = dict(DEFAULT_OPTIONS)  # 内置默认
    for key in ("seed", "rename", "strings", "dead_code", "arithmetic"):
        if key in (global_cfg or {}):
            options[key] = global_cfg[key]  # 全局配置
    if per_language and lang_name:
        options.update(per_language.get(lang_name, {}))  # 按语言配置
    for key in ("seed", "rename", "strings", "dead_code", "arithmetic"):
        value = getattr(args, key)
        if value is not None:
            options[key] = value  # 命令行显式参数
    return options


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
    # jar 是字节码模式：只应用 seed 与字符串加密开关（无语言级配置）
    options = _build_options(args, global_cfg, per_language, lang_name=None)
    if options.get("rename") is False or options.get("dead_code") is False \
            or options.get("arithmetic") is False:
        print(
            "注意: JAR 字节码模式只做调试信息移除 + 字符串加密，"
            "--no-rename/--no-dead-code/--no-arithmetic 不适用（已忽略）",
            file=sys.stderr,
        )
    out_path = args.output or f"{os.path.splitext(args.path)[0]}.obf.jar"
    try:
        stats = obfuscate_jar(
            args.path, out_path,
            seed=int(options.get("seed", 0)),
            strings=bool(options.get("strings", True)),
        )
    except (OSError, ValueError) as e:
        print(f"错误: JAR 混淆失败: {e}", file=sys.stderr)
        return 2
    for warning in stats["warnings"]:
        print(f"警告: {warning}", file=sys.stderr)
    print(
        f"JAR 混淆完成: {args.path} -> {out_path} "
        f"（处理 {stats['class']} 个 class，跳过 {stats['skipped']} 个）"
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
    options = _build_options(args, global_cfg, per_language, adapter.name)

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
    for src in files:
        rel = os.path.relpath(src, in_root)
        dst = os.path.join(out_root, rel)
        try:
            adapter = (
                get_adapter(args.language) if args.language
                else adapter_for_filename(src)
            )
            options = _build_options(args, global_cfg, per_language, adapter.name)
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
