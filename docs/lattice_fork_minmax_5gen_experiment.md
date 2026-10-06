# Min-Maxスケーリング実験(`config/ros_gazebo_lattice_fork_minmax_5gen.yaml`)

## 目的

これまでのfitnessは`planning_reference`/`path_reference`/`arrival_reference`/
`expansion_reference`という**固定の基準値**に対して各指標を正規化していた
(`1.0 - 値/基準値`)。この基準値は過去の実測ログから手動でキャリブレーションした
ものであり、マップやゴール範囲を変えるたびに再キャリブレーションが必要という
指摘があった。

この実験では、fitnessの正規化を**その世代内で実際に成功した個体群のmin/max**を
使ったMin-Maxスケーリングに置き換え、固定基準値への依存をなくせるかを検証する。

## 何を変更したか

- `llm_gp/fitness.py`: `GenerationMetricStats`(cost/path/arrivalそれぞれの
  min/max)を導入し、`calculate_fitness`は固定reference値ではなくこの統計量に
  対して各指標を`(hi - value) / (hi - lo)`で正規化するように変更。
- `llm_gp/evolution.py`: 評価(`_process_individuals`)と採点
  (`_score_individuals`)を分離。世代ごとに「親+子」のプール単位でmin/maxを
  計算し、**親も含めて毎世代再採点**してから生存選択(`select_survivors`)に
  渡す(固定reference方式では親のfitnessは生成時のまま不変だったが、相対正規化
  では比較対象の世代が変われば値も変わるため)。
- `llm_gp/database.py`: `update_evaluation_fitness`を追加。再評価はしない
  親個体でも、毎世代再採点された`fitness`を`evaluations`テーブルへ反映する
  (ここを怠ると`generation_report`等の分析がDB上の古いfitnessを読み続ける)。
- `llm_gp/config.py`: 「4島ちょうど必須」というバリデーションを「1島以上・
  名前重複なし」に緩和(本実験は下記の理由で単一集団構成にするため)。
- `llm_gp/verification.py`: GP実行後のinitial-vs-best比較も、固定reference
  ではなくこの2個体間のmin/maxで相対採点するように追従。

## 設計上の決定(ユーザーとの合意事項)

- **正規化の母集団の範囲**: 本来は「島ごと」か「全島まとめて」かという論点が
  あったが、今回は**島アルゴリズム自体をこの実験のスコープ内だけ一時的に
  やめる**(`islands.count: 1`)ことでこの論点を回避した。Island/migrationの
  コード自体は残しており、恒久的な方針変更ではない。
- **成功個体が0〜1体しかいない世代の扱い**: min/maxが計算できない
  (`compute_generation_stats`が`None`を返す)場合、比較対象がいないとみなし
  該当個体は満点(`planning_weight + path_weight + arrival_weight`)を与える。
- **前世代から生き残った親(エリート含む)の扱い**: 毎世代、その世代の子個体と
  同じmin/maxで再採点する(凍結しない)。

## 実験設定

| 項目 | 値 |
|---|---|
| Config | `config/ros_gazebo_lattice_fork_minmax_5gen.yaml` |
| マップ | lattice_fork(`ros_gazebo_lattice_fork_5gen.yaml`と同一) |
| 世代数 | 5 |
| 集団構成 | 単一集団10個体(島なし。旧構成は4島×2個体) |
| 子生成 | 2ペア × 3個体(crossover_only/mutation_only/crossover_and_mutation) = 6個体/世代 |
| エリート数 | 1 |
| 選択方式 | roulette(親選択)/ roulette_without_replacement(生存選択)。旧5gen構成と同一 |
| LLM crossover/mutation | `ros_gazebo_lattice_fork_5gen.yaml`と同一(`provider: openai`, `model: gpt-4o-mini`, `fallback_to_mock: true`) |
| fitness正規化 | 世代内min-maxスケーリング(固定reference値はもう使われないが、operators.pyのLLMプロンプト用比率表示のためyaml上は残置) |
| 評価 | `repetitions_per_goal: 3`、ゴールは`goal_x_range=[15.0,16.5]`/`goal_y_range=[1.5,2.3]`からランダムサンプリング |
| データ保存先 | `experiment_results/lattice_fork_minmax_5gen/` |

