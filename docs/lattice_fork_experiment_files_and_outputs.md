# lattice_fork 実験で使用するファイルと保存先

最終更新: 2026-08-09

この文書は `C:\Users\adachi\catkin_ws` にある `lattice_fork` マップでの LLM-GP・ROS/Gazebo実験を対象に、使用するファイル・ROS側の参照ファイル・生成物の保存先をまとめたものである。共通インフラ(`llm_gp/` 本体、テスト等)は
[`docs/current_experiment_files_and_outputs.md`](current_experiment_files_and_outputs.md) と重複するため詳細はそちらを参照し、本書は **lattice_fork固有の部分**(マップ、launch、ゴールランダム化など)を中心に記載する。

## 1. 実行コマンド

10世代版:

```powershell
cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_lattice_fork.yaml
```

5世代版(10世代版と地図・設定は同一、世代数のみ短縮):

```powershell
cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_lattice_fork_5gen.yaml
```

主な条件(共通):

- 島数: 4、島ごとの生存個体数: 2、島ごとの親ペア数: 1(smoke_mode)
- 各個体の走行反復数: 3(`repetitions_per_goal: 3`)
- 開始姿勢: `[-11.8, 2.0, -0.00143]`(固定)
- ゴール: **反復ごとにランダム化**(下記4章参照)。`x ∈ [15.0, 16.5]`, `y ∈ [1.5, 2.3]`, yawは`fixed_goal`の値(0.0)で固定
- LLM: OpenAI `gpt-4o-mini`(失敗時モックへフォールバック)
- 進化対象: Relaxed A* の `rastar.cpp`
- 乱数シード: 42(`random_seed`。ゴールのサンプリングにも同じシード系列を使用するため再現可能)

| 設定 | 世代数 | 保存先ルート |
|---|---:|---|
| `config/ros_gazebo_lattice_fork.yaml` | 10 | `experiment_results/lattice_fork/` |
| `config/ros_gazebo_lattice_fork_5gen.yaml` | 5 | `experiment_results/lattice_fork_5gen/` |

## 2. マップ・ワールド(lattice_fork固有)

| ファイル | 役割 |
|---|---|
| `src/dwa_local_planner/maps/lattice_fork.pgm` | occupancy grid画像(660×460px, 解像度0.05m/px)。map_serverとGazeboワールドの壁配置は**同じ骨格線から生成**しており画素単位で整合している |
| `src/dwa_local_planner/maps/lattice_fork.yaml` | 上記pgmのメタデータ。`origin: [-14.0, -9.0, 0.0]`, `resolution: 0.05` |
| `src/dwa_local_planner/worlds/lattice_fork.world` | Gazebo SDFワールド。壁は `wall_0`〜`wall_87` の直方体コリジョン(88個) |

マップの構造: 中央水平レーン(y≈2.0)を軸に、上側・下側それぞれにループ状の迂回路(隠れループ)を持つラティス状の通路。詳細な形状説明は `astar_dwa_lattice_fork.launch` 冒頭のXMLコメントを参照。

### このセッションで行った主な修正(2026-08-07〜08-09)

1. **通路幅を約1.0m→2.0mに拡幅**。元の自由空間を `skimage.morphology.skeletonize` で中心線化し、一定幅(41px=2.05m)で再膨張させて再生成。map(pgm)とworld(壁ボックス)は同じ処理から同時に作ったため、見た目のズレが生じない。
2. **拡幅で消えた壁を1本復元**: `wall_86`(対角壁、`[11.9,2.58]→[12.3,1.15]`、厚み0.3m)。もう1本の候補(`[12.5,0.973]→[14.4,0.896]`)は経路を塞ぐため追加せず削除済み。
3. **開始位置を `[-12.5, 2.0, 0.0]` → `[-11.8, 2.0, -0.00143]` に変更**。
4. 上記1〜3の変更は `lattice_fork.pgm` / `lattice_fork.world` を直接編集する形で行っており、生成用の一時スクリプトはリポジトリには残していない(スクラッチ領域で使い捨て)。再現する場合は本書とこの日付のセッション記録を参照。

## 3. launchファイル

