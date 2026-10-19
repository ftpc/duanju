#!/usr/bin/env python3
"""短剧下载器（GUI 版）入口。

用法：
    python app.py                # 正常启动
    python app.py --version      # 打印版本
    python app.py --smoke-shot DIR   # 离屏渲染界面截图（开发自检）

注意：运行时不需要管理员权限；配置与日志在 %APPDATA%\\DuanjuDownloader。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duanju.version import APP_NAME, APP_TITLE, VERSION  # noqa: E402

logger = logging.getLogger(__name__)


def _resource_dir(name: str) -> Path:
    """兼容源码运行与 PyInstaller 打包后的资源目录。"""
    bases = [Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)), PROJECT_ROOT]
    for base in bases:
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT / name


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} v{VERSION}")
    parser.add_argument("--version", action="store_true", help="打印版本后退出")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="打印运行环境 / 数据目录诊断信息后退出",
    )
    parser.add_argument(
        "--smoke-shot",
        metavar="DIR",
        help="离屏渲染各页面截图到目录（开发自检用）",
    )
    return parser.parse_args(argv)


def _run_smoke_shot(output_dir: str) -> int:
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication

    from duanju.config import Config
    from duanju.logging_setup import setup_logging
    from duanju.service import Service
    from ui.bridge import Bridge
    from ui.main_window import MainWindow
    from ui.theme import stylesheet

    temp_base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "")
    if not temp_base.is_absolute():
        temp_base = Path.home() / "AppData" / "Local" / "Temp"
    temp_base.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="duanju-smoke-", dir=str(temp_base)))
    os.environ["APPDATA"] = str(temp_root)
    config = Config()
    config.download_dir = str(temp_root / "downloads")
    config._path = temp_root / "config.json"  # noqa: SLF001
    config.ensure_dirs()

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(stylesheet(config.theme))
    project_icon = _resource_dir("assets") / "app.ico"
    if project_icon.is_file():
        app.setWindowIcon(QIcon(str(project_icon)))

    # 离屏渲染时 Qt 不会自动加载系统字体，手动注册中文字体，保证截图可读
    for font_file in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if Path(font_file).is_file():
            QFontDatabase.addApplicationFont(font_file)
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            break

    bridge = Bridge()
    setup_logging(config, bridge.log_message.emit)
    service = Service(config)
    bridge.attach(service)

    window = MainWindow(config, service, bridge)
    window.load_demo()
    window.resize(1280, 840)
    window.show()
    app.processEvents()

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    shots = {
        0: "01-search.png",
        1: "02-downloads.png",
        2: "03-library.png",
        3: "04-settings.png",
    }
    for index, name in shots.items():
        window.switch_page(index)
        for _ in range(3):
            app.processEvents()
            time.sleep(0.03)
        window.grab().save(str(output / name))
    window.switch_page(0)
    for _ in range(3):
        app.processEvents()
    window.grab().save(str(output / "00-main.png"))

    try:
        service.db.close()
        service.client.close()
    except Exception:  # noqa: BLE001
        pass
    print(f"smoke screenshots saved to {output}")
    return 0


def _bootstrap_help(extra: str = "") -> str:
    from duanju.winenv import integrity_level

    lines = []
    if extra:
        lines.append(extra)
        lines.append("")
    lines.extend(
        [
            f"当前进程完整性级别：{integrity_level()}",
            f"程序路径：{sys.executable}",
            "",
            "如果级别是 low/untrusted，说明程序位于受沙箱/低完整性限制的目录"
            "（例如 DSH 工作区）中运行，无法写入 %APPDATA%。",
            "请把程序解压到桌面或普通文件夹（如 C:\\Users\\<你>\\Desktop、D:\\Tools）后重试。",
            "也可以直接运行源码版 run.bat（它使用系统 Python，不受该限制）。",
        ]
    )
    return "\n".join(lines)


def _diagnose() -> int:
    import json

    from duanju.config import Config
    from duanju.winenv import integrity_level

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    info: dict[str, object] = {
        "version": VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "integrity": integrity_level(),
        "appdata": os.environ.get("APPDATA"),
    }
    code = 0
    try:
        config = Config.load()
        config.ensure_dirs()
        info.update(
            {
                "data_dir": str(config.data_dir),
                "config_path": str(config.path),
                "download_dir": config.download_dir,
                "ffmpeg": config.resolved_ffmpeg(),
                "warning": config.warning,
            }
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        code = 1
    text = json.dumps(info, ensure_ascii=False, indent=2)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path.cwd()
    diagnose_file = base / "diagnose.json"
    try:
        diagnose_file.write_text(text, encoding="utf-8")
    except OSError:
        diagnose_file = Path(tempfile.gettempdir()) / "duanju-diagnose.json"
        try:
            diagnose_file.write_text(text, encoding="utf-8")
        except OSError:
            diagnose_file = None
    if diagnose_file is not None:
        info["diagnose_file"] = str(diagnose_file)
        text = json.dumps(info, ensure_ascii=False, indent=2)
    if getattr(sys, "frozen", False):
        # windowed exe 没有控制台，改为弹窗展示
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([sys.argv[0]])
        _ = app
        QMessageBox.information(None, "运行环境诊断", text)
    else:
        print(text)
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"{APP_TITLE} v{VERSION}")
        return 0
    if args.diagnose:
        return _diagnose()

    if args.smoke_shot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        return _run_smoke_shot(args.smoke_shot)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from duanju.config import Config
    from duanju.errors import ConfigError
    from duanju.logging_setup import setup_logging
    from duanju.service import Service
    from duanju.winenv import integrity_level
    from ui.bridge import Bridge
    from ui.main_window import MainWindow
    from ui.theme import stylesheet

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)

    def early_hook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        traceback.print_exception(exc_type, exc_value, exc_tb)
        QMessageBox.critical(
            None,
            "启动失败",
            _bootstrap_help(f"{exc_value}"),
        )

    sys.excepthook = early_hook

    try:
        config = Config.load()
    except ConfigError as exc:
        QMessageBox.critical(None, "启动失败", _bootstrap_help(str(exc)))
        return 1
    except OSError as exc:
        QMessageBox.critical(None, "启动失败", _bootstrap_help(f"文件系统错误：{exc}"))
        return 1
    config.ensure_dirs()

    bridge = Bridge()
    setup_logging(config, bridge.log_message.emit)
    app.setStyleSheet(stylesheet(config.theme))

    def exception_hook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        traceback.print_exception(exc_type, exc_value, exc_tb)
        QMessageBox.critical(
            None,
            "程序出错",
            f"{exc_value}\n\n详细信息已写入日志：{config.log_dir}",
        )

    sys.excepthook = exception_hook
    logger.info(
        "完整性级别：%s，数据目录：%s，下载目录：%s",
        integrity_level(),
        config.data_dir,
        config.download_dir,
    )

    service = Service(config)
    bridge.attach(service)
    window = MainWindow(config, service, bridge)

    icon_path = _resource_dir("assets") / "app.ico"
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))

    window.show()
    service.start()
    if config.warning:
        logger.warning("运行环境提示：%s", config.warning)
        QTimer.singleShot(
            300,
            lambda: QMessageBox.warning(window, "运行环境提示", config.warning),
        )
    logger.info("%s v%s 启动", APP_TITLE, VERSION)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
