# 30世代実験 モデル比較レポート(gpt-4o-mini版 vs gpt-5.6-luna版)

同一設定(`config/ros_gazebo_lattice_fork_minmax_30gen.yaml`、単一集団10個体・
2ペア×3個体=6個体/世代・世代内min-maxスケーリング)で、LLM crossover/mutationに
使うモデルだけを変えて実行した2回の30世代実験を比較する。

| | gpt-4o-mini版 | gpt-5.6-luna版 |
|---|---|---|
| runディレクトリ | `run_20260908_110059` | `run_20260927_082603` |
| 完了世代数 | **27/30**(WSLクラッシュで中断) | **30/30**(完走) |
| 評価個体数/成功数 | 174体中157件成功(90.2%) | 193体中142件成功(73.6%) |
| 初期基準個体 | `ind_000008` | `ind_000001` |
| 実験全体最良個体 | `mig_000172`(世代20、`ind_000111`と同一コード) | `ind_000240`(世代29) |
| LLM呼び出し回数 | 347回(成功123回) | 346回(成功228回) |
| LLM消費トークン | 1,254,915(prompt 839,854 / completion 415,061) | 1,504,017(prompt 755,282 / completion 748,735) |

詳細はそれぞれ[gpt-4o-mini版レポート](lattice_fork_minmax_30gen_experiment_report.md)、
[gpt-5.6-luna版レポート](lattice_fork_minmax_30gen_gpt56luna_experiment_report.md)を参照。

## 10回再評価による統計的検証の比較

両実験とも「初期基準個体 vs 実験全体最良個体」を10回ずつ再評価している
(gpt-4o-mini版は修正済みの正しい基準個体`ind_000008`との比較、
[経緯](lattice_fork_minmax_30gen_experiment_report.md#検証スクリプトのバグとその修正)参照)。

| 指標 | gpt-4o-mini: 初期→最良 | gpt-4o-mini 改善率(p値, 有意) | gpt-5.6-luna: 初期→最良 | gpt-5.6-luna 改善率(p値, 有意) |
|---|---:|---:|---:|---:|
| ノード展開数 | 41,958 → 20,427 | **+51.32%**(p<0.0001, ✅) | 42,503 → 8,312 | **+80.44%**(p=1.1e-05, ✅) |
| 経路長 | 39.00 m → 38.84 m | +0.41%(p=0.599, ❌) | 38.98 m → 38.08 m | +2.30%(p=0.045, ✅僅差) |
| 到達時間 | 176.02秒 → 172.97秒 | +1.73%(p=0.115, ❌) | 172.56秒 → 171.83秒 | +0.42%(p=0.771, ❌) |
| 経路生成時間(参考) | 1.678秒 → 0.019秒 | +98.86%(p=0.208, ❌) | 0.022秒 → 1.779秒 | -7828.79%(p=0.999, ❌) |
| 両個体の成功率 | 10/10 vs 10/10 | - | 10/10 vs 10/10 | - |

(経路生成時間はfitness計算に使われない参考指標。両実験とも10回中1回の
外れ値が平均を大きく歪めており、解釈には注意が必要——gpt-4o-mini版は
初期個体側、gpt-5.6-luna版は最良個体側に外れ値が出ている。)

## 比較から分かること

1. **ノード展開数の改善はどちらのモデルでも統計的に頑健**(p<0.0001水準)
   だが、**gpt-5.6-luna版の方が改善幅が大きい**(+51%に対し+80%)。ただし
   世代数(27 vs 30)・完走の有無が異なるため、モデルの優劣そのものを
   直接比較しているわけではない点に注意。
2. **経路長の改善は両モデルで小さい**(1%未満〜2.3%)。gpt-4o-mini版は
   有意差なしだったのに対し、gpt-5.6-luna版はp=0.045とぎりぎり有意判定
   だが、実用上の差(0.9m弱)としては小さい。
3. **到達時間は両モデルとも有意な改善なし**(p=0.115、p=0.771)。
   gpt-4o-mini版は追加の[チェックポイント検証](lattice_fork_minmax_30gen_checkpoint_verification_report.md)で
   「実は有意に悪化していた」ことが判明しているが、gpt-5.6-luna版では
   同様のチェックポイント検証はまだ実施していない(GP直後の1回の
   10回検証のみ)。
4. 両モデルとも**探索効率(ノード展開数)は確実に改善するが、実際の
   走行性能(経路長・到達時間)への波及効果は限定的、または見かけ上の
   ものに留まる**、という共通の傾向が見える。これはmin-maxスケーリングの
   問題ではなく(両実験で動作を確認済み)、各指標の母集団内バラツキの
   大きさの違い(ノード展開数は個体間で数百%動くのに対し、経路長・到達時間は
   マップの構造上10%未満しか動かない)に起因すると考えられる
   (詳細は[チェックポイント検証レポート](lattice_fork_minmax_30gen_checkpoint_verification_report.md)参照)。

## 未実施・今後の検討事項

- gpt-5.6-luna版の最良個体(`ind_000240`)についても、gpt-4o-mini版で
  行ったのと同じ「GP実行時とは異なる新しいランダムゴール群での
  10回再検証」(過学習チェック)はまだ実施していない。現時点の
  「有意差なし」がgpt-4o-mini版のような「実は悪化」に転じるかは未確認。
- 世代数が27 vs 30と揃っていないため、両モデルの真の性能差を見るには
  同じ世代数(例: どちらも27世代、または両方30世代完走)での比較が望ましい。
- gpt-4o-mini版は世代0〜27を通じて概ね安定していたが、gpt-5.6-luna版は
  世代0〜5で一時的に成功率が著しく低下する現象があった(原因未解明、
  Defender由来のRTF低下ではないことは確認済み)。この違いがモデル特性
  (生成するコードの傾向)によるものか、単なる環境要因かは未検証。

## 関連ファイル

- [lattice_fork_minmax_30gen_experiment_report.md](lattice_fork_minmax_30gen_experiment_report.md) — gpt-4o-mini版レポート
- [lattice_fork_minmax_30gen_checkpoint_verification_report.md](lattice_fork_minmax_30gen_checkpoint_verification_report.md) — gpt-4o-mini版のチェックポイント(過学習)検証
- [lattice_fork_minmax_30gen_gpt56luna_experiment_report.md](lattice_fork_minmax_30gen_gpt56luna_experiment_report.md) — gpt-5.6-luna版レポート
- 生データ:
  - `experiment_results/lattice_fork_minmax_30gen/run_20260908_110059/analysis/repeat10_statistics.csv` / `repeat10_significance.csv`
  - `experiment_results/lattice_fork_minmax_30gen/run_20260927_082603/analysis/repeat10_statistics.csv` / `repeat10_significance.csv`
