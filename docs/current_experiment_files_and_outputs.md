# 現在のLLM-GP・ROS/Gazebo実験で使用するファイルと保存先

最終更新: 2026-07-24

この文書は、`C:\Users\adachi\catkin_ws` にある現在の実装を対象に、実験で直接使用するファイル、ROS側で参照する主要ファイル、生成物の保存先をまとめたものである。

## 1. 推奨する実験

10世代、4島、各個体3回評価を行う現在の推奨コマンドは次のとおり。

```powershell
cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_10gen_repeated.yaml
```

使用する主設定は [`config/ros_gazebo_10gen_repeated.yaml`](../config/ros_gazebo_10gen_repeated.yaml) である。

主な条件:

- 世代数: 10
- 島数: 4
- 島ごとの生存個体数: 2
- 島ごとの親ペア数: 1
- 1親ペアから生成する子: 3種類
- 1世代の新規個体: 12
- 各個体の走行反復数: 3
- 固定ゴール: `[1.69, 0.954, -0.00143]`
- 開始姿勢: `[-2.0, -0.5, 0.0]`
- LLM: OpenAI `gpt-4o-mini`
- LLM失敗時のモック切り替え: 無効
- 島間コピー: 第10世代、リング状に各島1個体
- DWA: 固定
- 進化対象: Relaxed A* の `rastar.cpp`

この小規模構成では、初期個体8、子個体120、移住コピー4がSQLiteへ記録される。移住コピーは再評価しないため、最大のGazebo走行数は `(8 + 120) × 3 = 384` 回である。

## 2. 実行入口とPython実装

### 実行入口

| ファイル | 役割 |
|---|---|
| `llm_gp/main.py` | YAML読込、排他ロック、進化実行、レポート生成を順番に実行するメイン入口 |
| `llm_gp/ros_smoke.py` | 初期Relaxed A*を1個体・1回だけROS/Gazebo評価する動作確認入口 |
| `llm_gp/__init__.py` | Pythonパッケージ定義 |

### 進化アルゴリズム

| ファイル | 役割 |
|---|---|
| `llm_gp/evolution.py` | 4島の初期化、親選択、子生成、評価、生存選択、移住、世代ループ |
| `llm_gp/island.py` | 各島の個体群と乱数生成器を保持 |
| `llm_gp/selection.py` | 適応度比例ルーレット親選択、エリート保存、復元なしルーレット生存選択 |
| `llm_gp/operators.py` | 初期コード生成、C++交叉、OpenAI LLM変異、モック演算、個体ID生成 |
| `llm_gp/fitness.py` | 経路生成時間、経路長、到達時間から重み付き適応度を計算 |
| `llm_gp/validation.py` | C++構文形式、禁止文字列、DWA変更禁止などの事前検査 |
| `llm_gp/models.py` | 個体、評価結果、ゴール、LLM呼び出し、世代サマリーのデータ型 |

子生成は各島・各親ペアについて次の3種類である。

1. `crossover_only`: 交叉のみ
2. `mutation_only`: 親1個体をLLM変異
3. `crossover_and_mutation`: 交叉後にLLM変異

### 設定・評価・保存・レポート

| ファイル | 役割 |
|---|---|
| `llm_gp/config.py` | YAMLをデータクラスへ読み込み、島数・個体数・反復数などを検証 |
| `llm_gp/evaluator.py` | モック評価器とROS/Gazebo評価器。指定回数をすべて走行し、平均値を返す |
| `llm_gp/database.py` | SQLiteスキーマ作成と全実験データの保存 |
| `llm_gp/report.py` | 世代比較、3指標比較、反復統計、コード差分、最良コードを書き出す |
| `llm_gp/run_lock.py` | 同じプロジェクトで2つのROS/Gazebo実験が同時実行されることを防止 |

## 3. 設定ファイル

