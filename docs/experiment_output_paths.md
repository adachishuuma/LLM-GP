# 実験出力の保存先一覧

## 概要
このドキュメントは、ワークスペース内で実験を実行した際に生成される主要な出力ファイルと保存先ディレクトリをまとめたものです。各実験設定ファイル（`config/*.yaml`）の `database.path`、`output.generation_csv`、`output.source_directory` を参照してください。

## 共通出力（ルート）
- `experiment_results/generation_summary.csv` : 全体の世代別サマリ（プロジェクト共通の総合サマリ）
- `experiment_results/analysis/` : 手動で作成される分析レポート出力先
- 各種 SQLite DB の存在例：
  - `experiment_results/roulette_evolution.db`
  - `experiment_results/ros_evolution.db`
  - `experiment_results/ros_evolution_10gen.db`
  - `experiment_results/roulette_ros_evolution.db`

## 設定ファイル別の標準出力先

- `config/default.yaml`（モック実験）
  - DB: `experiment_results/roulette_evolution.db`
  - 世代CSV: `experiment_results/roulette_generation_summary.csv`
  - 個体ソース: `experiment_results/roulette_individual_sources/`
  - 分析出力: `experiment_results/roulette_evolution_analysis/`

- `config/ros_gazebo_mini.yaml`（ミニ実験）
  - DB: `experiment_results/mini/roulette_ros_mini.db`
  - 世代CSV: `experiment_results/mini/generation_summary.csv`
  - 個体ソース: `experiment_results/mini/individual_sources/`
  - ROSログ: `experiment_results/mini/ros_logs/`

- `config/ros_gazebo_10gen_small.yaml`（小規模 10 世代）
  - DB: `experiment_results/ten_generation_small/roulette_ros_10gen_small.db`
  - 世代CSV: `experiment_results/ten_generation_small/generation_summary.csv`
  - 個体ソース: `experiment_results/ten_generation_small/individual_sources/`
  - ROSログ: `experiment_results/ten_generation_small/ros_logs/`
  - 分析出力: `experiment_results/ten_generation_small/roulette_ros_10gen_small_analysis/`

- `config/ros_gazebo_10gen_repeated.yaml`（反復評価 10 世代）
  - DB: `experiment_results/ten_generation_repeated/roulette_ros_10gen_repeated.db`
  - 世代CSV: `experiment_results/ten_generation_repeated/generation_summary.csv`
  - 個体ソース: `experiment_results/ten_generation_repeated/individual_sources/`
  - ROSログ: `experiment_results/ten_generation_repeated/ros_logs/`

- `config/ros_gazebo.yaml`（本番の ROS/Gazebo 実験）
  - 既定の出力先例:
    - DB: `experiment_results/ros_evolution.db`（設定により `database.path` を確認）
    - 世代CSV: `experiment_results/ros_generation_summary.csv`
    - 個体ソース: `experiment_results/ros_individual_sources/` または `ros_individual_sources_10gen/`
    - ROSログ: `experiment_results/ros_logs/`

## 実行時に出力先を確認する方法
- 各設定ファイル（例: `config/ros_gazebo_10gen_small.yaml`）の `database.path` を確認すると、使用される SQLite ファイルが分かります。
- `output.generation_csv` と `output.source_directory` に実際の CSV と個体ソースのパスが設定されています。
- 実行ログはコンソールに出力されますが、`ros_logs/` や `experiment_results/<run>/` 配下にも保存されます。

## 追加メモ
- 実験ごとに `analysis/` フォルダを作り、`generation_best/` や `overall_best/` などの差分・レポートを保存する運用が想定されています（`.github/instructions` に手順あり）。

---
ファイル: [docs/experiment_output_paths.md](docs/experiment_output_paths.md)
