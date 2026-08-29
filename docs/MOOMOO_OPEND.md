# moomoo OpenD のインストールと証券口座の認証

moomoo には **API キーがありません**。認証の実体は **OpenD** という
ゲートウェイ・プログラムで、これを自分の PC で動かし、moomoo 証券の口座で
ログインし、その OpenD に対してローカル接続する — という形をとります。

```
このリポジトリ (Python)  ──TCP 127.0.0.1:11111──▶  OpenD  ──インターネット──▶  moomoo のサーバ
                                                  ↑
                                         ここにあなたがログインする
```

つまり **「認証」＝「OpenD にログインした状態を維持すること」** です。
OpenD を閉じれば、キーを消したのと同じ状態になります。

> このリポジトリ側に認証情報は保存されません。`.env` に入るのは接続先
> (ホスト・ポート) と、実口座を使う場合の取引暗証番号だけです。

---

## 0. 事前に必要なもの

| 必要なもの | 補足 |
|---|---|
| moomoo 証券の口座 | OpenD のダウンロードページ自体がログイン必須です |
| moomoo の ID とログインパスワード | アプリにログインするときと同じもの |
| スマホまたはメール | 初回ログイン時に**デバイス認証コード**が届きます |
| 取引暗証番号 (6桁) | **実口座で発注する場合のみ**。ログインパスワードとは別物です |
| Windows PC | この手順書は Windows 前提。macOS / Ubuntu / CentOS 版もあります |

---

## 1. OpenD をダウンロードする

1. moomoo にログインした状態で、ダウンロードページを開きます。
   - <https://www.moomoo.com/jp/support/topic7_476> （moomoo OpenD / moomoo API のダウンロード案内）
   - 直リンク: <https://www.moomoo.com/ja/download/OpenAPI>
2. **Windows 版**を選びます。GUI 版（ウィンドウが出るもの）を選んでください。
   コマンドライン版もありますが、初回ログインとデバイス認証は画面がある方が
   確実です。

> 本稿執筆時点のバージョンは 10.10 系（Python クライアント `moomoo-api`
> 10.10.7008 と対応）です。数字が違っても手順は変わりません。

## 2. インストールする

ダウンロードしたインストーラを実行します。インストール先は既定のままで
構いません。**このリポジトリのフォルダに入れる必要はありません。**

## 3. OpenD を起動してログインする（ここが「認証」）

1. moomoo OpenD を起動します。ログイン画面が出ます。
2. moomoo の **ID（またはメールアドレス／電話番号）とログインパスワード**を入力します。
3. 初めてその PC でログインするときは、**デバイス認証コード**を求められます。
   moomoo アプリの通知・SMS・メールのいずれかに届くので、それを入力します。
4. ログインが完了すると、OpenD のウィンドウが「接続済み」の状態になります。

**OpenD は開いたままにしておきます。** 閉じると認証も切れます。

## 4. ポート番号を確認する

OpenD の設定画面にポート番号が表示されます。既定は **11111** です。
既定のままなら、このあと何も変更する必要はありません。

## 5. `.env` を設定する

`.env` がまだ無ければ `.env.example` をコピーして作ります。
該当箇所は次のとおりです（既定値のままで日本株・模擬口座の確認ができます）。

```ini
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_SECURITY_FIRM=FUTUJP       # moomoo証券(日本)
MOOMOO_TRD_MARKET=JP              # JP | US
MOOMOO_TRD_ENV=SIMULATE           # SIMULATE(模擬) | REAL(実口座)
MOOMOO_TRADE_PASSWORD=            # 実口座で使うときだけ
```

- `MOOMOO_OPEND_HOST` は **`127.0.0.1` から変えないでください。** OpenD は
  ログイン済みの証券口座セッションそのものです。外から届くアドレスに向けると、
  そのセッションをネットワークに公開することになります。
- `MOOMOO_SECURITY_FIRM` は口座を持っている法人です。日本の moomoo 証券は
  `FUTUJP`。ここが違っても**エラーにはならず、口座一覧が空で返ってきます**
  （後述の「よくあるつまずき」参照）。
- **`MOOMOO_TRD_ENV` は口座に合わせてください。** 日本の moomoo 証券口座には
  模擬（SIMULATE）口座が無いことがあります。その場合 `SIMULATE` のままだと
  「口座が見つからない」で止まります。実口座を指定しても、このリポジトリは
  **発注を一切しません**（参照のみ）。どちらの口座があるかは確認コマンドが
  一覧で教えてくれるので、迷ったらまず実行してかまいません。
- 取引暗証番号を保存する場合は、`.env` を手で編集せず次のコマンドを使います。
  入力は画面に出ず、PowerShell の履歴にも残りません。

  ```powershell
  .\scripts\set-key.ps1 MOOMOO_TRADE_PASSWORD
  ```

  （`APIキー設定.bat` のメニューからも選べます）

## 6. 接続を確認する

