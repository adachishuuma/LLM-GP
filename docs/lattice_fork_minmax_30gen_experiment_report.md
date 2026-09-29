# Min-Maxスケーリング30世代実験レポート(`run_20260908_110059`)

設計・実行方法は[lattice_fork_minmax_30gen_experiment.md](lattice_fork_minmax_30gen_experiment.md)を参照。
本レポートは実際に実行した結果(世代27/30で中断)をまとめる。

**このレポートは`scripts/verify_generation_gain.py`のバグ修正後に書き直したものです。**
修正前は初期基準個体を誤って`ind_000002`としており、それとの比較では4指標すべてが
有意という結論になっていましたが、これは誤りでした。詳細は下記「検証スクリプトの
バグとその修正」を参照。

## 実験設定(再掲)

- Config: `config/ros_gazebo_lattice_fork_minmax_30gen.yaml`
- マップ: lattice_fork
- 集団構成: 単一集団10個体(島なし)、2ペア×3個体=6個体/世代、最大30世代
- 評価: `repetitions_per_goal: 3`、ゴールは`goal_x_range=[15.0,16.5]`/`goal_y_range=[1.5,2.3]`からランダムサンプリング

## 実行結果の概要

- 2026-09-08 20:01〜09-10 00:15(約28時間)実行し、**世代27/30まで完了**
- 174個体を評価、157件成功(成功率90.2%)
- 世代28〜30の途中(個体233のビルド中)でWindows/WSLの再起動に巻き込まれ中断
  (`dmesg`にsystemd-shutdown〜再起動の形跡あり、GPコード自体のバグではない)
- 途中終了のため`generate_report()`が自動実行されておらず、手動で
  `python3 -m llm_gp.report --database ... --config ...`を実行してレポートを生成した

## 実行環境の問題と対応(概要)

本実験も、コードとは無関係な環境要因(Gazeboの実時間性能低下)でつまずいた。
5gen実験の時と症状は同じだが原因・対処が異なった:

- Gazeboの実時間係数(RTF)が0.02〜0.11程度まで低下し、大半の評価がタイムアウト
- ログ削除・Windows完全再起動・時刻ソース変更(`tsc`→`hyperv_clocksource_tsc_page`、
  むしろ悪化したため復元)・`.wslconfig`での固定メモリ設定・`wsl --update`(既に
  最新版で効果なし)を試したがいずれも解消せず
- **最終的に、Windows Defenderの除外設定(`vmmem`/`vmmemWSL`/`wsl.exe`/
  `wslhost.exe`プロセスと`catkin_ws`フォルダ)を追加したところ解消し、
  世代27まで成功率90.2%で完走した**
- 詳しい診断手順・対策一覧は[wsl_gazebo_troubleshooting_runbook.md](wsl_gazebo_troubleshooting_runbook.md)
  にまとめてある

## 検証スクリプトのバグとその修正

`scripts/verify_generation_gain.py`(および`llm_gp/main.py`のGP実行後自動検証)は、
`llm_gp.report.load_rows()`が返す生データをそのまま`select_baseline_and_best()`に
渡していた。これは5gen実験レポートで発覚した`llm_gp/report.py`のバグと**全く同じ
原因**によるもので、`generate_report()`側は既に修正されていたが、この2箇所は
未修正のまま残っていた。

- 世代0の個体は、生存し続ける限り毎世代min-maxスケーリングで再採点される
  (`EvolutionEngine._score_individuals`)
- `evaluations.fitness`列は**その個体が最後に再採点された時点の値**しか保持しない
- そのため生データから「世代0で最も適応度が高かった個体」を選ぶと、本来の
  世代0時点の値ではなく後の世代で上書きされた値で比較してしまい、**誤った個体を
  選ぶ**ことがある

本実験でも実際にこれが発生しており、修正前は`ind_000002`(fitness=0.710645、
生データ上の見かけの値)を初期基準個体として選んでいたが、修正後は
`ind_000008`(fitness=0.876947、世代0時点で正しく再計算した値)が選ばれた。

**修正内容**: `llm_gp/report.py`に`load_rows_with_corrected_generation_zero_fitness()`
を新設し、`generate_report()`・`llm_gp/main.py`・`scripts/verify_generation_gain.py`の
3箇所すべてがこの関数経由でデータを取得するように統一した。回帰テスト
(`tests/test_report.py::test_select_baseline_and_best_requires_generation_zero_corrected_fitness`)
も追加済み。

## 世代比較(`ind_000008`基準、レポート修正後)

- 初期基準個体: `ind_000008`
- 実験全体の最良個体: `mig_000172`(世代20、`migration_copy`)

