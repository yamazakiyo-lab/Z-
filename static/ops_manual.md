## 1. 目的

共有Zフォルダ上の写真・動画・資料を、工番を軸に自動で整理・命名・圧縮するプログラムの運用手順をまとめる。対象は現行コードと実運用に一致する内容とする。

写真の取り込み経路は次の2本(2026-07-24にGoogle Drive経路を廃止し一本化)。

1. LINE WORKS bot(TSEGFMBot): 現場がスマホで写真を送り、工番・コメントを対話で付ける。日常の投稿はこちら。

2. 手動投入口 `_manual_input`: `Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_manual_input` に写真フォルダを置くと、夜間ランが工番マスタ名に整えて91へ投入する。SDカード・過去写真などの一括取り込み用。

## 2. デイリータスク

Windows タスクスケジューラで、毎日 00:00 に2本のタスクを実行する。処理後にログ要約が動く。

| タスク | 実行経路 |
| --- | --- |
| GDX_DailyRun | run_gdx_wrapper.bat → run_gdx_logged.ps1 → run_gdx.py → 91GDX・252WORKNO-program/cli.py |
| OTHER_DailyRun | run_91other_wrapper.bat → run_91other_logged.ps1 → run_91other.py → 91OTHER-program/cleanup.py |

【2026-07-18 更新】 両タスクの wrapper 冒頭で最新コードを自動取得するようにした(git stash → git pull origin master → git stash pop)。従来 GDX のみだったが OTHER にも追加。実行ホストは常に master ブランチで運用する。

## 3. 処理対象範囲

- Z全体の走査ではない。実質対象は 2to9_業務別フォルダ 配下。

- GDX側の対象: 91_工番別実績写真・動画、252_整備資料、92_PO LIST、9781_工事工番。

- OTHER側: base_dir は Z:\takachiho だが allowed_top_dirs が ["2to9_業務別フォルダ"] のため 2to9 配下に限定。

注意: allowed_top_dirs を空にすると base_dir 全体走査になり得るため、空にしないこと。

## 4. 取り込みとマスタ補正

### 4-1. 写真の取り込み

- LINE WORKS bot経由: 投稿された写真は Blob(lw-raw) に保存され、lw_blob_sync が `_LWExtraction` に同期 → ld_sort が工番マスタ名で 91/<工番>/B1〜B4 へ振り分ける。コメントは `_annotations/<工番>/` にJSONで蓄積され、検索インデックスに反映される。

- 手動投入口(`_manual_input`)経由: 置かれた写真フォルダを夜間ラン([2]工程)が工番マスタ名に整えて 91 の B4 へ投入する。フォルダ名の先頭に工番を付けておくこと(例: `4031-00 ○○作業`)。

- デイリーランは `GDX_NO_DRIVE=1`(run_gdx_logged.ps1 で設定)で動き、Google Drive への接続は行わない。

### 4-2. 工番マスタによる補正

- マスタCSVの置き場は `<91ルート>\_masters`(工事一覧表.csv / 発注者一覧表.csv)。T-NEXUSから手動出力してここに置く。鮮度は週次チェック(毎週土曜)でLW通知される。

- そろえる対象: 91のAフォルダ名、252_整備資料のAフォルダ名、9781_工事工番のAフォルダ名、92_PO_LISTのファイル名、271_修理工事指令書のファイル名。

## 5. 91_工番別実績写真・動画 の整理

### 5-1. 投入