| 設定 | 評価器 | 世代 | 島個体数 | 親ペア/島 | 反復 | 用途 |
|---|---|---:|---:|---:|---:|---|
| `config/ros_gazebo_10gen_repeated.yaml` | ROS/Gazebo | 10 | 2 | 1 | 3 | 現在推奨する複数評価実験 |
| `config/ros_gazebo_10gen_small.yaml` | ROS/Gazebo | 10 | 2 | 1 | 1 | 以前の10世代予備実験 |
| `config/ros_gazebo_mini.yaml` | ROS/Gazebo | 1 | 1 | 1 | 1 | 最小の進化ループ確認 |
| `config/ros_gazebo.yaml` | ROS/Gazebo | 5 | 10 | 5 | 3 | 大きい個体群の5世代設定 |
| `config/default.yaml` | モック | 50 | 10 | 5 | 3 | ROSを使わないアルゴリズム確認 |

世代数は各YAMLの次の値で変更する。

```yaml
evolution:
  max_generations: 10
```

反復数は次の値で変更する。

```yaml
evaluation:
  repetitions_per_goal: 3
```

ゴールは次の値で固定している。

```yaml
evaluation:
  fixed_goal: [1.69, 0.954, -0.00143]
```

出力先を分けたい場合は、同じYAMLの `database.path`、`output.source_directory`、`output.generation_csv` を同時に変更する。

## 4. OpenAI関連ファイル

| ファイル | 役割・注意 |
|---|---|
| `.env` | `OPENAI_API_KEY` と、必要なら `OPENAI_MODEL` を設定する。Git管理対象外。値を文書やログへコピーしない |
| `pyproject.toml` | Python依存関係とpytest設定。OpenAI SDKは `openai` オプション依存 |
| `.venv/` | Windows側Python仮想環境。実験コードではなく実行環境 |

LLMへ送られる主な内容は、進化対象のRelaxed A*コード、親・世代・島に関する変異指示、前回の生成が不正だった場合の修正指示である。DWAコードは変異対象にしない。

## 5. ROS/Gazebo評価スクリプト

| ファイル | 役割 |
|---|---|
| `scripts/run_ros_gazebo_evaluation.sh` | 候補コードをWSLワークスペースへコピー、ビルド、専用ROS/Gazebo Masterで起動、測定、終了、元コード復元 |
| `scripts/ros_gazebo_trial.py` | ロボット初期化、ゴール送信、経路購読、3指標測定、JSON保存 |
| `scripts/repeat10_compare.py` | 進化ループ本体（`llm_gp.main`）を使わず、既存runの`individual_sources/`にある特定2個体（既定は初期`ind_000001`と全体最良の実ソース`ind_000012`）だけをN回（既定10回）再評価し、平均・標準偏差・成功率・適応度を比較するための追加スクリプト |

`repeat10_compare.py` は内部で `RosGazeboEvaluator` と `run_lock.py` をそのまま再利用するため、`llm_gp.main` によるROS/Gazebo実験と同時には実行できない（ロック競合時はエラーで終了する）。

実行例:

```powershell
.\.venv\Scripts\python.exe scripts\repeat10_compare.py `
    --run-dir experiment_results\ten_generation_repeated\run_20260722_053957 `
    --repetitions 10
```

出力は指定した run ディレクトリの `analysis/repeat10_logs/`（生JSON）と `analysis/repeat10_statistics.csv`（集計済み平均・標準偏差・適応度）。既存の3回評価版 `repetition_statistics.csv` とは別ファイルなので上書きされない。

`run_ros_gazebo_evaluation.sh` は評価ごとに専用の `ROS_MASTER_URI` と `GAZEBO_MASTER_URI` を割り当てる。終了時はroslaunchのプロセスグループ全体を停止し、WSL側の `rastar.cpp` を復元して再ビルドする。

## 6. 進化対象とROSの主要ファイル

### 直接変更・評価するファイル

| ファイル | 役割 |
|---|---|
| `src/global_planner/src/rastar.cpp` | Windows側に置く初期Relaxed A*基準コード。個体生成の出発点 |
| `src/global_planner/include/global_planner/rastar.h` | Relaxed A*の宣言 |
| `/home/adachi/catkin_ws_2024_12_2/catkin_ws/src/global_planner/src/rastar.cpp` | WSL側のビルド対象。一時的に候補コードへ置換され、評価後に復元される |

`src/global_planner/src/astar.cpp` やDWAコードは今回の進化対象ではない。

