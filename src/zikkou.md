cd ~/catkin_ws_2024_12_2/catkin_ws
source devel/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch dwa_local_planner astar_dwa.launch

pkill -f roslaunch
pkill -f rosmaster
pkill -f rosout
pkill -f move_base
pkill -f map_server
pkill -f amcl
pkill -f rviz
pkill -f gzserver
pkill -f gzclient

rosclean purge
開かない場合
wsl --shutdown

cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo.yaml

cd C:\Users\adachi\catkin_ws
.\.venv\Scripts\python.exe -m llm_gp.main --config config\ros_gazebo_10gen_repeated.yaml

cd /home/adachi/catkin_ws_2024_12_2/catkin_ws
source /opt/ros/noetic/setup.bash
source devel/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch dwa_local_planner astar_dwa.launch

python scripts/repeat10_compare.py \
    --run-dir experiment_results/ten_generation_repeated/run_20260722_053957 \
    --repetitions 10

ten_generation_repeated/
├── roulette_ros_10gen_repeated.db
├── generation_summary.csv
├── individual_sources/
├── ros_logs/
└── roulette_ros_10gen_repeated_analysis/
    ├── generation_algorithm_report.md
    ├── generation_comparison.csv
    ├── individual_comparison.csv
    ├── metric_comparison.md
    ├── metric_comparison.csv
    ├── repetition_statistics.csv
    ├── generation_XXX_best_XXXX.diff
    └── best_algorithm/
        ├── best_algorithm.cpp
        ├── README.md
        └── diff_from_initial.diff

各ファイルの意味:
roulette_ros_10gen_repeated.db
全個体、評価値、親子関係、島、LLM呼び出し、移住履歴を保存するSQLite DB

generation_summary.csv
各世代の生成数、移住数、島ごとの個体数

individual_sources/
初期個体、交叉個体、LLM変異個体など、生成された全C++コード

ros_logs/
各個体・各反復のROS/Gazebo測定結果
planning_time、path_length、arrival_time、成功・失敗理由を保存

generation_algorithm_report.md
各世代の適応度最良個体とコード情報

metric_comparison.md
経路生成時間、経路長、到達時間を世代ごとに比較する読みやすいレポート

metric_comparison.csv
3指標と初期値からの改善率を表形式で保存

repetition_statistics.csv
各個体の3回評価について、成功率、平均値、標準偏差を保存

individual_comparison.csv
全個体の評価結果、生成方法、親、ソースパス

generation_XXX_best_XXXX.diff
各世代の最良コードと初期コードの差分

best_algorithm/best_algorithm.cpp
実験全体で最も適応度が高かったコード

best_algorithm/README.md
最良個体の世代、生成方法、3指標、元個体ID

best_algorithm/diff_from_initial.diff
最良コードと初期Relaxed A*との差分

注意点として、同じ設定で再実行するとSQLite DBは初期化されます。既存結果を残したい場合は、実行前に ten_generation_repeated フォルダを別名で保存してください。


11:23 AM