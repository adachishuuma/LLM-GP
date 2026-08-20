# A* / Relaxed A*(RAstar) 関連コードまとめ

最終更新: 2026-07-28

## 1. 位置づけ

このワークスペースには2系統のA*実装が存在する。

| 系統 | 役割 |
|---|---|
| `global_planner`パッケージ内の`AStarExpansion`（`astar.cpp`） | 標準A*。**LLM-GPの進化対象外**、固定 |
| `global_planner`パッケージ内の`RAStarExpansion`（`RAstar.cpp`／論理パス`rastar.cpp`） | Relaxed A*。**LLM-GP進化対象そのもの** |
| `src/relaxed_astar/`パッケージ（`RAstar_ros.cpp`） | move_baseグローバルプランナーpluginの別実装（USV_SIM_LSA由来のテンプレート）。現行の`astar_dwa.launch`では`global_planner/GlobalPlanner`（上記`global_planner`パッケージ内蔵のRA*）を使っており、こちらは**現行実験では未使用**とみられる |

## 2. `global_planner`パッケージ内のA*関連ファイル

| ファイル | 内容 |
|---|---|
| `src/global_planner/include/global_planner/astar.h` / `src/global_planner/src/astar.cpp` | 標準`AStarExpansion`。`Index`/`greater1`によるヒープ探索。進化対象外・固定 |
| `src/global_planner/include/global_planner/rastar.h` | `RAStarExpansion`クラス宣言。`RIndex`/`Rgreater1`、`calculatePotentials()`、`add()` |
| `src/global_planner/src/RAstar.cpp` | `RAStarExpansion`実装本体。A*との違いは`tBreak`（tie-breaking係数）を使ったマンハッタン距離ヒューリスティック加算。**LLM-GPが変異・交叉するのはこのファイル** |
| `src/global_planner/CMakeLists.txt` (53行目) | `add_library`に`src/rastar.cpp`として登録 |

### Windowsのファイル名の実態（注意点）

リポジトリ上の実ファイル名は`RAstar.cpp`（大文字始まり）だが、CMakeLists.txtや`config/*.yaml`の`candidate_target`は`src/rastar.cpp`（小文字）を指している。NTFSは大文字小文字を区別しないため両者は同一ファイルとして解決されるが、Linux側（WSL）でcatkinビルドする際は大文字小文字が一致している必要がある点に留意する。

### 孤立ファイル（進化・ビルド対象外）

同じ`src/global_planner/src/`直下に、進化とは無関係と思われる古いバックアップ／断片ファイルが残っている。

- `rastar.cpp~`（エディタのバックアップ）
- `relaxed astar`（拡張子なし、スペース入りファイル名）
- `standard astar`（同上）

これらはビルド対象・LLM-GP対象いずれにも含まれていない。

## 3. LLM-GP側でのRAstarの扱い

| ファイル | 関わり方 |
|---|---|
| `config/*.yaml`の`candidate_target` | `src/global_planner/src/rastar.cpp`を進化対象ソースとして指定 |
| `llm_gp/evolution.py`（105行目付近） | `candidate_target`から初期個体の基準コードを読み込み |
| `llm_gp/operators.py` | 交叉・LLM変異のプロンプト内で`RAStarExpansion`クラスの保持を明示的に要求（`global_planner::RAStarExpansion`を壊さないよう指示） |
| `llm_gp/validation.py` | 生成コードに`#include <global_planner/rastar.h>`、`RAStarExpansion::calculatePotentials`、`RAStarExpansion::add`が残っているかを事前検査 |
| `llm_gp/run_lock.py`（35行目付近） | ロックの目的コメントに「rastar.cppの変更・評価の二重実行防止」と明記 |
| `llm_gp/ros_smoke.py` / `llm_gp/evaluator.py`（174行目付近） | `candidate_target`をWSL側へ一時コピーしてビルド・評価 |
| `scripts/run_ros_gazebo_evaluation.sh` | WSL側`.../global_planner/src/rastar.cpp`を候補コードへ一時置換→ビルド→評価→復元 |

DWA（`dwa_local_planner/DWAPlannerROS`）と標準A*（`astar.cpp`）は変異対象から明確に除外されている。

## 4. 実行時（ナビゲーション）でのA*の位置

`docs/astar_dwa_run_guide.md`より抜粋。

```text
RViz でゴール送信
  → move_base
  → global_planner (A*/RA*で大域経路)
  → DWAPlannerROS (局所速度)
  → /cmd_vel
  → Gazebo上のTurtleBot3
```

- `param2/global_planner_params.yaml`の`use_dijkstra: false`により、大域探索はA*系（Dijkstraでない）が使われる設定になっている。
- 起動ファイル: `src/dwa_local_planner/launch/astar_dwa.launch`
- 大域経路が壁に近すぎる等の問題は「A*自体ではなくDWA/costmap側の問題」と切り分ける指針が明記されている。

## 5. `src/relaxed_astar/`パッケージ（別リポジトリ）

`src/relaxed_astar/README.md`によれば、USV_SIM_LSA向けに調整されたmove_baseグローバルプランナーpluginのテンプレート実装（独立git管理下、`.git`あり）。`config/relaxed_astar_planner_plugin.xml`でpluginlib登録されているが、現行のLLM-GP実験・`astar_dwa.launch`では`global_planner`パッケージ内蔵のRA*を使っているため、このパッケージ自体は現在の実験フローには組み込まれていない。

## 6. 関連ドキュメント

| ファイル | 内容 |
|---|---|
| `docs/astar_dwa_run_guide.md` | astar_dwa.launch実行手順・A*/DWA動作フロー |
| `docs/current_experiment_files_and_outputs.md` | LLM-GP実験全体の使用ファイル・出力先まとめ |
| `docs/llm_gp_operation_flow.md` | ルーレット選択・4島モデルの動作フロー |