### 起動・ナビゲーション設定

| ファイル/ディレクトリ | 役割 |
|---|---|
| `src/dwa_local_planner/launch/astar_dwa.launch` | Gazebo、TurtleBot3、map_server、AMCL、move_baseを起動。実験では `use_rviz:=false` |
| `src/dwa_local_planner/param/dwa_local_planner_params_burger.yaml` | 固定DWAパラメータ |
| `src/dwa_local_planner/param/move_base_params.yaml` | move_base設定 |
| `src/dwa_local_planner/param/costmap_common_params_burger.yaml` | 共通costmap設定 |
| `src/dwa_local_planner/param2/global_costmap_params.yaml` | global costmap設定 |
| `src/dwa_local_planner/param2/local_costmap_params.yaml` | local costmap設定 |
| `src/dwa_local_planner/param2/global_planner_params.yaml` | GlobalPlanner設定 |
| `src/turtlebot3/turtlebot3_navigation/maps/map.yaml` と対応PGM | map_serverが読む地図 |
| `src/turtlebot3_simulations/turtlebot3_gazebo/worlds/turtlebot3_world.world` | Gazeboワールド |

### 間接的に使用するROSパッケージ

次のパッケージ群はlaunchやビルドから参照されるが、進化によって変更しない。

- `src/global_planner/`
- `src/dwa_local_planner/`
- `src/base_local_planner/`
- `src/costmap_2d/`
- `src/move_base/`
- `src/turtlebot3/`
- `src/turtlebot3_msgs/`
- `src/turtlebot3_simulations/`
- ROS Noetic標準の `gazebo_ros`、`map_server`、`amcl`、`actionlib`

## 7. 評価する3指標と適応度

`scripts/ros_gazebo_trial.py` が次を測定する。

| 指標 | 意味 | 良い方向 |
|---|---|---|
| `planning_time` | ゴール送信から最初の有効なグローバル経路を受信するまで | 小さい |
| `path_length` | 最初に生成されたグローバル経路の各点間距離の合計 | 小さい |
| `arrival_time` | ゴール送信からmove_base成功まで | 小さい |

各個体を3回評価し、3回すべて成功した場合に各指標の平均を適応度へ使用する。1回でも失敗した個体は失敗適応度となるが、残りの反復も実行し、個別JSONと成功回数を残す。

現在の適応度重みは次のとおり。

- 経路生成時間: 0.33
- 経路長: 0.34
- 到達時間: 0.33

適応度だけでなく、後述する `metric_comparison.*` と `repetition_statistics.csv` で3指標を個別に確認する。

## 8. SQLiteの保存内容

主DB:

```text
experiment_results/ten_generation_repeated/roulette_ros_10gen_repeated.db
```

テーブル:

| テーブル | 内容 |
|---|---|
| `individuals` | 個体ID、世代、島、生成方法、親ID、ソースパス、妥当性、評価成功、エリート、生存状態 |
| `evaluations` | 3指標の反復平均、適応度、成功、エラー、個別ROSログのパス |
| `population_memberships` | 各世代・各島で生存した個体と選択方法 |
| `migrations` | コピー元・コピー先個体、送信島、受信島、世代 |
| `change_history` | 交叉・変異内容と変更履歴 |
| `llm_calls` | モデル名、プロンプト、応答、成功、エラー。送受信コードを含むため取扱注意 |

`llm_gp/main.py` は実行のたびに `run_<UTCタイムスタンプ>`（例: `run_20260722_053957`）というサブフォルダを自動作成し、DB・CSV・個体ソース・分析結果をすべてその中に書き出す（`prepare_run_output_directory`）。そのため同じYAMLで再実行しても既存runのDBが上書きされることはなく、`experiment_results/ten_generation_repeated/` の下にrunごとのフォルダが増えていく。

## 9. 全出力フォルダ

複数評価10世代実験のルート:

```text
C:\Users\adachi\catkin_ws\experiment_results\ten_generation_repeated\
```

実行ごとに `run_<UTCタイムスタンプ>` フォルダが自動生成される（例: `run_20260722_053957`）。実際の構成は次のとおり。

