/*********************************************************************
 *
 * Software License Agreement (BSD License)
 *
 *  Copyright (c) 2009, Willow Garage, Inc.
 *  All rights reserved.
 *
 *  Redistribution and use in source and binary forms, with or without
 *  modification, are permitted provided that the following conditions
 *  are met:
 *
 *   * Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 *   * Redistributions in binary form must reproduce the above
 *     copyright notice, this list of conditions and the following
 *     disclaimer in the documentation and/or other materials provided
 *     with the distribution.
 *   * Neither the name of Willow Garage, Inc. nor the names of its
 *     contributors may be used to endorse or promote products derived
 *     from this software without specific prior written permission.
 *
 *  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 *  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 *  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 *  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 *  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 *  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 *  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 *  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 *  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 *  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 *  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 *  POSSIBILITY OF SUCH DAMAGE.
 *
 * Author: Eitan Marder-Eppstein
 *********************************************************************/

#include <Eigen/Core>
#include <Eigen/LU>
#include <math.h>
#include <cmath>
#include <dwa_local_planner/dwa_planner_ros.h>

#include <ros/console.h>

#include <pluginlib/class_list_macros.hpp>

#include <base_local_planner/goal_functions.h>
#include <nav_msgs/Path.h>
#include <tf2/utils.h>

#include <nav_core/parameter_magic.h>

#include <ros/ros.h>
#include <sensor_msgs/LaserScan.h>
#include <tuple>

using namespace Eigen;
using namespace std;

// register this planner as a BaseLocalPlanner plugin
PLUGINLIB_EXPORT_CLASS(dwa_local_planner::DWAPlannerROS,
                       nav_core::BaseLocalPlanner)

