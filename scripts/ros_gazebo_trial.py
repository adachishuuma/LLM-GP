#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from threading import Lock

import actionlib
import rospy
from actionlib_msgs.msg import GoalStatus
from gazebo_msgs.msg import ModelState
from gazebo_msgs.srv import SetModelState
from geometry_msgs.msg import PoseWithCovarianceStamped, Quaternion
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from nav_msgs.msg import Path as NavPath
from std_msgs.msg import Int32
from std_srvs.srv import Empty


def quaternion_from_yaw(yaw: float) -> Quaternion:
    return Quaternion(0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def path_length(message: NavPath) -> float:
    total = 0.0
    for previous, current in zip(message.poses, message.poses[1:]):
        dx = current.pose.position.x - previous.pose.position.x
        dy = current.pose.position.y - previous.pose.position.y
        total += math.hypot(dx, dy)
    return total


def write_result(output: Path, **payload: object) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--start", nargs=3, required=True, type=float)
    parser.add_argument("--goal", nargs=3, required=True, type=float)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--plan-topic", required=True)
    parser.add_argument(
        "--node-expansions-topic",
        default="",
        help="std_msgs/Int32 topic published by GlobalPlanner alongside --plan-topic. "
        "Optional: if omitted or the topic never fires, node_expansions is reported as null.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rospy.init_node("llm_gp_ros_gazebo_trial", anonymous=True, disable_signals=True)
    lock = Lock()
    goal_started_at: float | None = None
    first_plan_at: float | None = None
    initial_path_length: float | None = None
    first_node_expansions: int | None = None
    # Updated continuously; GlobalPlanner publishes node_expansions right
    # before the plan itself (see planner_core.cpp), so whatever value is
    # latest when on_plan first fires is the count for that same search.
    latest_node_expansions: int | None = None

    def on_plan(message: NavPath) -> None:
        nonlocal first_plan_at, initial_path_length, first_node_expansions
        if not message.poses:
            return
        with lock:
            if goal_started_at is None:
                return
            if first_plan_at is None:
                first_plan_at = time.monotonic()
                initial_path_length = path_length(message)
                first_node_expansions = latest_node_expansions

    def on_node_expansions(message: Int32) -> None:
        nonlocal latest_node_expansions
        latest_node_expansions = int(message.data)

    rospy.Subscriber(args.plan_topic, NavPath, on_plan, queue_size=10)
    if args.node_expansions_topic:
        rospy.Subscriber(
            args.node_expansions_topic, Int32, on_node_expansions, queue_size=10
        )
    try:
        rospy.wait_for_service("/gazebo/set_model_state", timeout=15.0)
        set_model_state = rospy.ServiceProxy("/gazebo/set_model_state", SetModelState)
        sx, sy, syaw = args.start
        state = ModelState()
        state.model_name = args.model_name
        state.reference_frame = "world"
        state.pose.position.x = sx
        state.pose.position.y = sy
        state.pose.position.z = 0.0
        state.pose.orientation = quaternion_from_yaw(syaw)
        response = set_model_state(state)
        if not response.success:
            raise RuntimeError(response.status_message or "Gazebo model reset failed")

        initial_pose = PoseWithCovarianceStamped()
        initial_pose.header.frame_id = "map"
        initial_pose.pose.pose = state.pose
        initial_pose.pose.covariance[0] = 0.01
        initial_pose.pose.covariance[7] = 0.01
        initial_pose.pose.covariance[35] = 0.01
        publisher = rospy.Publisher("/initialpose", PoseWithCovarianceStamped, queue_size=1)
        for _ in range(5):
            initial_pose.header.stamp = rospy.Time.now()
            publisher.publish(initial_pose)
            time.sleep(0.2)

        try:
            rospy.wait_for_service("/move_base/clear_costmaps", timeout=10.0)
            rospy.ServiceProxy("/move_base/clear_costmaps", Empty)()
        except (rospy.ROSException, rospy.ServiceException):
            pass

        client = actionlib.SimpleActionClient("move_base", MoveBaseAction)
        if not client.wait_for_server(rospy.Duration(20.0)):
            raise RuntimeError("move_base action server is unavailable")

        gx, gy, gyaw = args.goal
        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = "map"
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = gx
        goal.target_pose.pose.position.y = gy
        goal.target_pose.pose.orientation = quaternion_from_yaw(gyaw)
        goal_started_at = time.monotonic()
        client.send_goal(goal)

        terminal_states = {
            GoalStatus.PREEMPTED,
            GoalStatus.SUCCEEDED,
            GoalStatus.ABORTED,
            GoalStatus.REJECTED,
            GoalStatus.RECALLED,
            GoalStatus.LOST,
        }
        deadline = goal_started_at + args.timeout
        while time.monotonic() < deadline and not rospy.is_shutdown():
            if client.get_state() in terminal_states:
                break
            time.sleep(0.1)
        arrival_time = time.monotonic() - goal_started_at
        state_code = client.get_state()
        if state_code != GoalStatus.SUCCEEDED:
            client.cancel_goal()
            state_name = GoalStatus.to_string(state_code)
            write_result(
                args.output,
                success=False,
                planning_time=None if first_plan_at is None else first_plan_at - goal_started_at,
                path_length=initial_path_length,
                arrival_time=arrival_time,
                node_expansions=first_node_expansions,
                error_message=f"move_base did not succeed: {state_name}",
                goal_x=gx,
                goal_y=gy,
            )
            return 1
        if first_plan_at is None or initial_path_length is None:
            raise RuntimeError(f"No global plan was received on {args.plan_topic}")
        write_result(
            args.output,
            success=True,
            planning_time=first_plan_at - goal_started_at,
            path_length=initial_path_length,
            arrival_time=arrival_time,
            node_expansions=first_node_expansions,
            error_message=None,
            goal_x=gx,
            goal_y=gy,
        )
        return 0
    except Exception as exc:
        write_result(
            args.output,
            success=False,
            planning_time=None,
            path_length=None,
            arrival_time=None,
            node_expansions=None,
            error_message=str(exc),
            goal_x=args.goal[0],
            goal_y=args.goal[1],
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
