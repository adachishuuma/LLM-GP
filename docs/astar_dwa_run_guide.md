# astar_dwa.launch 実行手順と動作の流れ

## 概要

この実験環境は、Gazebo 上の TurtleBot3 を RViz から指定したゴールへ走行させる構成である。

現在の主な構成は次の通り。

- 起動ファイル: `src/dwa_local_planner/launch/astar_dwa.launch`
- Gazebo world: `src/dwa_local_planner/worlds/myroom_long.world`
- map: `src/dwa_local_planner/maps/myroom_long.yaml`
- 大域的経路計画: `global_planner/GlobalPlanner`
- 現在の大域探索: A*
- 局所経路計画・速度制御: `dwa_local_planner/DWAPlannerROS`
- Gazebo GUI: 無効
- RViz: 有効

## 実行前の注意

Windows 側で開いているワークスペースは以下である。

```text
C:\Users\adachi\catkin_ws
```

一方、実際に WSL 側の `roslaunch` が読んでいるワークスペースは以下であることがある。

```text
/home/adachi/catkin_ws_2024_12_2/catkin_ws
```

そのため、Windows 側で launch や yaml を編集した場合は、必要に応じて WSL 側へコピーする。

```bash
cp /mnt/c/Users/adachi/catkin_ws/src/dwa_local_planner/launch/astar_dwa.launch \
   ~/catkin_ws_2024_12_2/catkin_ws/src/dwa_local_planner/launch/astar_dwa.launch
```

パラメータファイルを変更した場合も同様に、対応する WSL 側ファイルへ反映する。

## 起動方法

WSL のターミナルで以下を実行する。

```bash
cd ~/catkin_ws_2024_12_2/catkin_ws
source devel/setup.bash
export TURTLEBOT3_MODEL=burger
roslaunch dwa_local_planner astar_dwa.launch
```

`TURTLEBOT3_MODEL` は使用するモデルに合わせる。

```bash
export TURTLEBOT3_MODEL=burger
```

`waffle` や `waffle_pi` を使う場合は、この値を変更する。

## 再起動方法

起動中のターミナルで `Ctrl+C` を押す。

完全に停止したら、再度以下を実行する。

```bash
roslaunch dwa_local_planner astar_dwa.launch
```

もし次のようなエラーが出る場合は、古い Gazebo や ROS プロセスが残っている。

```text
model name turtlebot3_burger already exist
```

その場合は以下で停止してから再起動する。

```bash
pkill -f roslaunch
pkill gzserver
pkill rviz
pkill rosmaster
```

## 起動後の動作フロー

### 1. launch ファイルを読む

入口は以下である。

```text
src/dwa_local_planner/launch/astar_dwa.launch
```

このファイルが Gazebo、map server、AMCL、move_base、RViz を起動する。

### 2. Gazebo world を読み込む

現在は以下の world が指定されている。

```xml
<arg name="world_name" value="$(find dwa_local_planner)/worlds/myroom_long.world"/>
```

Gazebo GUI は重くなりやすいため、現在は無効である。

```xml
<arg name="gui" value="false"/>
```

画面は出ないが、`gzserver` によりシミュレーション本体は動作する。

### 3. TurtleBot3 を spawn する

ロボットは `robot_description` から URDF を生成し、Gazebo に spawn される。

```xml
<node pkg="gazebo_ros" type="spawn_model" name="spawn_urdf"
      args="-urdf -model turtlebot3_$(arg model) -x $(arg x_pos) -y $(arg y_pos) -z $(arg z_pos) -param robot_description" />
```

初期位置は以下である。

```xml
<arg name="x_pos" default="0.0"/>
<arg name="y_pos" default="0.0"/>
<arg name="z_pos" default="0.0"/>
```

### 4. map server が地図を配信する

現在は以下の地図が使われる。

```xml
<arg name="map_file" default="$(find dwa_local_planner)/maps/myroom_long.yaml"/>
```

`map_server` はこの yaml が参照する `.pgm` 画像を読み込み、`/map` として配信する。

```xml
<node pkg="map_server" name="map_server" type="map_server" args="$(arg map_file)"/>
```

### 5. AMCL が自己位置推定を行う

AMCL は以下の launch から起動される。

```text
src/turtlebot3/turtlebot3_navigation/launch/amcl.launch
```

RViz 上でロボット位置が地図と合わない場合は、`2D Pose Estimate` を使って自己位置を合わせる。

### 6. move_base が起動する

`move_base` はゴールを受け取り、大域経路計画と局所制御をつなぐ。

```xml
<node pkg="move_base" type="move_base" respawn="false" name="move_base" output="screen">
```

使用する planner は以下で指定される。

```xml
<param name="base_local_planner" value="dwa_local_planner/DWAPlannerROS" />
<param name="base_global_planner" value="global_planner/GlobalPlanner"/>
```

### 7. パラメータを読み込む

以下の yaml が読み込まれる。

