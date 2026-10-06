# Min-Maxスケーリング30世代実験(`config/ros_gazebo_lattice_fork_minmax_30gen.yaml`)

## 目的

`ros_gazebo_lattice_fork_minmax_5gen.yaml`(世代内min/max正規化、単一集団10個体)
をそのまま**30世代**に延長し、より長い進化でfitnessや`node_expansions`等の
生指標がどこまで改善し続けるか(あるいは頭打ちになるか)を確認する。
5gen実験からの変更点は`max_generations: 5 -> 30`とデータ保存先のみで、
fitness正規化方式・母集団構成・選択方式・LLM設定は5gen実験と完全に同一。
詳細な設計根拠は`docs/lattice_fork_minmax_5gen_experiment.md`を参照。

併せて、世代数が増えて実行時間・LLM呼び出し回数が大きくなることを見越し、
**LLMのトークン消費量を計測・集計する仕組み**を追加した(下記「LLMトークン計測」)。

## 実験設定

| 項目 | 値 |
|---|---|
| Config | `config/ros_gazebo_lattice_fork_minmax_30gen.yaml` |
| マップ | lattice_fork(5gen実験と同一) |
| 世代数 | 30(5gen実験の6倍) |
| 集団構成 | 単一集団10個体(島なし) |
| 子生成 | 2ペア × 3個体 = 6個体/世代 |
| エリート数 | 1 |
| 選択方式 | roulette(親選択)/ roulette_without_replacement(生存選択) |
| LLM crossover/mutation | `provider: openai`, `model: gpt-4o-mini`, `fallback_to_mock: true` |
| fitness正規化 | 世代内min-maxスケーリング |
| 評価 | `repetitions_per_goal: 3`、ゴールは`goal_x_range=[15.0,16.5]`/`goal_y_range=[1.5,2.3]`からランダムサンプリング |
| データ保存先 | `experiment_results/lattice_fork_minmax_30gen/` |
| 総評価数(目安) | 初期10体 + 30世代×6体 = 190体。各3回評価なので最大570回のGazebo評価 |

## LLMトークン計測

30世代にわたるLLM呼び出しがどれだけのトークンを消費したかを追跡できるよう、
以下を追加した(`llm_gp/models.py`, `llm_gp/operators.py`, `llm_gp/database.py`,
`llm_gp/report.py`, `llm_gp/main.py`)。

- `LLMCallRecord`に`prompt_tokens`/`completion_tokens`/`total_tokens`
  (いずれも`int | None`)を追加。OpenAI Responses APIの`response.usage`
  (`input_tokens`/`output_tokens`/`total_tokens`)から値を取り出して記録する。
  `usage`を返さない応答(通常のOpenAI応答では発生しないが、フォールバックの
  mock演算子や過去互換のテスト用ダブルなど)では全て`None`のまま保存され、
  0トークン消費とは区別される。
- `llm_calls`テーブルに`prompt_tokens`/`completion_tokens`/`total_tokens`
  列を追加(SQLiteの`NULL`許容カラム)。
- `llm_gp/report.py`の`generate_report()`が、各runの`analysis/`配下に
  `token_usage_by_generation.csv`(世代ごとのLLM呼び出し回数・成功数・
  usage情報なし件数・prompt/completion/totalトークン合計)を出力し、
  `generation_algorithm_report.md`の冒頭に実験全体のトークン合計を追記する。
- `llm_gp/main.py`は実行完了時にコンソールへ
  `LLM tokens: total=... (prompt=..., completion=...) over N call(s), M without usage data`
  という1行サマリを出力する(`report.token_usage_summary()`を使用)。

過去に保存済みのDB(5gen実験など、`prompt_tokens`列を持たない古いスキーマ)は
`generate_report`や`token_usage_summary`をそのまま実行すると列が存在せず
エラーになる。新しいスキーマでの再実行が必要な場合は、5gen実験を再実行して
再生成すること(min-maxスケーリング実験自体は`reset=True`で毎回DBを作り直す
運用のため、通常は問題にならない)。

## 実行方法

### 前提

`ros_gazebo_lattice_fork_minmax_5gen.yaml`と同じ(WSL側catkinワークスペースが
ビルド可能な状態であること、`OPENAI_API_KEY`未設定でも`fallback_to_mock: true`
のため動作すること)。

### 実行コマンド

Windows側のPowerShellから(WSLに入っている状態からは実行しないこと):

```powershell
.\scripts\restart_wsl_and_run.ps1 -Command "cd /mnt/c/Users/adachi/catkin_ws && setsid nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_30gen.yaml > gp_minmax_30gen.log 2>&1 < /dev/null & disown"
```

既存のWSLセッション内でそのまま実行する場合:

```bash
cd /mnt/c/Users/adachi/catkin_ws
python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_30gen.yaml --verify-repetitions 10
```

30世代 × 6個体 × 3反復 は5gen実験よりかなり長時間(目安6倍)かかるため、
`nohup ... & disown`でのバックグラウンド実行を推奨する。

## 実行後に確認するもの

- `experiment_results/lattice_fork_minmax_30gen/run_<timestamp>/`
  - `roulette_ros_lattice_fork_minmax_30gen.db`
  - `generation_summary.csv`
  - `analysis/generation_algorithm_report.md` — 世代ごとの最良個体・改善率・LLMトークン合計
  - `analysis/token_usage_by_generation.csv` — 世代ごとのLLMトークン消費内訳
  - `analysis/best_algorithm.cpp` / `analysis/best_algorithm_diff`
  - `analysis/repeat10_statistics.csv` / `repeat10_significance.csv`(`--verify-repetitions`実行時)
- 実行完了時のコンソール出力の`LLM tokens: ...`行(runディレクトリを開かずに
  概算を確認したい場合)

## 結果を読むときの注意

5gen実験と同様、fitnessは世代ごとのmin/maxで正規化されるため世代をまたいで
単純比較できない。世代を通した改善傾向は`node_expansions`/`path_length`/
`arrival_time`の生値で確認すること(詳細は
`docs/lattice_fork_minmax_5gen_experiment.md`参照)。
