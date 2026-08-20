 cd /mnt/c/Users/adachi/catkin_ws
  nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_5gen.yaml > gp_5gen_v3.log 2>&1 &[1] 245474
  disown

  pgrep -af "llm_gp.main"
RUN=$(ls -td experiment_results/lattice_fork_5gen/run_*/ | head -1)
tail -f "$RUN/generation_summary.csv"
