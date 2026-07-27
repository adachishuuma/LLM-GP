# Codex実装仕様書：LLM変異GP + ルーレット選択 + 島モデル

## 1. 目的

ROS/Gazeboで評価する経路計画アルゴリズムを個体として扱い、以下を組み合わせた進化計算フレームワークをPythonで実装する。

- GPによる親選択・交叉
- LLMを用いた変異
- 適応度に基づくルーレット選択
- 島ベースの母集団モデル
- SQLiteによる全個体・評価結果・変更履歴の保存

今回はDWAは変更せず、A*のみを改善対象とする。

アルゴリズムの具体的な改変方法は後から決めるため、交叉、LLM変異、ROS/Gazebo評価は交換可能なインターフェースとして実装する。最初はモックでも全体ループが動作するようにする。

---

## 2. 島の構成

4つの島を用意する。

すべての島でA*を改善対象とするが、各島は独立した母集団、乱数系列、親選択、交叉、LLM変異を持つ。

```text
island_1: A*母集団
island_2: A*母集団
island_3: A*母集団
island_4: A*母集団
```

各島は10世代の間、他島と個体を共有せず独立に進化する。

10世代ごとに、各島の最良個体を隣接島へコピーする。

---

## 3. 母集団管理

MAP-Elitesのセル分類は使用しない。

各島は固定数の個体を保持する。

```text
1島あたりの母集団サイズ: 10個体
島数: 4
全体の保持個体数: 40個体
```

各世代では、現在の親世代10個体と、新たに生成・評価した子個体15個体を合わせた25個体を次世代候補とする。

```text
親世代: 10個体
子個体: 15個体
候補合計: 25個体
```

次世代には次の10個体を残す。

1. 候補群の最高適応度個体を1個体、エリートとして必ず保存
2. 残り9個体を適応度比例ルーレット選択で選ぶ

同一個体を複数回選択しない「重複なしルーレット選択」を基本とする。

```text
次世代10個体
├─ エリート保存: 1個体
└─ ルーレット選択: 9個体
```

全生成個体は、次世代に残らなかった場合でもSQLiteへ保存する。

---

## 4. 初期個体

初期個体はすべて現在のA*ベースラインから作成する。

各島に10個体ずつ配置し、合計40個体とする。

ただし、完全に同一の40コピーとして扱うのではなく、個体ID、島ID、乱数シードを別々に付与する。

必要に応じて、初期ヒューリスティック重みなどにごく小さな差を設定できるようにする。

例:

```text
island_1: A1_01 ～ A1_10
island_2: A2_01 ～ A2_10
island_3: A3_01 ～ A3_10
island_4: A4_01 ～ A4_10
```

初期40個体をすべてROS/Gazeboまたはモック評価器で評価する。

---

## 5. 各世代で生成する個体

各島で1世代につき親ペアを5組選択する。

1組の親から次の3個体を生成する。

1. `crossover_only`
2. `mutation_only`
3. `crossover_and_mutation`

したがって、生成数は次のとおり。

```text
1島: 5組 × 3個体 = 15個体
4島: 15個体 × 4島 = 60個体/世代
```

---

## 6. 親選択

各島の現在の母集団10個体から、適応度比例ルーレット選択で親を決める。

初期設定:

```python
PARENT_PAIRS_PER_ISLAND = 5
POPULATION_SIZE = 10
```

親選択の流れ:

1. 島内10個体の適応度を取得する
2. 適応度を非負の選択重みに変換する
3. 重みに比例した確率で親1を選ぶ
4. 親1を除外して親2を選ぶ
5. 島内に1個体しかいない場合のみ同一個体を許可する
6. 同じ個体が複数の親ペアで選ばれることは許可する

適応度が0以下を含む場合に備え、選択重みは次のように補正する。

```python
epsilon = 1e-9
min_fitness = min(ind.fitness for ind in population)
offset = -min_fitness + epsilon if min_fitness <= 0 else 0.0
weight = individual.fitness + offset
```

すべての重みが0に近い場合は、一様ランダム選択へフォールバックする。

---

## 7. 子個体生成

親1と親2から以下を生成する。

### 7.1 交叉のみ

```text
Parent1 + Parent2
        ↓
     crossover
        ↓
     Child_C
```

