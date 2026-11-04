"""Shared naming controls and first-run setup pages."""
import platform
import sys
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWizard, QWizardPage, QWidget, QLabel, QPushButton, QLineEdit,
    QComboBox, QCheckBox, QVBoxLayout, QFormLayout, QMessageBox, QScrollArea, QPlainTextEdit)
from naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, render_name

NAMING_HELP = ('{course}＝コース名　{aircraft}＝機体名　{kind}＝1lap / 3lap　{record}＝記録タイム\n'
               '{year}＝年　{month}＝月　{date}＝日　{time}＝時-分-秒\n'
               '{datetime}＝保存日時　{full_date}＝年月日。拡張子は自動で付きます。')

class NamingPanel(QWidget):
    def __init__(self, settings, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.custom_template = settings.get('folder_custom_template', settings.get('folder_template', DEFAULT_FOLDER_TEMPLATE))
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        for label, value in [('コースごと', 'course'), ('機体名ごと', 'aircraft'), ('日付ごと', 'date'), ('フォルダなし', 'none'), ('命名ルールを指定', 'custom')]:
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.get('folder_mode', 'course'))))
        self.folder = QLineEdit()
        self.filename = QLineEdit(settings.get('filename_template', DEFAULT_FILE_TEMPLATE))
        form.addRow('保存フォルダ', self.mode)
        form.addRow('フォルダ名のルール', self.folder)
        form.addRow('ファイル名のルール', self.filename)
        help_text = QLabel(NAMING_HELP + '\nフォルダ名は「命名ルールを指定」を選ぶと編集できます。階層を分ける記号は使えません。')
        help_text.setWordWrap(True)
        form.addRow('差し込み項目', help_text)
        self.check_button = QPushButton('作成される名前を確認')
        form.addRow('', self.check_button)
        self.mode.currentIndexChanged.connect(self.sync_folder)
        self.folder.textEdited.connect(self.remember_custom)
        self.sync_folder()

    def remember_custom(self, value):
        if self.mode.currentData() == 'custom': self.custom_template = value

    def sync_folder(self, *_):
        mode = self.mode.currentData()
        value = {'course': '{course}', 'aircraft': '{aircraft}', 'date': '{full_date}', 'none': ''}.get(mode, self.custom_template)
        self.folder.setText(value)
        self.folder.setEnabled(mode == 'custom')
        self.folder.setPlaceholderText('フォルダは作成しません' if mode == 'none' else '{course}')

    def values(self):
        if self.mode.currentData() == 'custom': self.custom_template = self.folder.text()
        return {'folder_mode': self.mode.currentData(), 'folder_template': self.folder.text(),
                'filename_template': self.filename.text(), 'folder_custom_template': self.custom_template}

    def preview(self):
        course, aircraft = self.context()
        args = (course or 'Course', '3lap', 95.534)
        template = self.filename.text()
        if template.lower().endswith('.mp4'): template = template[:-4]
        filename = render_name(template, *args, aircraft=aircraft)
        folder = render_name(self.folder.text(), *args, aircraft=aircraft) if self.mode.currentData() != 'none' else '作成しません'
        return folder, filename

    def show_preview(self):
        try:
            folder, filename = self.preview()
        except ValueError as exc:
            QMessageBox.warning(self, '名前のルールを修正してください', str(exc))
            return
        QMessageBox.information(self, '作成される名前', f'記録95.534秒の場合の例です。\nフォルダ：{folder}\nファイル：{filename}.mp4')

class NamingPage(QWizardPage):
    def __init__(self, panel):
        super().__init__()
        self.setTitle('保存先と動画の名前')
        self.panel = panel
        layout = QVBoxLayout(self)
        layout.addWidget(panel)
        panel.check_button.clicked.connect(panel.show_preview)

    def validatePage(self):
        try:
            self.panel.preview()
            return True
        except ValueError as exc:
            QMessageBox.warning(self, '名前のルールを修正してください', str(exc))
            return False

class LaunchPage(QWizardPage):
    def __init__(self, app_name, detail):
        super().__init__()
        self.setTitle(app_name + 'を起動')
        layout = QVBoxLayout(self)
        label = QLabel(detail)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.confirm = QCheckBox(app_name + 'を起動しました')
        self.confirm.toggled.connect(lambda _checked: self.completeChanged.emit())
        layout.addWidget(self.confirm)
        layout.addStretch()

    def isComplete(self):
        return self.confirm.isChecked()

class ConsentPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle('ライセンスとクリップ保存について')
        layout = QVBoxLayout(self)
        description = QLabel('ライセンス文書を確認してください。アプリ本体と同梱ライブラリには、それぞれの条件が適用されます。')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.documents = QComboBox()
        for label, path in [
            ('アプリ本体：MIT License', 'LICENSE'),
            ('第三者ライセンス一覧', 'THIRD_PARTY_NOTICES.md'),
            ('Microsoft再配布コードの注意事項', 'MICROSOFT_REDISTRIBUTABLE_NOTICE.md'),
            ('Pythonライセンス（Windows追加条件を含む）', 'LICENSES/Python-License.txt'),
            ('Qt / PySide6：LGPL 3.0', 'LICENSES/LGPL-3.0.txt'),
        ]:
            self.documents.addItem(label, path)
        project = Path(__file__).resolve().parent.parent
        roots = [project / 'docs', project, Path(__file__).resolve().parent]
        if getattr(sys, 'frozen', False):
            bundle = Path(sys.executable).resolve().parent
            roots = [bundle / 'docs', bundle, bundle.parent / 'docs', bundle.parent, *roots]
        self.document_roots = roots
        self.documents_available = all(any((root / self.documents.itemData(i)).is_file() for root in roots)
                                       for i in range(self.documents.count()))
        if not self.documents_available:
            missing = QLabel('ライセンス文書が不足しています。配布ZIPをすべて展開してからやり直してください。')
            missing.setWordWrap(True)
            layout.addWidget(missing)
        layout.addWidget(self.documents)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        warning = QLabel('不具合、通信・録画環境、ゲームやOBSの仕様などにより、クリップが正常に保存されない場合があります。\n'
                         '3周のタイムを取得できなかった場合は、元動画の保存後に入力画面が表示されます。結果画面のタイムを入力してください。\n'
                         '必要に応じて、記録画面の「今すぐ元動画を保存」もお使いください。保存できない場合もあるため、大切な飛行は別途バックアップすることをおすすめします。')
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.license_consent = QCheckBox('適用されるライセンス条件を確認し、同意します')
        self.clip_consent = QCheckBox('クリップが正常に保存されない場合があり、\n必要に応じて「元動画を保存」ボタンを使用する必要があることを理解しました')
        for checkbox in (self.license_consent, self.clip_consent):
            checkbox.toggled.connect(lambda _checked: self.completeChanged.emit())
            layout.addWidget(checkbox)
        self.documents.currentIndexChanged.connect(self.show_document)
        self.show_document()

    def show_document(self):
        relative = self.documents.currentData()
        for root in self.document_roots:
            try:
                self.text.setPlainText((root / relative).read_text(encoding='utf-8-sig'))
                return
            except (OSError, UnicodeError):
                continue
        self.text.setPlainText('ライセンス文書が見つかりません。配布フォルダを確認してください。')

    def isComplete(self):
        return self.documents_available and self.license_consent.isChecked() and self.clip_consent.isChecked()


