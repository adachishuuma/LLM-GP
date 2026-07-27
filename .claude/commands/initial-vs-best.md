---
description: 初期個体と最良個体のコード差分を説明する
argument-hint: [run_YYYYMMDD_HHMMSS]
---

対象 run: $ARGUMENTS （指定がなければユーザーに確認するか、最新の run を使う）

「初期個体と最良個体で何が変わっているか」「差分を説明して」に答えるため、以下を行う。

1. まずレポートまたは DB から、初期基準個体と実験全体で最良の個体を特定する。
2. 対応するソースコードを、対象 run フォルダ配下の `individual_sources/` か、既定の `individual_sources` 配下から読み取る。
3. 既存の差分ファイル（対象 run フォルダ配下の `analysis/generation_best/*.diff`、`analysis/overall_best/*.diff`、または `*_analysis/...`）を優先して確認し、必要に応じて新たに差分を作る。
4. 変更点を次の観点で説明する。
   - ヒューリスティック重みの変更
   - planning / path / arrival の指標への影響
   - 変更履歴や演算種別（mutation / crossover / migration）
   - どのコードブロックが改善に寄与したか
5. 可能なら、要約表か箇条書きで「何が改善されたか」と「なぜ改善したか」を説明する。

単なる差分列挙ではなく、改善に直結したポイントを中心に説明する。

## 出力: 比較結果を Markdown にまとめる

チャット上で説明するだけでなく、比較結果を **`docs/run_<timestamp>_diff.md`** として必ず保存する（`<timestamp>` は対象 run の `run_YYYYMMDD_HHMMSS` 部分）。既に同名ファイルが存在する場合は、対象 run が同じであることを確認したうえで内容を最新の比較結果に更新する。構成は次の見出しに従う。

```markdown
# run_<timestamp> — 初期個体と最良個体のコード差分要約

**対象**
- 初期基準個体: `<individual_id>` — [individual source](<relative path>#L1-L200)
- 全体最良個体: `<individual_id>`（ソースは `<source file>` を使用） — [individual source](<relative path>#L1-L200)

**参照ファイル**
- 実行レポート: [analysis/generation_algorithm_report.md](<relative path>)
- 初期→最良の差分ファイル: [analysis/initial_vs_best.diff もしくは best_algorithm/diff_from_initial.diff](<relative path>)

**コード差分の要点（簡潔）**

変更点は文章で説明するのではなく、実際の diff（該当ハンク）をコードブロックで示す。各ハンクの直前・直後に、そのハンクの分類（formatting / microopt / behavioral / refactor）を一言添える。

- <formatting|microopt|behavioral|refactor>: `<ファイル名・関数名>`
  ```diff
  - queue_.push_back(RIndex(start_i, 0));
  + queue_.emplace_back(RIndex(start_i, 0));
  ```
- 変更が複数ハンクにわたる場合は同様に diff コードブロックを繰り返す。差分全体を貼り付けるのではなく、意味のあるまとまり（1関数・1変更点）ごとに抜粋する。

**影響の評価**
- planning / path / arrival 指標への影響
- 挙動変更の有無、改善に寄与したコードブロック
- 指標の変動が実際の改善か、走行間ノイズ（`repetition_statistics.csv` の標準偏差）の範囲内かを必ず考察する

**補足**
- 個体 ID とソースファイルの対応にズレ（重複排除によるソース共有など）がある場合はここに明記する
```

このファイルはチャットでの説明の要約版ではなく、チャットで述べた内容を過不足なく Markdown 化したものにすること。

## 差分をコードで比較・出力する手順

- 既存の差分ファイルが `analysis/generation_best/*.diff` や `analysis/overall_best/*.diff` に存在する場合はそれを優先して利用する。
- 差分が存在しない場合は、初期個体ソースと最良個体ソースを比較して次の出力を作成する。
  1. 統一 diff（unified）形式のファイルを `analysis/initial_vs_best.diff` に保存する。生成方法の例:
     - `git` が利用可能な環境では `git --no-pager diff --no-index --unified=3 fileA fileB > initial_vs_best.diff` を推奨。
     - Python 標準の `difflib` を使う場合は、`difflib.unified_diff()` で同等の unified diff を作成して保存する。
  2. 行単位の差分を視覚的に見やすくするため、HTML のサイドバイサイド差分を `analysis/initial_vs_best.html` として出力する（`difflib.HtmlDiff().make_file()` を利用）。
  3. 変更が発生した関数・メソッド単位のサマリ `analysis/initial_vs_best_functions.md` を作成する。生成手順の例:
     - C/C++ の場合、簡易的に関数定義（戻り値/名前/引数を含む行）を正規表現で抽出し、diff の変更行が属する関数名をピンポイントで特定する。
     - 各関数について「追加行数」「削除行数」「主な変更（キーワード列挙）」を出力する。
- 出力ファイルは走行結果の `run_<timestamp>/analysis/` 配下に作成し、既存のファイルを上書きする前に実行対象の run を確認すること。

## 差分の意味付けと影響評価

- 変更のカテゴリを自動推定するラベルを付ける: `formatting`（書式）、`microopt`（マイクロ最適化）、`behavioral`（挙動変更）、`refactor`。
- `behavioral` と判定された場合は、該当変更が `planning` / `path` / `arrival` に与えた影響を `analysis/individual_comparison.csv` の指標と突合し短い考察を付す。
- 変更が LLM によるものであれば、`change_history` と `llm_calls` を参照してどのプロンプト・モデル呼び出しで生成されたかを記載する。

## 自動化スクリプトの雛形（推奨）

小さなユーティリティスクリプト `scripts/generate_initial_vs_best_diff.py` を用意すると運用が楽になる。機能:
- run ディレクトリを引数に取り、初期個体と最良個体のパスを `run_manifest.json` や `analysis/generation_algorithm_report.md` から特定する。
- 上記の unified diff / HTML / 関数サマリを生成し、`analysis/` に保存する。
- 生成結果のパスをコンソールに出力する。

## レポートへの組み込み

生成した `initial_vs_best.diff` と `initial_vs_best_functions.md` を `analysis/generation_algorithm_report.md` に参照リンクとして追加し、差分の要点を 2〜5 行でまとめて掲載する。

実装のポイント: 単純な差分列挙に留まらず、関数単位の影響把握と LLM/演算種別（mutation/crossover）の紐付けを最優先で追加し、改善に直結する変更を強調すること。
