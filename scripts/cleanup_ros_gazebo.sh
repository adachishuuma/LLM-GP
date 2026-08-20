#!/usr/bin/env bash
# Force-kill any leftover ROS/Gazebo processes from a prior evaluation.
#
# llm_gp/run_lock.py (ros_gazebo_run_lock) serializes every ROS/Gazebo
# evaluation project-wide, so nothing legitimate should still be using these
# processes when this runs after a GP run (llm_gp/main.py) or a repeated
# comparison (scripts/repeat10_compare.py / scripts/verify_generation_gain.py)
# has finished. Each individual evaluation already self-cleans via
# run_ros_gazebo_evaluation.sh's own trap; this is an extra sweep run once
# after the whole batch, and can also be run by hand if needed:
#
#   bash scripts/cleanup_ros_gazebo.sh          (from WSL)
#   wsl.exe -d <distro> -- bash /mnt/c/.../scripts/cleanup_ros_gazebo.sh  (from Windows)
set -uo pipefail

pkill -9 -f 'gzserver' 2>/dev/null || true
pkill -9 -f 'gzclient' 2>/dev/null || true
pkill -9 -f 'roslaunch' 2>/dev/null || true
pkill -9 -f 'rosmaster' 2>/dev/null || true
pkill -9 -f 'rosout' 2>/dev/null || true
exit 0