class SetupWizard(QWizard):
    def __init__(self, owner, native_page):
        super().__init__(owner)
        self.setWindowTitle('FPVSIM-Clipper 初期セットアップ')
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(720, 600)
        self.setButtonText(QWizard.NextButton, '次へ')
        self.setButtonText(QWizard.BackButton, '戻る')
        self.setButtonText(QWizard.FinishButton, '設定を保存')
        self.setButtonText(QWizard.CancelButton, 'キャンセル')
        self.addPage(ConsentPage())
        self.text_page('OBS Studioの入手',
            'FPVSIM-ClipperはOBS Studioのリプレイバッファを使って、飛行の映像と音声を保存します。\n\n'
            '1. 「OBS公式ダウンロードを開く」を押し、Windows版を選んでください。\n'
            '2. ダウンロードしたインストーラーを開き、画面の案内に従ってインストールします。\n\n'
            'すでにOBS Studioをインストールしている場合は、ダウンロードせず「次へ」を押してください。'
            '設定項目の名前や配置はOBSのバージョン・表示言語によって異なる場合があります。',
            ('OBS公式ダウンロードを開く', 'https://obsproject.com/download'))
        self.addPage(LaunchPage('OBS Studio', 'OBS Studioを起動し、設定が終わるまで開いたままにしてください。\n\n'
                               '初回起動時に「自動構成ウィザード」が表示された場合は、録画を主に行う選択肢を選んで進めます。'
                               'すでにOBSを使用している場合は、現在の設定を確認したうえで次へ進んでください。'))
        self.text_page('OBSの録画設定',
            'OBSの右下にある「設定」を開き、左側の「出力」を選びます。以下は「出力モード」が「詳細」の場合です。\n\n'
            '1. 「録画」タブの「録画ファイルのパス」で保存先を選びます。リプレイの元動画と、このアプリのクリップはここを基準に保存されます。\n'
            '2. 「種別」は「標準」にします。「カスタム出力 (FFmpeg)」ではリプレイバッファを使えません。\n'
            '3. 「録画フォーマット」はMP4またはMOV、映像エンコーダはH.264、音声エンコーダはAACを目安に選びます。'
            '表示されるエンコーダはPCによって異なります。切り出せない形式では元動画を保持します。\n'
            '4. 「リプレイバッファ」タブで「リプレイバッファを有効にする」をオンにします。\n'
            '5. 「最大リプレイ時間」は、最長の飛行時間にクリップの前後の追加時間と余裕を足した秒数にします。'
            '例えば300秒から試し、5分を超える飛行ではさらに増やしてください。長くするとメモリ使用量も増えます。'
            '「最大メモリ」が表示される場合は、時間より先にメモリの上限に達しないよう確認してください。\n'
            '6. 左側の「映像」で解像度とFPSを確認し、「適用」または「OK」で保存します。'
            '負荷が高い場合は出力解像度やFPSを下げてください。\n\n'
            '通常のMP4は録画の終了処理前にOBSやPCが停止すると開けなくなる場合があります。'
            'OBSのバージョンによって項目名が多少異なる場合があります。')
        self.addPage(LaunchPage('VelociDrone', 'VelociDroneをランチャーから起動し、メインメニューまで進んでください。\n\n'
                               'このあとOBSにゲーム画面を追加し、VelociDroneの通信設定を確認します。'))
        capture = ('1. OBSの「シーン」で使用するシーンを選びます。\n'
                   '2. 「ソース」の「＋」→「ゲームキャプチャ」を選び、「新規作成」→「OK」を押します。\n'
                   '3. プロパティの「モード」で「特定のウィンドウをキャプチャ」を選び、「ウィンドウ」で起動中のVelociDroneを選んで「OK」を押します。\n'
                   '4. プレビューが黒い場合は、VelociDroneを前面にしてから再確認してください。')
        self.text_page('映像と音声の確認', capture + '\n\n'
            'OBSのプレビューにゲーム画面が表示されることを確認します。\n'
            'ゲームで音を出し、「音声ミキサー」のメーターが動くか確認します。動かない場合はOBSの音声入力や取り込み先を見直します。\n'
            '不要なウィンドウや通知、マイク音声が録画に含まれないかも確認してください。',
            ('OBSの設定ガイドを開く', 'https://obsproject.com/kb/quick-start-guide'))
        self.addPage(LaunchPage('OBS Studio', 'OBS Studioが起動していることを確認してください。\n'
                               '次のページでOBSとFPVSIM-Clipperの接続を設定します。'))
        obs = QWizardPage()
        obs.setTitle('OBSとの接続')
        form = QFormLayout(obs)
        hint = QLabel('OBSの上部メニュー「ツール」→「WebSocket サーバー設定」を開きます。\n'
                      '1. 「WebSocketサーバーを有効にする」と「認証を有効にする」にチェックを入れます。\n'
                      '2. 「接続情報を表示」を押し、「サーバーポート」と「サーバーパスワード」を確認します。\n'
                      '3. この画面のポートとパスワードに同じ値を入力し、OBSで「OK」を押します。\n'
                      'OBSと同じPCならIPアドレスは通常127.0.0.1、ポートは通常4455です。'
                      'OBSが別のPCの場合は、そのPCのアドレスを入力してください。パスワードは他人に共有しないでください。')
        hint.setWordWrap(True)
        form.addRow(hint)
        self.obs_host = QLineEdit(owner.host.text())
        self.obs_port = QLineEdit(owner.port.text())
        self.obs_password = QLineEdit(owner.password.text())
        self.obs_password.setEchoMode(QLineEdit.Password)
        form.addRow('IPアドレス（通常は127.0.0.1）', self.obs_host)
        form.addRow('ポート（通常は4455）', self.obs_port)
        form.addRow('パスワード', self.obs_password)
        self.addPage(obs)
        self.addPage(LaunchPage('VelociDrone', 'VelociDroneが起動していることを確認してください。\n'
                               '次のページでVelociDroneとFPVSIM-Clipperの接続を設定します。'))
        velo = QWizardPage()
        velo.setTitle('VelociDroneとの接続')
        form = QFormLayout(velo)
        hint = QLabel('VelociDroneのメインメニューで「Options」→「Main Settings」を開きます。\n'
                      '1. 設定一覧をスクロールし、「Websocket Communication」を探します。\n'
                      '2. 「Websocket Communication」を「YES」にし、設定を閉じます。\n'
                      '3. この画面のポートは通常60003です。監視開始時に接続先IPを自動検出します。'
                      '下のIP欄の値は監視開始時に検出結果で更新されます。\n'
                      'Windowsファイアウォールの確認が出た場合は、利用する信頼できるプライベートネットワークでの通信を許可してください。\n'
                      'ゲームの画面・表記はバージョンによって異なる場合があります。')
        hint.setWordWrap(True)
        form.addRow(hint)
        self.velo_host = QLineEdit(owner.velo_host.text())
        self.velo_port = QLineEdit(owner.velo_port.text())
        form.addRow('IPアドレス', self.velo_host)
        form.addRow('ポート', self.velo_port)
        self.addPage(velo)
        preference = QWizardPage()
        preference.setTitle('保存する記録')
        form = QFormLayout(preference)
        self.mode = QComboBox()
        for label, value in [('1周と3周の両方', 'both'), ('1周のみ', 'lap'), ('3周のみ', 'three_lap')]: self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(owner.mode())))
        self.latest_lap = QCheckBox('1周：最新のベスト動画だけ残す')
        self.latest_three = QCheckBox('3周：最新のベスト動画だけ残す')
        self.latest_lap.setChecked(owner.latest_lap.isChecked())
        self.latest_three.setChecked(owner.latest_three.isChecked())
        form.addRow('保存する自己ベスト', self.mode)
        form.addRow('過去動画の整理', self.latest_lap)
        form.addRow('', self.latest_three)
        note = QLabel('選択した周回数の自己ベストを更新したときに保存します。\n'
                      '「最新のベスト動画だけ残す」をオンにすると、同じコース・周回数の古い管理対象動画を削除します。'
                      '1周と3周は別々に設定できます。タイム未確定の元動画は削除しません。')
        note.setWordWrap(True)
        form.addRow(note)
        self.addPage(preference)
        self.naming = NamingPanel(owner.naming_panel.values(), lambda: (owner.course_name.text(), owner.current_aircraft_name))
        self.addPage(NamingPage(self.naming))
        self.addPage(native_page)
        self.text_page('設定後の確認',
            '「設定を保存」を押すと、記録画面に戻ります。\n\n'
            '1. OBSとVelociDroneを起動したまま、このアプリの「記録」タブで「監視開始」を押します。\n'
            '2. 接続待ちは「接続キャンセル」、OBSとVelociDroneへの接続および録画準備が整うと「録画停止」と表示されます。\n'
            '3. 数秒飛行し、「今すぐ元動画を保存」を押します。保存先を開き、映像と音声が記録されているか確認します。\n'
            '4. 記録を更新した際は、一覧の結果と保存された動画を確認します。タイムを取得できない場合は元動画の保存後に入力を案内します。\n\n'
            'OBSのプレビューが黒い場合は、接続できていてもゲーム画面は録画できません。\n'
            'クリップの前後の追加時間は「録画設定」で変更できます。ウィザードも同じタブから再実行できます。')

    def text_page(self, title, body, link=None):
        page = QWizardPage()
        page.setTitle(title)
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        label = QLabel(body)
        label.setWordWrap(True)
        layout.addWidget(label)
        if link:
            button = QPushButton(link[0])
            button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(link[1])))
            layout.addWidget(button)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.addPage(page)
