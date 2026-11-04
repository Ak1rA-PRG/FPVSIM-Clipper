# FPVSIM-Clipper

VelociDroneの自己ベスト更新を検出し、OBS Studioのリプレイバッファから飛行動画を保存する非公式ツールです。

> **公開準備中**：Windows版の動画書き出しと同梱ライブラリの再配布条件を確認中です。ダウンロード可能な正式版はまだありません。

## 主な機能

- 1周・3周の自己ベストを検出し、記録タイムに合わせて動画を切り出し
- 開始前・終了後に追加する時間を設定
- コース名・機体名の自動取得と手動修正
- 保存先・フォルダ・ファイル名の命名ルールを設定
- 記録を選び、差し込み項目を使ってXの投稿文を作成
- タイムを取得できなかった場合、元動画の確保を試行し、3周タイムの未取得時は手入力を案内

自動取得や保存は通信やOBSの状態によって失敗する場合があります。大切な動画はバックアップしてください。

## 必要なもの

- Windows 10 バージョン1809以降 / Windows 11（64ビット）
- VelociDrone
- [OBS Studio](https://obsproject.com/ja)（WebSocketとリプレイバッファを使用）

Windows向け配布版はPythonのインストールを不要とする予定です。macOS版は対応していません。

## インストールと初期設定

1. 配布開始後、[Releases](https://github.com/Ak1rA-PRG/FPVSIM-Clipper/releases)からWindows版をダウンロードし、ZIPを展開します。
2. OBS StudioとVelociDroneをインストールして起動します。
3. OBSでVelociDroneの映像・音声が映るシーンを作成し、リプレイバッファとWebSocketサーバーを有効にします。
4. VelociDrone側のWebSocket通信を有効にします。
5. FPVSIM-Clipperを起動し、セットアップウィザードの案内に従って接続先、保存先、命名ルールなどを設定します。
6. 接続テストとテスト保存を行い、映像・音声・保存先を確認してから監視を開始します。

OBSのリプレイバッファは、保存したい周回時間と前後の追加時間を収められる長さに設定してください。各ソフトの項目名や設定方法はバージョンによって異なる場合があります。

## 保存と記録

保存する動画の長さは記録タイムに前後の追加時間を加えたものです。ファイル名のタイムは動画の長さではなく、レースの記録タイムを表します。切り出し位置には映像フレーム等による誤差が生じる場合があります。

レース終了時にタイムを取得できなかった場合は、元動画の確保を優先します。3周タイムが未取得の場合は、元動画の保存後に入力を案内します。1周タイムが未取得の場合は、元動画の保存を通知します。ただしOBSや通信の状態によっては、元動画も保存できない場合があります。画面の文字認識（OCR）によるタイム取得は実装されていません。

「最新動画だけ残す」は1周・3周それぞれに設定できます。タイム未確定の元動画はこの整理の対象外です。

## 命名ルールとXへの投稿

保存先やファイル名、投稿文は設定画面で編集できます。命名ルールでは次の差し込み項目を使用できます。

| 項目 | 内容 |
| --- | --- |
| `{course}` / `{aircraft}` | コース名 / 機体名 |
| `{kind}` / `{record}` | 1lapまたは3lap / 記録タイム |
| `{year}` / `{month}` / `{date}` / `{time}` | 年 / 月 / 日 / 時刻 |
| `{datetime}` / `{full_date}` | 日時 / 日付 |

例：`{course}_{kind}_{record}s_{datetime}`

使用できないファイル名や文字を入力した場合は修正を求めます。Xへの投稿は内容を確認してから行ってください。

## データとプライバシー

設定・記録はアプリフォルダ内の`fpvsim_clipper_data.json`と`fpvsim_clipper_internal`に保存されます。設定ファイルにはOBS WebSocketのパスワードが含まれます。ファイルを共有・公開せず、移行時はアプリを終了してからバックアップしてください。

受信データは`fpvsim_clipper_ws_debug.log`に記録されます。ログや設定ファイルに接続情報・参加者情報などが含まれる可能性があるため、公開や問い合わせの前に内容を確認し、必要に応じて伏せてください。

## 不具合の報告

[Issues](https://github.com/Ak1rA-PRG/FPVSIM-Clipper/issues)に、発生した状況、OS・OBS・VelociDrone・アプリのバージョン、再現手順を記載してください。ログを共有する場合は、個人情報や接続情報を取り除いてください。

## ソースからの起動

アプリのソース、テスト、アイコンは `src` にあります。WindowsでPython 3.14を使用する場合、リポジトリのルートで次を実行します。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\fpvsim_clipper_qt.py
```

テストは `.\.venv\Scripts\python.exe -m unittest discover -s src -p 'test_*.py'` で実行できます。

## Windows版のビルド

Python 3.14の仮想環境と、適用条件を満たすVisual Studioのx64再配布用CRTが必要です。`FPVSIM_VC_REDIST` には、`MSVCP140.dll`などがある再配布用フォルダを指定してください。バージョンとパスは環境に合わせます。`debug_nonredist` は使用しません。

```powershell
.\.venv\Scripts\python.exe -m pip install -r build-requirements.txt
$env:FPVSIM_VC_REDIST = 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Redist\MSVC\<version>\x64\Microsoft.VC145.CRT'
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm FPVSIM-Clipper.spec
```

`dist/FPVSIM-Clipper` はアプリ本体の出力です。これだけでは配布用として完成しません。ライセンス資料、対応ソース、利用者にライブラリを差し替えてもらうための案内などを含むフォルダ構成と、配布条件を別途確認してください。[公開準備メモ](RELEASE_PREPARATION.md)を参照してください。

## ライセンスと免責

アプリ本体は[MIT License](LICENSE)です。PySide6 / Qtを含む第三者ライブラリには、それぞれのライセンスが適用されます。詳細は[第三者ライセンス一覧](THIRD_PARTY_NOTICES.md)と[Qtの差し替えについて](QT_REPLACEMENT.md)を参照してください。配布物に必要な通知・ソースの提供方法は公開前に確認します。

本アプリはVelociDroneのWebSocket、ログ、ローカルデータベースを読み取ります。具体的な取得・配布方法、特にデータベースの直接読み取りについて、VelociDrone公式の許可は未確認です。

FPVSIM-ClipperはBat Cave GamesまたはOBS Projectの公式製品ではなく、各社による公認・支援・提携を意味しません。VelociDroneおよびOBS Studioはそれぞれの権利者に帰属します。