```python
operator_type = "crossover_only"
```

### 7.2 LLM変異のみ

親1または親2をランダムに1個体選び、LLM変異する。

```text
Parent1 or Parent2
        ↓
   LLM mutation
        ↓
     Child_M
```

```python
operator_type = "mutation_only"
```

### 7.3 交叉 + LLM変異

```text
Parent1 + Parent2
        ↓
     crossover
        ↓
 intermediate child
        ↓
   LLM mutation
        ↓
     Child_CM
```

```python
operator_type = "crossover_and_mutation"
```

3個体はすべて別個体として検証・評価・保存判定する。

---

## 8. コード検証

ROS/Gazebo評価前に次を確認する。

1. ファイルが生成されている
2. 構文エラーがない
3. ビルド可能
4. 必須関数・クラスが存在する
5. 禁止されたファイルを変更していない
6. タイムアウトしない
7. NaN/Infを出す危険な処理がないか簡易確認
8. A*以外のDWA関連コードを変更していない
9. 変更許可範囲外のファイルを変更していない

無効な個体はROS/Gazeboで実行しない。

失敗理由はSQLiteへ保存する。

LLM修正の再試行は初期設定で1回までとする。

---

## 9. ROS/Gazebo評価

最初はモック評価器を実装し、後から実評価器へ差し替え可能にする。

```python
@dataclass
class EvaluationResult:
    success: bool
    planning_time: float | None
    path_length: float | None
    arrival_time: float | None
    error_message: str | None = None
    raw_log_path: str | None = None
```

実評価では複数ゴール・複数試行を行い、平均値を使用できるようにする。

初期設定では1ゴールにつき3回以上実行する。

```yaml
evaluation:
  repetitions_per_goal: 3
```

---

## 10. 適応度

3指標は小さいほど良い。

基準値に対して正規化し、重み付き和を計算する。

```python
planning_score = max(0.0, 1.0 - planning_time / planning_reference)
path_score = max(0.0, 1.0 - path_length / path_reference)
arrival_score = max(0.0, 1.0 - arrival_time / arrival_reference)

fitness = (
    planning_score * planning_weight
    + path_score * path_weight
    + arrival_score * arrival_weight
)
```

初期重み:

```yaml
fitness:
  planning_weight: 0.33
  path_weight: 0.34
  arrival_weight: 0.33
  planning_reference: 1.0
  path_reference: 30.0
  arrival_reference: 60.0
  failure_fitness: 0.0
```

評価失敗個体には`failure_fitness`を設定する。

---

## 11. 次世代選択

各島について、親10個体と子15個体を合わせた25個体から次世代10個体を選ぶ。

### 11.1 エリート保存

候補25個体の中で最も適応度が高い個体を必ず1個体保存する。

同一適応度の場合は、次の優先順位で決める。

1. 評価成功率が高い
2. 到達時間が短い
3. 経路長が短い
4. 経路生成時間が短い
5. 個体IDが小さい

### 11.2 残り9個体

エリートを除いた候補群から、適応度比例ルーレット選択を重複なしで行い、9個体を選ぶ。

候補数が9個体未満の場合は、存在する個体をすべて残す。

すべての適応度が同じ、または重みが0になる場合は、一様ランダム選択へフォールバックする。

---

## 12. 島間コピー

10世代ごとにリング型でコピーする。

```text
island_1
   ↓
island_2
   ↓
island_3
   ↓
island_4
   ↓
island_1
```

各島から1個体をコピーする。

```text
各島の最高適応度個体: 1個体
```

コピー元の個体は削除しない。

コピー個体には新しいIDを付け、以下を記録する。

- 元個体ID
- コピー元島
- コピー先島
- コピー世代
- ソースコード
- 評価値
- 適応度
- 変更履歴

コピー処理は、各島の通常世代更新が完了した後に行う。

コピー先では、現在の10個体とコピー個体1個体を合わせた11個体から次の10個体を選ぶ。

```text
候補11個体
↓
エリート1個体
+
重複なしルーレット選択9個体
↓
次世代10個体
```

重要:

- すべてのコピー元個体を先に確定する
- その後、コピー先へ一括登録する
- 同一の移住処理中に多段コピーが起きないようにする
- コピーは10、20、30、...世代でのみ実行する

---

## 13. データ構造

