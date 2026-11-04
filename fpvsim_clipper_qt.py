from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QInputDialog, QStyle, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    QWizard, QWizardPage, QScrollArea,
)

from fpvsim_clipper import (
    LapEvent, LogParser, Obs, Store, VeloRaceParser, default_log_path,
    archive_replay, delete_older_clips, detect_course_name, detect_velocidrone_ip,
    rename_clip_metadata, trim_clip, detect_aircraft_name,
)
from websocket import WebSocketApp, setdefaulttimeout
from native_media import validate_native_media
from race_state import FinishGate
from naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, render_name, read_metadata, write_metadata
from setup_ui import NamingPanel, SetupWizard
from veloci_connection import request_disconnect
import updater

DEBUG_FILE = Path(sys.executable).resolve().parent / "fpvsim_clipper_ws_debug.log" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "fpvsim_clipper_ws_debug.log"
DEFAULT_SHARE_TEMPLATE = "{course}  {kind} {record}秒 #VelociDrone"


class Signals(QObject):
    status = Signal(str)
    row = Signal(str, str, str, str)
    alert = Signal(str, str)
    course = Signal(str)
    aircraft = Signal(str)
    velo_address = Signal(str)
    saved = Signal(str, str, str, float)
    missing_time = Signal(object)
    close_ready = Signal()
    connection_state = Signal(str)
    timing_alert = Signal(str, str)
    update_checked = Signal(object)
    update_downloaded = Signal(object)


