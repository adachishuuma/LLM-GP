#!/usr/bin/env python
import rospy
from nav_msgs.msg import Odometry
import tf
import math

def odom_callback(msg):
    # x, y座標の取得
    x = msg.pose.pose.position.x
    y = msg.pose.pose.position.y

    # クォータニオンから角度θを取得
    orientation_q = msg.pose.pose.orientation
    orientation_list = [orientation_q.x, orientation_q.y, orientation_q.z, orientation_q.w]
    (roll, pitch, yaw) = tf.transformations.euler_from_quaternion(orientation_list)

    # yawが角度θに相当
    theta = yaw
    angle = theta * 180 / math.pi

    rospy.loginfo("Position: [x: %f, y: %f, angle: %f]", x, y, angle)

def main():
    rospy.init_node('getCoodeinate')

    rospy.Subscriber("/odom", Odometry, odom_callback)

    rospy.spin()

if __name__ == '__main__':
    main()