```python
@dataclass
class Individual:
    individual_id: str
    generation: int
    current_island: str
    origin_island: str
    source_path: str
    parent_ids: list[str]
    operator_type: str

    planning_time: float | None = None
    path_length: float | None = None
    arrival_time: float | None = None
    fitness: float | None = None

    valid: bool = True
    evaluation_success: bool = False
    error_message: str | None = None

    is_elite: bool = False
    selected_for_next_generation: bool = False
    change_history: list[str] = field(default_factory=list)
```

---

## 14. SQLite

最低限、次のテーブルを作成する。

### 14.1 individuals

- individual_id
- generation
- current_island
- origin_island
- operator_type
- source_path
- parent_ids
- valid
- evaluation_success
- is_elite
- selected_for_next_generation
- created_at

### 14.2 evaluations

- individual_id
- planning_time
- path_length
- arrival_time
- fitness
- success
- error_message
- raw_log_path
- evaluated_at

### 14.3 population_memberships

- island_name
- generation
- individual_id
- selection_method
- selected_at

### 14.4 migrations

- source_individual_id
- copied_individual_id
- source_island
- target_island
- generation
- migrated_at

### 14.5 change_history

- individual_id
- sequence_number
- operation_type
- description
- diff_text

### 14.6 llm_calls

- individual_id
- model_name
- prompt_text
- response_text
- success
- error_message
- created_at

---

## 15. インターフェース

### 15.1 評価器

```python
class Evaluator(Protocol):
    def evaluate(self, individual: Individual) -> EvaluationResult:
        ...
```

### 15.2 交叉

```python
class CrossoverOperator(Protocol):
    def crossover(
        self,
        parent1: Individual,
        parent2: Individual,
    ) -> Individual:
        ...
```

### 15.3 LLM変異

```python
class MutationOperator(Protocol):
    def mutate(
        self,
        individual: Individual,
        context: MutationContext,
    ) -> Individual:
        ...
```

---

## 16. LLMモデル

LLM変異には`gpt-4o-mini`を使用する。

OpenAI APIキーはプロジェクトルートの`.env`に保存されているものとして実装する。

```env
OPENAI_API_KEY=xxxxxxxxxxxxxxxx
OPENAI_MODEL=gpt-4o-mini
```

環境変数の読み込みには`python-dotenv`を使用する。

```python
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
```

初期実装では次の2種類を用意する。

- `MockMutationOperator`
- `OpenAIGPT4oMiniMutationOperator`

`OpenAIGPT4oMiniMutationOperator`はOpenAI Python SDKを利用する。

APIキーが存在しない場合は、警告を出してモック変異へフォールバックする。

`.env`はGit管理しない。

```gitignore
.env
```

---

## 17. ディレクトリ構成

管理しやすい構成を採用する。

## 18. 世代ループ

```python
for generation in range(1, max_generations + 1):

    for island in islands:
        parents = list(island.population)
        children = []

        for _ in range(parent_pairs_per_island):
            parent1 = roulette_select(parents)
            parent2 = roulette_select(
                parents,
                exclude_ids={parent1.individual_id},
            )

            child_c = crossover(parent1, parent2)

            mutation_parent = random.choice([parent1, parent2])
            child_m = llm_mutation(mutation_parent)

            intermediate = crossover(parent1, parent2)
            child_cm = llm_mutation(intermediate)

            generated = [child_c, child_m, child_cm]

            for child in generated:
                save_individual(child)

                if not validate(child):
                    child.fitness = failure_fitness
                    save_failure(child)
                    children.append(child)
                    continue

                result = evaluator.evaluate(child)
                child.evaluation_success = result.success

                if not result.success:
                    child.fitness = failure_fitness
                    save_evaluation(child, result)
                    children.append(child)
                    continue

                child.fitness = calculate_fitness(result)
                save_evaluation(child, result)
                children.append(child)

        candidates = parents + children

        island.population = select_survivors(
            candidates=candidates,
            population_size=10,
            elite_count=1,
            method="roulette_without_replacement",
        )

        save_population_membership(
            island=island,
            generation=generation,
        )

    if generation % 10 == 0:
        perform_ring_migration(
            islands=islands,
            generation=generation,
            migrants_per_island=1,
        )

    save_generation_summary(generation, islands)
```