```text
ten_generation_repeated/
└── run_20260722_053957/
    ├── run_manifest.json
    ├── ros_gazebo_10gen_repeated.yaml        # 実行時に使ったYAMLのスナップショット
    ├── roulette_ros_10gen_repeated.db
    ├── generation_summary.csv
    ├── individual_sources/
    │   ├── ind_000001.cpp
    │   ├── ind_000002.cpp
    │   ├── ...
    │   └── mig_XXXXXX.cpp
    ├── ros_logs/
    │   ├── ind_XXXXXX_repetition_1.json
    │   ├── ind_XXXXXX_repetition_1.build.log
    │   ├── ind_XXXXXX_repetition_1.roslaunch.log
    │   ├── ind_XXXXXX_repetition_2.json
    │   └── ind_XXXXXX_repetition_3.json
    └── analysis/
        ├── generation_algorithm_report.md
        ├── generation_comparison.csv
        ├── individual_comparison.csv
        ├── metric_comparison.md
        ├── metric_comparison.csv
        ├── repetition_statistics.csv         # 既定の反復数（3回）での平均・標準偏差
        ├── generation_000_best_XXXX.diff
        ├── generation_001_best_XXXX.diff
        ├── ...
        ├── repeat10_logs/                    # scripts/repeat10_compare.py を実行した場合のみ
        ├── repeat10_statistics.csv           # 同上（初期・最良個体を10回ずつ再評価した集計）
        └── best_algorithm/
            ├── best_algorithm.cpp
            ├── README.md
            └── diff_from_initial.diff
```

`run_manifest.json` にはそのrunで使った設定パス・DBパス・個体ソースパス・タイムスタンプが記録されている。

### run直下の出力

| 出力 | 内容 |
|---|---|
| `run_manifest.json` | そのrunのconfigパス・DBパス・個体ソースパス・タイムスタンプ |
| `<config名>.yaml` | 実行時に使用したYAML設定のスナップショット（再現性確認用） |
| `roulette_ros_10gen_repeated.db` | 正本となる実験データ |
| `generation_summary.csv` | 世代、生成数、移住数、島ごとの個体数 |
| `individual_sources/` | 初期・交叉・LLM変異・移住コピーを含む全個体コード |
| `ros_logs/` | 各個体・各反復の測定JSON、ビルドログ、roslaunchログ |

### 分析フォルダの出力

| 出力 | 内容 |
|---|---|
| `generation_algorithm_report.md` | 各世代の適応度最良個体、親、演算、3指標、コードパス、差分 |
| `generation_comparison.csv` | 世代ごとの成功数、平均適応度、最良個体、3指標、初期比 |
| `individual_comparison.csv` | 全個体の生成方法、親、成功、適応度、3指標、ソースパス |
| `metric_comparison.md` | 初期最良と実験最良、および各世代最良を3指標で比較 |
| `metric_comparison.csv` | 3指標と各指標の初期比改善率 |
| `repetition_statistics.csv` | 個体ごとの試行数、成功数、成功率、各指標の平均と標準偏差 |
| `generation_XXX_best_XXXX.diff` | 初期最良コードと各世代最良コードの差分 |

### 最良コード専用フォルダ

| 出力 | 内容 |
|---|---|
| `best_algorithm/best_algorithm.cpp` | 実験全体で重み付き適応度が最大のコードを固定名でコピー |
| `best_algorithm/README.md` | 個体ID、世代、島、生成方法、適応度、3指標、元ファイル |
| `best_algorithm/diff_from_initial.diff` | 初期最良コードとの差分 |

## 10. その他の実験結果保存先

| 設定 | 保存ルート |
|---|---|
| `ros_gazebo_10gen_repeated.yaml` | `experiment_results/ten_generation_repeated/` |
| `ros_gazebo_10gen_small.yaml` | `experiment_results/ten_generation_small/` |
| `ros_gazebo_mini.yaml` | `experiment_results/mini/` |
| `ros_gazebo.yaml` | `experiment_results/` の `roulette_ros_*` |
| `default.yaml` | `experiment_results/` の `roulette_*` |