| 指標 | 初期最良(`ind_000008`) | 実験全体最良(`mig_000172`) | 改善率 |
|---|---:|---:|---:|
| 経路生成時間 [秒](参考・適応度には非使用) | 0.022104 | 0.019890 | +10.02% |
| ノード展開数(適応度で使用) | 39,054.7 | 20,095.7 | +48.54% |
| 経路長 [m] | 39.302009 | 38.008870 | +3.29% |
| 到達時間 [秒] | 174.603897 | 167.971967 | +3.80% |

世代12(`ind_000101`)〜世代27まで、上位個体がほぼ固定(`ind_000111`、後に
`mig_000172`)で推移しており、世代7以降は実質的に改善が頭打ちになっている。

## 10回再評価による統計的検証(修正後、正しい比較)

`ind_000008`(初期基準)と`mig_000172`(実験全体最良)をそれぞれ10回ずつ
再評価した結果:

| 指標 | 初期個体平均 | 最良個体平均 | 改善率 | p値 | 有意(p<0.05) |
|---|---:|---:|---:|---:|:---:|
| ノード展開数 | 41,958.3 | 20,426.7 | +51.32% | <0.0001 | **True** |
| 経路生成時間(参考) | 1.6781 | 0.0191 | +98.86% | 0.2079 | False |
| 経路長 | 38.9997 | 38.8380 | +0.41% | 0.5993 | False |
| 到達時間 | 176.0177 | 172.9652 | +1.73% | 0.1150 | False |

**統計的に頑健なのはノード展開数のみ**(約51%削減、p<0.0001)。経路長・
到達時間の数%の改善はp値からGazebo評価ノイズの範囲と区別できず、有意とは
言えない。経路生成時間は1回の試行で極端に大きい値が混入し平均が歪んでおり
(fitnessには使われない参考指標のため実害はない)、この数値をそのまま解釈すべきではない。

修正前(誤って`ind_000002`と比較していた版)ではこの4指標すべてが有意という
結論になっていたが、**これは誤った基準個体との比較によるアーティファクトであり、
撤回する。**

## LLMトークン消費量

世代27までの実行で:

- LLM呼び出し回数: 347回(成功123回)
- prompt tokens: 839,854 / completion tokens: 415,061 / **合計: 1,254,915トークン**

世代ごとの内訳は`analysis/token_usage_by_generation.csv`を参照。

## 結論

- min-maxスケーリング方式のfitness計算自体は妥当に機能しており、初期集団と
  比べてノード展開数を約半分に削減する個体を探索できた。この改善は10回再評価でも
  統計的に頑健(p<0.0001)。
- 一方、経路長・到達時間の改善は数%程度に留まり、統計的にはGazebo評価ノイズと
  区別できない。「3指標すべてで改善した」と単純に主張することはできない。
- 世代7付近から改善が頭打ちになっており、単一集団10個体という規模のGP探索では
  ノード展開数以外の指標を大きく動かすには不十分だった可能性がある。
- 実行基盤(WSL2+Gazebo)側の問題(Defenderによる実時間性能低下)と、分析コード側の
  バグ(世代0個体のfitness上書き)という、2種類の異なる問題が本実験を通じて
  発見・修正された。後者は5gen実験でも一度発覚していたにもかかわらず、
  検証スクリプト側には修正が伝播していなかった教訓が残る(共通関数化と回帰テストで対応済み)。

## 関連ファイル

- 手順書: [wsl_gazebo_troubleshooting_runbook.md](wsl_gazebo_troubleshooting_runbook.md)
- 設計文書: [lattice_fork_minmax_30gen_experiment.md](lattice_fork_minmax_30gen_experiment.md)
- 世代・個体データ: `experiment_results/lattice_fork_minmax_30gen/run_20260908_110059/`
  - `analysis/generation_algorithm_report.md` — 世代ごとの詳細
  - `analysis/metric_comparison.md` — 指標別の世代比較
  - `analysis/token_usage_by_generation.csv` — 世代ごとのLLMトークン消費
  - `analysis/best_algorithm/` — 実験全体最良個体(`mig_000172`)の情報
  - `analysis/repeat10_statistics.csv` / `repeat10_significance.csv` — 10回比較の生データ(`ind_000008` vs `mig_000172`、修正後の正しい組み合わせ)
- レポート修正コミット対象: `llm_gp/report.py`(`load_rows_with_corrected_generation_zero_fitness`を新設)、
  `llm_gp/main.py`、`scripts/verify_generation_gain.py`、`tests/test_report.py`(回帰テスト追加)
