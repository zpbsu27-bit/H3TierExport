#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace h3tier::apngscale {
// Preview geometry only. Never crop to alpha, rewrite images, or normalize bodies.
inline double factor(const std::vector<std::pair<std::uint32_t,std::uint32_t>>& canvases,
                     std::size_t selected,bool wholeGroup,int viewportW,int viewportH,int padding=12){
 if(canvases.empty()||selected>=canvases.size())return 1;
 auto [width,height]=canvases[selected];
 if(wholeGroup)for(const auto& size:canvases){width=std::max(width,size.first);height=std::max(height,size.second);}
 if(!width||!height)return 1;
 return std::min(double(std::max(1,viewportW-2*padding))/width,
                 double(std::max(1,viewportH-2*padding))/height);
}
} // namespace h3tier::apngscale
