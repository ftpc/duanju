"""设置页：下载、网络、ffmpeg、外观与多站源管理。"""

from __future__ import annotations

from pathlib import Path
import shutil

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from duanju.version import VERSION
from ui.widgets import PageTitle, small_label


class AddSourceDialog(QDialog):
    def __init__(self, page: "SettingsPage", parent: QWidget | None = None):
        super().__init__(parent)
        self.page = page
        self.setWindowTitle("添加苹果CMS 站源")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：我的站源")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.example.com")
        form.addRow("名称", self.name_edit)
        form.addRow("站点地址", self.url_edit)
        layout.addLayout(form)
        self.status = small_label("支持苹果CMS（conch 模板）站点：有 /voddetail/ 与 /vodplay/ 页面")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        test_button = QPushButton("测试站点")
        test_button.clicked.connect(self._test)
        buttons.addWidget(test_button)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

    def _test(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self.status.setText("请先填写站点地址")
            return
        self.status.setText("正在测试…")
        self.page.bridge.run(
            lambda: self.page.service.probe_source(url),
            on_ok=self._on_test_ok,
            on_err=lambda message: self.status.setText(f"测试失败：{message}"),
        )

    def _on_test_ok(self, result) -> None:
        ok, message = result
        self.status.setText(("✅ " if ok else "❌ ") + message)

    def values(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.url_edit.text().strip()


class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, config, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.service = service
        self.bridge = bridge
        self._build()
        self.reload()

    # ---------- UI ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(PageTitle("设置", "修改后点「保存设置」立即生效"))

        body = QHBoxLayout()
        left = QVBoxLayout()

        basic = QGroupBox("下载")
        form = QFormLayout(basic)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(browse)
        form.addRow("保存目录", dir_row)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 6)
        form.addRow("同时下载数", self.concurrency)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.0, 10.0)
        self.interval.setSingleStep(0.1)
        form.addRow("请求间隔（秒）", self.interval)
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        form.addRow("失败重试次数", self.retries)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        form.addRow("请求超时（秒）", self.timeout)
        left.addWidget(basic)

        net = QGroupBox("网络")
        net_form = QFormLayout(net)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("可选，例如 http://127.0.0.1:7890")
        net_form.addRow("代理", self.proxy_edit)
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setPlaceholderText("可选：浏览器复制的 Cookie（个别 CDN 需要）")
        net_form.addRow("Cookie", self.cookie_edit)
        left.addWidget(net)

        media = QGroupBox("ffmpeg")
        media_form = QFormLayout(media)
        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_edit.setPlaceholderText("留空自动探测 PATH / 程序目录 / 常见路径")
        choose = QPushButton("选择…")
        choose.clicked.connect(self._browse_ffmpeg)
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(choose)
        media_form.addRow("ffmpeg 路径", ffmpeg_row)
        self.ffmpeg_status = small_label("")
        detect = QPushButton("自动检测")
        detect.clicked.connect(self._detect_ffmpeg)
        status_row = QHBoxLayout()
        status_row.addWidget(self.ffmpeg_status, 1)
        status_row.addWidget(detect)
        media_form.addRow("状态", status_row)
        left.addWidget(media)

        # 外观
        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        appearance_form.addRow("主题", self.theme_combo)
        left.addWidget(appearance)

        # 图片缓存管理
        cache_group = QGroupBox("图片缓存")
        cache_layout = QVBoxLayout()
        
        # 显示体积的 Label
        self.cache_info_label = QLabel("缓存目录: C:\\DuanjuLogs\\cache\\images\\")
        self.cache_info_label.setWordWrap(True)
        cache_layout.addWidget(self.cache_info_label)
        
        self.cache_size_label = QLabel("当前缓存大小: 计算中...")
        self.cache_size_label.setStyleSheet("color: #ff5f57; font-weight: bold;")
        cache_layout.addWidget(self.cache_size_label)

        self.clear_cache_btn = QPushButton("清理图片缓存")
        self.clear_cache_btn.clicked.connect(self._clear_image_cache)
        cache_layout.addWidget(self.clear_cache_btn)

        cache_group.setLayout(cache_layout)
        left.addWidget(cache_group)

        left.addStretch(1)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        
        # 关于
        about = QGroupBox("关于")
        about_layout = QVBoxLayout(about)
        about_layout.addWidget(QLabel(f"短剧下载器 v{VERSION}"))
        about_layout.addWidget(
            small_label("仅供个人学习分镜、离线研究使用；请勿传播、二创或商用。")
        )
        about_buttons = QHBoxLayout()
        open_config = QPushButton("打开配置目录")
        open_config.clicked.connect(self._open_config)
        open_logs = QPushButton("打开日志目录")
        open_logs.clicked.connect(self._open_logs)
        about_buttons.addWidget(open_config)
        about_buttons.addWidget(open_logs)
        about_buttons.addStretch(1)
        about_layout.addLayout(about_buttons)
        right.addWidget(about)

        # 站源（隐藏）
        sources_box = QGroupBox("站源（多站源）")
        sources_box.setVisible(False)
        sources_layout = QVBoxLayout(sources_box)
        self.sources_table = QTableWidget(0, 2)
        self.sources_table.setHorizontalHeaderLabels(["名称", "地址"])
        self.sources_table.verticalHeader().setVisible(False)
        self.sources_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.sources_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sources_table.horizontalHeader().setStretchLastSection(True)
        self.sources_table.setColumnWidth(0, 140)
        self.sources_table.setMinimumHeight(200)
        sources_layout.addWidget(self.sources_table)
        sources_buttons = QHBoxLayout()
        add_button = QPushButton("添加站点")
        add_button.clicked.connect(self._add_source)
        remove_button = QPushButton("移除选中")
        remove_button.setObjectName("Danger")
        remove_button.clicked.connect(self._remove_source)
        test_button = QPushButton("测试选中")
        test_button.clicked.connect(self._test_source)
        sources_buttons.addWidget(add_button)
        sources_buttons.addWidget(remove_button)
        sources_buttons.addWidget(test_button)
        sources_buttons.addStretch(1)
        sources_layout.addLayout(sources_buttons)
        sources_layout.addWidget(
            small_label("任何苹果CMS 站点都可添加；短剧聚合内置两个域名互为备份")
        )
        right.addWidget(sources_box, 1)

        right.addStretch(1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status = small_label("")
        footer.addWidget(self.status, 1)
        reset_button = QPushButton("重新载入")
        reset_button.clicked.connect(self.reload)
        save_button = QPushButton("保存设置")
        save_button.setObjectName("Primary")
        save_button.clicked.connect(self._save)
        footer.addWidget(reset_button)
        footer.addWidget(save_button)
        root.addLayout(footer)

    # ---------- 数据 ----------
    def reload(self) -> None:
        config = self.service.config
        self.dir_edit.setText(config.download_dir)
        self.concurrency.setValue(config.concurrency)
        self.interval.setValue(config.interval)
        self.retries.setValue(config.retries)
        self.timeout.setValue(config.timeout)
        self.proxy_edit.setText(config.proxy)
        self.cookie_edit.setText(config.cookie)
        self.ffmpeg_edit.setText(config.ffmpeg_path)
        index = self.theme_combo.findData(config.theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.service.ffmpeg.refresh()
        self.ffmpeg_status.setText(self.service.ffmpeg_status())
        self._fill_sources()
        self.status.setText("")
        
        # 刷新缓存大小
        self._update_cache_size()

    def _update_cache_size(self) -> None:
        """计算图片缓存目录的体积。"""
        cache_dir = Path(r"C:\DuanjuLogs\cache\images")
        if not cache_dir.exists():
            self.cache_size_label.setText("当前缓存大小: 0 B (目录不存在)")
            return
            
        total_size = 0
        file_count = 0
        try:
            for file in cache_dir.glob("*"):
                if file.is_file():
                    total_size += file.stat().st_size
                    file_count += 1
                    
            # 格式化大小
            if total_size < 1024:
                size_str = f"{total_size} B"
            elif total_size < 1024 * 1024:
                size_str = f"{total_size / 1024:.1f} KB"
            else:
                size_str = f"{total_size / 1024 / 1024:.2f} MB"
                
            self.cache_size_label.setText(f"当前缓存大小: {size_str} ({file_count} 个文件)")
        except Exception as e:
            self.cache_size_label.setText(f"当前缓存大小: 计算失败 ({e})")

    def _fill_sources(self) -> None:
        self.sources_table.setRowCount(0)
        for source in self.service.config.sources:
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            name_item = QTableWidgetItem(source.name)
            name_item.setData(Qt.UserRole, source.key)
            self.sources_table.setItem(row, 0, name_item)
            self.sources_table.setItem(row, 1, QTableWidgetItem("  ".join(source.bases)))

    # ---------- 操作 ----------
    def _browse_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if directory:
            self.dir_edit.setText(directory)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg.exe", self.ffmpeg_edit.text(), "可执行文件 (*.exe);;所有文件 (*.*)"
        )
        if path:
            self.ffmpeg_edit.setText(path)

    def _detect_ffmpeg(self) -> None:
        self.service.ffmpeg.refresh()
        self.ffmpeg_status.setText(self.service.ffmpeg_status())
        if self.service.ffmpeg.available:
            self.ffmpeg_edit.setText(self.service.ffmpeg.binary)
        else:
            QMessageBox.warning(
                self,
                "ffmpeg",
                "没有找到 ffmpeg。请下载 ffmpeg 并加入 PATH，或在这里手动选择 ffmpeg.exe。",
            )

    def _add_source(self) -> None:
        dialog = AddSourceDialog(self, self)
        if dialog.exec() == QDialog.Accepted:
            name, url = dialog.values()
            if not url:
                QMessageBox.warning(self, "添加站源", "请填写站点地址")
                return
            try:
                self.service.add_source(name, url)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "添加站源失败", str(exc))
                return
            self._fill_sources()
            self.status.setText("已添加站源")
            self.settings_changed.emit()

    def _remove_source(self) -> None:
        row = self.sources_table.currentRow()
        if row < 0:
            return
        key = self.sources_table.item(row, 0).data(Qt.UserRole)
        if QMessageBox.question(self, "移除站源", "确定移除选中的站源？") != QMessageBox.Yes:
            return
        self.service.remove_source(str(key))
        self._fill_sources()
        self.settings_changed.emit()

    def _test_source(self) -> None:
        row = self.sources_table.currentRow()
        if row < 0:
            return
        source = next(
            (item for item in self.service.config.sources if item.key == self.sources_table.item(row, 0).data(Qt.UserRole)),
            None,
        )
        if source is None or not source.bases:
            return
        self.status.setText("正在测试站源…")
        self.bridge.run(
            lambda: self.service.probe_source(source.bases[0]),
            on_ok=self._on_test_ok,
            on_err=lambda message: self.status.setText(f"测试失败：{message}"),
        )

    def _on_test_ok(self, result) -> None:
        ok, message = result
        self.status.setText(("✅ " if ok else "❌ ") + message)

    def _save(self) -> None:
        values = {
            "download_dir": self.dir_edit.text().strip() or self.service.config.download_dir,
            "concurrency": self.concurrency.value(),
            "interval": self.interval.value(),
            "retries": self.retries.value(),
            "timeout": self.timeout.value(),
            "proxy": self.proxy_edit.text().strip(),
            "cookie": self.cookie_edit.text().strip(),
            "ffmpeg_path": self.ffmpeg_edit.text().strip(),
            "theme": self.theme_combo.currentData(),
        }
        try:
            self.service.save_settings(values)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.service.ffmpeg.refresh()
        self.ffmpeg_status.setText(self.service.ffmpeg_status())
        self.status.setText("设置已保存")
        self.settings_changed.emit()

    def _open_config(self) -> None:
        from duanju.util import open_in_explorer

        open_in_explorer(self.service.config.path)

    def _open_logs(self) -> None:
        from duanju.util import open_in_explorer

        open_in_explorer(self.service.config.log_dir)

    def _clear_image_cache(self) -> None:
        """清理图片缓存目录。"""
        cache_dir = Path(r"C:\DuanjuLogs\cache\images")
        if not cache_dir.exists():
            QMessageBox.information(self, "清理缓存", "缓存目录不存在")
            return

        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认清理",
            f"确定要清理图片缓存吗？\n\n缓存目录：{cache_dir}\n这将删除所有已下载的图片文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # 删除所有图片文件
            count = 0
            for file in cache_dir.glob("*"):
                if file.is_file():
                    file.unlink()
                    count += 1
                elif file.is_dir():
                    shutil.rmtree(file)
                    count += 1

            QMessageBox.information(self, "清理完成", f"已清理 {count} 个缓存文件")
            print(f"[SETTINGS] 清理图片缓存完成，共删除 {count} 个文件")
            
            # 清理完毕后刷新体积显示
            self._update_cache_size()
            
        except Exception as exc:
            QMessageBox.critical(self, "清理失败", f"清理缓存时出错：{exc}")
            print(f"[SETTINGS] 清理图片缓存失败: {exc}")