| ファイル | 役割 |
|---|---|
| `src/dwa_local_planner/launch/astar_dwa_lattice_fork.launch` | lattice_fork専用launch。Gazebo(headless, `gui:=false`)、TurtleBot3スポーン、map_server、AMCL、move_base、(任意で)RVizを起動 |
| `src/turtlebot3/turtlebot3_navigation/launch/amcl.launch` | 共有amclラウンチ(他マップからも参照される)。**`initial_cov_xx`/`initial_cov_yy`/`initial_cov_aa` 引数を追加**(未指定時はAMCL標準デフォルトのままなので他の呼び出し元への影響なし) |

`astar_dwa_lattice_fork.launch` の主な引数:

| 引数 | 既定値 | 備考 |
|---|---|---|
| `x_pos` / `y_pos` / `yaw_pos` | `-11.8` / `2.0` / `-0.00143` | スポーン位置とAMCLの`initial_pose_*`の両方に使われる |
| `use_rviz` | `true` | 評価スクリプトは `use_rviz:=false` を明示的に渡してヘッドレス実行する |
| (Gazebo `gui`) | `false` | empty_world.launchへの固定値。手動確認時に見たい場合はファイルを直接編集する必要がある |

AMCLへは `initial_cov_xx=0.05, initial_cov_yy=0.05, initial_cov_aa=0.02` という狭い共分散を渡している。lattice_forkは通路が広く・左右対称的な複数レーン構造のため、初期共分散が広いとパーティクルフィルタが似た別の場所へ誤収束することがあり(位置推定が実際とズレる/飛ぶ現象)、既知の正確なスポーン位置を強く信頼させることで安定させている。

WSLg環境でのレンダリング安定化として `QT_X11_NO_MITSHM=1`(launch全体)と `LIBGL_ALWAYS_SOFTWARE=1`(RVizノードのみ)も設定済み(GPUパススルーが機能しないWSL環境でGazebo GUI/RVizがクラッシュ・黒画面になる問題への対処)。

## 4. ゴールのランダム化(lattice_fork固有の追加機能)

2026-08-09に追加。他マップ用の設定ファイルには影響しない(`goal_x_range`/`goal_y_range`未指定時は従来通り`fixed_goal`固定)。

| ファイル | 変更内容 |
|---|---|
| `llm_gp/config.py` | `EvaluationSettings` に `goal_x_range`/`goal_y_range`(オプション)を追加。両方セット必須のバリデーションあり |
| `llm_gp/evaluator.py` | `RosGazeboEvaluator` が **反復ごとに**新しいゴールx/yを一様分布からサンプリング(yawは`fixed_goal.yaw`のまま固定)。乱数生成器は実験全体のシード付きrng(`EvolutionEngine.rng`)を共有し再現性を保つ |
| `llm_gp/evolution.py` | `RosGazeboEvaluator` 生成時に `rng` と範囲設定を渡すよう変更 |
| `scripts/ros_gazebo_trial.py` | 実際に使ったゴール座標を `goal_x`/`goal_y` として結果JSONに追記(トレーサビリティ用) |
| `config/ros_gazebo_lattice_fork*.yaml` | `goal_x_range: [15.0, 16.5]`, `goal_y_range: [1.5, 2.3]` |

同じ個体の3反復でも毎回異なるゴール(範囲内)へ向かうため、1点への過学習ではなく範囲内のどこへでも到達できるかを評価する設計になっている。

## 5. 出力フォルダ

```text
experiment_results/lattice_fork/            # 10世代版のルート
experiment_results/lattice_fork_5gen/       # 5世代版のルート
└── run_<UTCタイムスタンプ>/
    ├── run_manifest.json
    ├── ros_gazebo_lattice_fork(_5gen).yaml   # 実行時に使ったYAMLのスナップショット
    ├── roulette_ros_lattice_fork(_5gen).db
    ├── generation_summary.csv
    ├── individual_sources/
    │   ├── ind_000001.cpp, ind_000002.cpp, ...
    │   └── mig_XXXXXX.cpp
    ├── ros_logs/
    │   ├── ind_XXXXXX_repetition_N.json        # success, planning_time, path_length,
    │   │                                        # arrival_time, node_expansions, goal_x, goal_y
    │   ├── ind_XXXXXX_repetition_N.build.log
    │   └── ind_XXXXXX_repetition_N.roslaunch.log
    └── analysis/
        ├── generation_algorithm_report.md
        ├── generation_comparison.csv
        ├── individual_comparison.csv
        ├── metric_comparison.md
        ├── metric_comparison.csv
        ├── repetition_statistics.csv
        ├── generation_XXX_best_XXXX.diff
        └── best_algorithm/
            ├── best_algorithm.cpp
            ├── README.md
            └── diff_from_initial.diff
```