namespace dwa_local_planner {

void DWAPlannerROS::reconfigureCB(DWAPlannerConfig &config, uint32_t level) {
    if (setup_ && config.restore_defaults) {
        config = default_config_;
        config.restore_defaults = false;
    }
    if (!setup_) {
        default_config_ = config;
        setup_ = true;
    }

    // update generic local planner params
    base_local_planner::LocalPlannerLimits limits;
    limits.max_vel_trans = config.max_vel_trans;            //ロボットの並進運動速度の絶対値の上限
    limits.min_vel_trans = config.min_vel_trans;            //下限
    limits.max_vel_x = config.max_vel_x;                    //ロボットの縦方向速度の上限
    limits.min_vel_x = config.min_vel_x;                    //下限
    limits.max_vel_y = config.max_vel_y;                    //ロボットの横方向速度の上限
    limits.min_vel_y = config.min_vel_y;                    //下限
    limits.max_vel_theta = config.max_vel_theta;            //ロボットの回転速度絶対値の上限
    limits.min_vel_theta = config.min_vel_theta;            //下限
    limits.acc_lim_x = config.acc_lim_x;                    //ロボットの縦方向加速度の上限
    limits.acc_lim_y = config.acc_lim_y;                    //ロボットの横方向加速度の上限
    limits.acc_lim_theta = config.acc_lim_theta;            //ロボットの回転加速度の上限
    limits.acc_lim_trans = config.acc_lim_trans;            //ロボットの並進運動加速度の上限
    limits.xy_goal_tolerance = config.xy_goal_tolerance;    //ゴール地点に到達したときの、コントローラーの 2D平面上距離の許容誤差  
    limits.yaw_goal_tolerance = config.yaw_goal_tolerance;  //ゴール地点に到達したときの、コントローラーの向き(回転角)の許容誤差
    limits.prune_plan = config.prune_plan;
    limits.trans_stopped_vel = config.trans_stopped_vel;    //最終補正にあたって停止したとみなす X-Y合成速度。停止後その場回転します
    limits.theta_stopped_vel = config.theta_stopped_vel;    //最終補正にあたって停止したとみなす回転速度。停止後その場回転します
    planner_util_.reconfigureCB(limits, config.restore_defaults);

    // update dwa specific configuration
    dp_->reconfigure(config);
}

DWAPlannerROS::DWAPlannerROS()
    : initialized_(false), odom_helper_("odom"), setup_(false) {}

void DWAPlannerROS::initialize(std::string name, tf2_ros::Buffer *tf,
                               costmap_2d::Costmap2DROS *costmap_ros) {
    if (!isInitialized()) {

        ros::NodeHandle private_nh("~/" + name);
        g_plan_pub_ = private_nh.advertise<nav_msgs::Path>("global_plan", 1);
        l_plan_pub_ = private_nh.advertise<nav_msgs::Path>("local_plan", 1);
        tf_ = tf;
        costmap_ros_ = costmap_ros;
        costmap_ros_->getRobotPose(current_pose_);

        // make sure to update the costmap we'll use for this cycle
        costmap_2d::Costmap2D *costmap = costmap_ros_->getCostmap();

        planner_util_.initialize(tf, costmap, costmap_ros_->getGlobalFrameID());

        // create the actual planner that we'll use.. it'll configure itself
        // from the parameter server
        dp_ =
            boost::shared_ptr<DWAPlanner>(new DWAPlanner(name, &planner_util_));

        if (private_nh.getParam("odom_topic", odom_topic_)) {
            odom_helper_.setOdomTopic(odom_topic_);
        }

        initialized_ = true;

        // Warn about deprecated parameters -- remove this block in N-turtle
        nav_core::warnRenamedParameter(private_nh, "max_vel_trans",
                                       "max_trans_vel");
        nav_core::warnRenamedParameter(private_nh, "min_vel_trans",
                                       "min_trans_vel");
        nav_core::warnRenamedParameter(private_nh, "max_vel_theta",
                                       "max_rot_vel");
        nav_core::warnRenamedParameter(private_nh, "min_vel_theta",
                                       "min_rot_vel");
        nav_core::warnRenamedParameter(private_nh, "acc_lim_trans",
                                       "acc_limit_trans");
        nav_core::warnRenamedParameter(private_nh, "theta_stopped_vel",
                                       "rot_stopped_vel");

        dsrv_ = new dynamic_reconfigure::Server<DWAPlannerConfig>(private_nh);
        dynamic_reconfigure::Server<DWAPlannerConfig>::CallbackType cb =
            [this](auto &config, auto level) { reconfigureCB(config, level); };
        dsrv_->setCallback(cb);
    } else {
        ROS_WARN("This planner has already been initialized, doing nothing.");
    }
}

bool DWAPlannerROS::setPlan(
    const std::vector<geometry_msgs::PoseStamped> &orig_global_plan) {
    if (!isInitialized()) {
        ROS_ERROR("This planner has not been initialized, please call "
                  "initialize() before using this planner");
        return false;
    }

    // 総実行時間の計測開始
    static bool flag = true;
    if (flag) {
        t_start = ros::Time::now();
        flag = false;
    }
    // ROS_INFO("STARTING caluculate of RUN TIME");

    // when we get a new plan, we also want to clear any latch we may have on
    // goal tolerances
    latchedStopRotateController_.resetLatching();

    ROS_INFO("Got new plan");
    return dp_->setPlan(orig_global_plan);
}

bool DWAPlannerROS::isGoalReached() {
    if (!isInitialized()) {
        ROS_ERROR("This planner has not been initialized, please call "
                  "initialize() before using this planner");
        return false;
    }
    if (!costmap_ros_->getRobotPose(current_pose_)) {
        ROS_ERROR("Could not get robot pose");
        return false;
    }

    if (latchedStopRotateController_.isGoalReached(&planner_util_, odom_helper_,
                                                   current_pose_)) {
        ROS_INFO("Finally, Goal reached!!");

        // 総実行時間の計測終了
        double runtime = (ros::Time::now() - t_start).toSec();
        ROS_INFO("FULL of RUN TIME:%lf\n", runtime);

        return true;
    } else {
        return false;
    }
}

void DWAPlannerROS::publishLocalPlan(
    std::vector<geometry_msgs::PoseStamped> &path) {
    base_local_planner::publishPlan(path, l_plan_pub_);
}

void DWAPlannerROS::publishGlobalPlan(
    std::vector<geometry_msgs::PoseStamped> &path) {
    base_local_planner::publishPlan(path, g_plan_pub_);
}

DWAPlannerROS::~DWAPlannerROS() {
    // make sure to clean things up
    delete dsrv_;
}

bool DWAPlannerROS::dwaComputeVelocityCommands(
    geometry_msgs::PoseStamped &global_pose, geometry_msgs::Twist &cmd_vel) {
    // dynamic window sampling approach to get useful velocity commands
    if (!isInitialized()) {
        ROS_ERROR("This planner has not been initialized, please call "
                  "initialize() before using this planner");
        return false;
    }

    geometry_msgs::PoseStamped robot_vel;
    odom_helper_.getRobotVel(robot_vel);

    /* For timing uncomment
    struct timeval start, end;
    double start_t, end_t, t_diff;
    gettimeofday(&start, NULL);
    */

    // compute what trajectory to drive along
    geometry_msgs::PoseStamped drive_cmds;
    drive_cmds.header.frame_id = costmap_ros_->getBaseFrameID();

    // call with updated footprint
    base_local_planner::Trajectory path =
        dp_->findBestPath(global_pose, robot_vel, drive_cmds);
    // ROS_ERROR("Best: %.2f, %.2f, %.2f, %.2f", path.xv_, path.yv_,
    // path.thetav_, path.cost_);

    /* For timing uncomment
    gettimeofday(&end, NULL);
    start_t = start.tv_sec + double(start.tv_usec) / 1e6;
    end_t = end.tv_sec + double(end.tv_usec) / 1e6;
    t_diff = end_t - start_t;
    ROS_INFO("Cycle time: %.9f", t_diff);
    */

    // pass along drive commands
    //x,y軸線形速度、角速度(z)？ 2Dならyは使わないらしい
    cmd_vel.linear.x = drive_cmds.pose.position.x;
    cmd_vel.linear.y = drive_cmds.pose.position.y;
    cmd_vel.angular.z = tf2::getYaw(drive_cmds.pose.orientation);
    
    // x, y座標の取得
    double xb = global_pose.pose.position.x;
    double yb = global_pose.pose.position.y;

    // クォータニオンを取得
    tf2::Quaternion q(
        global_pose.pose.orientation.x,
        global_pose.pose.orientation.y,
        global_pose.pose.orientation.z,
        global_pose.pose.orientation.w
    );

    // クォータニオンからroll, pitch, yawを取得
    double roll, pitch, yaw;
    tf2::Matrix3x3(q).getRPY(roll, pitch, yaw);
    int theta_b = (int)yaw;

    double v = 0;
    double omega = 0;
    double min_l, min_r; //障害物距離
    int angle_l, angle_r;  //障害物角度
    ros::NodeHandle n;
    n.getParam("move_base_min_l",min_l);
    n.getParam("move_base_angle_l",angle_l);
    n.getParam("move_base_min_r",min_r);
    n.getParam("move_base_angle_r",angle_r);
    ROS_INFO("move_base_min_l:%f, move_base_angle_l:%d",min_l,angle_l);
    ROS_INFO("move_base_min_r:%f, move_base_angle_r:%d",min_r,angle_r);

    double l1 = 0.3;
    double l2 = 0.3;
    int theta_l = (360 + theta_b + angle_l) % 360;
    int theta_r = (360 + theta_b + angle_r) % 360;
    bool L = false;
    bool R = false;
    double xe_l = xb + min_l*cos(theta_l); //左補助MPU先端x座標
    double ye_l = yb + min_l*sin(theta_l); //左補助MPU先端y座標
    double xe_r = xb + min_r*cos(theta_r);
    double ye_r = xb + min_r*sin(theta_r);

    double passtime = (ros::Time::now() - t_start).toSec();
    double xe_f,ye_f;//先導MPUの先端座標：あとこれだけ？＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊＊直線を与えたい
    xe_f = passtime/100;
    ye_f = xe_f;
    double v_ = drive_cmds.pose.position.x;//先導MPUに与える先端速度（DWAによる速度）
    double omega_ = tf2::getYaw(drive_cmds.pose.orientation);//与える先端角速度

    double w = 0.65;
    if(min_l < w*(l1+l2))L = true;
    if(min_r < w*(l1+l2))R = true;
    // Use the original DWA angular velocity. The VM calculation can produce
    // NaN while this experimental branch is incomplete.
    // dwaComputeVMVelocityCommands(&v,&omega,xb,yb,theta_b,v_,omega_,xe_f,ye_f,L,xe_l,ye_l,R,xe_r,ye_r,l1,l2);
    // cmd_vel.linear.x = v;
    cmd_vel.angular.z = tf2::getYaw(drive_cmds.pose.orientation);

    //先行研究では腕のリンク長合計の0.65倍を基準として腕の追加削除している
    // if(min<(l1+l2)){//そもそも障害物に腕が届かないなら計算意味なし,でもほぼ常に実行される...
    //     dwaComputeVMVelocityCommands(&v,&omega,xb,yb,theta_b,v_,omega_,xe,ye,xe_l,ye_l,xe_r,ye_r,l1,l2);
    //     ROS_INFO("omega updated!");//omega=0のときだけ表示されてる...?
    //      if(-5 < omega && omega < 5){//何故か実行されない...
    //         cmd_vel.angular.z = omega;
    //         ROS_INFO("DWV is working!");
    //      }
    // }
    // v,omegaがしばらくするとnanになってしまう
    // cmd_vel.linear.x = v;
    // cmd_vel.linear.y = drive_cmds.pose.position.y;
    //dwaComputeVMVelocityCommandsを完全にコメントアウトしているのにomegaの値が変化してる...
    //omegaが外れ値でないかつ障害物に腕が届くときDWV
    //omega updated!が表示されてomegaの範囲も正常なときでも表示されない...
    // if(-5 < omega && omega < 5 && min<(l1+l2)){
    //     cmd_vel.angular.z = omega;
    //     ROS_INFO("angular.z updated!");
    // }else{
    //     cmd_vel.angular.z = tf2::getYaw(drive_cmds.pose.orientation);
    // }

    // ROS_INFO("omega:%f",omega);

    // if we cannot move... tell someone
    std::vector<geometry_msgs::PoseStamped> local_plan;
    if (path.cost_ < 0) {
        ROS_DEBUG_NAMED("dwa_local_planner",
                        "The dwa local planner failed to find a valid plan, "
                        "cost functions discarded all candidates. This can "
                        "mean there is an obstacle too close to the robot.");
        local_plan.clear();
        publishLocalPlan(local_plan);
        return false;
    }

    ROS_DEBUG_NAMED("dwa_local_planner",
                    "A valid velocity command of (%.2f, %.2f, %.2f) was found "
                    "for this cycle.",
                    cmd_vel.linear.x, cmd_vel.linear.y, cmd_vel.angular.z);

    // Fill out the local plan
    for (unsigned int i = 0; i < path.getPointsSize(); ++i) {
        double p_x, p_y, p_th;
        path.getPoint(i, p_x, p_y, p_th);

        geometry_msgs::PoseStamped p;
        p.header.frame_id = costmap_ros_->getGlobalFrameID();
        p.header.stamp = ros::Time::now();
        p.pose.position.x = p_x;
        p.pose.position.y = p_y;
        p.pose.position.z = 0.0;
        tf2::Quaternion q;
        q.setRPY(0, 0, p_th);
        tf2::convert(q, p.pose.orientation);
        local_plan.push_back(p);
    }

    // publish information to the visualizer

    publishLocalPlan(local_plan);
    return true;
}

bool DWAPlannerROS::computeVelocityCommands(geometry_msgs::Twist &cmd_vel)
{
    // dispatches to either dwa sampling control or stop and rotate control,
    // depending on whether we have been close enough to goal
    if (!costmap_ros_->getRobotPose(current_pose_)) {
        ROS_ERROR("Could not get robot pose");
        return false;
    }
    std::vector<geometry_msgs::PoseStamped> transformed_plan;
    if (!planner_util_.getLocalPlan(current_pose_, transformed_plan)) {
        ROS_ERROR("Could not get local plan");
        return false;
    }

    // if the global plan passed in is empty... we won't do anything
    if (transformed_plan.empty()) {
        ROS_WARN_NAMED("dwa_local_planner",
                       "Received an empty transformed plan.");
        return false;
    }
    ROS_DEBUG_NAMED("dwa_local_planner",
                    "Received a transformed plan with %zu points.",
                    transformed_plan.size());

    // update plan in dwa_planner even if we just stop and rotate, to allow
    // checkTrajectory
    dp_->updatePlanAndLocalCosts(current_pose_, transformed_plan,
                                 costmap_ros_->getRobotFootprint());

    if (latchedStopRotateController_.isPositionReached(&planner_util_,
                                                       current_pose_)) {
        // publish an empty plan because we've reached our goal position
        std::vector<geometry_msgs::PoseStamped> local_plan;
        std::vector<geometry_msgs::PoseStamped> transformed_plan;
        publishGlobalPlan(transformed_plan);
        publishLocalPlan(local_plan);
        base_local_planner::LocalPlannerLimits limits =
            planner_util_.getCurrentLimits();
        return latchedStopRotateController_.computeVelocityCommandsStopRotate(
            cmd_vel, limits.getAccLimits(), dp_->getSimPeriod(), &planner_util_,
            odom_helper_, current_pose_,
            [this](auto pos, auto vel, auto vel_samples) {
                return dp_->checkTrajectory(pos, vel, vel_samples);
            });
    } else {
        bool isOk = dwaComputeVelocityCommands(current_pose_, cmd_vel);
        if (isOk) {
            publishGlobalPlan(transformed_plan);
        } else {
            ROS_WARN_NAMED("dwa_local_planner",
                           "DWA planner failed to produce path.");
            std::vector<geometry_msgs::PoseStamped> empty_plan;
            publishGlobalPlan(empty_plan);
        }
        return isOk;
    }
}

//VM(仮想マニピュレータ)で速度計算
//入力：機体の現在位置(xb,yb,theta_b)、先端速度(v_,omega_)、MPU3本の先端位置(xe_l-ye_r)、MPU長(l1,l2)
//(xe,ye)にはGPの座標を順に格納、とりあえず直線を想定して試す
//左・右半分のLidar値内で閾値以下かつ最小値をそれぞれ(xe_l,ye_l)・(xe_r,ye_r)に取得
//bool型のL,Rで左・右のMPU有無を表現
//右・左それぞれのMPUの追加・削除の方法は？->それぞれに関するヤコビ行列部分などの数値をすべて0にする？or右のみ・左のみ・どちらもなしの３通り分追加する...？（メンドイ）
//->いったん引数situに応じて４通りを場合分けsitu=(0:補助なし、1:左のみ、2:右のみ、3:どちらも)
//先導マニピュレータの先端座標はとりあえず直線などを指定して、最終的にGPPに変更
//pre_theta_2[3]の初期化がいる？
void DWAPlannerROS::dwaComputeVMVelocityCommands(double *v, double *omega, double xb, double yb, double theta_b, double v_, double omega_,
                                                 double xe_f, double ye_f, bool L, double xe_l, double ye_l, bool R, double xe_r, double ye_r, double l1, double l2){
    double xr[] = {0.05, 0.05, 0.05};  //{先導MPU，左補助MPU，右補助MPU}
    double yr[] = {0, 0.05, -0.05};
    double xe[] = {xe_f, xe_l, xe_r};
    double ye[] = {ye_f, ye_l, ye_r};    
    double part_theta_1[3];//part_theta_1はchoiceCloseThetaでより前回に近い値を選ぶために正負を入れ替えるため用
    double theta_1[] = {M_PI/4, M_PI, -M_PI};
    double theta_2[] = {-M_PI/2, 0, 0};
    Matrix<double, 6, 8> J;//(2p)*(np+2),p:腕数,n:関節数=>6*8
    MatrixXd I2 = MatrixXd::Identity(2,2);
    std::array<Matrix<double, 2, 2>, 3> J_b = {I2, I2, I2};
    std::array<Matrix<double, 2, 2>, 3> J_m = {I2, I2, I2};

    for(int i = 0;i < 3;i++){
        if((i == 1 && !L) || (i == 2 && !R))continue;
        calcJointAngle(&part_theta_1[i],&theta_1[i],&theta_2[i],xe[i],ye[i],l1,l2);
        choiceCloseTheta(i,&part_theta_1[i],&theta_1[i],&theta_2[i]);
        J_b[i] = makeJacobiB(theta_b,xr[i],yr[i],theta_1[i],theta_2[i],l1,l2);//2*2, i番目のマニピュレータに関する車両移動成分のヤコビ
        J_m[i] = makeJacobiM(theta_b,xr[i],yr[i],theta_1[i],theta_2[i],l1,l2);//2*n, 車両の方位や取り付け位置を考慮に入れたi番目のマニピュレータのヤコビ
    }
    
    J <<
        J_b[0](0,0), J_b[0](0,1), J_m[0](0,0), J_m[0](0,1), 0, 0, 0, 0,
        J_b[0](1,0), J_b[0](1,1), J_m[0](1,0), J_m[0](1,1), 0, 0, 0, 0,
        J_b[1](0,0), J_b[1](0,1), 0, 0, J_m[1](0,0), J_m[1](0,1), 0, 0,
        J_b[1](1,0), J_b[1](1,1), 0, 0, J_m[1](1,0), J_m[1](1,1), 0, 0,
        J_b[2](0,0), J_b[2](0,1), 0, 0, 0, 0, J_m[2](0,0), J_m[2](0,1),
        J_b[2](1,0), J_b[2](1,1), 0, 0, 0, 0, J_m[2](1,0), J_m[2](1,1);
    double k = 0;
    double omega_0 = 0.08;
    double omega_1 = sqrt((J*J.transpose()).determinant());
    if(omega_1 < omega_0)k = pow(1 - omega_1/omega_0, 2);
    MatrixXd I6 = MatrixXd::Identity(6,6);
    Matrix<double, 8, 6> J_sharp = J.transpose()*(k*I6 - J*J.transpose()).inverse();
    
    Matrix<double, 6, 1> x_dot; //先端速度を代入．先導MPUのv_,omega_は引数にする
    x_dot <<
        v_,
        omega_,
        0,
        0,
        0,
        0;
    double theta_ref[ ] = {0, 0, M_PI/4, -M_PI/2, M_PI, 0, -M_PI, 0};//関節角の初期値（真横に伸ばす時の値）
    double theta[ ] = {0, 0, theta_1[0], theta_2[0], theta_1[1], theta_2[1], theta_1[2], theta_2[2]};//関節角の現在値
    Matrix<double, 8, 1> y;
    for(int i = 0;i < 8;i++){
        y(i,0) = theta_ref[i] - theta[i];
    }

    Matrix<double, 8, 8> W;     //調整用重み行列
    W <<
        1,0,0,0,0,0,0,0,
        0,1,0,0,0,0,0,0,
        0,0,1,0,0,0,0,0,
        0,0,0,1,0,0,0,0,
        0,0,0,0,1,0,0,0,
        0,0,0,0,0,1,0,0,
        0,0,0,0,0,0,1,0,
        0,0,0,0,0,0,0,1;
    MatrixXd I8 = MatrixXd::Identity(8,8);
    Matrix<double, 8, 6> J_sharp_w = W * J_sharp;
    double lmd = 0.075;//0,02（元論文） or 0.075（小林さん）
    //各マニピュレータの手先目標位置を並べたベクトルx_dotの一次微分値を使って求めた車両の目標速度・角速度とマニピュレータの各関節の目標速度ベクトル
    //q_dotは行列サイズ偶数？
    //そもそもこれが最終的にほしい値（出力）？
    Matrix<double, 8, 1> q_dot = J_sharp_w*x_dot + lmd*(I8 - J_sharp_w*J_sharp.transpose())*y;
    
    *v = q_dot(0,0);
    *omega = q_dot(1,0);
}

//1つのマニピュレータ状態を計算
Eigen::Matrix<double, 2, 2> DWAPlannerROS::makeJacobiB(double theta_b, double xr, double yr, double theta_1, double theta_2, double l1, double l2){
    Matrix<double, 2, 2> J;
    J <<
        cos(theta_b), -xr*sin(theta_b)-yr*cos(theta_b)-l1*sin(theta_b+theta_1)-l2*sin(theta_b+theta_1+theta_2),
        sin(theta_b), xr*cos(theta_b)-yr*sin(theta_b)+l1*cos(theta_b+theta_1)+l2*cos(theta_b+theta_1+theta_2); 
    return J;
}

Eigen::Matrix<double, 2, 2> DWAPlannerROS::makeJacobiM(double theta_b, double xr, double yr, double theta_1, double theta_2, double l1, double l2){
    Matrix<double, 2, 2> J;
    J <<
        -l1*sin(theta_b+theta_1)-l2*sin(theta_b+theta_1+theta_2), -l2*sin(theta_b+theta_1+theta_2),
        l1*cos(theta_b+theta_1)+l2*cos(theta_b+theta_1+theta_2), l2*cos(theta_b+theta_1+theta_2);
        
    return J;
}

//先端座標からθを計算
void DWAPlannerROS::calcJointAngle(double *part_theta_1, double *theta_1, double *theta_2, double xe, double ye, double l1, double l2){
    *part_theta_1 = acos((pow(l1,2)+pow(xe,2)+pow(ye,2)-pow(l2,2)) / (2*l1*sqrt(pow(xe,2)+pow(ye,2))));
    *theta_1 = atan(ye/xe);
    *theta_2 = -acos((pow(xe,2)+pow(ye,2)-pow(l1,2)-pow(l2,2)) / (2*l1*l2));
}

//一つ前の腕角度に近い方を選ぶ
void DWAPlannerROS::choiceCloseTheta(int pre_num, double* part_theta_1, double* theta_1, double* theta_2){
    if(pow(*theta_2 - pre_theta_2[pre_num],2) > pow(-*theta_2 - pre_theta_2[pre_num],2)){
        *theta_1 = *theta_1 - *part_theta_1;
        *theta_2 = -*theta_2;
    }else{
        *theta_1 = *theta_1 + *part_theta_1;
    }
    pre_theta_2[pre_num] = *theta_2;
}
}; // namespace dwa_local_planner
