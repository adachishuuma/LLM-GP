#include <ros/ros.h>
#include <sensor_msgs/LaserScan.h>
/*
void scanCallback(const sensor_msgs::LaserScan::ConstPtr& msg)
{
    //global_pathを１回しか求めなくなった結果↓の障害物距離の計算＆ウインドウサイズ更新を定期的にする方法がわからなくなった
    //->move_base/src/move_base.cppの884行目
    //周囲の障害物割合（距離X未満の割合）がA未満　サイズ拡大
    //割合がA以上B未満　変更なし
    //割合がB以上　サイズ縮小

    //割合の求め方
    //
    int middle_index = msg->ranges.size() / 2;  //ロボットの真正面にある一番近い障害物までの距離: ranges配列の中央要素
    float range_ahead = msg->ranges[middle_index];

    ROS_INFO("range ahead: %.1f", range_ahead);
}*/

int main(int argc, char** argv)
{
    using namespace std;
    ros::init(argc, argv, "range_ahead");
    ros::NodeHandle nh;

    double g_obstacle_range;
    double l_obstacle_range;
    // nh.getParam("/move_base/global_costmap/obstacle_range", g_obstacle_range);
    nh.getParam("/move_base/local_costmap/obstacle_range", l_obstacle_range);
    ROS_INFO("Current local obstacle range: %f", l_obstacle_range);
    // ROS_INFO("Current global obstacle range: %f", g_obstacle_range);

    double new_l_obstacle_range = l_obstacle_range;
    while(true){
        int a;
        cout << "input(0:ObRa now,1:SHORT,2:LONG,else:END)" << endl;
        cin >> a;
        if(a==0){
            nh.getParam("/move_base/local_costmap/obstacle_range", l_obstacle_range);
            ROS_INFO("Current local obstacle range: %f", l_obstacle_range);
        }else if(a==1){
            new_l_obstacle_range = 0.5;
            //new_l_obstacle_range = new_l_obstacle_range + 0.1;
        }else if(a==2){
            new_l_obstacle_range = 3.0;
        }else{
            break;
        }
        // }else if(new_l_obstacle_range >= 0.1){
        //     //new_l_obstacle_range = new_l_obstacle_range - 0.1;
        // }

        nh.setParam("/move_base/local_costmap/obstacle_range", new_l_obstacle_range);
        nh.getParam("/move_base/local_costmap/obstacle_range", l_obstacle_range);
        ROS_INFO("New local obstacle range: %f", l_obstacle_range);
    }

    // // Set a new obstacle_range value on the parameter server
    // double new_g_obstacle_range = g_obstacle_range - 1;
    // double new_l_obstacle_range = l_obstacle_range - 1;
    // nh.setParam("/move_base/global_costmap/obstacle_range", new_g_obstacle_range);
    // nh.setParam("/move_base/global_costmap/obstacle_range", new_l_obstacle_range);



    //サブスクライバはループ内に入れてはいけない
    //SpinOnceはループ内で使う
    // while(1){
    //     ros::Subscriber scan_sub = nh.subscribe("scan", 1, scanCallback);
    //     ros::spinOnce();
    //     sleep(1);
    // }
    return 0;
}
