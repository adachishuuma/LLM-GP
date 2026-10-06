# 決定的30世代実験(`config/ros_gazebo_lattice_fork_minmax_30gen_deterministic.yaml`)

## 目的

これまでの30世代実験(gpt-4o-mini版・gpt-5.6-luna版)では、結果に影響する
ランダム性が複数あり、改善が「アルゴリズムの進化によるもの」か「乱数の
偶然によるもの」かを切り分けにくかった。特に以下が判明している:

- ゴールを毎回ランダムサンプリングしていたため、GP実行中にたまたま評価された
  少数のゴールに個体が過学習し、別のゴール群で再評価すると到達時間が
  有意に悪化するケースがあった
  ([gpt-4o-mini版チェックポイント検証](lattice_fork_minmax_30gen_checkpoint_verification_report.md))
- 親選択・生存選択がルーレット方式(適応度に比例した確率的選択)のため、
  同じ集団からでも実行のたびに異なる個体が選ばれる

本実験では、**このリポジトリ側のコードに由来するランダム性をすべて排除**し、
同じ条件で同じ選択が行われる状態でGP探索の効果を確認する。

## 変更点

### 1. 開始位置・ゴールの固定

| 項目 | 値 | 備考 |
|---|---|---|
| 開始位置(`start_pose`) | `(-11.8, 2.0, -0.00143)` | 従来から固定 |
| ゴール(`fixed_goal`) | `(16.5, 2.0, 0.0)` | **`goal_x_range`/`goal_y_range`を削除し固定** |

全個体・全世代・全反復(`repetitions_per_goal: 3`)が同じ1点のゴールを目指す。

### 2. 親選択: ルーレット → ランクピーリング(`parent_method: rank_pairing`)

`llm_gp/selection.py`の`rank_select_parent_pair`。集団を適応度順に並べ、
隣接する順位同士をペアにする。

- カップル1: 1位 × 2位
- カップル2: 3位 × 4位

同順位の場合は`elite_sort_key`(到達時間→経路長→経路生成時間→個体ID)で
順序を決めるため、同じ集団からは常に同じペアが作られる。

### 3. 生存選択: ルーレット → 上位固定数選択(`survivor_method: truncation`)

`llm_gp/selection.py`の`truncation_select_survivors`。親+子の全個体を
適応度順に並べ、上位`population_size_per_island`(10体)をそのまま次世代に
残す。1位がエリートになる。

### 4. 変異させる親の選択: ランダム → 順位の高い方

`mutation_only`および`crossover_and_mutation`で変異の対象にする親を、
従来は親1・親2からランダムに選んでいたが、`rank_pairing`設定時は
**常に順位の高い方の親**を選ぶ(`llm_gp/evolution.py`)。

## 残るランダム性

以下はこのリポジトリのコードでは制御できないため残る:

- **LLM(gpt-5.6-luna)の出力**: 同じプロンプトでも生成されるコードは毎回異なりうる
- **Gazebo/DWAの実行時の揺らぎ**: 物理演算の非決定性やタイミングにより、
  同じコード・同じゴールでも評価値は毎回わずかに変わる

## 実験設定

| 項目 | 値 |
|---|---|
| Config | `config/ros_gazebo_lattice_fork_minmax_30gen_deterministic.yaml` |
| マップ | lattice_fork |
| 世代数 | 30 |
| 集団構成 | 単一集団10個体(島なし) |
| 子生成 | 2ペア × 3個体(crossover_only/mutation_only/crossover_and_mutation) = 6個体/世代 |
| エリート数 | 1 |
| 親選択 | `rank_pairing`(決定的) |
| 生存選択 | `truncation`(決定的) |
| fitness正規化 | 世代内min-maxスケーリング(従来と同じ) |
| fitness重み | ノード展開数0.33 / 経路長0.34 / 到達時間0.33 |
| 評価 | `repetitions_per_goal: 3`、`timeout_seconds: 200`、ゴール固定 |
| LLM | `provider: openai`, `model: gpt-5.6-luna` |
| データ保存先 | `experiment_results/lattice_fork_minmax_30gen_deterministic/` |

従来のルーレット方式の設定(`parent_method: roulette` /
`survivor_method: roulette_without_replacement`)も引き続き使える。
既存のconfigはそのまま動く。

## 実行方法

事前に[wsl_gazebo_troubleshooting_runbook.md](wsl_gazebo_troubleshooting_runbook.md)の
Defender除外設定が有効であること、PCがスリープしない設定であることを確認する。

```powershell
wsl --shutdown
```

5秒ほど待ってから:

```powershell
wsl -d Ubuntu-20.04 -- bash -lc "cd /mnt/c/Users/adachi/catkin_ws && setsid nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_30gen_deterministic.yaml > gp_minmax_30gen_deterministic.log 2>&1 < /dev/null & disown"
```

起動後は`pgrep -af llm_gp.main`と新しい`run_<timestamp>`ディレクトリの
作成を必ず確認する。

## 実行後に確認するもの

- `experiment_results/lattice_fork_minmax_30gen_deterministic/run_<timestamp>/`
  - `analysis/generation_algorithm_report.md` — 世代ごとの最良個体・改善率
  - `analysis/repeat10_statistics.csv` / `repeat10_significance.csv` — 初期 vs 最良の10回検証
  - `analysis/token_usage_by_generation.csv` — LLMトークン消費
  - `analysis/best_algorithm/` — 最良個体のコードと差分

## 結果を読むときの注意

- ゴールが1点に固定されているため、得られた個体は**そのゴールに特化して
  最適化される**。別のゴールへの汎化性能はこの実験では評価されない。
  汎化性能を見たい場合は、GP後に異なるゴール群でチェックポイント検証を
  別途行う。
- GP直後の10回検証も同じ固定ゴールで行われるため、「固定ゴールでの
  再現性」の確認であり、汎化性能の確認ではない。
- 選択が決定的になったことで集団の多様性が失われやすく、早い世代で
  同じ系統ばかりが残る(早期収束)可能性がある。世代ごとの最良個体が
  長期間変わらない場合はこれが起きていると考えられる。

## 関連ファイル

- 実装: `llm_gp/selection.py`(`rank_select_parent_pair`, `truncation_select_survivors`)、
  `llm_gp/evolution.py`、`llm_gp/config.py`
- テスト: `tests/test_selection.py`
- ゴール固定のみ(選択はルーレット)の設定: `config/ros_gazebo_lattice_fork_minmax_30gen_fixed_goal.yaml`
- 比較対象: [gpt-5.6-luna版レポート](lattice_fork_minmax_30gen_gpt56luna_experiment_report.md)、
  [モデル比較レポート](lattice_fork_minmax_30gen_model_comparison_report.md)
