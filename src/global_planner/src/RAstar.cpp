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

bool RAStarExpansion::calculatePotentials(unsigned char *costs, double start_x,
                                          double start_y, double end_x,
                                          double end_y, int cycles,
                                          float *potential) {
    queue_.clear();
    int start_i = toIndex(start_x, start_y);
    queue_.push_back(RIndex(start_i, 0));

    std::fill(potential, potential + ns_, POT_HIGH);
    potential[start_i] = 0;

    int goal_i = toIndex(end_x, end_y);
    int cycle = 0;
    float tBreak = 1 + 1 / (nx_ + ny_);

    while (!queue_.empty() && cycle < cycles) {
        RIndex top = queue_[0];
        std::pop_heap(queue_.begin(), queue_.end(), Rgreater1());
        queue_.pop_back();

        int i = top.i;
        if (i == goal_i) {
            expansion_count_ = cycle;
            return true;
        }

        add(costs, potential, potential[i], i + 1, end_x, end_y, tBreak, nx_);
        add(costs, potential, potential[i], i - 1, end_x, end_y, tBreak, nx_);
        add(costs, potential, potential[i], i + nx_, end_x, end_y, tBreak, ny_);
        add(costs, potential, potential[i], i - nx_, end_x, end_y, tBreak, ny_);
        cycle++;
    }

    // Search exhausted the open list or the cycle budget without reaching the
    // goal; still record how many nodes were expanded so callers can tell
    // "failed after expanding 5 nodes" from "failed after expanding 50000".
    expansion_count_ = cycle;
    return false;
}

void RAStarExpansion::add(unsigned char *costs, float *potential,
                          float prev_potential, int next_i, int end_x,
                          int end_y, float tBreak, int dist) {
    if (next_i < 0 || next_i >= ns_)
        return;
    if (potential[next_i] < POT_HIGH)
        return;
    if (costs[next_i] >= lethal_cost_ &&
        !(unknown_ && costs[next_i] == costmap_2d::NO_INFORMATION))
        return;

    potential[next_i] = prev_potential + neutral_cost_ + costs[next_i];
    int x = next_i % nx_, y = next_i / nx_;
    float distance = abs(end_x - x) + abs(end_y - y);

    queue_.push_back(
        RIndex(next_i, potential[next_i] + distance * neutral_cost_ * tBreak));
    std::push_heap(queue_.begin(), queue_.end(), Rgreater1());
}

} // end namespace global_planner