## 実行方法

### 前提

- WSL(`Ubuntu-20.04`)側のcatkinワークスペース
  `/home/adachi/catkin_ws_2024_12_2/catkin_ws`がビルド可能な状態であること
  (`config/ros_gazebo_lattice_fork_minmax_5gen.yaml`の`ros_gazebo.workspace`)。
- `OPENAI_API_KEY`が設定されていれば実際にgpt-4o-miniでLLM crossover/mutationを
  行う。未設定でも`fallback_to_mock: true`なので決定論的フォールバック
  (重み平均等)で止まらずに動く。

### 方法A: クリーンな状態から実行(推奨、既存の10gen実験と同じやり方)

Windows側のPowerShellから(WSLに入っている状態からは実行しないこと。
`wsl --shutdown`が今開いているセッション自体を巻き込んで落ちるため):

```powershell
.\scripts\restart_wsl_and_run.ps1 -Command "cd /mnt/c/Users/adachi/catkin_ws && setsid nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_5gen.yaml > gp_minmax_5gen.log 2>&1 < /dev/null & disown"
```

WSLを一度完全にシャットダウンしてから起動するため、前回実験の残存プロセス
(gzserver/roslaunch等)の影響を受けない状態で始められる。`nohup ... & disown`で
バックグラウンド実行するので、PowerShellのウィンドウを閉じても実行は継続する。

### 方法B: 既存のWSLセッション内でそのまま実行

既にWSLターミナルを開いていて、クリーン再起動が不要な場合:

```bash
cd /mnt/c/Users/adachi/catkin_ws
python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_5gen.yaml --verify-repetitions 10
```

`--verify-repetitions`(デフォルト10)は、GP実行後に初期個体と最良個体を
それぞれN回ずつ再評価し、世代間の改善が単発評価のノイズでないかを検証する
ステップ。スキップしたい場合は`--verify-repetitions 0`。

### 実行中の様子

- 標準出力に世代ごとの`generation=N generated=... populations[main=10]`という
  進捗行が出る(`gp_minmax_5gen.log`にリダイレクトした場合はそこで確認)。
- 総評価数は初期10体 + 5世代×6体 = 40体。各個体は3回評価
  (`repetitions_per_goal: 3`)されるため、実際のGazebo評価は最大120回。
  規模としては旧`ros_gazebo_lattice_fork_5gen.yaml`(初期8体+5世代×12体=68体)より
  やや少なく、既に実行済みの5gen実験と近い所要時間になる見込み。

## 実行後に確認するもの

- `experiment_results/lattice_fork_minmax_5gen/run_<timestamp>/`
  - `roulette_ros_lattice_fork_minmax_5gen.db` — SQLite(個体・評価・世代所属の全記録)
  - `generation_summary.csv` — 世代ごとの集計
  - `analysis/generation_algorithm_report.md` — 世代ごとの最良個体・演算種別・改善率
  - `analysis/best_algorithm.cpp`と`analysis/best_algorithm_diff` — 初期個体との実コード差分
  - `analysis/repeat10_statistics.csv` / `repeat10_significance.csv` —
    (`--verify-repetitions`を実行した場合)初期 vs 最良の統計的有意差検定

## 結果を読むときの注意(Min-Max特有)

固定reference方式と違い、**fitnessの値そのものは世代をまたいで単純比較できない**。
ある世代のfitness=0.7と別の世代のfitness=0.7は、正規化に使ったmin/maxが世代ごとに
異なるため同じ「良さ」を意味しない。世代を通した改善傾向を見るときは、
`analysis/generation_algorithm_report.md`のfitness列だけでなく、
`node_expansions`/`path_length`/`arrival_time`という生の指標(世代に依存しない
絶対値)の推移を必ず併読すること。GP実行後の`--verify-repetitions`による
initial-vs-best比較は、その2個体だけのmin/maxで採点し直すため、fitnessも含めて
一貫した比較になる。
