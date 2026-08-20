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
#include <costmap_2d/cost_values.h> //障害物のコスト値の定義
#include <global_planner/rastar.h> //実装するクラスRAStarExpansionの宣言

namespace global_planner {//global_plannerという名前空間の開始。名前の衝突を防ぐための「グループ分け」。

RAStarExpansion::RAStarExpansion(PotentialCalculator *p_calc, int xs, int ys)
    : Expander(p_calc, xs, ys) {}//スタンス生成時に呼ばれる初期化処理 p_calc(ポテンシャル計算用オブジェクト)、xs・ys(地図の幅・高さ)

bool RAStarExpansion::calculatePotentials(unsigned char *costs, double start_x,//スタートからゴールまでの各マスの到達コスト(ポテンシャル)」を計算する関数
                                          double start_y, double end_x,
                                          double end_y, int cycles,
                                          float *potential) {
    queue_.clear();//前回の計算結果が残らないようにするための初期化
    int start_i = toIndex(start_x, start_y);//2次元座標(x, y)を1次元配列のインデックスに変換
    queue_.push_back(RIndex(start_i, 0));//スタート地点を、コスト0としてキューに追加

    std::fill(potential, potential + ns_, POT_HIGH);//potential配列全体を「非常に大きい値(POT_HIGH)」で初期化。まだ誰もそのマスに到達していない=「無限大コスト」を意味する。ns_は地図の全マス数。
    potential[start_i] = 0;//スタート地点自身のコストは0に設定。

    int goal_i = toIndex(end_x, end_y);//ゴール座標もインデックスに変換しておく(毎ループで座標変換しないための事前計算)。
    int cycle = 0;//ループ回数を数えるカウンタを0で初期化。
    float tBreak = 1 + 1 / (nx_ + ny_);//「タイブレーク」用の係数。複数の経路が同じコストになったとき、より直線的な経路を優先させるための微小な補正値

    while (!queue_.empty() && cycle < cycles) {//「キューが空になる」か「最大ループ回数に達する」まで繰り返す。
        RIndex top = queue_[0];//キューの先頭(=現時点で最もコストが低い候補)を取り出す。
        std::pop_heap(queue_.begin(), queue_.end(), Rgreater1());//ヒープから最小値を取り除く標準的な手順,Rgreater1()は比較関数(コストの大小比較ルール)
        queue_.pop_back();

        int i = top.i;//取り出したマスのインデックスを変数iに保存。
        if (i == goal_i) {//もし取り出したマスがゴールなら、探索成功。何回のループでゴールに到達したかを記録し、trueを返して終了。
            expansion_count_ = cycle;
            return true;
        }
        //現在のマスから見て「右」「左」「下」「上」の4方向の隣接マスを、それぞれadd関数でキューに追加候補として調べる。
        add(costs, potential, potential[i], i + 1, end_x, end_y, tBreak, nx_);
        add(costs, potential, potential[i], i - 1, end_x, end_y, tBreak, nx_);
        add(costs, potential, potential[i], i + nx_, end_x, end_y, tBreak, ny_);
        add(costs, potential, potential[i], i - nx_, end_x, end_y, tBreak, ny_);
        cycle++;//ループ回数を1増やす。
    }

    // Search exhausted the open list or the cycle budget without reaching the
    // goal; still record how many nodes were expanded so callers can tell
    // "failed after expanding 5 nodes" from "failed after expanding 50000".
    expansion_count_ = cycle;//実際に展開した回数を記録し、探索失敗としてfalseを返す。
    return false;
}

void RAStarExpansion::add(unsigned char *costs, float *potential,//隣接マスを調べて、必要ならキューに追加するヘルパー関数です。
                          float prev_potential, int next_i, int end_x,
                          int end_y, float tBreak, int dist) {
    if (next_i < 0 || next_i >= ns_)//地図の範囲外(マイナスや配列サイズ以上)なら、何もせず終了
        return;
    if (potential[next_i] < POT_HIGH)//そのマスが既に(より良いコストで)探索済みなら、何もせず終了
        return;
    if (costs[next_i] >= lethal_cost_ &&
        !(unknown_ && costs[next_i] == costmap_2d::NO_INFORMATION))//そのマスが「致命的な障害物」(lethal_cost_以上のコスト)であり、かつ「未知領域を許可する設定で、かつそのマスが未知領域」という例外にも当てはまらない場合は、通行不可として処理を打ち切る。
        return;

    potential[next_i] = prev_potential + neutral_cost_ + costs[next_i];//そのマスへの到達コストを計算して記録。「一つ前のマスまでのコスト」+「基本移動コスト」+「そのマス自体のコスト」の合計。
    int x = next_i % nx_, y = next_i / nx_;//1次元インデックスを、2次元座標(x, y)に戻す。% nx_で横位置、/ nx_で縦位置を求める。
    float distance = abs(end_x - x) + abs(end_y - y);//そのマスからゴールまでの「マンハッタン距離」(斜め移動を考えない、縦横の合計距離)を計算。これがA*でいう「ヒューリスティック(推定コスト)」。

    queue_.push_back(
        RIndex(next_i, potential[next_i] + distance * neutral_cost_ * tBreak));//「実際のコスト」+「ゴールまでの推定コスト×補正係数」を優先度として、そのマスをキューに追加。これがA*アルゴリズムの核心部分(f = g + h)。
    std::push_heap(queue_.begin(), queue_.end(), Rgreater1());//追加した要素をヒープ構造として正しい位置に配置し直す(優先度付きキューを維持するための操作)。
}

} // end namespace global_planner
