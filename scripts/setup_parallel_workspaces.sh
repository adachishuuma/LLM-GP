#!/usr/bin/env bash
# One-time setup: clone the base catkin workspace N times so
# additional_workspaces (see llm_gp/config.py RosGazeboSettings) can back N
# extra concurrent Gazebo evaluations. Run this once in WSL before starting a
# parallel-evaluation experiment; each clone is a full independent copy
# (source + build + devel) so RosGazeboEvaluator can build/evaluate a
# different candidate in each one at the same time without racing on a
# shared rastar.cpp.
#
# Usage (from WSL):
#   bash scripts/setup_parallel_workspaces.sh <base_workspace> <count>
#
# Example (3 extra clones, for 4-way parallelism alongside the base):
#   bash scripts/setup_parallel_workspaces.sh /home/adachi/catkin_ws_2024_12_2/catkin_ws 3
#
# Prints the resulting clone paths (one per line, stdout only) so they can be
# pasted straight into a config file's evaluation.ros_gazebo.additional_workspaces
# list. Already-existing clones are left untouched (safe to re-run).
set -euo pipefail

base="${1:?usage: setup_parallel_workspaces.sh <base_workspace> <count>}"
count="${2:?usage: setup_parallel_workspaces.sh <base_workspace> <count>}"

if [[ ! -d "$base" ]]; then
  echo "error: base workspace '$base' does not exist" >&2
  exit 1
fi

for ((i = 1; i <= count; i++)); do
  clone="${base}_worker${i}"
  if [[ -e "$clone" ]]; then
    echo "skip (already exists): $clone" >&2
  else
    echo "cloning $base -> $clone ..." >&2
    cp -a "$base" "$clone"
  fi
  echo "$clone"
done
