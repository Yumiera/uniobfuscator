# -*- coding: utf-8 -*-
"""JAR 打包/重打包：解包 -> 逐 .class 混淆 -> 重打包（保留元数据）。"""
from __future__ import annotations

import os
import zipfile

from .classfile import parse_class_file
from .passes import (
    arithmetic_obfuscate,
    encrypt_strings,
    inject_dead_code,
    remove_debug_info,
    scramble_control_flow,
)
from .rename import apply_rename, build_rename_map

_SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")
_VERSION_PREFIX = "META-INF/versions/"

#: JAR 字节码模式支持的混淆特性及出厂默认开关（CLI/配置中的键名）。
#: strings 与文本语言共享（都是字符串加密）；java_* 为 JAR 专属。
#: java_rename 破坏性最强（改常量池类名引用），默认关闭。
JAR_FEATURES = {
    "strings": True,
    "java_arithmetic": True,
    "java_dead_code": True,
    "java_scramble": True,
    "java_rename": False,
}


def _compile_excludes(patterns) -> list[tuple[str, str]]:
    """把用户排除模式编译为 (kind, value) 列表。

    - 'com.foo.Secret'   -> ('exact', 'com/foo/Secret')   精确类
    - 'com.foo.secret.*' -> ('prefix', 'com/foo/secret/') 包前缀（含子包）
    """
    rules: list[tuple[str, str]] = []
    for p in patterns or ():
        p = p.strip().replace(".", "/")
        if not p:
            continue
        if p.endswith("/*"):
            rules.append(("prefix", p[:-2] + "/"))
        else:
            rules.append(("exact", p))
    return rules


def _is_excluded(rules, internal_name: str) -> bool:
    for kind, value in rules:
        if kind == "exact" and internal_name == value:
            return True
        if kind == "prefix" and internal_name.startswith(value):
            return True
    return False


def _real_internal(zip_name: str) -> str:
    """zip 内 .class 路径 -> 实际内部类名（多重发布 jar 还原为包名）。"""
    internal = zip_name[:-6]  # 去掉 ".class"
    if internal.startswith(_VERSION_PREFIX):
        rest = internal[len(_VERSION_PREFIX):]
        _, _, real = rest.partition("/")
        internal = real or rest
    return internal


def _new_zip_name(zip_name: str, internal: str, name_map: dict[str, str]) -> str:
    """按重命名映射计算新 zip 路径（保留多重发布版本前缀）。"""
    new = name_map.get(internal)
    if not new:
        return zip_name
    if zip_name.startswith(_VERSION_PREFIX):
        rest = zip_name[len(_VERSION_PREFIX):]
        ver, _, _ = rest.partition("/")
        return f"{_VERSION_PREFIX}{ver}/{new}.class"
    return f"{new}.class"


def _rewrite_manifest(data: bytes, name_map: dict[str, str]) -> bytes:
    """同步 MANIFEST 的 Main-Class（点分 <-> 内部名）。"""
    if not name_map:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    lines = []
    changed = False
    for line in text.splitlines(keepends=True):
        if line.startswith("Main-Class:"):
            cls = line.split(":", 1)[1].strip()
            new = name_map.get(cls.replace(".", "/"))
            if new:
                line = f"Main-Class: {new.replace('/', '.')}\r\n"
                changed = True
        lines.append(line)
    return "".join(lines).encode("utf-8") if changed else data


