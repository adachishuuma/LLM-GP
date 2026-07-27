# Copilot instructions for this workspace

このワークスペースでは、LLM-GP / ROS Gazebo の実験結果を扱う際は、まず SQLite データベースと関連するソース・ログを確認してください。

- 実験結果の分析は、依頼内容に応じて [.github/instructions](.github/instructions) 配下の指示ファイルを使い、SQLite / individual_sources / change_history / llm_calls から必要な情報を自力で収集してレポートを作成する。
- ユーザーが `/run_YYYYMMDD_HHMMSS/` 形式の実行フォルダを指定した場合は、その run フォルダ配下の DB とソース・分析出力を優先して使う。
- 既存のレポート生成コードに依存せず、必要なら独自に Markdown / CSV / Mermaid / Graphviz を生成する。
- 個体差分や系譜可視化は、[llm_gp/database.py](llm_gp/database.py) のスキーマを前提に扱う。
- スキルや指示は、依頼文が「世代比較」「差分説明」「系譜可視化」「変異履歴」などに合致したときに自動的に適用される。明示的な呼び出しコマンドは不要。
