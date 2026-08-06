# -*- coding: utf-8 -*-
"""uniobfuscator 图形界面（Flet）。

运行方式：
  python -m uniobfuscator.gui
  uniobfuscator-gui

界面功能：选择输入（文件/目录/JAR）、语言、输出、随机种子、
四个混淆开关、可选配置文件、运行日志。
混淆在后台线程执行，不阻塞界面。
"""
from __future__ import annotations

import contextlib
import io
import sys

import flet as ft

from .cli import main as cli_main
from .config import load_config, split_config

LANG_OPTIONS = [
    ft.DropdownOption(key="auto", text="自动识别"),
    ft.DropdownOption(key="python", text="Python"),
    ft.DropdownOption(key="javascript", text="JavaScript"),
    ft.DropdownOption(key="java", text="Java"),
]


class UniObfuscatorApp:
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "uniobfuscator - 多语言代码混淆工具"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.padding = 16
        page.spacing = 12

        # 文件选择器（flet 0.86 为同步 API）
        self.picker = ft.FilePicker()

        # 顶部提示条（复用实例，避免 overlay 堆积）
        self.snack = ft.SnackBar(ft.Text(""))
        page.overlay.append(self.snack)
        page.overlay.append(self.picker)

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.input_field = ft.TextField(
            label="输入（文件 / 目录 / JAR）", expand=True,
            hint_text="例如 app.py、src/、app.jar",
        )
        self.output_field = ft.TextField(
            label="输出（留空自动生成）", expand=True,
            hint_text="单文件 *.obf.ext；目录模式必须填写输出目录",
        )
        self.config_field = ft.TextField(
            label="配置文件（可选，.json / .toml）", expand=True,
        )
        self.lang_dropdown = ft.Dropdown(
            label="语言", options=LANG_OPTIONS, value="auto", width=160,
        )
        self.seed_field = ft.TextField(
            label="随机种子", value="0", width=130,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self.sw_rename = ft.Switch(label="标识符重命名", value=True)
        self.sw_strings = ft.Switch(label="字符串加密", value=True)
        self.sw_dead_code = ft.Switch(label="死代码注入", value=True)
        self.sw_arithmetic = ft.Switch(label="算术混淆", value=True)

        self.run_btn = ft.FilledButton(
            "开始混淆", icon=ft.Icons.PLAY_ARROW, on_click=self._on_run,
        )
        self.progress = ft.ProgressRing(width=18, height=18, visible=False)
        self.log_view = ft.ListView(expand=True, auto_scroll=True, spacing=2)

        self.page.add(
            ft.Row([
                ft.Text("uniobfuscator", size=22, weight=ft.FontWeight.BOLD),
                ft.Text("多语言代码混淆工具", color=ft.Colors.GREY),
            ]),
            ft.Divider(),
            ft.Row([
                self.input_field,
                ft.OutlinedButton("选择文件", icon=ft.Icons.FILE_OPEN,
                                  on_click=self._on_pick_input_file),
                ft.OutlinedButton("选择目录", icon=ft.Icons.FOLDER_OPEN,
                                  on_click=self._on_pick_input_dir),
            ]),
            ft.Row([
                self.output_field,
                ft.OutlinedButton("另存为", icon=ft.Icons.SAVE_OUTLINED,
                                  on_click=self._on_pick_output),
            ]),
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
            ft.Row([self.run_btn, self.progress]),
            ft.Text("输出日志", size=13, color=ft.Colors.GREY),
            self.log_view,
        )

    # ------------------------------------------------------------- 文件选择
    def _on_pick_input_file(self, _e) -> None:
        files = self.picker.pick_files(
            allowed_extensions=["py", "js", "java", "jar"],
            allow_multiple=False,
        )
        if files:
            self.input_field.value = files[0].path
            self.page.update()

    def _on_pick_input_dir(self, _e) -> None:
        path = self.picker.get_directory_path()
        if path:
            self.input_field.value = path
            self.page.update()

    def _on_pick_output(self, _e) -> None:
        path = self.picker.save_file()
        if path:
            self.output_field.value = path
            self.page.update()

    # ------------------------------------------------------------- 加载配置
    def _on_load_config(self, _e) -> None:
        conf = self.config_field.value.strip()
        if not conf:
            files = self.picker.pick_files(
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
        # 填入表单（全局项；按语言配置由运行时 -c 自动应用）
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
                self.log_view.controls.append(ft.Text(line))
        else:
            self.log_view.controls.append(ft.Text("（无输出）"))
        self.log_view.controls.append(ft.Text(
            f"— 结束，退出码 {rc}",
            color=ft.Colors.ERROR if rc != 0 else ft.Colors.GREEN,
        ))
        self._snack("混淆完成" if rc == 0 else "混淆失败", is_error=rc != 0)
        self.page.update()

    # ------------------------------------------------------------- 工具
    def _snack(self, msg: str, is_error: bool = False) -> None:
        self.snack.content = ft.Text(msg)
        self.snack.bgcolor = ft.Colors.ERROR if is_error else None
        self.snack.open = True


def main() -> None:
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
