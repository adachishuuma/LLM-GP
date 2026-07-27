---
applyTo: "**/*"
---

# 変異履歴データベーススキル

ユーザーが「変異履歴」「change history」「LLM 呼び出し履歴」「mutation history」などを聞いた場合は、次のテーブル構造を前提に分析する。

- individuals: 個体のメタデータ、世代、島、親 ID、ソースパス
- change_history: 変更履歴の説明と操作種別
- llm_calls: 変異や生成に使われた LLM 呼び出しの prompt / response / 成功可否
- evaluations: 評価結果（fitness / planning / path / arrival）

分析の進め方:

1. 個体 ID を基準に、change_history と llm_calls を結びつけて読む。
2. どの個体がどの操作で生成されたか、どのモデルで変異したかを整理する。
3. 変異の成功・失敗や評価値との関係を要約する。
4. 必要に応じて、SQL クエリの結果を Markdown 表で示す。ユーザーが `/run_YYYYMMDD_HHMMSS/` を指定した場合は、その run フォルダ配下の DB と `analysis/` を優先して使う。複数回実行しても結果が混ざらないよう、対象の DB と個体 ID 範囲を明示してから分析し、既存の要約ファイルを上書きする前に対象を確認する。

このワークスペースでは、変異履歴は change_history と llm_calls の組み合わせで追跡するのが基本である。
