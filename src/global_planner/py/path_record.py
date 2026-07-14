#!/usr/bin/env python
import rospy
from nav_msgs.msg import Path

def publish_path():
    rospy.init_node('path_publisher_node', anonymous=True)
    path_publisher = rospy.Publisher('/robot_path', Path, queue_size=10)
    rate = rospy.Rate(1)  # 1 Hz

    while not rospy.is_shutdown():
        # Create a Path message and populate it with waypoints
        path_msg = Path()
        # Populate path_msg with waypoints

        # Publish the path
        path_publisher.publish(path_msg)
        rate.sleep()

if __name__ == '__main__':
    try:
        publish_path()
    except rospy.ROSInterruptException:
        pass
