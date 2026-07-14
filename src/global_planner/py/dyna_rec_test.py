import rospy
import dynamic_reconfigure.client

if __name__ == '__main__':
    rospy.init_node("dynamic_reconfigure_client")
    client = dynamic_reconfigure.client.Client("/move_base/local_costmap/inflation_layer/")
    while not rospy.is_shutdown():
        client.update_configuration({"inflation_radius": 0.5})
        rospy.sleep(1)
        client.update_configuration({"inflation_radius": 1.0})
        rospy.sleep(1)

    rospy.spin()