class NativeSetupPage(QWizardPage):
    checked = Signal(int, bool, str)

    def __init__(self):
        super().__init__()
        self.setTitle("動画の切り出し確認")
        self.check_ok = False
        self.checking = False
        self.attempt = 0
        form = QFormLayout(self)
        label = QLabel("OS標準の動画処理が使えるかを確認します。確認は最大20秒です。"
                       "実際の動画形式・コーデックの確認は保存時に行います。")
        label.setWordWrap(True)
        form.addRow(label)
        self.check_status = QLabel("「動作確認」を押してください。")
        self.check_status.setWordWrap(True)
        form.addRow(self.check_status)
        self.check_button = QPushButton("動作確認")
        self.check_button.clicked.connect(self.check_backend)
        form.addRow(self.check_button)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(20000)
        self.timeout.timeout.connect(self.check_timed_out)
        self.checked.connect(self.finish_check)

    def initializePage(self):
        QTimer.singleShot(0, self.check_backend)

    def check_backend(self):
        if self.checking:
            return
        self.attempt += 1
        attempt = self.attempt
        self.check_ok = False
        self.checking = True
        self.check_button.setEnabled(False)
        self.check_status.setText("確認中… OS標準の動画機能へ接続しています。")
        self.completeChanged.emit()
        self.timeout.start()
        def run():
            try:
                validate_native_media()
                ok, message = True, "確認成功：OS標準の動画処理が利用できます。"
            except Exception as exc:
                ok, message = False, f"確認失敗：{exc}"
            try:
                self.checked.emit(attempt, ok, message)
            except RuntimeError:
                pass  # Wizard was closed while the worker was finishing.
        threading.Thread(target=run, daemon=True).start()

    def finish_check(self, attempt, ok, message):
        if attempt != self.attempt or not self.checking:
            return
        self.timeout.stop()
        self.checking = False
        self.check_ok = ok
        self.check_button.setEnabled(True)
        self.check_status.setText(message)
        self.completeChanged.emit()

    def check_timed_out(self):
        self.attempt += 1
        self.checking = False
        self.check_ok = False
        self.check_button.setEnabled(True)
        self.check_status.setText("確認が20秒以内に完了しませんでした。OSのメディア機能を確認して再試行してください。")
        self.completeChanged.emit()

    def cleanupPage(self):
        self.attempt += 1
        self.timeout.stop()
        self.checking = False
        self.check_ok = False
        self.check_button.setEnabled(True)
        self.completeChanged.emit()

    def isComplete(self):
        return self.check_ok and not self.checking

    def validatePage(self):
        return self.isComplete()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FPVSIM-Clipper v1.0.0")
        icon = QIcon(str(Path(__file__).resolve().parent / "assets" / "app-icon.png"))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(820, 820)
        self.store = Store()
        s = self.store.data.get("settings", {})
        self.signals = Signals()
        self.signals.status.connect(self.set_status)
        self.signals.row.connect(self.add_row)
        self.signals.alert.connect(lambda title, body: QMessageBox.warning(self, title, body))
        self.signals.course.connect(self.set_course)
        self.signals.aircraft.connect(self.set_detected_aircraft)
        self.signals.velo_address.connect(lambda value: self.velo_host.setText(value))
        self.signals.saved.connect(self.show_saved_path)
        self.signals.missing_time.connect(self.prompt_missing_time)
        self.signals.close_ready.connect(self.finish_pending_close)
        self.signals.connection_state.connect(self.set_connection_state)
        self.signals.timing_alert.connect(self.show_timing_alert)
        self.signals.update_checked.connect(self.finish_update_check)
        self.signals.update_downloaded.connect(self.finish_update_download)
        self.update_busy = False
        self.timing_popups = []
        self.stop_event = threading.Event()
        self.worker = None
        self.buffer_stop_worker = None
        self.obs = None
        self.race_started = None
        self.race_clock_started = None
        self.race_three_detected = False
        self.race_lap_detected = False
        self.finish_gate = FinishGate()
        self.emergency_saved_for_race = False
        self.pending_best = []
        self.monitoring = False
        self.connection_state = "stopped"
        self.save_state_lock = threading.Lock()
        self.obs_save_lock = threading.RLock()
        self.protected_replays = set()
        self.emergency_lock = threading.Lock()
        self.emergency_capture_active = False
        self.active_saves = 0
        self.close_after_save = False
        self.force_closing = False
        self.last_saved_path = None
        self.last_share_text = ""
        self.last_share_context = None
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("FPVSIM-Clipper")
        self.tray_icon.show()

        body = QWidget()
        root_layout = QVBoxLayout(body)
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)
        record_page = QWidget()
        layout = QVBoxLayout(record_page)
        record_form = QFormLayout()
        layout.addLayout(record_form)
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        form = QFormLayout()
        self.log = QLineEdit(s.get("log_path", default_log_path()))
        logrow = QHBoxLayout(); logrow.addWidget(self.log)
        choose_log = QPushButton("選択"); choose_log.clicked.connect(self.pick_log); logrow.addWidget(choose_log)
        form.addRow("VelociDroneログ", logrow)

        modes = QHBoxLayout(); self.mode_group = QButtonGroup(self)
        for label, value in (("1周", "lap"), ("3周", "three_lap"), ("両方", "both")):
            button = QRadioButton(label); button.setProperty("value", value)
            button.setChecked(value == s.get("mode", "both")); self.mode_group.addButton(button); modes.addWidget(button)
        record_form.addRow("保存する自己ベスト", modes)
        retention = QHBoxLayout()
        self.latest_lap = QCheckBox("1周：最新のベスト動画だけ残す")
        self.latest_three = QCheckBox("3周：最新のベスト動画だけ残す")
        self.latest_lap.setChecked(bool(s.get("latest_lap", False)))
        self.latest_three.setChecked(bool(s.get("latest_three", False)))
        retention.addWidget(self.latest_lap); retention.addWidget(self.latest_three)
        self.rename_on_edit = QCheckBox("結果を修正したとき動画ファイル名も変更する")
        self.rename_on_edit.setChecked(bool(s.get("rename_on_edit", True)))
        self.rename_on_edit.toggled.connect(self.save_rename_setting)
        form.addRow("結果修正", self.rename_on_edit)
        self.lap_before = self.padding_spinbox(s.get("lap_before", 3.0))
        self.lap_after = self.padding_spinbox(s.get("lap_after", 1.5))
        form.addRow("1周クリップの追加時間", self.padding_row(self.lap_before, self.lap_after))
        self.three_before = self.padding_spinbox(s.get("three_before", 3.0))
        self.three_after = self.padding_spinbox(s.get("three_after", 1.5))
        form.addRow("3周クリップの追加時間", self.padding_row(self.three_before, self.three_after))
        self.naming_panel = NamingPanel(s, lambda: (self.course_name.text(), self.current_aircraft_name))
        self.folder_mode = self.naming_panel.mode
        self.folder_template = self.naming_panel.folder
        self.filename_template = self.naming_panel.filename
        self.folder_template_value = self.folder_template.text()
        self.filename_template_value = self.filename_template.text()
        self.naming_panel.check_button.setText("名前を確認して保存")
        self.naming_panel.check_button.clicked.connect(self.save_naming_rules)
        form.addRow("保存先と動画の名前", self.naming_panel)
        share_layout = QVBoxLayout()
        self.share_template = QPlainTextEdit()
        template = s.get("share_template", DEFAULT_SHARE_TEMPLATE)
        if s.get("share_tokens_version", 1) < 2:
            # In older templates {time} meant the race time, not the clock.
            template = template.replace("{time}", "{record}")
        for old, new in (("{コース名}", "{course}"), ("{種類}", "{kind}"), ("{タイム}", "{record}")):
            template = template.replace(old, new)
        self.share_template.setPlainText(template)
        self.share_template.setMaximumHeight(72)
        self.template_save_timer = QTimer(self)
        self.template_save_timer.setSingleShot(True)
        self.template_save_timer.setInterval(500)
        self.template_save_timer.timeout.connect(self.save_share_template)
        self.share_template.textChanged.connect(self.on_share_template_changed)
        share_layout.addWidget(self.share_template)
        token_help = QLabel("差し込み項目：{course}＝コース名　{aircraft}＝機体名\n{kind}＝1lap / 3lap　{record}＝記録タイム（秒）\n"
                            "{year}＝年　{month}＝月　{date}＝日　{time}＝時刻（時:分:秒）\n"
                            "{datetime}＝保存日時全体　{full_date}＝年月日　※日時は選択した動画の保存日時です")
        token_help.setStyleSheet("color: #666;")
        share_layout.addWidget(token_help)
        form.addRow("X投稿文テンプレート", share_layout)
        self.host = QLineEdit(s.get("host", "127.0.0.1")); self.port = QLineEdit(str(s.get("port", 4455)))
        self.password = QLineEdit(s.get("password", "")); self.password.setEchoMode(QLineEdit.Password)
        obsrow = QHBoxLayout(); obsrow.addWidget(QLabel("接続先")); obsrow.addWidget(self.host); obsrow.addWidget(QLabel("ポート")); obsrow.addWidget(self.port); obsrow.addWidget(QLabel("パスワード")); obsrow.addWidget(self.password)
        form.addRow("OBS WebSocket", obsrow)
        saved_velo_host = s.get("velo_host", "")
        self.velo_host = QLineEdit(saved_velo_host if saved_velo_host not in ("", "127.0.0.1", "localhost") else detect_velocidrone_ip())
        self.velo_port = QLineEdit(str(s.get("velo_port", 60003)))
        velorow = QHBoxLayout(); velorow.addWidget(QLabel("Host")); velorow.addWidget(self.velo_host); velorow.addWidget(QLabel("Port")); velorow.addWidget(self.velo_port)
        form.addRow("VelociDrone WebSocket", velorow)
        detected_course = detect_course_name(self.log.text())
        self.course_name = QLineEdit(detected_course or s.get("course_name", "Current Course"))
        record_form.addRow("コース名", self.course_name)
        self.aircraft_name = QLineEdit(s.get("aircraft_name", ""))
        self.aircraft_name.setPlaceholderText("自動取得できない場合は手入力できます")
        self.current_aircraft_name = s.get("aircraft_name", "")
        self.manual_aircraft_name = s.get("manual_aircraft_name", self.current_aircraft_name)
        self.aircraft_auto_enabled = bool(s.get("aircraft_auto", True))
        self.aircraft_auto = QCheckBox("機体名を自動で取得")
        self.aircraft_auto.setChecked(self.aircraft_auto_enabled)
        self.aircraft_name.setReadOnly(self.aircraft_auto_enabled)
        self.aircraft_auto.toggled.connect(self.change_aircraft_mode)
        self.aircraft_timer = QTimer(self)
        self.aircraft_timer.setInterval(3000)
        self.aircraft_timer.timeout.connect(self.refresh_aircraft)
        self.aircraft_timer.start()
        QTimer.singleShot(0, self.refresh_aircraft)
        self.aircraft_name.editingFinished.connect(self.save_aircraft_name)
        record_form.addRow("機体名", self.aircraft_name)
        record_form.addRow("", self.aircraft_auto)
        record_form.addRow("過去動画の整理", retention)
        settings_layout.addLayout(form)
        setup_button = QPushButton("初期セットアップウィザードを開く")
        setup_button.setMinimumHeight(40)
        setup_button.clicked.connect(self.run_setup_wizard)
        settings_layout.addWidget(setup_button)
        update_row = QHBoxLayout()
        self.update_button = QPushButton("更新を確認")
        self.update_button.clicked.connect(self.check_for_updates)
        update_row.addWidget(self.update_button)
        settings_layout.addLayout(update_row)
        settings_layout.addStretch()
        self.tabs.addTab(record_page, "記録")
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_page)
        self.tabs.addTab(settings_scroll, "録画設定")

        self.monitor_button = QPushButton("監視開始")
        self.monitor_button.setMinimumHeight(58)
        self.set_connection_state("stopped")
        self.monitor_button.clicked.connect(self.toggle_monitoring)
        layout.addWidget(self.monitor_button)
        self.emergency_button = QPushButton("今すぐ元動画を保存")
        self.emergency_button.setMinimumHeight(38)
        self.emergency_button.clicked.connect(self.emergency_save)
        layout.addWidget(self.emergency_button)
        buttons = QHBoxLayout()
        for label, fn in (("テスト保存", self.test_save), ("ログ内の記録を確認", self.test_log)):
            b = QPushButton(label); b.clicked.connect(fn); buttons.addWidget(b)
        layout.addLayout(buttons)
        self.status = QLabel("停止中"); layout.addWidget(self.status)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("投稿する結果"))
        self.saved_selector = QComboBox()
        self.saved_selector.setFixedHeight(32)
        self.saved_selector.setPlaceholderText("保存されたクリップを選択")
        self.saved_selector.currentIndexChanged.connect(self.select_saved_clip)
        selector_row.addWidget(self.saved_selector, 1)
        edit_result = QPushButton("選択結果を修正")
        edit_result.setFixedHeight(32)
        edit_result.clicked.connect(self.edit_selected_result)
        selector_row.addWidget(edit_result)
        self.share_x = QPushButton("Xで投稿")
        self.share_x.setFixedHeight(32)
        self.share_x.setMinimumWidth(96)
        self.share_x.setStyleSheet('font-size: 13px; font-weight: bold;')
        self.share_x.clicked.connect(self.share_on_x)
        selector_row.addWidget(self.share_x)
        layout.addLayout(selector_row)
        saved_row = QHBoxLayout()
        self.saved_path = QLineEdit(); self.saved_path.setReadOnly(True)
        self.saved_path.setPlaceholderText("クリップを保存すると、ここに保存先が表示されます")
        saved_row.addWidget(self.saved_path, 1)
        open_saved = QPushButton("保存先を開く"); open_saved.clicked.connect(self.open_saved_location); saved_row.addWidget(open_saved)
        layout.addLayout(saved_row)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["種類", "コース", "タイム", "結果"]); self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table); self.setCentralWidget(body)
        self.load_recent_clips()
        if not bool(s.get("setup_complete", False)):
            QTimer.singleShot(0, self.run_setup_wizard)

    def mode(self):
        return self.mode_group.checkedButton().property("value")


    def check_for_updates(self):
        if self.update_busy:
            return
        self.update_busy = True
        self.update_button.setEnabled(False)
        self.status.setText('更新を確認しています…')
        def check():
            try: result = {'release': updater.check_latest()}
            except Exception as exc: result = {'error': str(exc)}
            self.signals.update_checked.emit(result)
        threading.Thread(target=check, daemon=True).start()

    def finish_update_check(self, result):
        self.update_busy = False
        self.update_button.setEnabled(True)
        if 'error' in result:
            self.status.setText('更新を確認できませんでした。')
            QMessageBox.warning(self, '更新を確認できません', result['error'])
            return
        release = result['release']
        if not release['newer']:
            self.status.setText('最新バージョンです。')
            QMessageBox.information(self, '更新の確認', '最新バージョンです。')
            return
        # The EXE-only updater cannot safely replace the dependencies of an onedir build.
        # Open the release page instead until a verified folder-update mechanism exists.
        can_install = (updater.supports_self_update(sys.executable, sys.platform, getattr(sys, 'frozen', False))
                       and release.get('asset'))
        if not can_install:
            answer = QMessageBox.question(self, '新しいバージョン',
                f"{release['tag']} が公開されています。GitHubの配布ページを開きますか？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(release['page']))
            return
        if self.monitoring or self.is_saving() or (self.buffer_stop_worker and self.buffer_stop_worker.is_alive()):
            QMessageBox.information(self, '更新する前に', '監視と保存を停止してから更新してください。')
            return
        answer = QMessageBox.question(self, '新しいバージョン',
            f"{release['tag']} に更新しますか？\n更新のためFPVSIM-Clipperを再起動します。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.update_busy = True
        self.update_downloading = True
        self.update_button.setEnabled(False)
        self.monitor_button.setEnabled(False)
        self.status.setText('更新ファイルをダウンロードしています…')
        def download():
            try: result = {'path': str(updater.download_update(release['asset'], Path(sys.executable).resolve().parent))}
            except Exception as exc: result = {'error': str(exc)}
            self.signals.update_downloaded.emit(result)
        threading.Thread(target=download, daemon=True).start()

    def finish_update_download(self, result):
        self.update_busy = False
        self.update_downloading = False
        self.update_button.setEnabled(True)
        self.monitor_button.setEnabled(True)
        if 'error' in result:
            self.status.setText('更新できませんでした。')
            QMessageBox.warning(self, '更新できません', result['error'])
            return
        if self.monitoring or self.is_saving() or (self.buffer_stop_worker and self.buffer_stop_worker.is_alive()):
            QMessageBox.warning(self, '更新できません', '監視と保存を停止してから、もう一度更新してください。')
            return
        try:
            updater.launch_installer(Path(result['path']), Path(sys.executable).resolve())
        except Exception as exc:
            QMessageBox.warning(self, '更新できません', str(exc))
            return
        self.force_closing = True
        self.close()


    def naming_options(self):
        return {'folder_template': self.folder_template_value,
                'filename_template': self.filename_template_value}

    def check_naming_rules(self):
        return self.naming_panel.preview()

    def save_naming_rules(self):
        try:
            folder, filename = self.check_naming_rules()
        except ValueError as exc:
            QMessageBox.warning(self, '命名ルールを修正してください', str(exc))
            return
        settings = self.store.data.setdefault('settings', {})
        settings.update(self.naming_panel.values())
        try:
            self.store.save()
        except OSError as exc:
            QMessageBox.warning(self, '設定を保存できません', str(exc))
            return
        if self.monitoring:
            detail = '\n監視中のため、新しいルールは次回の監視開始から適用します。'
        else:
            self.folder_template_value = self.folder_template.text()
            self.filename_template_value = self.filename_template.text()
            detail = ''
        QMessageBox.information(self, '命名ルールを保存しました', f'記録95.534秒の場合の例です。\nフォルダ：{folder}\nファイル：{filename}.mp4{detail}')

    def padding_spinbox(self, value):
        box = QDoubleSpinBox()
        box.setRange(0.0, 120.0)
        box.setDecimals(1)
        box.setSingleStep(0.5)
        box.setSuffix(" 秒")
        box.setValue(float(value))
        return box

    def padding_row(self, before, after):
        row = QHBoxLayout()
        row.addWidget(QLabel("開始前")); row.addWidget(before)
        row.addSpacing(16)
        row.addWidget(QLabel("終了後")); row.addWidget(after)
        row.addStretch()
        return row

    def run_setup_wizard(self):
        wizard = SetupWizard(self, NativeSetupPage())
        if wizard.exec() != QDialog.Accepted:
            return
        try:
            obs_port_value = int(wizard.obs_port.text().strip() or "4455")
            velo_port_value = int(wizard.velo_port.text().strip() or "60003")
            if not 1 <= obs_port_value <= 65535 or not 1 <= velo_port_value <= 65535:
                raise ValueError("ポートには1～65535の整数を入力してください。")
            wizard.naming.preview()
        except ValueError as exc:
            QMessageBox.warning(self, "入力内容を確認してください", str(exc))
            return
        self.host.setText(wizard.obs_host.text().strip() or "127.0.0.1")
        self.port.setText(str(obs_port_value))
        self.password.setText(wizard.obs_password.text())
        self.velo_host.setText(wizard.velo_host.text().strip() or "127.0.0.1")
        self.velo_port.setText(str(velo_port_value))
        for button in self.mode_group.buttons():
            button.setChecked(button.property("value") == wizard.mode.currentData())
        values = wizard.naming.values()
        self.naming_panel.custom_template = values['folder_custom_template']
        self.folder_mode.setCurrentIndex(max(0, self.folder_mode.findData(values['folder_mode'])))
        self.naming_panel.sync_folder()
        self.filename_template.setText(values['filename_template'])
        self.latest_lap.setChecked(wizard.latest_lap.isChecked())
        self.latest_three.setChecked(wizard.latest_three.isChecked())
        self.store.data["settings"] = dict(self.settings(), setup_complete=True)
        try:
            self.store.save()
            self.notify_user("設定を保存しました", "記録画面で「監視開始」を押してください。")
            self.tabs.setCurrentIndex(0)
        except OSError as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))

    def pick_log(self):
        path, _ = QFileDialog.getOpenFileName(self, "Player.logを選択", "", "Log (*.log);;All (*)")
        if path: self.log.setText(path)

    def settings(self):
        setup_complete = bool(self.store.data.get("settings", {}).get("setup_complete", False))
        return {"log_path": self.log.text(), "mode": self.mode(), "host": self.host.text(), "port": int(self.port.text()), "password": self.password.text(), "velo_host": self.velo_host.text(), "velo_port": int(self.velo_port.text()), "course_name": self.course_name.text(), "aircraft_name": self.aircraft_name.text().strip(), "latest_lap": self.latest_lap.isChecked(), "latest_three": self.latest_three.isChecked(), "rename_on_edit": self.rename_on_edit.isChecked(), "folder_mode": self.folder_mode.currentData(), "folder_template": self.folder_template.text(), "filename_template": self.filename_template.text(), "precise_cut": True, "folder_custom_template": self.naming_panel.custom_template, "aircraft_auto": self.aircraft_auto_enabled, "manual_aircraft_name": self.manual_aircraft_name, "lap_before": self.lap_before.value(), "lap_after": self.lap_after.value(), "three_before": self.three_before.value(), "three_after": self.three_after.value(), "share_template": self.share_template.toPlainText(), "share_tokens_version": 2, "setup_complete": setup_complete}

    def set_detected_aircraft(self, name):
        if self.aircraft_auto_enabled:
            self.current_aircraft_name = name or ""
            self.aircraft_name.setText(name or "")
            self.aircraft_name.setPlaceholderText("取得できません。自動取得を外すと手入力できます" if not name else "")

    def refresh_aircraft(self):
        if self.aircraft_auto_enabled:
            self.set_detected_aircraft(detect_aircraft_name() or "")

    def change_aircraft_mode(self, enabled):
        self.aircraft_auto_enabled = bool(enabled)
        self.aircraft_name.setReadOnly(enabled)
        if enabled:
            self.refresh_aircraft()
        else:
            self.current_aircraft_name = self.manual_aircraft_name
            self.aircraft_name.setText(self.manual_aircraft_name)
        self.store.data.setdefault("settings", {})["aircraft_auto"] = bool(enabled)
        try: self.store.save()
        except OSError: pass

    def save_aircraft_name(self):
        self.current_aircraft_name = self.aircraft_name.text().strip()
        if not self.aircraft_auto_enabled:
            self.manual_aircraft_name = self.current_aircraft_name
            self.store.data.setdefault("settings", {})["manual_aircraft_name"] = self.manual_aircraft_name
        self.store.data.setdefault("settings", {})["aircraft_name"] = self.aircraft_name.text().strip()
        try:
            self.store.save()
        except OSError as exc:
            self.status.setText(f"機体名を保存できませんでした：{exc}")

    def toggle_monitoring(self):
        if self.monitoring:
            self.stop()
        else:
            self.start()

    def set_connection_state(self, state):
        self.connection_state = state
        if state == "connected":
            self.monitor_button.setText("録画停止")
            normal, hover, pressed = '#b83232', '#9f2828', '#862020'
        elif state == "connecting":
            self.monitor_button.setText("接続キャンセル")
            normal, hover, pressed = '#8a5100', '#754500', '#603900'
        else:
            self.monitor_button.setText("監視開始")
            normal, hover, pressed = '#246644', '#1d5538', '#16432c'
        self.monitor_button.setStyleSheet(
            'QPushButton {font-size: 20px; font-weight: bold; color: #ffffff; '
            f'background-color: {normal}; border: 1px solid {normal}; border-radius: 6px; padding: 8px;}} '
            f'QPushButton:hover {{background-color: {hover}; color: #ffffff; border-color: {hover};}} '
            f'QPushButton:pressed {{background-color: {pressed}; color: #ffffff; border-color: {pressed};}} '
            'QPushButton:disabled {background-color: #dddddd; color: #555555; border-color: #aaaaaa;}'
        )

    def connect_obs(self):
        return Obs(self.host.text(), int(self.port.text()), self.password.text())

    def start(self):
        if self.buffer_stop_worker and self.buffer_stop_worker.is_alive():
            self.status.setText("OBSのリプレイバッファを停止しています。しばらくお待ちください。")
            return
        if self.worker and self.worker.is_alive(): return
        self.set_connection_state("connecting")
        self.status.setText("OBSへ接続しています…")
        QApplication.processEvents()
        try:
            self.velo_host.setText(detect_velocidrone_ip(int(self.velo_port.text())))
            detected = detect_course_name(self.log.text())
            if detected: self.course_name.setText(detected)
            self.check_naming_rules()
            self.latest_lap_enabled = self.latest_lap.isChecked()
            self.latest_three_enabled = self.latest_three.isChecked()
            self.folder_mode_value = self.folder_mode.currentData()
            self.folder_template_value = self.folder_template.text()
            self.filename_template_value = self.filename_template.text()
            self.precise_cut_value = True
            self.refresh_aircraft()
            self.connection_config = {"host": self.host.text(), "port": int(self.port.text()), "password": self.password.text(),
                                      "velo_port": int(self.velo_port.text()), "velo_host": self.velo_host.text()}
            self.lap_before_value = self.lap_before.value()
            self.lap_after_value = self.lap_after.value()
            self.three_before_value = self.three_before.value()
            self.three_after_value = self.three_after.value()
            self.share_template_value = self.share_template.toPlainText().strip() or DEFAULT_SHARE_TEMPLATE
            self.store.data["settings"] = self.settings(); self.store.save(); self.obs = None
        except Exception as exc:
            self.set_connection_state("stopped")
            QMessageBox.critical(self, "録画・切り出しの準備エラー", str(exc)); return
        self.stop_event.clear()
        self.monitoring = True
        self.status.setText("VelociDroneへ接続しています…")
        self.worker = threading.Thread(target=self.monitor, daemon=True); self.worker.start()

    def stop(self):
        if self.is_saving() and not self.force_closing:
            self.notify_user("保存中です", "保存が完了するまで監視を停止できません。")
            return
        self.stop_event.set()
        request_disconnect(getattr(self, "velo_socket", None))
        # OBS can still be recording while VelociDrone is disconnected.
        # Never use the combined connection state to decide whether to stop it.
        if self.obs and not (self.buffer_stop_worker and self.buffer_stop_worker.is_alive()):
            def stop_buffer(obs=self.obs):
                try:
                    obs.stop_buffer()
                except Exception as exc:
                    self.signals.status.emit(f"OBSのリプレイバッファを停止できませんでした。OBSで停止してください。詳細：{exc}")
            self.buffer_stop_worker = threading.Thread(target=stop_buffer, daemon=True)
            self.buffer_stop_worker.start()
        self.monitoring = False
        self.set_connection_state("stopped")
        self.status.setText("停止中")

    def is_saving(self):
        with self.save_state_lock:
            return self.active_saves > 0

    def begin_save(self):
        with self.save_state_lock:
            self.active_saves += 1

    def end_save(self):
        with self.save_state_lock:
            self.active_saves = max(0, self.active_saves - 1)
            all_finished = self.active_saves == 0
        if all_finished and self.close_after_save:
            self.signals.close_ready.emit()

    def notify_user(self, title, text):
        self.status.setText(text)
        self.tray_icon.showMessage(f"FPVSIM-Clipper：{title}", text, QSystemTrayIcon.Information, 8000)

    def show_timing_alert(self, track, detail):
        title = "VelociDroneのタイムを取得できませんでした"
        text = f"コース：{track}\n{detail}"
        QApplication.beep()
        self.status.setText(text.replace("\n", " / "))
        self.tray_icon.showMessage(f"FPVSIM-Clipper：{title}", text, QSystemTrayIcon.Warning, 10000)
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.setModal(False)
        self.timing_popups.append(box)
        def release_popup(_result):
            if box in self.timing_popups:
                self.timing_popups.remove(box)
            box.deleteLater()
        box.finished.connect(release_popup)
        box.show()

    def finish_pending_close(self):
        self.force_closing = True
        self.close()

    def confirm_close_while_saving(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("クリップを保存中です")
        box.setText("クリップを保存しています。")
        box.setInformativeText("保存完了後にFPVSIM-Clipperを終了しますか？")
        close_button = box.addButton("保存後に終了", QMessageBox.AcceptRole)
        cancel_button = box.addButton("終了をキャンセル", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is close_button

    def show_saved_path(self, path, track, kind_label, seconds):
        self.add_saved_clip(path, track, kind_label, seconds)
        self.persist_recent_clip(path, track, kind_label, seconds)

    def add_saved_clip(self, path, track, kind_label, seconds):
        timing_text = "タイム未確定" if "緊急" in kind_label else f"{seconds:.3f}秒"
        label = f"{kind_label}｜{track}｜{timing_text}"
        index = -1
        for item_index in range(self.saved_selector.count()):
            data = self.saved_selector.itemData(item_index)
            if isinstance(data, dict) and data.get("path") == path:
                index = item_index
                break
        data = {"path": path, "track": track, "kind": kind_label, "seconds": seconds}
        if index < 0:
            self.saved_selector.insertItem(0, label, data)
            index = 0
        else:
            self.saved_selector.setItemText(index, label)
            self.saved_selector.setItemData(index, data)
        self.saved_selector.setCurrentIndex(index)
        self.select_saved_clip(index)

    def persist_recent_clip(self, path, track, kind_label, seconds, previous_path=None):
        new_item = {"path": path, "track": track, "kind": kind_label, "seconds": round(float(seconds), 3)}
        history = self.store.data.get("recent_clips", [])
        kept = []
        previous_index = None
        for item in history:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            item_path = os.path.normcase(item["path"])
            if previous_path is not None and item_path == os.path.normcase(previous_path):
                previous_index = len(kept)
                continue
            try:
                exists = Path(item["path"]).is_file()
            except OSError:
                exists = False
            if exists and item_path != os.path.normcase(path):
                kept.append(item)
        if previous_index is None:
            kept.append(new_item)
        else:
            kept.insert(previous_index, new_item)
        self.store.data["recent_clips"] = kept
        try: self.store.save()
        except OSError: pass

    @staticmethod
    def result_kind(kind_label):
        return "lap" if "1周" in str(kind_label) else "three_lap"

    def save_rename_setting(self, checked):
        self.store.data.setdefault("settings", {})["rename_on_edit"] = bool(checked)
        try: self.store.save()
        except OSError: pass

    def recompute_best_after_edit(self, old_data, new_data):
        bests = self.store.data.setdefault("bests", {})
        old_kind = self.result_kind(old_data["kind"])
        new_kind = self.result_kind(new_data["kind"])
        old_key = f'{old_data["track"]}|{old_kind}'
        new_key = f'{new_data["track"]}|{new_kind}'
        old_was_best = old_key in bests and abs(float(bests[old_key]) - float(old_data["seconds"])) < 0.0005

        if old_was_best:
            candidates = [
                float(item["seconds"])
                for item in self.store.data.get("recent_clips", [])
                if isinstance(item, dict)
                and str(item.get("track", "")) == str(old_data["track"])
                and self.result_kind(item.get("kind", "")) == old_kind
            ]
            if candidates:
                bests[old_key] = min(candidates)
            else:
                bests.pop(old_key, None)

        new_seconds = float(new_data["seconds"])
        current = bests.get(new_key)
        if current is None or new_seconds < float(current) or old_was_best and old_key == new_key:
            candidates = [
                float(item["seconds"])
                for item in self.store.data.get("recent_clips", [])
                if isinstance(item, dict)
                and str(item.get("track", "")) == str(new_data["track"])
                and self.result_kind(item.get("kind", "")) == new_kind
            ]
            bests[new_key] = min(candidates) if candidates else new_seconds

    def edit_selected_result(self):
        index = self.saved_selector.currentIndex()
        old_data = self.saved_selector.itemData(index) if index >= 0 else None
        if not isinstance(old_data, dict):
            QMessageBox.information(self, "結果を修正", "修正する保存済みクリップを選択してください")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("保存結果を修正")
        form = QFormLayout(dialog)
        track_edit = QLineEdit(str(old_data["track"]))
        kind_edit = QComboBox()
        kind_edit.addItem("最速1周", "lap")
        kind_edit.addItem("最速3周", "three_lap")
        kind_edit.setCurrentIndex(max(0, kind_edit.findData(self.result_kind(old_data["kind"]))))
        seconds_edit = QDoubleSpinBox()
        seconds_edit.setRange(0.001, 36000.0)
        seconds_edit.setDecimals(3)
        seconds_edit.setSingleStep(0.001)
        seconds_edit.setSuffix(" 秒")
        seconds_edit.setValue(float(old_data["seconds"]))
        form.addRow("コース名", track_edit)
        form.addRow("記録の種類", kind_edit)
        form.addRow("タイム", seconds_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        new_track = track_edit.text().strip()
        if not new_track:
            QMessageBox.warning(self, "結果を修正", "コース名を入力してください")
            return
        old_path = Path(str(old_data["path"]))
        new_kind = str(kind_edit.currentData())
        new_kind_label = "最速1周" if new_kind == "lap" else "最速3周"
        new_seconds = round(float(seconds_edit.value()), 3)
        new_path = old_path
        renamed = False
        if self.rename_on_edit.isChecked():
            try:
                new_path = rename_clip_metadata(
                    old_path,
                    str(old_data["track"]), self.result_kind(old_data["kind"]), float(old_data["seconds"]),
                    new_track, new_kind, new_seconds,
                )
                renamed = new_path != old_path
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.critical(self, "ファイル名の変更に失敗", str(exc))
                return

        new_data = {
            "path": str(new_path), "track": new_track,
            "kind": new_kind_label, "seconds": new_seconds,
        }
        self.saved_selector.setItemText(index, f"{new_kind_label}｜{new_track}｜{new_seconds:.3f}秒")
        self.saved_selector.setItemData(index, new_data)
        self.persist_recent_clip(str(new_path), new_track, new_kind_label, new_seconds, previous_path=str(old_path))
        self.recompute_best_after_edit(old_data, new_data)
        try: self.store.save()
        except OSError: pass
        self.select_saved_clip(index)

        if renamed:
            result = f"結果と動画ファイル名を修正しました: {new_path}"
        elif self.rename_on_edit.isChecked():
            result = "結果を修正しました（動画名に一致する旧情報がないため、ファイル名は変更していません）"
        else:
            result = "結果を修正しました（動画ファイル名の自動変更はオフ）"
        self.add_row("1周" if new_kind == "lap" else "3周", new_track, f"{new_seconds:.3f}秒", result)
        self.notify_user("結果を修正しました", result)

    def load_recent_clips(self):
        history = self.store.data.get("recent_clips", [])
        history_items = history if isinstance(history, list) else []
        all_valid = []
        for item in history_items:
            try:
                path = str(item["path"])
                track = str(item["track"])
                kind_label = str(item["kind"])
                seconds = float(item["seconds"])
                if not Path(path).is_file():
                    continue
                read_metadata(path)
            except (KeyError, TypeError, ValueError, OSError):
                continue
            all_valid.append({"path": path, "track": track, "kind": kind_label, "seconds": seconds})
        for item in all_valid[-5:]:
            path = item["path"]
            track = item["track"]
            kind_label = item["kind"]
            seconds = item["seconds"]
            self.add_saved_clip(path, track, kind_label, seconds)
            emergency = "緊急" in kind_label
            table_kind = "緊急" if emergency else ("3周" if "3周" in kind_label else "1周")
            timing_text = "未確定" if emergency else f"{seconds:.3f}秒"
            self.add_row(table_kind, track, timing_text, f"前回保存: {path}")
        if all_valid != history_items:
            self.store.data["recent_clips"] = all_valid
            try: self.store.save()
            except OSError: pass

    def select_saved_clip(self, index):
        data = self.saved_selector.itemData(index) if index >= 0 else None
        if not isinstance(data, dict):
            return
        self.last_saved_path = Path(data["path"])
        self.last_share_context = data
        self.last_share_text = self.render_share_text(data["track"], data["kind"], data["seconds"], data["path"])
        self.saved_path.setText(data["path"])
        self.saved_path.setCursorPosition(0)

    def on_share_template_changed(self):
        self.template_save_timer.start()
        if self.last_share_context:
            data = self.last_share_context
            self.last_share_text = self.render_share_text(data["track"], data["kind"], data["seconds"], data["path"])

    def save_share_template(self):
        self.store.data.setdefault("settings", {})["share_template"] = self.share_template.toPlainText()
        self.store.data["settings"]["share_tokens_version"] = 2
        try: self.store.save()
        except OSError: pass

    def open_saved_location(self):
        if not self.last_saved_path:
            QMessageBox.information(self, "保存先", "まだ保存されたクリップがありません")
            return
        folder = self.last_saved_path.parent
        if not folder.exists():
            QMessageBox.warning(self, "保存先が見つかりません", str(folder))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def share_on_x(self):
        if not self.last_saved_path:
            QMessageBox.information(self, "Xで投稿", "先にクリップを保存してください")
            return
        if self.last_share_context:
            data = self.last_share_context
            self.last_share_text = self.render_share_text(data["track"], data["kind"], data["seconds"], data["path"])
        QApplication.clipboard().setText(str(self.last_saved_path))
        self.open_saved_location()
        url = QUrl("https://twitter.com/intent/tweet")
        query = QUrlQuery(); query.addQueryItem("text", self.last_share_text)
        url.setQuery(query); QDesktopServices.openUrl(url)
        notice = "Xと保存フォルダを開きました。動画を投稿画面へ追加してください。動画のパスはコピー済みです。"
        self.status.setText(notice)
        self.tray_icon.showMessage(
            "FPVSIM-Clipper：Xで投稿", notice,
            QSystemTrayIcon.Information, 8000,
        )

    def render_share_text(self, track, kind_label, seconds, path=None):
        text = self.share_template.toPlainText().strip() or DEFAULT_SHARE_TEMPLATE
        timing_text = "未確定" if "緊急" in kind_label else f"{seconds:.3f}"
        aircraft = self.current_aircraft_name if path is None else read_metadata(path).get("aircraft", "")
        kind_text = "1lap" if self.result_kind(kind_label) == "lap" else "3lap"
        saved_datetime = None
        if path:
            metadata = read_metadata(path)
            if metadata.get('saved_at'):
                try:
                    saved_datetime = datetime.fromisoformat(metadata['saved_at'])
                except (ValueError, TypeError):
                    pass
            # Our filenames preserve the original save date even after a
            # result/filename correction, copy, or a later application launch.
            match = re.search(r"_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_\d+)?$", Path(path).stem)
            if match and saved_datetime is None:
                try:
                    saved_datetime = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    pass
            if saved_datetime is None:
                try:
                    saved_datetime = datetime.fromtimestamp(Path(path).stat().st_mtime)
                except (OSError, ValueError, OverflowError):
                    pass
        datetime_text = saved_datetime.strftime("%Y-%m-%d %H:%M:%S") if saved_datetime else "未確定"
        def date_part(format_string):
            return saved_datetime.strftime(format_string) if saved_datetime else "未確定"
        replacements = {
            "{コース名}": track,
            "{種類}": kind_text,
            "{タイム}": timing_text,
            "{course}": track,
            "{kind}": kind_text,
            "{Kind}": kind_text,
            "{record}": timing_text,
            "{aircraft}": aircraft or "未取得",
            "{time}": date_part("%H:%M:%S"),
            "{year}": date_part("%Y"),
            "{month}": date_part("%m"),
            "{date}": date_part("%d"),
            "{datetime}": datetime_text,
            "{full_date}": date_part("%Y-%m-%d"),
        }
        for token, value in replacements.items():
            text = text.replace(token, value)
        return text.replace('_', ' ')

    def evaluate_event(self, event):
        if self.mode() not in ("both", event.kind):
            return False
        is_best, old = self.store.update_best(event)
        timing_prefix = "手入力 " if event.manual else ("約" if event.estimated else "")
        timing_text = timing_prefix + f"{event.seconds:.3f}秒"
        if not is_best:
            self.signals.row.emit(
                "1周" if event.kind == "lap" else "3周",
                event.track, timing_text, f"更新なし（ベスト {old:.3f}秒）",
            )
            return False
        if event.manual:
            result = "手入力したタイムで自己ベストを更新しました。設定した前後の時間を含めて保存します。"
        elif event.estimated:
            result = "推定タイムで自己ベストを更新しました。設定した前後の時間を含めて保存します。"
        else:
            result = "自己ベストを更新しました。設定した前後の時間を含めて保存します。"
        self.signals.row.emit(
            "1周" if event.kind == "lap" else "3周",
            event.track, timing_text, result,
        )
        return True

    def prompt_missing_time(self, context):
        estimate = float(context["estimated_seconds"])
        track = str(context["track"])
        QApplication.beep()
        self.notify_user("タイム入力が必要です", "元動画は保存済みです。結果画面のタイムを入力してください")
        seconds, accepted = QInputDialog.getDouble(
            self,
            "3周タイムを入力",
            "元動画は保存済みです。入力を放置・キャンセルしても元動画は残ります。\n"
            "VelociDroneから完走タイムが届きませんでした。\n"
            "VelociDroneの結果画面に表示された3周タイムを秒で入力してください。\n"
            f"参考（PC計測・記録ではありません）: {estimate:.3f}秒\n保存先: {context['source']}",
            max(0.001, estimate),
            0.001,
            36000.0,
            3,
        )
        events = list(context.get("events", []))
        if accepted:
            manual_event = LapEvent(
                "three_lap", round(float(seconds), 3), track,
                race_elapsed=float(context["duration"]), manual=True, aircraft=context.get("aircraft", ""),
            )
            if self.evaluate_event(manual_event):
                events.append(manual_event)
            try:
                with DEBUG_FILE.open("a", encoding="utf-8") as debug:
                    debug.write(
                        time.strftime("%Y-%m-%d %H:%M:%S ")
                        + f"MANUAL three_lap={seconds:.3f} estimate={estimate:.3f} track={track}\n"
                    )
            except OSError:
                pass
        else:
            self.signals.status.emit("タイム入力をキャンセルしました。元動画は保存済みです")
        if events:
            self.begin_save()
            threading.Thread(
                target=self.finish_race_clips,
                args=(events, float(context["duration"]), Path(context["source"]), context["wait_after_finish"], True),
                daemon=True,
            ).start()

    def save_obs_replay(self):
        with self.obs_save_lock:
            return self.obs.save()

    def capture_missing_race(self, context):
        """Secure the original before requesting an authoritative manual time."""
        try:
            wait_after_finish = max(2.0, 2.0 + self.three_after_value)
            if context.get("events") and any(item.kind == "lap" for item in context["events"]):
                wait_after_finish = max(wait_after_finish, self.lap_after_value)
            time.sleep(wait_after_finish)
            missing_kind = context.get("missing_kind", "three_lap")
            pending_event = LapEvent(
                missing_kind, max(0.001, float(context["estimated_seconds"])), str(context["track"]),
                race_elapsed=float(context["duration"]), estimated=True, aircraft=context.get("aircraft", ""),
            )
            # Keep the pending original in the OBS root. If the dialog is left
            # open or cancelled, this file is still a complete recoverable clip.
            with self.obs_save_lock:
                requested_at = time.monotonic()
                source = self.save_obs_replay()
                self.protected_replays.add(Path(source).resolve())
                try:
                    source_info = read_metadata(source)
                    if not source_info or source_info.get('pending'):
                        write_metadata(source, pending_event.track,
                                       '1lap' if missing_kind == 'lap' else '3lap',
                                       0.0, datetime.now(), pending=True, aircraft=pending_event.aircraft)
                except OSError:
                    # The dedicated copy below still has its pending filename.
                    pass
                try:
                    # Secure each run before the next OBS replay request.
                    secured = archive_replay(source, pending_event, "none", delete_source=False, pending=True)
                except Exception as exc:
                    if not Path(source).is_file() or Path(source).stat().st_size == 0:
                        raise
                    secured = Path(source)
                    self.signals.status.emit(f"専用名で保存できませんでした。OBSの元動画を保持します: {exc}")
                    if not read_metadata(source).get('pending'):
                        self.signals.timing_alert.emit(pending_event.track, f"元動画はOBSの保存先にありますが、保護情報を保存できませんでした。別の場所にコピーしてください: {source}")
                self.protected_replays.add(Path(secured).resolve())
            finished_at = context.get('finished_at')
            if finished_at is not None:
                wait_after_finish = max(wait_after_finish, requested_at - float(finished_at))
            context["source"] = str(secured)
            context["wait_after_finish"] = wait_after_finish
            # Unknown times are never entered into the best-time database.
            label = "緊急保存（1周・元動画）" if missing_kind == "lap" else "緊急保存（元動画）"
            self.signals.saved.emit(str(secured), pending_event.track, label, 0.0)
            self.signals.row.emit(
                "1周" if missing_kind == "lap" else "3周", pending_event.track, "タイム未確定",
                f"元動画を確保しました: {secured}",
            )
            events = list(context.get("events", []))
            context["events"] = []
            if missing_kind == "three_lap":
                self.signals.missing_time.emit(dict(context))
            else:
                self.signals.timing_alert.emit(pending_event.track, f"1周のタイムが届きませんでした。元動画を保存しました: {secured}")
            if events:
                self.begin_save()
                self.finish_race_clips(
                    events, float(context["duration"]), secured,
                    wait_after_finish=wait_after_finish, source_preserved=True,
                )
        except Exception as exc:
            self.signals.row.emit(
                "1周" if context.get("missing_kind") == "lap" else "3周", str(context.get("track", "Current Course")), "タイム未取得",
                f"元動画の保存に失敗: {exc}",
            )
            self.signals.timing_alert.emit(str(context.get("track", "Current Course")), f"OBSから元動画を保存できませんでした。OBSの状態を確認してください。詳細: {exc}")
        finally:
            self.end_save()

    def emergency_save(self):
        try:
            self.check_naming_rules()
            if not self.monitoring:
                self.folder_template_value = self.folder_template.text()
                self.filename_template_value = self.filename_template.text()
        except ValueError as exc:
            QMessageBox.warning(self, '命名ルールを修正してください', str(exc))
            return
        if not self.obs:
            try:
                self.obs = self.connect_obs()
                self.obs.start_buffer()
            except Exception as exc:
                QMessageBox.critical(self, "緊急保存に失敗", str(exc))
                return
        elapsed = 0.0
        if self.race_clock_started is not None:
            elapsed = max(0.0, time.monotonic() - self.race_clock_started)
        self.queue_emergency_save(
            self.course_name.text().strip() or "Current Course", elapsed,
            self.folder_mode.currentData(),
        )

    def queue_emergency_save(self, track, elapsed, folder_mode=None):
        with self.emergency_lock:
            if self.emergency_capture_active:
                self.signals.status.emit("元動画を保存しています")
                return
            self.emergency_capture_active = True
        self.begin_save()
        threading.Thread(
            target=self.capture_emergency_replay,
            args=(track, elapsed, folder_mode or getattr(self, "folder_mode_value", "course")), daemon=True,
        ).start()

    def capture_emergency_replay(self, track, elapsed, folder_mode):
        try:
            source = self.save_obs_replay()
            event = LapEvent("three_lap", round(max(0.001, elapsed), 3), track, estimated=True, aircraft=self.current_aircraft_name)
            target = archive_replay(source, event, folder_mode, delete_source=True, pending=True, **self.naming_options())
            self.signals.saved.emit(str(target), track, "緊急保存（元動画）", event.seconds)
            self.signals.row.emit("緊急", track, "未確定", f"元動画を保存しました: {target}")
        except Exception as exc:
            self.signals.row.emit("緊急", track, "未確定", f"保存失敗: {exc}")
            if isinstance(exc, ValueError):
                self.signals.alert.emit('命名ルールを修正してください', str(exc))
        finally:
            with self.emergency_lock:
                self.emergency_capture_active = False
            self.end_save()

    def monitor(self):
        parser = VeloRaceParser()
        parser.track = self.course_name.text().strip() or "Current Course"
        config = getattr(self, "connection_config", None) or {"velo_port": int(self.velo_port.text()), "host": self.host.text(), "port": int(self.port.text()), "password": self.password.text()}
        setdefaulttimeout(3)
        self.race_aircraft_name = self.current_aircraft_name
        def on_open(ws):
            if self.stop_event.is_set():
                request_disconnect(ws)
                return
            try:
                validate_native_media()
                if self.obs is None:
                    self.obs = Obs(config["host"], config["port"], config["password"])
                self.obs.start_buffer()
                if self.stop_event.is_set():
                    self.obs.stop_buffer()
                    request_disconnect(ws)
                    return
                self.signals.status.emit("録画中：OBS・VelociDroneとも正常に接続しました")
                self.signals.connection_state.emit("connected")
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.signals.status.emit(f"OBSの準備待ちです。起動と接続設定を確認してください。再接続します。詳細：{exc}")
                    self.signals.connection_state.emit("connecting")
                request_disconnect(ws)
        def on_message(ws, message):
            try:
                with DEBUG_FILE.open("a", encoding="utf-8") as debug:
                    debug.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(message) + "\n")
            except OSError:
                pass
            try:
                payload = json.loads(message)
                keys = ", ".join(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
                self.signals.status.emit(f"受信中：{keys}")
            except Exception:
                payload = {}
                self.signals.status.emit("受信中：解析前データ")
            status = payload.get("racestatus", {}) if isinstance(payload, dict) else {}
            action = str(status.get("raceAction", "")).lower() if isinstance(status, dict) else ""
            completed_finish = self.finish_gate.feed(payload)
            if action == "start":
                self.race_aircraft_name = (detect_aircraft_name() or "") if self.aircraft_auto_enabled else self.current_aircraft_name
                self.signals.aircraft.emit(self.race_aircraft_name)
                self.race_started = time.monotonic(); self.race_clock_started = None
                self.race_three_detected = False; self.pending_best = []
                self.race_lap_detected = False
                self.emergency_saved_for_race = False
                detected = detect_course_name(self.log.text())
                parser.track = detected or self.course_name.text().strip() or "Current Course"
                if detected: self.signals.course.emit(detected)
            elif action in FinishGate.CANCEL_ACTIONS:
                self.race_started = None; self.race_clock_started = None
                self.race_three_detected = False; self.pending_best = []
            countdown = payload.get("countdown", {}) if isinstance(payload, dict) else {}
            if (isinstance(countdown, dict) and str(countdown.get("countValue", "")) == "0"
                    and self.race_started is not None and self.race_clock_started is None):
                self.race_clock_started = time.monotonic()
            for event in parser.feed(message):
                event.aircraft = self.race_aircraft_name
                if self.race_started is not None:
                    event.race_elapsed = time.monotonic() - self.race_started
                if event.kind == "three_lap":
                    self.race_three_detected = True
                elif event.kind == "lap":
                    self.race_lap_detected = True
                self.handle_event(event)
            if action == "race finished" and not completed_finish and not self.finish_gate.active:
                # A reset can emit this message too; retire the run without an alarm.
                self.race_started = None
                self.race_clock_started = None
                self.pending_best = []
                self.race_three_detected = False
                self.race_lap_detected = False
            if completed_finish and self.race_started is not None:
                now = time.monotonic()
                duration = now - self.race_started
                request_manual_time = False
                estimated_seconds = None
                missing_three_time = not self.race_three_detected and self.mode() in ("both", "three_lap")
                missing_lap_time = not self.race_lap_detected and self.mode() in ("both", "lap")
                if missing_three_time:
                    estimated_seconds = (round(now - self.race_clock_started, 3)
                                         if self.race_clock_started is not None else 0.0)
                    request_manual_time = True
                elif missing_lap_time:
                    estimated_seconds = (round(now - self.race_clock_started, 3)
                                         if self.race_clock_started is not None else 0.0)
                best_by_kind = {}
                for item in self.pending_best:
                    if item.kind not in best_by_kind or item.seconds < best_by_kind[item.kind].seconds:
                        best_by_kind[item.kind] = item
                events = list(best_by_kind.values())
                self.race_started = None; self.race_clock_started = None
                self.race_three_detected = False; self.pending_best = []
                if request_manual_time or missing_lap_time:
                    self.signals.status.emit("タイム未取得：元動画を先に保存しています。" + ("保存後に入力画面を表示します" if request_manual_time else ""))
                    context = {
                        "track": parser.track,
                        "estimated_seconds": estimated_seconds,
                        "aircraft": self.race_aircraft_name,
                        "duration": duration,
                        "events": events,
                        "missing_kind": "three_lap" if request_manual_time else "lap",
                        "finished_at": now,
                    }
                    self.begin_save()
                    threading.Thread(
                        target=self.capture_missing_race, args=(context,), daemon=True,
                    ).start()
                else:
                    if events:
                        self.begin_save()
                        threading.Thread(
                            target=self.finish_race_clips, args=(events, duration),
                            daemon=True,
                        ).start()
        def on_error(ws, error):
            if not self.stop_event.is_set():
                self.signals.status.emit(f"VelociDroneの起動と接続を待っています。WebSocketの設定も確認してください。詳細：{error}")
                self.signals.connection_state.emit("connecting")
                if self.race_clock_started is not None and not self.emergency_saved_for_race:
                    self.emergency_saved_for_race = True
                    elapsed = max(0.0, time.monotonic() - self.race_clock_started)
                    self.queue_emergency_save(
                        parser.track, elapsed, getattr(self, "folder_mode_value", "course")
                    )
        while not self.stop_event.is_set():
            self.signals.connection_state.emit("connecting")
            host = detect_velocidrone_ip(int(config["velo_port"]))
            self.signals.velo_address.emit(host)
            uri = f"ws://{host}:{config['velo_port']}/velocidrone"
            socket = WebSocketApp(uri, on_open=on_open, on_message=on_message, on_error=on_error)
            self.velo_socket = socket
            ping_stop = threading.Event()
            def keepalive(socket=socket, ping_stop=ping_stop):
                while not ping_stop.wait(5):
                    try:
                        transport = getattr(socket, 'sock', None)
                        if transport is not None and transport.connected:
                            socket.send(json.dumps({"command": "ping"}))
                    except Exception: pass
                    detected = detect_course_name(self.log.text())
                    if detected and detected != parser.track:
                        parser.track = detected
                        self.signals.course.emit(detected)
            threading.Thread(target=keepalive, daemon=True).start()
            try:
                if not self.stop_event.is_set():
                    socket.run_forever()
            except Exception as exc:
                on_error(socket, exc)
            finally:
                ping_stop.set()
                if getattr(self, 'velo_socket', None) is socket:
                    self.velo_socket = None
            if not self.stop_event.wait(2):
                self.signals.connection_state.emit("connecting")
                self.signals.status.emit("VelociDroneへの再接続を待っています")

    def handle_event(self, event):
        if self.evaluate_event(event):
            self.pending_best.append(event)

    def finish_race_clips(self, events, race_duration, source=None, wait_after_finish=None, source_preserved=False):
        try:
            if wait_after_finish is None:
                wait_after_finish = 2.0
                if any(event.kind == "lap" for event in events):
                    wait_after_finish = max(wait_after_finish, self.lap_after_value)
                if any(event.kind == "three_lap" for event in events):
                    wait_after_finish = max(wait_after_finish, 2.0 + self.three_after_value)
            if source is None:
                time.sleep(wait_after_finish)
                source = self.save_obs_replay()
            else:
                source = Path(source)
            replay_root = source.parent
            all_clips_succeeded = True
            for index, event in enumerate(events):
                try:
                    if event.kind == "lap":
                        clip_duration = event.seconds + self.lap_before_value + self.lap_after_value
                        lap_ended = event.race_elapsed if event.race_elapsed is not None else race_duration
                        end_offset = max(
                            0.0,
                            race_duration + wait_after_finish - lap_ended - self.lap_after_value,
                        )
                    else:
                        clip_duration = event.seconds + self.three_before_value + self.three_after_value
                        race_ended = event.race_elapsed if event.race_elapsed is not None else race_duration
                        end_offset = max(0.0, race_duration + wait_after_finish - race_ended - self.three_after_value)
                    target = trim_clip(
                        source, clip_duration, event,
                        delete_source=False,
                        folder_mode=self.folder_mode_value,
                        end_offset=end_offset,
                        precise=True,
                        **self.naming_options(),
                    )
                    latest_only = self.latest_lap_enabled if event.kind == "lap" else self.latest_three_enabled
                    with self.obs_save_lock:
                        protected_paths = tuple(self.protected_replays)
                    removed = delete_older_clips(target, event, replay_root, protected_paths=protected_paths) if latest_only else 0
                    result = f"保存完了（走行部分 {clip_duration:.3f}秒・前後の追加時間を含む）: {target}"
                    kind_label = "最速1周" if event.kind == "lap" else ("手入力最速3周" if event.manual else ("推定最速3周" if event.estimated else "最速3周"))
                    self.signals.saved.emit(str(target), event.track, kind_label, event.seconds)
                    if removed:
                        result += f"（過去動画{removed}本を削除）"
                except Exception as exc:
                    all_clips_succeeded = False
                    if isinstance(exc, ValueError):
                        self.signals.alert.emit('命名ルールを修正してください', str(exc) + '\n録画設定の命名ルールを変更してください。元動画は保持しています。')
                    result = f"切り出し未完了: {exc} / 元動画の保存先: {source}"
                timing_prefix = "手入力 " if event.manual else ("約" if event.estimated else "")
                timing_text = timing_prefix + f"{event.seconds:.3f}秒"
                self.signals.row.emit("1周" if event.kind == "lap" else "3周", event.track, timing_text, result)
            if events and all_clips_succeeded and not source_preserved:
                try:
                    source.unlink()
                except OSError:
                    pass
        except Exception as exc:
            for event in events:
                self.signals.row.emit("1周" if event.kind == "lap" else "3周", event.track, f"{event.seconds:.3f}秒", f"OBS保存失敗: {exc}")
        finally:
            self.end_save()

    def test_save(self):
        try: obs = self.connect_obs(); obs.start_buffer(); QMessageBox.information(self, "成功", f"OBSへ保存を要求しました\n{obs.save()}")
        except Exception as exc: QMessageBox.critical(self, "失敗", str(exc))

    def test_log(self):
        path = Path(self.log.text())
        if not path.exists(): QMessageBox.warning(self, "ログなし", "Player.logを選択してください"); return
        parser, found = LogParser(), []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]:
            event = parser.parse(line)
            if event: found.append({"kind": event.kind, "seconds": event.seconds, "track": event.track})
        QMessageBox.information(self, "検出結果", json.dumps(found[-10:], ensure_ascii=False, indent=2) if found else "ログの直近5000行に1周・3周の記録がありません。WebSocketの受信状況はこの確認には含みません。")

    def set_status(self, text): self.status.setText(text)
    def set_course(self, name):
        if name and self.course_name.text() != name:
            self.course_name.setText(name)
            self.status.setText(f"コース変更を検出：{name}")
    def add_row(self, kind, track, timing, result):
        self.table.insertRow(0)
        for column, value in enumerate((kind, track, timing, result)): self.table.setItem(0, column, QTableWidgetItem(value))
    def closeEvent(self, event):
        if getattr(self, 'update_downloading', False):
            QMessageBox.information(self, '更新中です', 'ダウンロードが完了するまでお待ちください。')
            event.ignore()
            return
        if self.force_closing:
            self.stop()
            self.tray_icon.hide()
            event.accept()
            return
        if self.is_saving():
            if self.confirm_close_while_saving():
                self.close_after_save = True
                self.status.setText("クリップの保存完了後にFPVSIM-Clipperを終了します。")
            else:
                self.close_after_save = False
                self.status.setText("終了をキャンセルしました。クリップの保存を続けています。")
            event.ignore()
            return
        if self.monitoring:
            answer = QMessageBox.question(
                self, "監視中です",
                "VelociDroneを監視しています。FPVSIM-Clipperを終了しますか？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.force_closing = True
        self.stop()
        self.tray_icon.hide()
        event.accept()


if __name__ == "__main__":
    if sys.platform != 'win32':
        sys.exit('FPVSIM-Clipper v1.0.0 は Windows のみに対応しています。')
    if len(sys.argv) == 4 and sys.argv[1] == '--apply-update':
        try:
            updater.apply_update(sys.argv[2], int(sys.argv[3]))
        except Exception as exc:
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, f'更新に失敗しました。FPVSIM-Clipperを起動し直してください。\n{exc}', 'FPVSIM-Clipper', 0x10)
            sys.exit(1)
        sys.exit(0)
    if len(sys.argv) == 6 and sys.argv[1] == "--trim-native":
        # Allows checking the packaged native engine without starting a recording session.
        from native_media import render_interval
        try:
            render_interval(Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]))
        except Exception as exc:
            if sys.stderr is not None:
                print(str(exc), file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "app-icon.png")))
    window = Window()
    window.show()
    sys.exit(app.exec())