def obfuscate_jar(src_jar: str, dst_jar: str, seed: int = 0,
                  strings: bool = True, exclude: list[str] | None = None,
                  arithmetic: bool = True, dead_code: bool = True,
                  scramble: bool = True, rename: bool = False) -> dict:
    """混淆 JAR 文件。

    exclude: 不参与混淆的类/包（原样复制，连调试信息都保留）。
      - 'com.foo.Secret'    精确类名（点分）
      - 'com.foo.secret.*'  包前缀，匹配该包及所有子包
      - 多重发布 jar（META-INF/versions/N/）下版本化类按实际包名参与匹配
      - 注意：开启 rename 时，被排除类对其它类的引用仍会同步更新
        （否则重命名后必然 ClassNotFoundException）。

    arithmetic/dead_code/scramble: 整型常量算术混淆 / 死代码注入 /
    控制流打散（仅对安全的无分支方法生效）。

    rename: 类名重命名（保留包路径）。Class.forName 等按名字符串无法
    跟踪，需用 exclude 排除对应类；MANIFEST Main-Class 会自动更新。

    返回统计信息：{"class": 处理数, "skipped": 解析失败的 class 数,
    "excluded": 被排除的 class 数, "signature": 检测到签名文件数,
    "renamed": 重命名的类数, "arithmetic"/"dead_code"/"scramble":
    各 pass 生效次数, "warnings": [警告列表]}。
    """
    if os.path.abspath(src_jar) == os.path.abspath(dst_jar):
        raise ValueError("输出 JAR 不能与输入 JAR 相同（避免覆盖）")
    try:
        zin = zipfile.ZipFile(src_jar, "r")
    except zipfile.BadZipFile as e:
        raise ValueError(f"不是合法的 JAR/ZIP 文件: {e}") from e

    rules = _compile_excludes(exclude)
    internals = _scan_internal_names(zin)
    excluded_set = {n for n in internals if _is_excluded(rules, n)}
    name_map = build_rename_map(internals, seed, excluded_set) if rename else {}
    stats: dict = {
        "class": 0, "skipped": 0, "excluded": 0, "renamed": len(name_map),
        "arithmetic": 0, "dead_code": 0, "scramble": 0,
        "signature": 0, "warnings": [],
    }
    with zin, zipfile.ZipFile(dst_jar, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            data = zin.read(info.filename)
            if name.endswith(".class"):
                internal = _real_internal(name)
                if rules and _is_excluded(rules, internal):
                    stats["excluded"] += 1
                    if rename and name_map:
                        # 被排除类不做混淆，但引用重命名类时需同步更新
                        try:
                            cf = parse_class_file(data)
                            apply_rename(cf, name_map)
                            data = cf.serialize()
                        except ValueError:
                            pass
                    zout.writestr(_new_zip_name(name, internal, name_map), data)
                    continue
                try:
                    cf = parse_class_file(data)
                    remove_debug_info(cf)
                    if rename and name_map:
                        apply_rename(cf, name_map)
                    if strings:
                        encrypt_strings(cf, seed)
                    # 顺序：算术先跑（仅替换常量，不引入分支/局部变量）；
                    # 打散后跑（要求方法不写局部变量）——若算术后跑，
                    # 替换常量会把 SMT 跳转目标指令删掉导致映射缺失；
                    # 注死代码最后（要求方法无分支，打散后的方法带 SMT 自动跳过）。
                    if arithmetic:
                        stats["arithmetic"] += arithmetic_obfuscate(cf, seed)
                    if scramble:
                        stats["scramble"] += scramble_control_flow(cf, seed)
                    if dead_code:
                        stats["dead_code"] += inject_dead_code(cf, seed)
                    zout.writestr(_new_zip_name(name, internal, name_map), cf.serialize())
                    stats["class"] += 1
                except ValueError as e:
                    stats["skipped"] += 1
                    stats["warnings"].append(f"跳过 {name}: {e}")
                    zout.writestr(_new_zip_name(name, internal, name_map), data)
            else:
                base = name.upper()
                if any(base.endswith(suf) for suf in _SIGNATURE_SUFFIXES):
                    stats["signature"] += 1
                    if not any("签名" in w for w in stats["warnings"]):
                        stats["warnings"].append(
                            "检测到 JAR 签名文件（META-INF/*.SF 等），"
                            "混淆后签名将失效，需重新签名"
                        )
                if name.upper() == "META-INF/MANIFEST.MF":
                    data = _rewrite_manifest(data, name_map)
                zout.writestr(info, data)
    return stats


def _scan_internal_names(zin: zipfile.ZipFile) -> list[str]:
    """预扫描所有 .class 的真实内部名（用于类名重命名映射）。"""
    return [_real_internal(name) for name in zin.namelist() if name.endswith(".class")]


def is_jar_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".jar"
