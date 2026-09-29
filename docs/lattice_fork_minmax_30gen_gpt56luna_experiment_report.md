# Min-Maxスケーリング30世代実験レポート(GPT-5.6 Luna版、`run_20260927_082603`)

設計・実行方法は[lattice_fork_minmax_30gen_experiment.md](lattice_fork_minmax_30gen_experiment.md)を
参照。本レポートは`config/ros_gazebo_lattice_fork_minmax_30gen.yaml`の`llm.model`を
`gpt-4o-mini`から`gpt-5.6-luna`に変更して実行した結果をまとめる。前回の
gpt-4o-mini版は世代27/30で中断したが(
[lattice_fork_minmax_30gen_experiment_report.md](lattice_fork_minmax_30gen_experiment_report.md)、
[チェックポイント検証レポート](lattice_fork_minmax_30gen_checkpoint_verification_report.md))、
今回は**30世代すべてを完走**した。

## 実験設定

- Config: `config/ros_gazebo_lattice_fork_minmax_30gen.yaml`(`llm.model: gpt-5.6-luna`)
- マップ: lattice_fork
- 集団構成: 単一集団10個体(島なし)、2ペア×3個体=6個体/世代、最大30世代
- 評価: `repetitions_per_goal: 3`、ゴールは`goal_x_range=[15.0,16.5]`/`goal_y_range=[1.5,2.3]`からランダムサンプリング

## 実行結果の概要

- 2026-09-27〜09-29にかけて実行し、**30/30世代を完走**(クラッシュなし)
- 193個体を評価、142件成功(成功率73.6%)
- LLM呼び出し: 346回(成功228回)、モデル`gpt-5.6-luna`
- GP実行直後の自動検証(`--verify-repetitions 10`、デフォルト)も正常に完了

## 実行環境について

前回(gpt-4o-mini版)は世代27でWSLクラッシュ(GPU仮想化ドライバ関連のカーネル
トレース)により中断したため、再実行前に[wsl_gazebo_troubleshooting_runbook.md](wsl_gazebo_troubleshooting_runbook.md)
の手順(Windows Defenderの除外設定を再適用 + `wsl --shutdown`によるクリーン
再起動)を踏んでから実行した。今回はこの対策が功を奏し、30世代を通じて
クラッシュは発生しなかった。

### 世代0(初期個体)が一時的に全滅した件について

`generation_algorithm_report.md`によると、世代0〜5は成功率が著しく低い
(世代0は10個体中0成功、世代1〜5も新規子6体中0成功が続いた)。世代6で
初めて5/6が成功し、以降は安定して推移している。

初期個体(`ind_000001`、ヒューリスティック重み1.0・無変更)の失敗ログを
確認したところ、`move_base did not succeed: ACTIVE`(200秒タイムアウト)が
原因だった。この時のGazeboリアルタイムファクター(RTF)を実測したところ
**RTF≈0.99〜1.00と正常**だったため、以前発見したDefender起因のRTF低下問題
とは異なる現象である。GP実行の最終盤で自動実行された検証では、同じ
`ind_000001`が10回中10回とも余裕を持って(172秒前後で)成功しており、
問題は世代0〜5の実行タイミングに限定された一時的なものだったと考えられる。
原因はGazebo物理エンジンの非決定性やこの期間にたまたま偏った乱数ゴールの
可能性があるが、未特定。今後同様の傾向が出た場合は追加調査が必要。

この一時的な不調のため、世代0〜5では実質的に有効なfitness差がなく
(適応度はすべて0.0)、GP探索が実質的に始動したのは世代6以降と見るべきである。

## 性能改善(初期最良個体`ind_000001` → 実験全体最良個体`ind_000240`、世代29)

| 指標 | 初期最良 | 最良 | 改善率 |
|---|---:|---:|---:|
| ノード展開数(適応度で使用) | 59,488.0 | 8,045.0 | **+86.48%** |
| 到達時間 | 195.40秒 | 167.41秒 | +14.32% |
| 経路長 | 38.55 m | 37.46 m | +2.81% |
| 経路生成時間(参考・適応度には非使用) | 0.0275秒 | 0.0124秒 | +54.93% |

