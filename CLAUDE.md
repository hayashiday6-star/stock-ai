# stock-ai

JP/US 株式のスクリーニング・バックテスト・監視システム。Python 3.13 / `uv` / typer / SQLAlchemy 2.0 / pydantic-settings。

利用者は Windows で `.bat` をダブルクリックして使う。コンソール上で `uv run` を直接叩くことは基本的にない。

## 開発ブランチ

作業は `claude/recent-activity-z1t0is` で行う。**このセッションだけで2回、別ブランチ（`main` や無関係な作業ブランチ）に乗ったまま作業しかけたことがある。** 何かコミットする前に `git branch --show-current` で確認すること。

まとまった区切りが付いたら `main` への取り込みは直接マージではなく Pull Request で行う（ユーザーの明示的な希望）。

## セットアップ・検証

```
uv sync --extra data --extra db --extra ai  # 必要な extra は都度足す。--extra は追加ではなく置き換え
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

コミット前に上記3つを必ず通す。CIも同じ3つを見る。

`per-file-ignores`（`pyproject.toml`）で日本語docstring由来の `D415` を個別ファイル無視している。新しいファイルで同じ理由の警告が出たら、そこに追記する。

## Windows の `.bat` / `.ps1`

- **`.bat` は純ASCII。** cmd.exe はコンソールのコードページ（日本語Windowsではcp932）で読むため、UTF-8の日本語はmojibakeになり、mojibakeのバイト列がecho途中で行を終わらせてその後をコマンドとして実行してしまうことがある。日本語は `.ps1` 側に書く。
- **日本語を含む `.ps1` は UTF-8 with BOM** で保存する。
- `.gitattributes` で `*.bat`/`*.cmd` は `eol=crlf`、それ以外は `eol=lf`。
- 新しい `.bat` を追加しても、pull するまでユーザーの手元には存在しない。**`.bat` は自分の存在を知らせられない** ので、`0-最新にする.bat`（`scripts/0-update.ps1`）が増えたファイルを一覧で出す。ユーザーには常にこれを先に実行してもらう。

## 秘密情報

- `.env` は git-ignore 済み。API キー・トークンは `APIキー設定.bat`（`scripts/set-key.ps1`）経由で設定する。このスクリプトは入力を隠し、`ValidateSet` に載っているキー（`EDINET_API_KEY` / `JQUANTS_API_KEY` / `ANTHROPIC_API_KEY` など）専用。
- **`JP_PRICE_SOURCE` や `JP_STATEMENT_SOURCE` のような設定値（秘密ではない）はこのツールでは扱えない。** `.env` を直接 `notepad .env` などで編集する。過去に両者を混同して、設定値を書くつもりが `EDINET_API_KEY` を上書きしてしまったことがある。
- ログや例外メッセージに秘密情報の生値を出さない。長さとSHA-256指紋（先頭のみ）で「値が変わったか」を確認できるようにする（`uv run stock-ai info` の表示方式）。
- `tachibana_private.pem` / `tachibana_session.json` は資格情報扱い。gitignore済み、POSIXでは0600、例外メッセージに内容を含めない。

## 日本株データソース

J-Quants の有料プランを解約する方向で移行中（`docs/TACHIBANA.md`、`docs/EDINET.md` に詳細）。

- 価格: `JP_PRICE_SOURCE`（`jquants` | `tachibana`）
- 財務諸表: `JP_STATEMENT_SOURCE`（`jquants` | `edinet`）

どちらも `uv run stock-ai info` の出力に必ず表示すること — 切り替えたつもりで切り替わっていないことに、数字が変わらないという形でしか気付けなくなるため。

新しいデータ取得経路を追加するときは、`bulk-fetch` のような一括コマンドが実際にその設定を見ているか確認する。`BulkIngester` はプロバイダを差し替え可能な作りだが、CLI側が新しい設定を配線し忘れて、切り替えたはずが常に旧経路を叩き続けていた実例が複数ある。

## 品質の姿勢

このプロジェクトで繰り返し起きる不具合は「例外で落ちる間違い」ではなく **「もっともらしいが違う値が黙って出る」** 種類のもの（例: 分割前後で尺度の違う値を組み合わせる、連結と単体を取り違える、古いコードのまま実行して気付かない）。

- 新しいデータ取得ロジックは、可能な限り実データ（実際のファイル・実際のAPIレスポンス）で検証する。fixtureは実物から作る。
- 数字を1つ検証したら終わりにせず、別の切り口（別の計算式、別の銘柄、別の期間）で同じ数字を出して一致するか確かめる。
- コード側のバージョンを確認する手段（`uv run stock-ai info` の `version` 行、各 `.ps1` の `Show-Version`）を必ず用意し、ユーザーが古いコードを実行していないか本人にもこちらにも分かるようにする。

## コミット・PR

- コミットメッセージ・PR本文・コード中のコメントに、使用したモデル名を含めない。
- モデル名を聞かれたら `get_session`（claude-code-remote MCP）で `session_context.model` と `external_metadata.last_served_model` を確認して答える。