```xml
<rosparam file="$(find dwa_local_planner)/param2/local_costmap_params.yaml" command="load" />
<rosparam file="$(find dwa_local_planner)/param2/global_costmap_params.yaml" command="load" />
<rosparam file="$(find dwa_local_planner)/param/move_base_params.yaml" command="load" />
<rosparam file="$(find dwa_local_planner)/param/dwa_local_planner_params_$(arg model).yaml" command="load" />
<rosparam file="$(find dwa_local_planner)/param2/global_planner_params.yaml" command="load" />
```

それぞれの役割は次の通り。

| ファイル | 役割 |
| --- | --- |
| `param2/local_costmap_params.yaml` | ロボット周辺の局所地図 |
| `param2/global_costmap_params.yaml` | map 全体の大域地図 |
| `param/move_base_params.yaml` | move_base の周期や recovery 設定 |
| `param/dwa_local_planner_params_burger.yaml` | DWA の速度、加速度、評価重み |
| `param2/global_planner_params.yaml` | A* / RA* や経路抽出の設定 |

### 8. RViz を起動する

RViz は以下の設定ファイルで起動する。

```text
src/dwa_local_planner/rviz/my_turtlebot3_navigation.rviz
```

WSLg で黒画面になることがあるため、現在は RViz のみソフトウェア描画にしている。

```xml
<node pkg="rviz" type="rviz" name="rviz" required="true" args="-d $(find dwa_local_planner)/rviz/my_turtlebot3_navigation.rviz">
  <env name="LIBGL_ALWAYS_SOFTWARE" value="1"/>
</node>
```

## ゴールの指定方法

RViz 上部の `2D Nav Goal` を使う。

1. RViz 上部の `2D Nav Goal` を押す
2. 地図上でゴール位置をクリックする
3. ドラッグして到着時の向きを指定する
4. マウスを離す

送信先は以下である。

```text
/move_base_simple/goal
```

コマンドで直接ゴールを送ることもできる。

```bash
rostopic pub /move_base_simple/goal geometry_msgs/PoseStamped "
header:
  frame_id: 'map'
pose:
  position:
    x: 2.0
    y: 0.0
    z: 0.0
  orientation:
    w: 1.0
" -1
```

## アルゴリズムの流れ

現在の構成では、次の順番で動く。

```text
RViz でゴールを送る
  ↓
move_base がゴールを受け取る
  ↓
global_planner が A* で大域経路を作る
  ↓
DWAPlannerROS が局所的な速度指令を計算する
  ↓
/cmd_vel が出る
  ↓
Gazebo 上の TurtleBot3 が動く
```

現在の `param2/global_planner_params.yaml` では以下になっている。

```yaml
use_dijkstra: false
```

このコードでは `false` のとき A* が使われる。

局所制御は以下で指定されているため DWA である。

```xml
<param name="base_local_planner" value="dwa_local_planner/DWAPlannerROS" />
```

## マップを変更する方法

`astar_dwa.launch` の以下 2 か所を変更する。

```xml
<arg name="map_file" default="$(find dwa_local_planner)/maps/myroom_long.yaml"/>
```

```xml
<arg name="world_name" value="$(find dwa_local_planner)/worlds/myroom_long.world"/>
```

map と world は対応する組み合わせにする。

例:

```xml
<arg name="map_file" default="$(find dwa_local_planner)/maps/myroom_plus.yaml"/>
<arg name="world_name" value="$(find dwa_local_planner)/worlds/myroom_plus.world"/>
```

```xml
<arg name="map_file" default="$(find dwa_local_planner)/maps/baraWall.yaml"/>
<arg name="world_name" value="$(find dwa_local_planner)/worlds/baraWall.world"/>
```

変更後は `roslaunch` を再起動する。

## よくある問題

### map が変わらない

Windows 側だけを編集していて、WSL 側にコピーされていない可能性がある。

```bash
cp /mnt/c/Users/adachi/catkin_ws/src/dwa_local_planner/launch/astar_dwa.launch \
   ~/catkin_ws_2024_12_2/catkin_ws/src/dwa_local_planner/launch/astar_dwa.launch
```

また、起動中の `map_server` は起動時の map を使い続けるため、launch の再起動が必要である。

### model name already exist

前回の Gazebo が残っている。

```bash
pkill -f roslaunch
pkill gzserver
pkill rviz
pkill rosmaster
```

### 違う方向へ動く

AMCL の自己位置推定がズレている可能性が高い。

RViz の `2D Pose Estimate` で、ロボット位置と向きを地図に合わせる。

### 大域経路は正しいのに壁に突っ込む

A* ではなく DWA または local costmap 側の問題である。

確認すべきファイル:

```text
src/dwa_local_planner/param/dwa_local_planner_params_burger.yaml
src/dwa_local_planner/param/costmap_common_params_burger.yaml
src/dwa_local_planner/param2/local_costmap_params.yaml
```

特に以下が関係する。

- `occdist_scale`
- `path_distance_bias`
- `goal_distance_bias`
- `inflation_radius`
- `acc_lim_x`
- `max_vel_x`
- `min_vel_trans`

### ログ容量の警告が出る

以下で確認する。

```bash
rosclean check
```

削除する場合は、起動中の ROS を止めてから実行する。

```bash
rosclean purge
```