各出力の詳細な意味は [`docs/current_experiment_files_and_outputs.md`](current_experiment_files_and_outputs.md) の9章と同一(生成コードは共通の `llm_gp/main.py` / `llm_gp/report.py` のため)。

## 6. 共通インフラ(参照のみ)

以下はlattice_fork専用ではなく、他マップの実験とも共有するファイル群。詳細な役割は [`docs/current_experiment_files_and_outputs.md`](current_experiment_files_and_outputs.md) の2章・4章・6章・12章を参照。

- `llm_gp/` パッケージ本体(`main.py`, `evolution.py`, `island.py`, `selection.py`, `operators.py`, `fitness.py`, `validation.py`, `models.py`, `database.py`, `run_lock.py`, `report.py`, `ros_smoke.py`)
- `scripts/run_ros_gazebo_evaluation.sh`(候補コードのビルド・専用ROS/Gazebo Master起動・測定・復元)
- `scripts/ros_gazebo_trial.py`(ロボット初期化・ゴール送信・3指標測定。lattice_forkでは`goal_x`/`goal_y`出力を追加済み — 4章参照)
- `src/global_planner/src/rastar.cpp`(進化対象。Windows側が原本、WSL側 `/home/adachi/catkin_ws_2024_12_2/catkin_ws/src/global_planner/src/rastar.cpp` は評価毎に候補コードへ一時置換され、評価後に復元される)。**A*/RAstarのアルゴリズム自体の詳細な解説**(標準A*との違い、`RAStarExpansion`クラス構成、`rastar.cpp`/`RAstar.cpp`のファイル名大小文字問題、実行時のナビゲーションフローなど)は本書には含めていない。[`docs/astar_rastar_code_map.md`](astar_rastar_code_map.md) を参照
- `.env`(`OPENAI_API_KEY`。Git管理対象外)
- `experiment_results/.ros_gazebo_experiment.lock`(同時実行防止の排他ロック)
- `tests/`(pytestスイート。`.\.venv\Scripts\python.exe -m pytest` で実行)

## 7. 動作確認・トラブルシュート

### ROS/Gazeboを1回だけ確認(lattice_fork向けに直接実行する場合)

手動でWSLに入って起動する場合:

```bash
cd /home/adachi/catkin_ws_2024_12_2/catkin_ws
source /opt/ros/noetic/setup.bash
source devel/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch dwa_local_planner astar_dwa_lattice_fork.launch
```

WSLgの描画が不安定な場合(ウィンドウが表示されない/クリックすると消える等)は `wsl --shutdown` で一度WSLを完全に再起動してから入り直すと解消することが多い(このセッションで複数回確認済み)。

### 実験結果を最初に見る場所

1. `run_<timestamp>/analysis/metric_comparison.md`
2. `run_<timestamp>/analysis/repetition_statistics.csv`(各個体の成功率・標準偏差)
3. `run_<timestamp>/analysis/best_algorithm/README.md` と `best_algorithm.cpp`
4. 各 `ros_logs/*.json` の `goal_x`/`goal_y` を見れば、その反復がどの座標へ向かったか個別に確認できる

### 注意点

- 手動でWSL側のroslaunchを直接起動して確認する場合、自動実験(`llm_gp.main`)と同時に動かすとCPU/GPUを取り合い不安定になりやすい。片方を止めてから試すことを推奨。
- `astar_dwa_lattice_fork.launch` のGazebo GUIは既定で非表示(`gui:=false`)。可視化して見たい場合はファイルを直接編集する(このセッションでは一時的に`true`にして目視確認を行った)。
