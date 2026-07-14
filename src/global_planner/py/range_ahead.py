#! /usr/bin/env python3
#https://qiita.com/Yuya-Shimizu/items/413b57b0305597be35be

import rospy
from sensor_msgs.msg import LaserScan   #レーザスキャンを扱うメッセージ

def scan_callback(msg):
    range_ahead = msg.ranges[len(msg.ranges)//2]    #ロボットの真正面にある障害物までの距離　ranges配列の中央要素

    print(f"range ahead: {range_ahead:.1f}")    #正面にある障害物までの距離を表示


rospy.init_node('range_ahead')  #'range_ahead'というノードを生成・初期化

scan_sub = rospy.Subscriber('scan', LaserScan, scan_callback)   #'scan'トピックをLaserScan型で購読し，scan_callback関数を呼び出す

rospy.spin()    #ループ
