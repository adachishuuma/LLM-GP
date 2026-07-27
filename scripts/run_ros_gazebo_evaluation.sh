#!/usr/bin/env bash
set -o pipefail

workspace="$1"
candidate_source="$2"
candidate_target_relative="$3"
package_name="$4"
launch_file="$5"
trial_script="$6"
output_json="$7"
robot_model="$8"
gazebo_model_name="$9"
start_x="${10}"
start_y="${11}"
start_yaw="${12}"
goal_x="${13}"
goal_y="${14}"
goal_yaw="${15}"
trial_timeout="${16}"
startup_timeout="${17}"
plan_topic="${18}"
node_expansions_topic="${19}"

target_source="$workspace/$candidate_target_relative"
backup_source="$(mktemp)"
build_log="${output_json%.json}.build.log"
launch_log="${output_json%.json}.roslaunch.log"
launch_pid=""

write_failure() {
  local message="$1"
  printf '{"success": false, "planning_time": null, "path_length": null, "arrival_time": null, "node_expansions": null, "error_message": "%s"}\n' "$message" > "$output_json"
}

build_package() {
  if [[ -d "$workspace/.catkin_tools" ]] && command -v catkin >/dev/null 2>&1; then
    catkin build "$package_name" --no-deps --no-status
  else
    catkin_make --pkg "$package_name" -DCMAKE_BUILD_TYPE=Release
  fi
}

stop_launch() {
  [[ -z "$launch_pid" ]] && return
  # Signal the whole session even if the roslaunch parent has already exited.
  # rosmaster, Gazebo and ROS nodes are children in this process group.
  kill -INT -- "-$launch_pid" 2>/dev/null || true
  for _ in {1..10}; do
    kill -0 -- "-$launch_pid" 2>/dev/null || break
    sleep 0.5
  done
  kill -TERM -- "-$launch_pid" 2>/dev/null || true
  for _ in {1..6}; do
    kill -0 -- "-$launch_pid" 2>/dev/null || break
    sleep 0.5
  done
  kill -KILL -- "-$launch_pid" 2>/dev/null || true
  wait "$launch_pid" 2>/dev/null || true
  launch_pid=""
}

restore_source() {
  stop_launch
  if [[ -s "$backup_source" ]]; then
    cp "$backup_source" "$target_source"
    cd "$workspace" || return
    source /opt/ros/noetic/setup.bash
    build_package >> "$build_log" 2>&1 || true
  fi
  rm -f "$backup_source"
}
trap restore_source EXIT INT TERM

mkdir -p "$(dirname "$output_json")"
if [[ ! -f "$candidate_source" || ! -f "$target_source" ]]; then
  write_failure "Candidate or target A* source file does not exist"
  exit 2
fi

cp "$target_source" "$backup_source"
cp "$candidate_source" "$target_source"

source /opt/ros/noetic/setup.bash
cd "$workspace" || exit 2
if [[ -f devel/setup.bash ]]; then
  source devel/setup.bash
fi
if ! build_package > "$build_log" 2>&1; then
  write_failure "Candidate failed to build; see build log"
  exit 3
fi
source devel/setup.bash

export TURTLEBOT3_MODEL="$robot_model"
# Every evaluation gets private ROS and Gazebo masters. Stale processes from a
# previous interrupted run therefore cannot register duplicate nodes or occupy
# the Gazebo master port used by this evaluation.
read -r ros_port gazebo_port < <(python3 -c 'import socket; a=socket.socket(); b=socket.socket(); a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0)); print(a.getsockname()[1], b.getsockname()[1])')
export ROS_MASTER_URI="http://127.0.0.1:$ros_port"
export GAZEBO_MASTER_URI="http://127.0.0.1:$gazebo_port"
printf 'ROS_MASTER_URI=%s\nGAZEBO_MASTER_URI=%s\n' "$ROS_MASTER_URI" "$GAZEBO_MASTER_URI" > "$launch_log"
setsid roslaunch "$launch_file" use_rviz:=false >> "$launch_log" 2>&1 &
launch_pid=$!

ready=0
for ((second=0; second<startup_timeout; second++)); do
  if ! kill -0 "$launch_pid" 2>/dev/null; then
    break
  fi
  if timeout 2 rosservice list 2>/dev/null | grep -q '^/gazebo/set_model_state$' && \
     timeout 2 rosnode list 2>/dev/null | grep -q '^/move_base$'; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" -ne 1 ]]; then
  write_failure "ROS/Gazebo did not become ready before startup timeout"
  exit 4
fi

python3 "$trial_script" \
  --output "$output_json" \
  --model-name "$gazebo_model_name" \
  --start "$start_x" "$start_y" "$start_yaw" \
  --goal "$goal_x" "$goal_y" "$goal_yaw" \
  --timeout "$trial_timeout" \
  --plan-topic "$plan_topic" \
  --node-expansions-topic "$node_expansions_topic"
trial_status=$?
if [[ "$trial_status" -ne 0 && ! -f "$output_json" ]]; then
  write_failure "ROS measurement script failed without a result"
fi
exit "$trial_status"
