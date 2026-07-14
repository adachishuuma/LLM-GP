#include <ros/ros.h>
#include <dynamic_reconfigure/client.h>
#include "costmap_2d/InflationPluginConfig.h"

#include "amcl/AMCLConfig.h"

int main(int argc, char **argv){
    ros::init(argc, argv, "dynamic_reconfigure_client");
    ros::NodeHandle nh;

    dynamic_reconfigure::Client<amcl::AMCLConfig> client("/amcl/");
    amcl::AMCLConfig default_config_;
    // default_config_.first_map_only = true;

    double lmr;
    nh.getParam("/amcl/laser_max_range", lmr);
    ROS_INFO("LMR = %f", lmr);

    // client.getCurrentConfiguration(default_config_);
    // double lmr = default_config_.laser_max_range;
    // ROS_INFO("LMR = %f", lmr);

    //一回目は変わらない？
    // default_config_.laser_max_range = 3.6;
    // client.setConfiguration(default_config_);
    // ros::Duration(1).sleep();

    for(int i=0;i<30;i++){
        default_config_.laser_max_range = 3.6 + 0.1*i;
        client.setConfiguration(default_config_);
        //やっぱり必要なのか？
        default_config_.laser_max_range = 3.6 + 0.1*i;
        client.setConfiguration(default_config_);

        nh.getParam("/amcl/laser_max_range", lmr);
        ROS_INFO("LMR = %f", lmr);
        ros::Duration(1).sleep();
    }

    // while(ros::ok())
    // {
    //     default_config_.laser_max_range = 3.5;
    //     client.setConfiguration(default_config_);
    //     ros::Duration(1).sleep();

    //     default_config_.laser_max_range = 7.0;
    //     client.setConfiguration(default_config_);
    //     ros::Duration(1).sleep();
    // }
    return 0;
}