異なる設定の結果を比較するときは、反復数と個体群サイズが異なることに注意する。どの設定でも実際の出力は上表の「保存ルート」直下ではなく、その中にできる `run_<UTCタイムスタンプ>/` フォルダに入る（9章参照）。2026-07-24時点で `experiment_results/` 配下に実データが存在するのは `ten_generation_repeated/`（`run_20260722_053957/` など）のみで、他の設定は未実行または結果が残っていない。

## 11. 排他ロックと一時ファイル

ROS/Gazebo実験中は次のファイルをOSレベルでロックする。

```text
experiment_results/.ros_gazebo_experiment.lock
```

このファイルが存在すること自体は異常ではない。ロックの有無はファイルの存在ではなくOSが管理する。別の実験が実行中の場合、新しい実験は開始前に拒否される。

シェルスクリプトはWSL側の対象ソースを一時バックアップし、終了・中断時に復元する。通常、バックアップ一時ファイルは終了時に削除される。

## 12. テストファイル

| ファイル | 主な検証対象 |
|---|---|
| `tests/conftest.py` | 共通テスト設定 |
| `tests/test_evolution.py` | 世代ループ、固定個体数、子生成、移住、DB件数 |
| `tests/test_selection.py` | ルーレット親選択、エリート、生存選択 |
| `tests/test_fitness_and_validation.py` | 適応度とコード検証 |
| `tests/test_ros_gazebo_pipeline.py` | ROS設定、C++演算、LLM応答処理、各実験YAML |
| `tests/test_report.py` | 世代・3指標・反復統計・最良コード出力 |
| `tests/test_run_lock.py` | 二重起動防止とROS/Gazebo専用ポート |

テストコマンド:

```powershell
cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m pytest
```

## 13. 実験前後の確認コマンド

### ROS/Gazeboを1回だけ確認

```powershell
.\.venv\Scripts\python.exe -m llm_gp.ros_smoke --config config\ros_gazebo_10gen_repeated.yaml
```

期待する表示:

```text
success=True
error_message=None
```

### 10世代・各個体3回評価を実行

```powershell
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_10gen_repeated.yaml
```

正常終了時にはSQLite、CSV、分析レポート、最良コードのパスがターミナルへ表示される。

### 最初に見る結果

実行のたびにできる `run_<UTCタイムスタンプ>/analysis/` の下を見る（9章参照）。

1. `run_<timestamp>/analysis/metric_comparison.md`
2. `run_<timestamp>/analysis/repetition_statistics.csv`
3. `run_<timestamp>/analysis/best_algorithm/README.md`
4. `run_<timestamp>/analysis/best_algorithm/best_algorithm.cpp`
5. 初期・最良個体を10回以上で比較したい場合は `scripts/repeat10_compare.py` を実行し、`run_<timestamp>/analysis/repeat10_statistics.csv` を見る（5章参照）
6. 必要に応じてSQLiteと個別ROSログ

## 14. 結果を妥当と判断する際の注意

- 適応度だけでなく、経路生成時間、経路長、到達時間を個別に確認する。
- `repetition_statistics.csv` で標準偏差と成功率を確認する。
- 3回評価は1回評価より妥当だが、最終結論には初期コードと最良コードを10回以上、独立に再評価することが望ましい。
- 最良個体は「設定した重み付き適応度が最大」であり、3指標すべてが最良とは限らない。
- `ABORTED`、ビルド失敗、LLM失敗を区別して確認する。
- 独立した複数の進化実験を行う場合は `random_seed` と出力先を変え、結果を上書きしない。

## 15. 仕様書と補助文書

| ファイル | 内容 |
|---|---|
| `CODEX_LLM_GP_MAP_ELITES_ISLAND_SPEC.md` | 元の要求・設計仕様書。現在の実装は後のルーレット固定個体群仕様への更新を含む |
| `README.md` | プロジェクト概要と基本コマンド |
| `docs/llm_gp_operation_flow.md` | 動作フロー |
| `docs/astar_dwa_run_guide.md` | A*/DWA実行ガイド |
| `src/zikkou.md` | 手動実行メモ。最新の複数評価コマンドは本書を優先する |

