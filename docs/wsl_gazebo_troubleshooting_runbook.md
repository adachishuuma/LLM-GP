# WSL/Gazebo実験 トラブルシューティング手順書

長時間(数時間〜数十時間)のros_gazebo実験(`llm_gp.main`)を走らせる際に
実際に遭遇した問題と、その診断・対処手順をまとめたもの。次回同じ症状が
出たときはこのドキュメントから該当箇所を辿ればよい。

## 1. まず状態を確認する

```powershell
wsl -l -v                     # WSLディストロが動いているか
```

```bash
# WSL内で
pgrep -af 'llm_gp.main|gzserver|roslaunch|rosmaster'   # 実験プロセスが生きているか
```

実験プロセスが生きていれば、進行中のrunディレクトリのDBを見て世代の
進み具合を確認する(`experiment_results/<experiment_name>/run_<timestamp>/`)。

```bash
python3 - "<dbファイルパス>" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print("individuals:", con.execute("SELECT COUNT(*) FROM individuals").fetchone()[0])
print("evaluations:", con.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0])
print("success evals:", con.execute("SELECT COUNT(*) FROM evaluations WHERE success=1").fetchone()[0])
print("by generation:", con.execute("SELECT generation, COUNT(*) FROM individuals GROUP BY generation").fetchall())
PYEOF
```

## 2. 症状: 評価がほぼ全部タイムアウトで失敗する(Gazeboの実時間性能低下)

### 症状

- `ros_logs/*.json` の `error_message` が軒並み
  `"move_base did not succeed: ACTIVE"`、`arrival_time` がほぼ
  `timeout_seconds`(例: 200.0x秒)に張り付く
- 個体の優劣に関係なく**ほぼ全個体が同じ理由で失敗**する

### 診断: 「遅い」のか「止まっている」のかを切り分ける

roslaunchの出力ログ(`*.roslaunch.log`)には毎行
`[wall_time, sim_time]` のタイムスタンプが付く。壁時計に対して
シミュレーション内時間がどれだけ進んでいるか(リアルタイムファクター、RTF)
を見れば一目で分かる。

```bash
python3 - "<roslaunch.logのパス>" <<'PYEOF'
import re, sys
pattern = re.compile(r"\[(\d+\.\d+), (\d+\.\d+)\]")
points = []
with open(sys.argv[1], errors="ignore") as f:
    for line in f:
        m = pattern.search(line)
        if m:
            points.append((float(m.group(1)), float(m.group(2))))
if not points:
    print("no timestamps found")
else:
    w0, s0 = points[0]
    w1, s1 = points[-1]
    print(f"wall elapsed={w1-w0:.1f}s  sim elapsed={s1-s0:.1f}s  RTF={((s1-s0)/(w1-w0)) if w1>w0 else float('nan'):.4f}")
PYEOF
```

正常なら RTF はおおむね 0.5〜1.5 程度。**RTFが0.01〜0.1のオーダーまで
落ちていたら、これがタイムアウトの直接原因。** 個体のアルゴリズム自体の
良し悪しとは無関係なので、GP側のコードを疑う前にここを確認する。

同時に、ROS/Gazebo抜きの「ただのCPU計算ループ」を測ってVM全体が
遅いのか、Gazebo実行時だけ遅いのかも切り分けられる:

```bash
time python3 -c "
n = 0
for i in range(30_000_000):
    n += i * i
"
```

`real`(壁時計)が`user`(実際に使ったCPU時間)よりずっと大きければ、
プロセスがCPUを要求しているのにスケジューリングされていない=VMまたは
ホスト側の問題。両者がほぼ一致するなら、単純計算は正常でGazebo実行時
特有の問題(下記の対策1が濃厚)。

### 試して効果がなかったもの(切り分け済み)

これらは今回のセッションで実際に試したが、単独では直らなかった。
再度ハマったときに無駄に時間をかけないための記録:

- `~/.ros/log` の肥大化 (`du -sh` で数GB) を削除する
  → ディスク使用量警告は出るが、これが原因ではなかった
