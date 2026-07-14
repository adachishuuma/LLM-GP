import rospy
import dynamic_reconfigure.client

if __name__ == '__main__':
    rospy.init_node("dynamic_reconfigure_client")
    client = dynamic_reconfigure.client.Client("/amcl/laser_max_range/", timeout = 10) 
    while not rospy.is_shutdown():
        client.update_configuration({"laser_max_range": 7.0})
        rospy.sleep(1)
        client.update_configuration({"laser_max_range": 3.5})
        rospy.sleep(1)

    rospy.spin()

