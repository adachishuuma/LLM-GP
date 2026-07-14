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