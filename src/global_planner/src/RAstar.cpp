/*********************************************************************
 *
 * Software License Agreement (BSD License)
 *
 *  Copyright (c) 2008, 2013, Willow Garage, Inc.
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
 *         David V. Lu!!
 *********************************************************************/
#include <costmap_2d/cost_values.h>
#include <global_planner/rastar.h>

namespace global_planner {

RAStarExpansion::RAStarExpansion(PotentialCalculator *p_calc, int xs, int ys)
    : Expander(p_calc, xs, ys) {}

double heuristic_cost(const Node& from, const Node& to) {
    // ヒューリスティック関数の実装（仮の実装）
    return abs(to.x - from.x) + abs(to.y - from.y);
}

bool RAStarExpansion::calculatePotentials(unsigned char *costs, double start_x,
                                          double start_y, double end_x,
                                          double end_y, int cycles,
                                          float *potential) {

    int start_i = toIndex(start_x, start_y);
    int goal_i = toIndex(end_x, end_y);
    std::fill(potential, potential + ns_, POT_HIGH);
    potential[start_i] = 0;
    
    //どっちがnx_なのか難しい
    int rows = nx_;
    int cols = ny_;

    double tBreak = 1 + 1.0 / (rows + cols);

    Node start = {start_x, start_y};
    Node goal = {end_x, end_y};
    openSet.insert(start);//開始地点の追加

    //スタート地点から座標(x, y)までの実距離
    std::vector<std::vector<double>> gScore(rows, std::vector<double>(cols, POT_HIGH));
    gScore[start.x][start.y] = 0;

    //座標(x, y)における評価値
    std::vector<std::vector<double>> fScore(rows, std::vector<double>(cols, POT_HIGH));
    fScore[start.x][start.y] = heuristic_cost(start, goal);

    //gptがこのコード単体で経路を構築するために追加した配列
    //このパッケージではおそらくstd::vector<Index> queue_にノード順を格納し経路を記憶させている？
    //queueはただ隣接ノード群を格納するためのもの
    // std::vector<std::vector<Node>> cameFrom(rows, std::vector<Node>(cols, {-1, -1}));

    //メモリエラー（無限ループ）はなくなったがnopath
    while (!openSet.empty()) {
        Node current = *std::min_element(openSet.begin(), openSet.end(), [&fScore](const Node& a, const Node& b) {
            return fScore[a.x][a.y] < fScore[b.x][b.y];
        });
        openSet.erase(current);
        add(&gScore, &fScore, current, -1, 0, potential, costs, tBreak, goal, &openSet);
        add(&gScore, &fScore, current, 0, -1, potential, costs, tBreak, goal, &openSet);
        add(&gScore, &fScore, current, 0,  1, potential, costs, tBreak, goal, &openSet);
        add(&gScore, &fScore, current, 1,  0, potential, costs, tBreak, goal, &openSet);
    }

    // if (gScore[goal.x][goal.y] != POT_HIGH) {
    if (potential[goal_i] != POT_HIGH) {
        return true;
    } else {
        return false;
    }
}

void RAStarExpansion::add(std::vector<std::vector<double>> *gScore, std::vector<std::vector<double>> *fScore,
                            Node current, int dx, int dy, float *potential, unsigned char *costs, double tBreak, Node goal, std::set<Node> *openSet){
    double nx = current.x + dx;
    double ny = current.y + dy;

    Node neighbor = {nx, ny};
    int next_i = toIndex(neighbor.x, neighbor.y);
    //近隣ノード追加条件
    if (next_i < 0 || next_i >= ns_) return;
    if (potential[next_i] < POT_HIGH) return;
    if (costs[next_i] >= lethal_cost_ && !(unknown_ && costs[next_i] == costmap_2d::NO_INFORMATION)) return;
    
    double dist_edge = 1;
    double tentativeGScore = (*gScore)[current.x][current.y] + dist_edge;
    if (tentativeGScore < (*gScore)[neighbor.x][neighbor.y]) {
        // (*cameFrom)[neighbor.x][neighbor.y] = current;
        (*gScore)[neighbor.x][neighbor.y] = tentativeGScore;

        (*fScore)[neighbor.x][neighbor.y] = (*gScore)[neighbor.x][neighbor.y] + tBreak * heuristic_cost(neighbor, goal);
        // (*fScore)[neighbor.x][neighbor.y] = (*gScore)[neighbor.x][neighbor.y] + tBreak * heuristic_cost(neighbor, goal) + costs[next_i]; //コスト（inflation layer）を追加
        potential[next_i] = (*fScore)[neighbor.x][neighbor.y];

        openSet->insert(neighbor);
        ROS_INFO("%d", openSet->size());
        
    }
}

} // end namespace global_planner
