---
description: 世代の系譜（親子関係）を可視化する
argument-hint: [run_YYYYMMDD_HHMMSS]
---

対象 run: $ARGUMENTS （指定がなければユーザーに確認するか、最新の run を使う）

「系譜可視化」「世代の親子関係」「lineage graph」に応えるため、次の情報を使って可視化する。

1. SQLite の individuals テーブルから個体 ID、generation、parent_ids を取得する。
2. parent_ids を親子関係のエッジとして扱い、必要に応じて migrations テーブルから島間移動もエッジとして追加する。
3. Mermaid 形式または Graphviz DOT 形式で出力する。
4. 代表的な出力先は、対象分析ディレクトリ配下の lineage_graph.mmd または lineage_graph.dot とする。`/run_YYYYMMDD_HHMMSS/` が指定された場合は、その run フォルダ配下の `analysis/` を優先して使う。複数回実行しても混ざらないよう、対象 DB ごとに出力先を明示し、同名ファイルの上書き前に対象を確認する。
5. 見やすさのため、世代ごとにノードを色分けし、初期個体・最良個体・移住個体を目立たせる。

可視化の説明は、どの個体がどの個体から生まれたか、どの世代でどの島に移動したかに焦点を当てる。
