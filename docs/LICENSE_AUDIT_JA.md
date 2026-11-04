# Windows配布前のライセンス確認

## 対象

FPVSIM-Clipper v1.0.0 のWindows x64フォルダ形式の配布候補です。macOS版は対象外です。アプリ本体はMIT License（著作権者 Ak1rA）ですが、同梱ライブラリの条件は別に適用されます。

## 確認できたこと

- 公式Python 3.14.10埋め込み版から集めた24ファイル、Qt / PySide6 / Shibokenの33ファイル、PyWinRTの9拡張を、それぞれ公式配布ファイルとハッシュ照合しました。
- Qt / PySide6 6.10.3の公式ソースを取得し、４つのアーカイブのSHA-256がQt公式配布サイトの公表値と一致すること、およびQtの94件の著作権表示に記載された参照ファイルがローカルのライセンス資料に存在することを確認しました。該当ソースのアーカイブとハッシュは `THIRD_PARTY_NOTICES.md` に記載しています。
- Qt / PySide6のDLLとPythonコードを外部化し、Qt 6.10.2への置換後に動画切り出しが成功することをテストしました。詳しくは `QT_REPLACEMENT.md` を参照してください。
- 不要なQt翻訳カタログ、任意のMesaソフトウェアOpenGLレンダラー、別製品由来のOpenSSLとWindowsシステムDLLを除外しました。Windows版のOBS・VelociDroneを使う実機確認は別途必要です。
- Microsoft製CRTは、この環境にインストールされたVisual Studio Communityの再配布用ファイルに統一しました。配布者から個人利用である旨の回答を得ています。これは規約上の利用資格や、将来の配布物での条件履行を自動的に証明するものではありません。
- VelociDrone公式の[更新情報](https://www.velocidrone.com/news)には、RotorHazardとの大会連携やWebSocketのkeep-alive対応が記載されています。ただし、これはFPVSIM-Clipperの具体的な通信、ローカルDBの直接読み取り、配布への許可を示すものではありません。

## 公開前に残る確認

1. Microsoft Distributable Codeには、下流の配布者と利用者への条件提示・同意など、MITとは別の要件があります。Visual Studio Community 2026の契約書の「DISTRIBUTABLE CODE」には、配布者がMicrosoftを第三者からの請求について補償・防御する義務も記載されています（該当請求が再頒布可能コードのみに基づく場合を除く）。配布者本人が契約内容を読み、利用資格、条件の受諾と履行、利用者向け文面の十分性を判断してください。`MICROSOFT_REDISTRIBUTABLE_NOTICE.md` と `LICENSES/Python-License.txt` のWindows向け追加条件も参照してください。これらの条件は、FPVSIM-Clipper本体やLGPL部品の権利を制限するものではありません。公式契約書: https://visualstudio.microsoft.com/license-terms/vs2026-ga-community/
2. VelociDroneの[EULA](https://www.velocidrone.com/eula)は、ソフト本体の再配布やリバースエンジニアリングに制限を設けています。WebSocket・ログ・読み取り専用のローカルDBに対する、このアプリの具体的な利用と公開について公式の明確な許可は未確認です。ゲーム側のコード・DB・ログ・利用者情報をアプリに同梱しないでください。問い合わせ文は別途、開発者用に用意しています。
3. Qtソースは公式の対応バージョンを同梱候補として準備しましたが、配布するホイールに対する全てのビルド設定・パッチとの厳密な対応、改変版の再ビルド手順の十分性は未確認です。現在の差し替えテストだけをLGPL適合の法的保証としないでください。
4. 公開する最終ZIPに、アプリ用ライセンス、第三者通知、各ライセンス本文、Qtソースへの永続的な入手方法または対応ソースを含めてください。配布物を変えたら棚卸しと照合をやり直してください。
5. GitHubリポジトリは非公開であり、正式リリースは未作成です。公開する前にソースと過去の履歴を確認してください。

この文書は技術的な調査記録であり、法律上の適合や許可を保証するものではありません。必要に応じて、権利者またはライセンスに詳しい専門家に確認してください。