---

## 19. 設定ファイル例

```yaml
evolution:
  max_generations: 50
  population_size_per_island: 10
  parent_pairs_per_island: 5
  elite_count: 1

islands:
  count: 4
  names:
    - island_1
    - island_2
    - island_3
    - island_4

selection:
  parent_method: roulette
  survivor_method: roulette_without_replacement
  epsilon: 1.0e-9
  avoid_same_parent_pair: true

migration:
  interval: 10
  migrants_per_island: 1
  migrant_selection: best
  topology: ring
  reevaluate_migrants: false

fitness:
  planning_weight: 0.33
  path_weight: 0.34
  arrival_weight: 0.33
  planning_reference: 1.0
  path_reference: 30.0
  arrival_reference: 60.0
  failure_fitness: 0.0

validation:
  max_repair_attempts: 1
  build_timeout_seconds: 180
  forbid_dwa_changes: true

evaluation:
  evaluator_type: mock
  repetitions_per_goal: 3
  timeout_seconds: 120

llm:
  provider: openai
  model: gpt-4o-mini
  fallback_to_mock: true

database:
  path: experiment_results/evolution.db

random_seed: 42
```

---

## 20. テスト要件

最低限、以下をpytestで確認する。

### 親選択

- 適応度が高い個体ほど高確率で選択される
- 適応度が低い個体にも選択確率が残る
- 親1と親2が可能な限り異なる
- すべての重みが0の場合に一様選択へフォールバックする

### 子生成

- 親ペア1組から必ず3種類の子が生成される
- `operator_type`が正しく記録される
- 1島1世代で15個体生成される
- 4島1世代で60個体生成される

### 次世代選択

- 親10個体と子15個体から10個体が選択される
- 最高適応度個体が必ず残る
- 残り9個体が重複なしルーレット選択される
- 母集団サイズが常に10個体になる

### 島間コピー

- 10世代ごとにのみ実行される
- 各島から最良1個体がコピーされる
- コピー元個体が削除されない
- リング方向が正しい
- 同一移住処理内で多段コピーが起きない
- コピー後も各島10個体になる

### 安全性

- DWA関連コードの変更が拒否される
- ビルド失敗個体がROS/Gazeboで実行されない
- NaN/Infを含む評価が失敗扱いになる

### DB

- 全生成個体がSQLiteへ保存される
- 次世代に残らなかった個体も履歴に残る
- 親ID、操作種別、LLM呼び出し、移住履歴が保存される

### 再現性

- 同じ乱数シードとモック評価器で同じ結果になる

---

## 21. 完了条件

以下でモック実験を実行できること。

```bash
python -m llm_gp.main --config config/default.yaml
```

実行後に確認できること:

- 4島が作成される
- 各島10個体、合計40個体の初期母集団が作成される
- 各世代で全体60個体が生成される
- `crossover_only`、`mutation_only`、`crossover_and_mutation`が記録される
- 各島の母集団サイズが常に10個体になる
- 各世代で最高適応度個体1個体が必ず残る
- 残り9個体が重複なしルーレット選択される
- 10世代ごとに各島の最良1個体が隣接島へコピーされる
- DWAコードは変更されない
- SQLiteに全個体、評価、履歴、LLM呼び出し、移住情報が残る
- 世代統計がCSVと標準出力へ保存される
- `pytest`が成功する

---

## 22. Codexへの最終指示

この仕様に基づき、まずモック評価器・モック交叉・モックLLM変異で全体ループが動く完成版を実装すること。

要件:

- Python 3.11以上
- 型ヒントを使用
- dataclassまたはPydanticを使用
- 設定値をハードコードしない
- 乱数シードを固定可能にする
- SQLiteへ保存する
- pytestを作成する
- READMEへ実行方法を書く
- ROS/Gazebo、LLM API、交叉処理は後から差し替え可能にする
- A*のみを改変対象とし、DWA関連コードは変更しない
- MAP-Elitesのセル分類は実装しない
- 親選択と次世代選択にルーレット選択を使用する
- 各世代でエリート1個体を必ず保存する
- 島間コピーは10世代ごとに行う
- 不明点は合理的な仮定を置き、モック版を先に完成させる
- 実装後、生成ファイル一覧、実行手順、主要設計を説明する
