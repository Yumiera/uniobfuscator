# -*- coding: utf-8 -*-
"""uniobfuscator 图形界面（Flet）。

深色 OLED 开发者主题：深蓝黑背景 + 绿色"运行"按钮 + 等宽字体日志。
设计系统参考 ui-ux-pro-max（Dark Mode / JetBrains Mono / 终端风格）。

运行方式：
  python -m uniobfuscator.gui
  uniobfuscator-gui
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

import flet as ft

from .cli import main as cli_main
from .config import ensure_default_config, load_config, split_config

LANG_OPTIONS = [
    ft.DropdownOption(key="auto", text="自动识别"),
    ft.DropdownOption(key="python", text="Python"),
    ft.DropdownOption(key="javascript", text="JavaScript"),
    ft.DropdownOption(key="java", text="Java"),
]

# 设计令牌（ui-ux-pro-max: Dark Mode (OLED) + Code dark, run green）
BG = "#0F172A"       # 背景（深蓝黑）
BG_DEEP = "#0B1120"  # 日志区更深
CARD = "#1E293B"     # 卡片 surface
FG = "#F8FAFC"       # 前景文字
MUTED = "#94A3B8"    # 次级文字
OUTLINE = "#334155"  # 边框/分隔线
ACCENT = "#22C55E"   # 运行绿（主操作）
DANGER = "#EF4444"   # 错误红
MONO = "monospace"   # 日志等宽字体


class UniObfuscatorApp:
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "uniobfuscator"
        page.padding = 20
        page.spacing = 14
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=ACCENT,
                on_primary=BG,
                primary_container="#14532D",
                on_primary_container=FG,
                secondary="#38BDF8",
                on_secondary=BG,
                surface=BG,
                on_surface=FG,
                surface_container_low="#111C2E",
                surface_container_lowest=BG_DEEP,
                surface_container_highest=CARD,
                on_surface_variant=MUTED,
                outline=OUTLINE,
                outline_variant="#1E293B",
                error=DANGER,
                on_error="#FFFFFF",
            )
        )

        self.picker = ft.FilePicker()  # Service 控件：构造时自动注册，勿加入 overlay

        # 顶部提示条（复用实例，避免 overlay 堆积）
        self.snack = ft.SnackBar(ft.Text(""))
        page.overlay.append(self.snack)

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.input_field = ft.TextField(
            label="输入（文件 / 目录 / JAR）", expand=True,
            hint_text="例如 app.py、src/、app.jar",
            prefix_icon=ft.Icons.FILE_OPEN,
        )
        self.output_field = ft.TextField(
            label="输出（留空自动生成）", expand=True,
            hint_text="单文件 *.obf.ext；目录模式必须填写输出目录",
            prefix_icon=ft.Icons.FOLDER_OPEN,
        )
        self.config_field = ft.TextField(
            label="配置文件（可选，.json / .toml）", expand=True,
            prefix_icon=ft.Icons.TUNE,
        )
        self.lang_dropdown = ft.Dropdown(
            label="语言", options=LANG_OPTIONS, value="auto", width=170,
        )
        self.seed_field = ft.TextField(
            label="随机种子", value="0", width=130,
            keyboard_type=ft.KeyboardType.NUMBER,
            prefix_icon=ft.Icons.TAG,
        )
        self.sw_rename = ft.Switch(label="标识符重命名", value=True)
        self.sw_strings = ft.Switch(label="字符串加密", value=True)
        self.sw_dead_code = ft.Switch(label="死代码注入", value=True)
        self.sw_arithmetic = ft.Switch(label="算术混淆", value=True)

        self.run_btn = ft.FilledButton(
            "开始混淆",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_run,
            style=ft.ButtonStyle(
                padding=ft.Padding(24, 14, 24, 14),
                text_style=ft.TextStyle(size=15, weight=ft.FontWeight.BOLD),
            ),
        )
        self.status_text = ft.Text(
            "就绪", size=13, color=MUTED, font_family=MONO,
        )
        self.progress = ft.ProgressRing(width=18, height=18, visible=False)
        self.log_view = ft.ListView(
            expand=True, auto_scroll=True, spacing=3, padding=8,
        )

        self.page.add(
            self._build_header(),
            self._section("输入 / 输出", [
                ft.Row([
                    self.input_field,
                    ft.OutlinedButton("选择文件", icon=ft.Icons.DESCRIPTION,
                                      on_click=self._on_pick_input_file),
                    ft.OutlinedButton("选择目录", icon=ft.Icons.CREATE_NEW_FOLDER,
                                      on_click=self._on_pick_input_dir),
                ]),
                ft.Row([
                    self.output_field,
                    ft.OutlinedButton("另存为", icon=ft.Icons.SAVE_OUTLINED,
                                      on_click=self._on_pick_output),
                ]),
            ]),
            self._section("混淆选项", [
                ft.Row([
                    self.lang_dropdown,
                    self.seed_field,
                    self.config_field,
                    ft.OutlinedButton("加载配置", icon=ft.Icons.SETTINGS,
                                      on_click=self._on_load_config),
                ]),
                ft.Row([
                    self.sw_rename, self.sw_strings,
                    self.sw_dead_code, self.sw_arithmetic,
                ]),
            ]),
            ft.Row([
                self.run_btn,
                self.progress,
                self.status_text,
            ]),
            ft.Column([
                ft.Text("输出日志", size=12, weight=ft.FontWeight.BOLD,
                        color=MUTED),
                ft.Container(
                    content=self.log_view,
                    expand=True,
                    height=260,
                    bgcolor=BG_DEEP,
                    border=ft.Border.all(1, OUTLINE),
                    border_radius=8,
                ),
            ]),
        )

    def _build_header(self) -> ft.Control:
        logo = ft.Container(
            width=44, height=44,
            bgcolor=ACCENT,
            border_radius=10,
            alignment=ft.alignment.Alignment.CENTER,
            content=ft.Text("U", size=24, weight=ft.FontWeight.W_900,
                            color=BG),
        )
        title = ft.Column([
            ft.Text("uniobfuscator", size=20, weight=ft.FontWeight.BOLD,
                    font_family=MONO),
            ft.Text("Multi-language Code Obfuscation Tool",
                    size=12, color=MUTED),
        ], spacing=2)
        version = ft.Text("v0.1.0", size=12, color=MUTED, font_family=MONO)
        return ft.Row([
            logo,
            title,
            ft.Container(expand=True),
            ft.Container(
                content=version,
                padding=ft.Padding(10, 6, 10, 6),
                bgcolor=CARD,
                border_radius=6,
            ),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _section(self, title: str, controls: list) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Column([
                    ft.Text(title, size=12, weight=ft.FontWeight.BOLD,
                            color=ACCENT),
                    *controls,
                ], spacing=12),
            )
        )

    # ------------------------------------------------------------- 文件选择
    async def _on_pick_input_file(self, _e) -> None:
        files = await self.picker.pick_files(
            allowed_extensions=["py", "js", "java", "jar"],
            allow_multiple=False,
        )
        if not files:
            return
        path = files[0].path
        self.input_field.value = path
        self._auto_detect_language(path)
        self.page.update()

    async def _on_pick_input_dir(self, _e) -> None:
        path = await self.picker.get_directory_path()
        if path:
            self.input_field.value = path
            self.page.update()

    async def _on_pick_output(self, _e) -> None:
        path = await self.picker.save_file()
        if path:
            self.output_field.value = path
            self.page.update()

    def _auto_detect_language(self, path: str) -> None:
        """选择文件后按扩展名自动切换语言（用户未手动选择时）。"""
        if self.lang_dropdown.value != "auto":
            return
        ext = os.path.splitext(path)[1].lower()
        mapping = {".py": "python", ".js": "javascript", ".java": "java"}
        if ext in mapping:
            self.lang_dropdown.value = mapping[ext]

    # ------------------------------------------------------------- 加载配置
    async def _on_load_config(self, _e) -> None:
        conf = self.config_field.value.strip()
        if not conf:
            files = await self.picker.pick_files(
                allowed_extensions=["json", "toml"], allow_multiple=False,
            )
            if not files:
                return
            conf = files[0].path
            self.config_field.value = conf
        try:
            cfg = load_config(conf)
        except ValueError as ex:
            self._snack(f"配置加载失败: {ex}", is_error=True)
            return
        global_cfg, per_language = split_config(cfg)
        if "path" in global_cfg:
            self.input_field.value = str(global_cfg["path"])
        if "output" in global_cfg:
            self.output_field.value = str(global_cfg["output"])
        if "seed" in global_cfg:
            self.seed_field.value = str(global_cfg["seed"])
        if "language" in global_cfg:
            self.lang_dropdown.value = global_cfg["language"]
        for name, sw in (
            ("rename", self.sw_rename),
            ("strings", self.sw_strings),
            ("dead_code", self.sw_dead_code),
            ("arithmetic", self.sw_arithmetic),
        ):
            if name in global_cfg:
                sw.value = bool(global_cfg[name])
        suffix = "（含按语言配置，运行时自动应用）" if per_language else ""
        self._snack(f"已加载配置: {conf}{suffix}")
        self.page.update()

    # ------------------------------------------------------------- 运行
    def _build_argv(self) -> list[str] | None:
        inp = self.input_field.value.strip()
        if not inp:
            self._snack("请先填写输入路径", is_error=True)
            return None
        try:
            seed = int(self.seed_field.value.strip() or "0")
        except ValueError:
            self._snack("随机种子必须是整数", is_error=True)
            return None
        argv = [inp]
        out = self.output_field.value.strip()
        if out:
            argv += ["-o", out]
        lang = self.lang_dropdown.value
        if lang and lang != "auto":
            argv += ["-l", lang]
        argv += ["--seed", str(seed)]
        if not self.sw_rename.value:
            argv.append("--no-rename")
        if not self.sw_strings.value:
            argv.append("--no-strings")
        if not self.sw_dead_code.value:
            argv.append("--no-dead-code")
        if not self.sw_arithmetic.value:
            argv.append("--no-arithmetic")
        conf = self.config_field.value.strip()
        if conf:
            argv += ["-c", conf]
        return argv

    def _on_run(self, _e) -> None:
        argv = self._build_argv()
        if argv is None:
            return
        self.run_btn.disabled = True
        self.progress.visible = True
        self.status_text.value = "混淆中…"
        self.status_text.color = ACCENT
        self.page.update()
        self.page.run_thread(self._run_worker, argv)

    def _run_worker(self, argv: list[str]) -> None:
        """后台线程执行 CLI，捕获输出后回到主线程更新 UI。"""
        buf = io.StringIO()
        rc = 1
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = cli_main(argv)
        except Exception as ex:  # 兜底，避免线程崩溃
            buf.write(f"意外错误: {ex}\n")
        self.page.run_task(self._finish, rc, buf.getvalue())

    async def _finish(self, rc: int, text: str) -> None:
        self.run_btn.disabled = False
        self.progress.visible = False
        lines = text.rstrip("\n")
        if lines:
            for line in lines.splitlines():
                self.log_view.controls.append(ft.Text(
                    line, font_family=MONO, size=13,
                    color=FG if not line.strip().startswith("错误") else DANGER,
                ))
        else:
            self.log_view.controls.append(ft.Text(
                "（无输出）", font_family=MONO, size=13, color=MUTED,
            ))
        self.status_text.value = f"完成，退出码 {rc}" if rc == 0 else f"失败，退出码 {rc}"
        self.status_text.color = ACCENT if rc == 0 else DANGER
        self._snack("混淆完成" if rc == 0 else "混淆失败", is_error=rc != 0)
        self.page.update()

    # ------------------------------------------------------------- 工具
    def _snack(self, msg: str, is_error: bool = False) -> None:
        self.snack.content = ft.Text(msg)
        self.snack.bgcolor = DANGER if is_error else "#14532D"
        self.snack.open = True


def main() -> None:
    created = ensure_default_config()
    if created:
        print(f"已生成默认配置文件: {created}（编辑后自动生效）", file=sys.stderr)
    try:
        ft.run(UniObfuscatorApp)
    except ImportError:
        print(
            "错误: 缺少 flet 依赖，请执行 pip install flet（或 pip install uniobfuscator[gui]）",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
