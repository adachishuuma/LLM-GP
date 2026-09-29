 cd /mnt/c/Users/adachi/catkin_ws
  nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_5gen.yaml > gp_5gen_v3.log 2>&1 &[1] 245474
  disown

  pgrep -af "llm_gp.main"
RUN=$(ls -td experiment_results/lattice_fork_5gen/run_*/ | head -1)
tail -f "$RUN/generation_summary.csv"

.\scripts\restart_wsl_and_run.ps1 -Command "cd /mnt/c/Users/adachi/catkin_ws && nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_30gen.yaml > gp_minmax_30gen.log 2>&1 & disown"

wsl --update
wsl --update

自動実行モードのセキュリティポリシーにより、Defenderの除外設定変更はブロックされました(セキュリティ関連の変更のため、明示的な承認が必要です)。


このマシン(Windows 11 Enterprise)には組織管理のEDR「Microsoft Defender for Endpoint」が常駐しています。WSL2上でGazebo/ROSを実行すると、1回の評価あたり大量の短命プロセス(roslaunch、gzserver、ビルド時のコンパイラ呼び出し、監視用コマンドなど)が生成されますが、これをDefenderの挙動監視がその都度スキャンすることで、WSL2の仮想マシン/プロセスのスケジューリングが極端に遅延していました。

症状: シミュレーション内時間が壁時計の1/20〜1/50程度の速度でしか進まず(リアルタイムファクター0.02〜0.1)、ほぼ全個体がmove_base did not succeedのタイムアウトで失敗する。

Add-MpPreference -ExclusionProcess "vmmem"
Add-MpPreference -ExclusionProcess "vmmemWSL"
Add-MpPreference -ExclusionProcess "wsl.exe"
Add-MpPreference -ExclusionProcess "wslhost.exe"
Add-MpPreference -ExclusionPath "C:\Users\adachi\AppData\Local\wsl\{369f2686-4212-4e20-a9fc-a1b67fbc38a7}"
Add-MpPreference -ExclusionPath "C:\Users\adachi\catkin_ws"
実行後、念のため「Windowsセキュリティ」→「ウイルスと脅威の防止の設定」→「除外」で実際にリストに反映されているか確認してください(組織ポリシーでサイレントに無視されることがあるため)。


事前準備(スリープで実験が巻き込まれないように):
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
実行(PowerShellから):
wsl --shutdown

wsl -d Ubuntu-20.04 -- bash -lc "cd /mnt/c/Users/adachi/catkin_ws && nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_minmax_30gen.yaml > gp_minmax_30gen.log 2>&1 & disown"

