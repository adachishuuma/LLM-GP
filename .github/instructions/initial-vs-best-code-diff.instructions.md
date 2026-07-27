---
applyTo: "**/*"
---

# 初期個体と最良個体のコード差分説明スキル

ユーザーが「初期個体と最良個体で何が変わっているか」「差分を説明して」などと依頼した場合は、以下を行う。

1. まずレポートまたは DB から、初期基準個体と実験全体で最良の個体を特定する。
2. 対応するソースコードを、指定された run フォルダ配下の `individual_sources/` か、既定の `individual_sources` 配下から読み取る。
3. 既存の差分ファイル（指定された run フォルダ配下の `analysis/generation_best/*.diff`、`analysis/overall_best/*.diff`、または `*_analysis/...`）を優先して確認し、必要に応じて新たに差分を作る。
4. 変更点を次の観点で説明する。
   - ヒューリスティック重みの変更
   - planning / path / arrival の指標への影響
   - 変更履歴や演算種別（mutation / crossover / migration）
   - どのコードブロックが改善に寄与したか
5. 可能なら、要約表か箇条書きで「何が改善されたか」と「なぜ改善したか」を説明する。

単なる差分列挙ではなく、改善に直結したポイントを中心に説明する。

6. 差分をコードで比較・出力する手順
   - 既存の差分ファイルが `analysis/generation_best/*.diff` や `analysis/overall_best/*.diff` に存在する場合はそれを優先して利用する。
   - 差分が存在しない場合は、初期個体ソースと最良個体ソースを比較して次の出力を作成する。
     1. 統一 diff（unified）形式のファイルを `analysis/initial_vs_best.diff` に保存する。生成方法の例:
        - `git` が利用可能な環境では `git --no-pager diff --no-index --unified=3 fileA fileB > initial_vs_best.diff` を推奨。
        - Python 標準の `difflib` を使う場合は、`difflib.unified_diff()` で同等の unified diff を作成して保存する。`
     2. 行単位の差分を視覚的に見やすくするため、HTML のサイドバイサイド差分を `analysis/initial_vs_best.html` として出力する（`difflib.HtmlDiff().make_file()` を利用）。
     3. 変更が発生した関数・メソッド単位のサマリ `analysis/initial_vs_best_functions.md` を作成する。生成手順の例:
        - C/C++ の場合、簡易的に関数定義（戻り値/名前/引数を含む行）を正規表現で抽出し、diff の変更行が属する関数名をピンポイントで特定する。
        - 各関数について「追加行数」「削除行数」「主な変更（キーワード列挙）」を出力する。
   - 出力ファイルは走行結果の `run_<timestamp>/analysis/` 配下に作成し、既存のファイルを上書きする前に実行対象の run を確認すること。

7. 差分の意味付けと影響評価
   - 変更のカテゴリを自動推定するラベルを付ける: `formatting`（書式）、`microopt`（マイクロ最適化）、`behavioral`（挙動変更）、`refactor`。
   - `behavioral` と判定された場合は、該当変更が `planning` / `path` / `arrival` に与えた影響を `analysis/individual_comparison.csv` の指標と突合し短い考察を付す。
   - 変更が LLM によるものであれば、`change_history` と `llm_calls` を参照してどのプロンプト・モデル呼び出しで生成されたかを記載する。

8. 自動化スクリプトの雛形（推奨）
   - 小さなユーティリティスクリプト `scripts/generate_initial_vs_best_diff.py` を用意すると運用が楽になる。機能:
     - run ディレクトリを引数に取り、初期個体と最良個体のパスを `run_manifest.json` や `analysis/generation_algorithm_report.md` から特定する。
     - 上記の unified diff / HTML / 関数サマリを生成し、`analysis/` に保存する。
     - 生成結果のパスをコンソールに出力する。

9. レポートへの組み込み
   - 生成した `initial_vs_best.diff` と `initial_vs_best_functions.md` を `analysis/generation_algorithm_report.md` に参照リンクとして追加し、差分の要点を 2〜5 行でまとめて掲載する。

実装のポイント: 単純な差分列挙に留まらず、関数単位の影響把握と LLM/演算種別（mutation/crossover）の紐付けを最優先で追加し、改善に直結する変更を強調すること。