- `wsl --shutdown` によるWSLの再起動
- Windows本体の完全な再起動(`wsl --shutdown`よりは効いた形跡があるが、
  単独では不十分だった)
- 時刻ソースの変更 (`tsc` → `hyperv_clocksource_tsc_page`):
  ```bash
  wsl -d Ubuntu-20.04 -u root -- bash -c \
    "echo hyperv_clocksource_tsc_page > /sys/devices/system/clocksource/clocksource0/current_clocksource"
  ```
  → **むしろ悪化した(起動シーケンスがほぼ完全に停止)。元の`tsc`に戻すこと。**
- `%USERPROFILE%\.wslconfig` で `memory=`/`processors=` を固定値にする
  (Hyper-Vのダイナミックメモリ対策) → 効果不明瞭、WSL自体が指示なく
  再起動する不安定化が見られたため削除した
- `wsl --update`(カーネル更新) → 既に最新版だったため効果なし

### 効いた(と思われる)対策: Windows Defenderの除外設定

このマシンは「Windows 11 Enterprise」+ Microsoft Defender for
Endpoint(組織管理のEDR、`MsSense`/`SenseIR`/`SenseNdr`/`SenseTVM`が常駐)
が入っている。WSL2は1回の評価あたり大量の短命プロセス(roslaunch、
gzserver、ビルド時の各コンパイラ呼び出し、監視用の`rosservice`/`rosnode`
呼び出し等)を生成するため、Defenderの挙動監視がその都度スキャンする
ことでVM/プロセスのスケジューリングが強く遅延している可能性が高い。

**管理者権限のPowerShellで以下を実行する:**

```powershell
Add-MpPreference -ExclusionProcess "vmmem"
Add-MpPreference -ExclusionProcess "vmmemWSL"
Add-MpPreference -ExclusionProcess "wsl.exe"
Add-MpPreference -ExclusionProcess "wslhost.exe"
Add-MpPreference -ExclusionPath "C:\Users\adachi\AppData\Local\wsl\{369f2686-4212-4e20-a9fc-a1b67fbc38a7}"
Add-MpPreference -ExclusionPath "C:\Users\adachi\catkin_ws"
```

適用後は `wsl --shutdown` してから再度実験を起動する。

**注意**: `Add-MpPreference`はエラーなく完了しても、組織管理の
Tamper Protection/ポリシーによりサイレントに無視されることがある
(実際に一度、再適用したのに効果が見られなかったことがあった)。
非管理者セッションからは `Get-MpPreference` で除外リストを確認できない
(`N/A: Must be an administrator to view exclusions`と出る)ため、
本当に反映されているかは以下のGUIで確認するのが確実:

「Windowsセキュリティ」→「ウイルスと脅威の防止」→
「ウイルスと脅威の防止の設定」→「設定の管理」→「除外」→
「除外の追加または削除」を開き、上記のプロセス/パスが実際に
一覧に出ているか目視確認する。

### 実績

2026-09-08〜09-10にかけて、上記の除外設定を適用した状態で
30世代実験(`ros_gazebo_lattice_fork_minmax_30gen.yaml`)を実行したところ、
世代27まで(157/174件、90.2%)正常に成功した実績がある
(`experiment_results/lattice_fork_minmax_30gen/run_20260908_110059/`)。
以前の90%前後という成功率の水準に一致しており、根本原因はほぼ
Defenderの挙動監視だったと考えられる。

## 3. 実験が長時間の途中で止まっていた場合

### 原因の切り分け

`dmesg`(WSL内)にシャットダウン/再起動の形跡がないか確認する:

```bash
dmesg | grep -iE 'systemd-shutdow|unmounting filesystem|WaitForBootProcess'
```

`Received SIGTERM from PID 1 (systemd-shutdow)` や
`EXT4-fs: unmounting filesystem` の直後に再起動ログが続いていれば、
Windows本体のスリープ/再起動でWSLごと巻き込まれて中断されたと分かる
(実験コード自体のバグではない)。

### 中断されたrunのデータを活かす