- フォルダ名の先頭から工番(123-45 / 123_45、先頭の # は無視)を読み取り、123-45 に正規化。

- 対応するAフォルダへ、写真・動画を「工番_B4整理前写真・動画」へ入れる(同名はスキップ)。

### 5-2. B1〜B4 分類

Aフォルダ配下に B1〜B4 を用意し、サブフォルダ名を見て振り分ける(判定前に NFKC 正規化)。A直下の写真・動画は B4 へ。

| フェーズ | 主な振り分けキーワード |
| --- | --- |
| B1 着手前 | 引取 / 入庫 / 入荷 / 受入 / 入庫時 |
| B2 着手中 | 整備 / 加工 / 切削 / 完成 / 整備中 |
| B3 出荷以降 | 据付 / 納入 / 出荷 / 引渡 / 搬入 / 出荷時 |
| B4 整理前 | 整理前 / 整理中 / 整理 |

### 5-3. メディアのリネーム

- 撮影日時順に並べ、連番+日付を含む名前へリネーム。A直下のBフォルダ内は工番を先頭に、B配下サブフォルダ内はサブフォルダ名を先頭に付ける。

## 6. 画像・動画処理

- 対象: JPG / JPEG / PNG / HEIC / HEIF。PNG/HEIC/HEIF は JPG へ変換されることがある。

- 圧縮上限: GDX側 max_kb=1000、OTHER側 max_kb=2048。超過時は画質を 95→30 まで段階的に下げて収める。

- HEIC/HEIF は読み込み環境が無い場合、変換をスキップすることがある。

## 7. B4運用・掃除

- Thumbs.db / desktop.ini / .DS_Store は不要ファイルとして除去。

- B4配下に 引取・整備・出荷 等の下位フォルダがあれば再判定し、中身を B1/B2/B3 へ移す。

- 空になった下位フォルダ、および空の B1〜B4 は削除(再試行付き)。

## 8. 命名ルール(最低条件)

- 工番はフォルダ名の先頭に、半角数字+半角ハイフン/アンダーバーで書く(全角は認識失敗のおそれ)。

- Windows 禁止文字 \ / : * ? " < > | は使わない(保存時 _ へ置換)。

- 区切りは半角ハイフン/アンダーバー、スペースは半角。全角スペースは避ける。

半角全角の吸収は B 判定(NFKC正規化)でのみ行い、保存名を一律半角化する処理は入っていない。

## 9. 運用ポリシー

- スクリプト更新は git 管理。push はノートPCに統一、実行ホスト(デスクトップ/KEIRI-PC)は pull で追従する。

- 【2026-07-18】 .gitattributes を追加し改行コードを正規化(リポジトリLF / Windows作業ツリーCRLF、.bat類はCRLF固定)。どのPCでもチェックアウト結果が揃う。

- 検索アプリ(TSEG WORKS)と受信Botは Azure へ GitHub Actions で自動デプロイ(concurrency で二重デプロイの409を防止)。

- ネットワークドライブ(K:/Y:等)へのファイル手動同期は不要。ログ等は自動で共有フォルダに出力される。

## 10. 日次確認

- Task Scheduler の最終結果を確認。

- ログを確認: Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\logs\ に日次ログが出る。

主なログ: dailyrun_YYYYMMDD_*_KEIRI-PC.txt(全体)、photo_video_91_YYYYMMDD_*.log(91整理)、photo_video_general_*(91以外)。

- 確認観点: 「Aフォルダ 469/469 完了」のように末尾まで到達しているか、ERROR/Traceback が無いか。

photo_video_general の当日ログが空になることがあるが、これは正常(該当処理が別経路のため)。

## 11. 障害対処

- 優先確認: 権限、.runtime のロック(gdx.lock / other.lock)、ネットワークドライブ(Z:/Y:)の接続状態。

- LW取り込みが止まった場合: Azure(tseg-lw-receiver)の稼働と Blob 接続、lw_blob_sync のログを確認。

- 仮想環境(venv)と依存パッケージの状態を確認。

- 実行ホストが master ブランチにいるか(feature ブランチ残りに注意)、git pull が通るかを確認。

## 12. 通知・監視の自動化（2026-07-18 追加）

検索アプリ(TSEG WORKS)・LINE WORKS BOT まわりに、以下の自動通知・監視を追加した。スケジュールタスクの正式定義と注意は SCHEDULED_TASKS.md に集約している。

- LWあいさつの氏名付与: 朝(LW_Morning_Greeting)・夕(LW_Evening_Reminder)の冒頭に「苗字名前さん、」を付ける。

- 検索アプリ未利用通知(TSEG_検索アプリ未利用通知): 毎週月曜8:00。先週アプリに未ログインのLWユーザーへLWで通知。氏名→Entra UPN の対応表 name_upn_map.json で突合し、事業所・予定アカウントは除外。

- 週次利用レポート(TSEG_週次利用レポート): 毎週月曜9:00。検索アプリ＋Teamsの利用状況を集計してCSV出力し、氏名入り要約を6名(山嵜喜隆・小山智樹・昆哲郎・松尾崇・松﨑誠一・山嵜絵里)へLW配信。Teams利用は Graph 利用状況レポート(Reports.Read.All)を使用。

- タスク点検通知(TSEG_タスク点検通知): 毎日12:00。重要タスクの前回結果を点検し、失敗・無効化があれば山嵜喜隆へLW通知(check_tasks_notify.py)。旧 CHECK_DAILYRUNS を刷新。

- 検索アプリ: ログインを Entra ID に一本化(Google認証を廃止)。部品在庫の見積単価を J列 に変更。

- デイリーラン安定化: run_lw_logged.ps1 / run_gdx_logged.ps1 のPython起動から -3 を除去(py→python.exe直結環境での「Unknown option: -3」を回避)。

- スケジュールタスク運用ルール: PowerShellで %CD% を使わない(展開されず壊れる)、「全タスク一括再作成」を実行しない。正式定義・復旧手順は SCHEDULED_TASKS.md 参照。

- フォルダ整理: Y の .env と旧バックアップを削除し README に役割を明記。K 直下の旧 Z_repo/reports/logs を削除。リポジトリの旧レポート・デバッグ生成物を削除し .gitignore を強化。

- ログ一括掃除(TSEG_ログ掃除): 毎日12:30、ノートとデスクトップの両方に設定。cleanup_logs_all.ps1 が実行マシンのローカルリポジトリ＋Y共有のログを保持14日で削除する(デスクトップ実行でK＋Y、ノート実行でノート＋Yをカバー)。従来の check_and_cleanup_logs.ps1(旧CHECK_DAILYRUNS由来)を統合・置換。

## 13. AI Q&A（2026-07-24 追加）

検索アプリのメニュー「AI Q&A」は、Azure OpenAI GPT-4o に質問できるチャット。回答前に Azure AI Search(photo-index)で社内データを検索し、関連する工番実績を回答に反映する。

- 必要な環境変数(Azure App Service「TSEG-FM-SEARCH」に設定済み): AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / AZURE_OPENAI_GPT4O_DEPLOYMENT / QA_LOG_ADMINS。

- 全やり取りは Blob(lw-raw/qa_log_YYYYMM.jsonl)に記録される(日時・氏名・質問・回答・参照工番)。

- 管理者(QA_LOG_ADMINS に列挙。既定は山嵜喜隆)のみ「AI Q&A ログ」メニューが表示され、月・利用者・キーワードで絞って閲覧できる。

- ログイン利用者の特定は Easy Auth ヘッダー(X-MS-CLIENT-PRINCIPAL-NAME)による。値はURLエンコードされた氏名のためデコードして使用する。

## 14. 改訂履歴

| 日付 | 内容 |
| --- | --- |
| 2026-07-18 | OTHERデイリーランに自動git pull追加 / .gitattributes追加(改行正規化) / git中心運用(push=ノート・pull=デスクトップ)を明記 / 日次ログの確認先(Y:のlogs)を追記 / Azure自動デプロイ・concurrencyを追記。 |
| 2026-06-07 | 運用実態に合わせて更新(GDX/OTHER 2タスク、対象範囲、命名ルール等)。 |
| 2026-07-18 | 通知・監視の自動化(未利用通知/週次利用レポート/毎日タスク点検)、LWあいさつ氏名付与、Entra一本化・部品在庫=J列、-3バグ修正、SCHEDULED_TASKS.md追加、K/Y/リポジトリ整理 |
| 2026-07-19 | 顧客検索に改称し正式名称・住所を表示／動治工具・測定具・消耗品検索を新設／部品在庫の仕入先除外・CSV出力無効化／在庫・工具のエクスポートをデイリーランへ組込／マスタCSV鮮度チェック(週次)を追加 |
| 2026-07-24 | AI Q&Aメニュー新設(GPT-4o+RAG、全やり取りをBlob記録、管理者用ログ閲覧ページ)／GDX卒業: Drive取り込み[1]・同期[4]を停止(GDX_NO_DRIVE=1、写真取り込みはLW botに一本化)／マスタCSV置き場を _masters に変更／_GDExtraction を _manual_input(手動投入口)に改名／LW bot: 放置工番待ちの6h自動リセット+学習協力返信の自動救済／両マニュアル改訂 |