前回のgpt-4o-mini版(ノード展開数+48.5%)と比べ、**ノード展開数の改善幅が
大きく上回った**(+86.5%)。

## 10回再評価による統計的検証(GP実行直後に自動実行)

初期基準個体(`ind_000001`)と実験全体最良個体(`ind_000240`)をそれぞれ
10回ずつ再評価:

| 指標 | 初期個体平均 | 最良個体平均 | 改善率 | p値 | 有意(p<0.05) |
|---|---:|---:|---:|---:|:---:|
| **ノード展開数** | 42,502.7 | 8,311.6 | **+80.44%** | 1.08e-05 | **True** |
| 経路長 | 38.978 m | 38.080 m | +2.30% | 0.0452 | True(僅差) |
| 到達時間 | 172.558秒 | 171.827秒 | +0.42% | 0.7713 | False |
| 経路生成時間(参考) | 0.0224秒 | 1.7789秒 | -7828.79% | 0.9988 | False |

両個体とも10/10(100%)成功。

**統計的に最も頑健なのはノード展開数**(p<0.0001)。経路長は僅かに有意
(p=0.045)。到達時間は有意差なし(改善も悪化もしていないと言うのが正確)。
経路生成時間の`best_ind_000240`平均が1.78秒と異常に大きいのは10回中1回の
外れ値によるもの(標準偏差5.58が平均を上回る)で、fitness計算には使われない
参考指標のため実害はない。

前回のgpt-4o-mini版で発覚した「到達時間が実は有意に悪化していた」という
過学習の兆候は、**今回のGP直後10回検証では見られなかった**(悪化ではなく
「有意差なし」)。ただしこれはGP実行時と同じ`fixed_goal`周辺の1つの
ゴール範囲サンプリングに基づく検証であり、前回実施したような**複数の
異なるチェックポイント世代・複数の異なる乱数ゴール群での頑健性の検証**は
本レポート作成時点ではまだ行っていない。

## LLMトークン消費量

- LLM呼び出し回数: 346回(成功228回)
- prompt tokens: 755,282 / completion tokens: 748,735 / **合計: 1,504,017トークン**

世代ごとの内訳は`analysis/token_usage_by_generation.csv`を参照。

## 結論

- `gpt-5.6-luna`をLLMとして使用した30世代実験は、クラッシュなく完走し、
  gpt-4o-mini版より大きなノード展開数の改善(+86.5%、10回検証でも+80.4%、
  p<0.0001)を達成した。
- 到達時間はGP直後の検証では有意な変化なしという結果で、前回発覚した
  「到達時間の過学習(見かけ上の改善が実は悪化だった)」問題は今回は
  再現していない。ただし異なるゴール群での追加検証はまだ行っていないため、
  頑健性については留保が必要。
- 世代0〜5で一時的に成功率が著しく低下する現象が見られたが、原因は
  リアルタイム性能低下(既知のDefender問題)ではないことを確認済み。
  未解明のまま残っている。

## 関連ファイル

- 実行ログ: `gp_minmax_30gen_gpt56luna.log`
- 全体レポート(自動生成): `experiment_results/lattice_fork_minmax_30gen/run_20260927_082603/analysis/generation_algorithm_report.md`
- 指標別比較(自動生成): `experiment_results/lattice_fork_minmax_30gen/run_20260927_082603/analysis/metric_comparison.md`
- 10回検証生データ: `analysis/repeat10_statistics.csv` / `analysis/repeat10_significance.csv`
- LLMトークン内訳: `analysis/token_usage_by_generation.csv`
- 最良個体: `analysis/best_algorithm/best_algorithm.cpp`(`ind_000240`、世代29)
- 前回(gpt-4o-mini版)との比較: [lattice_fork_minmax_30gen_experiment_report.md](lattice_fork_minmax_30gen_experiment_report.md)、
  [チェックポイント検証レポート](lattice_fork_minmax_30gen_checkpoint_verification_report.md)
- トラブルシューティング手順書: [wsl_gazebo_troubleshooting_runbook.md](wsl_gazebo_troubleshooting_runbook.md)
