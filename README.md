# LLM-GP ルーレット選択・4島モデル

Relaxed A*を個体として改善する進化実験フレームワークです。DWAは固定し、4島それぞれが
10個体の独立した母集団を保持します。MAP-Elitesのセル分類は使用しません。

## モック実験

```powershell
cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\default.yaml
```

既定値では50世代を実行します。

- 初期母集団: 4島 × 10個体 = 40個体
- 各世代: 1島5親ペア × 3種類 = 15個体、全体60個体
- 次世代: 親10＋子15から、エリート1＋重複なしルーレット9
- 移住: 10世代ごとに各島の最高1個体を隣接島へコピー
- 評価: 1個体につき3回のモック評価の平均

結果は次に保存されます。

- `experiment_results/roulette_evolution.db`
- `experiment_results/roulette_generation_summary.csv`
- `experiment_results/roulette_individual_sources/`
- `experiment_results/roulette_evolution_analysis/`

## ROS/Gazebo + OpenAI実験

```powershell
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo.yaml
```

この設定は`src/global_planner/src/rastar.cpp`だけを候補として一時適用します。
`dwa_local_planner/DWAPlannerROS`は固定です。1個体を3回走行し平均を使うため、実行時間と
OpenAI API利用量が大きくなります。

### 小規模な全体確認

本番前に、初期4個体＋子12個体、1世代、各個体1回だけのミニ実験を実行できます。

```powershell
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_mini.yaml
```

結果は`experiment_results/mini/`へ分離して保存されます。本番実験と同時に実行しないで
ください。どちらもWSL側の同じ`rastar.cpp`を一時的に差し替えるためです。

`.env`には次を設定します。`.env`はGit管理対象外です。

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

## 設定

- `config/default.yaml`: モック評価・モック変異
- `config/ros_gazebo.yaml`: ROS/Gazebo評価・OpenAI変異

主な設定は`evolution.population_size_per_island`、`max_generations`、`selection`、
`migration`、`evaluation.repetitions_per_goal`です。仕様上、母集団10、親ペア5、
エリート1、4島、3回以上の反復評価を必須としています。

## SQLite

- `individuals`: 全個体と選択状態
- `evaluations`: 3指標、適応度、成功・失敗理由、ログ
- `population_memberships`: 世代ごとの各島10個体
- `migrations`: コピー元・コピー先・世代
- `change_history`: 交叉・変異・移住の履歴
- `llm_calls`: モデル、プロンプト、応答、成功・失敗

次世代に残らなかった個体も削除せず保存します。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest
```

親ルーレット、生存者選択、エリート、母集団サイズ、リング移住、DB、安全検証、再現性を
確認します。

## 主なファイル

- `llm_gp/evolution.py`: 世代ループ、固定母集団、移住
- `llm_gp/selection.py`: 適応度比例ルーレットとエリート選択
- `llm_gp/island.py`: 島と独立乱数系列
- `llm_gp/operators.py`: モック/C++交叉、モック/OpenAI変異
- `llm_gp/evaluator.py`: モック/ROS-Gazebo評価器
- `llm_gp/validation.py`: C++/Python安全検証
- `llm_gp/database.py`: SQLite保存
- `llm_gp/report.py`: 世代・個体・コード差分レポート
