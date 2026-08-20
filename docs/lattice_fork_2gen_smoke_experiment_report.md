# 2世代smokeテスト実験レポート(`run_20260816_105513`)

## 目的

再設計したLLMオペレータ(フィードバック付き変異・meaningful-changeゲート・
LLMベースcrossover。設計詳細は
[llm_operator_meaningful_change_design.md](llm_operator_meaningful_change_design.md)参照)が、
実際に**アルゴリズム本体へ意味のある変更**を加えられるかを、統計的な厳密さより
ターンアラウンドを優先した小規模構成で確認する。

## 実験設定

- Config: `config/ros_gazebo_lattice_fork_2gen_smoke.yaml`
- マップ: lattice_fork(`run_20260811_065434`/`run_20260815_111026`と同一)
- 世代数: 2(`max_generations: 2`)
- 個体数: 4島 × 2個体(`population_size_per_island: 2`)
- 評価: `repetitions_per_goal: 1`(GP実行中の評価は速度優先で1回のみ。統計的な確認は
  実験後に別途10回比較で行う)
- fitness基準値: `run_20260811_065434`の実測データから校正済み
  (`expansion_reference=45000`, `arrival_reference=200`, `path_reference=41.0`)
- 実行コマンド:
  ```bash
  python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_2gen_smoke.yaml --verify-repetitions 0
  ```

## 世代ごとの結果

| 世代 | 成功/評価 | 平均適応度 | 世代最良個体 | 演算 | 最良適応度 | 初期最良比 |
|---:|---:|---:|---|---|---:|---:|
| 0 | 8/8 | 0.0781 | `ind_000006` | `initial` | 0.11970 | +0.00% |
| 1 | 10/12 | 0.0788 | `ind_000024` | `crossover_and_mutation` | 0.12760 | +6.61% |
| 2 | 10/12 | 0.0819 | `ind_000037` | `crossover_only` | 0.16140 | +34.84% |

## コード変化(実際に何が変わったか)

### 世代1: `ind_000024`(crossover_and_mutation、2回目の試行で採択)

変更履歴: `Relaxed A* crossover intermediate created | OpenAI gpt-4o-mini mutation (2 generation attempt(s))`

1回目の生成は恐らくmeaningful-changeゲートで拒否され、2回目でヒューリスティック項に
新しい係数を追加する変更が採択された:

```cpp
float modified_weight = 1.5f;
queue_.emplace_back(next_i, potential[next_i]
    + distance * neutral_cost_ * tBreak * modified_weight * kLlmGpHeuristicWeight);
```

`f(n) = g(n) + h(n)`のh(n)(ヒューリスティック項)を1.5倍に強め、探索をより
ゴール方向へ貪欲にする「weighted A*」的なチューニング。副次的に`push_back`から
`emplace_back(next_i, ...)`への書き換えも、今回は引数を直接渡す形になっており
(以前のような無意味な書き換えではなく)実質的な最適化になっている。

### 世代2: `ind_000037`(crossover_only、meaningful-changeゲートが実際に発動)

変更履歴: `OpenAI gpt-4o-mini crossover merge failed after 2 attempt(s) (Generated source is identical to the input aside from comments/whitespace); fell back to averaging heuristic weights to 1.00000000`

LLMによるcrossoverマージが2回とも「実質無変更」の応答を返したためゲートに拒否され、
決定論的フォールバック(親の重み平均)へ切り替わった。親1が`ind_000024`
(=上記の`modified_weight`変更を持つ個体)だったため、フォールバック後もその改善は
維持されている。**ゲートが「怪しい変更」を実際に検出・排除した実例**であり、
新しい仕組みが設計通り機能していることを裏付けている。

## 初期個体 vs 最良個体: 10回再評価による検証

GP実行中の評価は1回のみ(ノイズが大きい)なので、`ind_000006`(初期基準)と
`ind_000037`(最良)をそれぞれ独立に10回ずつ再評価して比較した
(`scripts/verify_generation_gain.py run_20260816_105513 --repetitions 10`)。

| 指標 | 初期個体平均 | 最良個体平均 | 改善率 | p値 | 有意(p<0.05)? |
|---|---:|---:|---:|---:|:---:|
| **node_expansions** | 42318.5 | 31834.1 | **+24.77%** | **0.0000** | **True** |
| path_length | 39.82 | 39.96 | -0.34% | 0.66 | False |
| arrival_time | 178.02 | 176.82 | +0.68% | 0.34 | False |
| planning_time | 0.0224 | 0.0207 | +7.57% | 0.29 | False |

両個体とも10/10成功。`node_expansions`(ヒューリスティック重みが直接効く指標)が
**24.77%減、p<0.001で統計的に有意**。`path_length`/`arrival_time`は有意差なし
(ノイズの範囲内)で、「探索効率は上がったが経路の質はほぼ変わらない」という
理論通りの結果になっている。

参考: GP実行中に記録された単発評価のfitness(初期0.1197 → 最良0.1614、+34.84%)と、
10回平均から計算したfitness(初期0.0657 → 最良0.1435)は数値が異なる。これは
GP実行中が1回評価のみでノイズの影響を強く受けているためで、**改善の「方向」は
一致しているが「大きさ」は単発評価だけでは信頼できない**ことを示している
(まさにこの10回比較のステップが必要な理由)。

## 結論

- 新しいLLMオペレータは、少なくとも今回の2世代・小規模実行において
  **意味のある(かつ統計的に裏付けられた)アルゴリズム改善**を生成できることを確認した
- meaningful-changeゲートは実際に「実質無変更の応答」を検出・拒否し、
  安全なフォールバックへ切り替える様子が観測できた
- 5世代・10世代実験(旧オペレータ)では一度も見られなかった、p<0.05の統計的有意差が
  今回初めて得られた
- サンプルサイズが小さい(2世代・各島2個体)ため、この結果だけで再設計の効果を
  最終確認したとは言えない。より大規模な実験(10世代など)での再検証が望ましい

## 関連ファイル

- 実行ログ: `gp_2gen_smoke.log`
- 世代・個体データ: `experiment_results/lattice_fork_2gen_smoke/run_20260816_105513/`
  - `analysis/generation_algorithm_report.md` — 世代ごとの詳細
  - `analysis/generation_001_best_ind_000024.diff` / `generation_002_best_ind_000037.diff` — 実際のコード差分
  - `analysis/repeat10_statistics.csv` / `repeat10_significance.csv` — 10回比較の生データ
- 仕組みの設計: [llm_operator_meaningful_change_design.md](llm_operator_meaningful_change_design.md)