**`moomoo接続確認.bat` をダブルクリック**します。メニューで口座を選びます
（模擬口座が無ければ `2` の実口座を選んでください。参照のみで発注はしません）。
次の順に一つずつ確認し、**最初に壊れているところで止まって、その場所を名指し**します。

1. `moomoo-api` が入っているか
2. OpenD のポートに何かが listen しているか
3. OpenD がログイン済みか（相場・取引はそれぞれ別に判定）
4. その口座が OpenD 越しに見えているか
5. その口座が実際に応答するか
6. （実口座 + 指定時のみ）取引暗証番号が通るか

PowerShell から直接実行する場合:

```powershell
.\scripts\moomoo-check.ps1              # 模擬口座
.\scripts\moomoo-check.ps1 -Real        # 実口座（参照のみ）
.\scripts\moomoo-check.ps1 -Real -Unlock  # 取引暗証番号も確認
uv run stock-ai moomoo-check --help     # CLI から直接
```

全部通ると次のように出ます。ここまで来れば**認証は完了**です。

```
| account visible      | OK     | REAL CASH ****1479 (US/JP)   |
| account answers      | OK     | balances returned in JPY     |

OpenD is up and your REAL account answers through it.
Authentication is done - nothing else is needed to read this account.
```

`account answers` まで OK なら、ゲートウェイまでではなく**口座本体まで届いて
いる**ということです。ここが認証の合格ラインです。

結果は `moomoo-output.txt` に保存されます。口座番号は下 4 桁以外マスクされ、
残高は `--show-assets` を付けない限り表示されないので、そのまま貼って
質問できます。

---

## よくあるつまずき

| 症状 | 実際の原因 | 対処 |
|---|---|---|
| `nothing is listening on 127.0.0.1:11111` | OpenD が起動していない／別ポート | OpenD を起動する。ポートが違うなら `MOOMOO_OPEND_PORT` を合わせる |
| `OpenD is running but no account is logged in` | OpenD は起動しているがログインしていない、または認証コード待ち | OpenD のウィンドウを見る。コード入力画面で止まっていることが多い |
| `trading: NO`（相場だけ通る） | 取引側のログインだけ切れている | OpenD を再起動してログインし直す |
| **口座一覧が空で返る**（エラーは出ない） | `MOOMOO_SECURITY_FIRM` か `MOOMOO_TRD_MARKET` が口座と合っていない | 確認コマンドが「見つかった口座」を一覧表示するので、そこに出ている法人・市場に合わせる |
| `no SIMULATE account on this login; it has REAL instead` | 模擬口座が無いだけ。**設定は間違っていません** | `moomoo接続確認.bat` で `2` を選ぶ、または `MOOMOO_TRD_ENV=REAL` にする |
| `no account at all for FUTUJP with JP permission`（一覧も空） | 法人・市場の指定が口座と合っていない／その市場が未承認 | 法人 (`FUTUJP`) と市場 (`JP` / `US`) を見直す。口座の開設状況を moomoo 側で確認する |
| `unlock_trade was refused` | ログインパスワードを取引暗証番号として入れている | 取引暗証番号は**6 桁の別物**。`set-key.ps1 MOOMOO_TRADE_PASSWORD` で入れ直す |
| Python が固まって返ってこない | OpenD が居ない／応答しない | これを避けるために確認コマンドは先にポートを見て、握手にも時間制限を設けています。素の `moomoo` クライアントを直接使うと固まります |

---

## セキュリティ上の注意

- **OpenD はログイン済みの証券口座そのものです。** `MOOMOO_OPEND_HOST` を
  `127.0.0.1` 以外にしない、リモートデスクトップ共有中に開きっぱなしにしない、
  といった扱いが必要です。
- **取引暗証番号はログインパスワードではありません。** `.env` は
  git-ignore されており GitHub には行きませんが、それでも実口座を触らないなら
  空のままにしておくのが一番安全です。
- 確認コマンドは**発注を一切行いません**。取引暗証番号を確認した場合も、
  通ったことを確かめた直後にロックし直します。

## 現時点でできること／できないこと

- 認証（このリポジトリで実装済み）: OpenD の到達確認、ログイン状態、口座の
  可視性、口座の応答、取引暗証番号の検証。
- 発注（**未実装**）: このリポジトリは moomoo 経由で注文を出しません。
  IBKR と同じ扱いで、実弾の発注は明示的に有効化する別ステップです
  （`src/stock_ai/broker/ibkr.py` の方針を参照）。ドライランは
  `PaperBroker` を使ってください。
- moomoo 側の制約: 公開情報によると、日本株は現物取引（単元未満株を含む）が
  対象で、信用取引と PTS（夜間取引）は対象外です。最新の対応範囲は
  moomoo の公式ページで確認してください。

## 参考リンク

- moomoo OpenD / moomoo API のダウンロード: <https://www.moomoo.com/jp/support/topic7_476>
- moomoo OpenAPI ドキュメント: <https://openapi.moomoo.com/moomoo-api-doc/>
- Python クライアント (`moomoo-api`): <https://pypi.org/project/moomoo-api/>