`llm_gp.main`は世代ループの途中で強制終了されると、そこまでの
individuals/evaluationsはDBにコミット済みだが、`generate_report()`
(実行完了後にのみ呼ばれる)は走っていない。既存のDBに対して手動で
レポートを生成できる:

```bash
python3 -m llm_gp.report \
  --database "<run_dir>/roulette_ros_<experiment_name>.db" \
  --output "<run_dir>/analysis" \
  --config "<run_dir>/ros_gazebo_<experiment_name>.yaml"
```

世代数が目標に届いていなくても、成功率が健全(概ね80%以上)であれば
分析上は十分価値のあるデータになる。全世代を最初からやり直すか、
途中までのデータで妥協するかはこの時点のデータ量と時間的制約で判断する
(現行の`llm_gp`にはrun再開/チェックポイント機能はなく、必ず世代0から
やり直しになる)。

## 4. 長時間実行中にWindowsがスリープ/再起動しないようにする

30世代規模の実験は数十時間かかるため、PCが自動スリープすると
そのまま実験が巻き込まれて死ぬ。事前に以下を確認/設定しておく:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

Windows 11のModern Standby(S0 Low Power Idle)は上記のスリープ設定を
無効化しても発動することがあるため、長時間実験中は物理的に
「PCをスリープ/再起動しない」よう気をつける(自動更新の再起動タイミング
にも注意)。

## 5. このセッションで踏んだ運用上の落とし穴

実験の中身とは関係ないが、繰り返しハマったので記録しておく。

- **`scripts/restart_wsl_and_run.ps1`経由の起動が、環境によっては
  サイレントに失敗することがある**(`wsl --shutdown`直後の
  `wsl -d <distro> -- bash -lc "..."`が実際には何も実行しない)。
  起動後は必ず `pgrep -af llm_gp.main` と新しいrunディレクトリの存在を
  確認すること。失敗していたら、`wsl --shutdown`を挟まず直接
  以下のように起動すると確実に動く:
  ```bash
  wsl -d Ubuntu-20.04 -- bash -lc \
    "cd /mnt/c/Users/adachi/catkin_ws && setsid nohup python3 -m llm_gp.main --config <config> > <log> 2>&1 < /dev/null & disown"
  ```
- **起動には必ず`setsid`を付ける**。`nohup ... & disown`だけだと、起動に使った
  `bash -lc`のシェルが終了した瞬間に、実行中だった子プロセス(最初の個体の
  `catkin build`など)へ`SIGHUP`が届くことがある。実際に2026-10-06の決定的
  30世代実験の初回起動で、`ind_000001`の1回目のビルドが
  `Error running link command: SIGHUP`で失敗した。1回でも失敗すると
  その個体は3回中全成功の条件を満たせず脱落扱いになり、環境起因で結果が
  歪む。`setsid`で新しいセッションとして起動し、標準入力も`< /dev/null`で
  切り離せば防げる。ビルドログ(`ros_logs/*_repetition_1.build.log`)に
  `SIGHUP`が出ていないかを起動直後に確認するとよい。
- Git BashなどMSYS系シェルから`wsl.exe`に`/mnt/c/...`のようなPOSIXパスを
  引数で渡すと、MSYSが勝手にWindowsパスへ変換して壊すことがある。
  `MSYS_NO_PATHCONV=1`を頭に付けて回避する。
- `wsl -d <distro> -- bash -lc "複数行にまたがるコマンドや$変数展開"`は
  ネストしたクォート/エスケープで壊れやすい。複雑な処理は素直に
  一時的な`.sh`ファイルに書いて`wsl -d <distro> -- bash <パス>`で
  実行する方が確実。
- WSL内で`sudo`にパスワードが求められて非対話シェルから応答できない
  場合、`wsl -d <distro> -u root -- <コマンド>` でパスワードなしに
  rootとして直接実行できる。
- `wsl --update`はUAC(管理者権限)の確認ダイアログが画面に出て
  止まることがある。バックグラウンド実行中に進捗が全く動かない
  ときは、画面上にUACダイアログが出ていないか確認する。
