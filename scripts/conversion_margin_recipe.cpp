// SPDX-License-Identifier: GPL-2.0-or-later
// Offline recipe exporter adapted from Blender 3.4.1 texture_margin.cc.
// Copyright 2001-2002 NaN Holding BV. All rights reserved.
// The spatial algorithm remains offline. WASM executes the emitted pixel operations.
#include <vector>
#include <algorithm>
#include <cmath>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define BLI_assert assert
struct float2{float x=0,y=0;float2(){} float2(float x,float y):x(x),y(y){} float&operator[](int i){return i?y:x;}};
float2 operator+(float2 a,float2 b){return {a.x+b.x,a.y+b.y};}
float2 operator-(float2 a,float2 b){return {a.x-b.x,a.y-b.y};}
float2 operator*(float a,float2 b){return {a*b.x,a*b.y};}
namespace math{float dot(float2 a,float2 b){return a.x*b.x+a.y*b.y;}float length_squared(float2 a){return dot(a,a);}float length(float2 a){return sqrtf(length_squared(a));}}
template<class T>struct Vector:std::vector<T>{void append(T v){this->push_back(v);}T pop_last(){T v=this->back();this->pop_back();return v;}};
template<class T>using Array=std::vector<T>;
struct MPoly{int loopstart,totloop;};struct MLoop{int e;};struct MLoopUV{float uv[2];};
struct ImBuf{int x,y;};
void copy_v2_v2(float*a,const float*b){a[0]=b[0];a[1]=b[1];}
std::vector<uint32_t> commands;
void putf(float f){uint32_t b;memcpy(&b,&f,4);commands.push_back(b);}
int index(int x,int y,int w,int h){return x<0||y<0||x>=w||y>=h?-1:y*w+x;}
void bilinear_interpolation(ImBuf*b,ImBuf*,float u,float v,int x,int y){
 int x1=int(floorf(u)),x2=int(ceilf(u)),y1=int(floorf(v)),y2=int(ceilf(v));
 float a=u-floorf(u),bb=v-floorf(v);
 commands.push_back(2);commands.push_back(y*b->x+x);
 for(int i:{index(x1,y1,b->x,b->y),index(x2,y1,b->x,b->y),index(x1,y2,b->x,b->y),index(x2,y2,b->x,b->y)})commands.push_back(uint32_t(i));
 for(float f:{(1.f-a)*(1.f-bb),a*(1.f-bb),(1.f-a)*bb,a*bb})putf(f);
}
void extend(char*mask,int w,int h,int passes){
 std::vector<char> next(mask,mask+w*h);
 for(int p=0;p<passes;p++){
  commands.push_back(0);
  for(int y=0;y<h;y++)for(int x=0;x<w;x++){
   int at=y*w+x;if(mask[at])continue;
   auto set=[&](int x,int y){int i=index(x,y,w,h);return i>=0&&mask[i];};
   if(!(set(x-1,y)||set(x+1,y)||set(x,y-1)||set(x,y+1)))continue;
   std::vector<uint32_t> entries;uint32_t sum=0;
   for(int dx=-1;dx<=1;dx++)for(int dy=-1;dy<=1;dy++)if((dx||dy)&&set(x+dx,y+dy)){
    uint32_t weight=(!dx||!dy)?2:1;entries.push_back(index(x+dx,y+dy,w,h));entries.push_back(weight);sum+=weight;
   }
   if(sum){commands.push_back(1);commands.push_back(at);commands.push_back(entries.size()/2);commands.push_back(sum);commands.insert(commands.end(),entries.begin(),entries.end());next[at]=2;}
  }
  memcpy(mask,next.data(),w*h);commands.push_back(3);
 }
}
class TextureMarginMap {
  static const int directions[8][2];
  static const int distances[8];

  /** Maps UV-edges to their corresponding UV-edge. */
  Vector<int> loop_adjacency_map_;
  /** Maps UV-edges to their corresponding polygon. */
  Array<int> loop_to_poly_map_;

  int w_, h_;
  float uv_offset_[2];
  Vector<uint32_t> pixel_data_;

  uint32_t value_to_store_;
  char *mask_;

  MPoly const *mpoly_;
  MLoop const *mloop_;
  MLoopUV const *mloopuv_;
  int totpoly_;
  int totloop_;
  int totedge_;

 public:
  TextureMarginMap(size_t w,
                   size_t h,
                   const float uv_offset[2],
                   MPoly const *mpoly,
                   MLoop const *mloop,
                   MLoopUV const *mloopuv,
                   int totpoly,
                   int totloop,
                   int totedge)
      : w_(w),
        h_(h),
        mpoly_(mpoly),
        mloop_(mloop),
        mloopuv_(mloopuv),
        totpoly_(totpoly),
        totloop_(totloop),
        totedge_(totedge)
  {
    copy_v2_v2(uv_offset_, uv_offset);

    pixel_data_.resize(w_ * h_, 0xFFFFFFFF);



    build_tables();
  }

  ~TextureMarginMap()
  {

  }

  inline void set_pixel(int x, int y, uint32_t value)
  {
    BLI_assert(x < w_);
    BLI_assert(x >= 0);
    pixel_data_[y * w_ + x] = value;
  }

  inline uint32_t get_pixel(int x, int y) const
  {
    if (x < 0 || y < 0 || x >= w_ || y >= h_) {
      return 0xFFFFFFFF;
    }

    return pixel_data_[y * w_ + x];
  }

/* The map contains 2 kinds of pixels: DijkstraPixels and polygon indices. The top bit determines
 * what kind it is. With the top bit set, it is a 'dijkstra' pixel. The bottom 4 bits encode the
 * direction of the shortest path and the remaining 27 bits are used to store the distance. If
 * the top bit  is not set, the rest of the bits is used to store the polygon index.
 */
#define PackDijkstraPixel(dist, dir) (0x80000000 + ((dist) << 4) + (dir))
#define DijkstraPixelGetDistance(dp) (((dp) ^ 0x80000000) >> 4)
#define DijkstraPixelGetDirection(dp) ((dp)&0xF)
#define IsDijkstraPixel(dp) ((dp)&0x80000000)
#define DijkstraPixelIsUnset(dp) ((dp) == 0xFFFFFFFF)

  /**
   * Use dijkstra's algorithm to 'grow' a border around the polygons marked in the map.
   * For each pixel mark which direction is the shortest way to a polygon.
   */
  void grow_dijkstra(int margin)
  {
    class DijkstraActivePixel {
     public:
      DijkstraActivePixel(int dist, int _x, int _y) : distance(dist), x(_x), y(_y)
      {
      }
      int distance;
      int x, y;
    };
    auto cmp_dijkstrapixel_fun = [](DijkstraActivePixel const &a1, DijkstraActivePixel const &a2) {
      return a1.distance > a2.distance;
    };

    Vector<DijkstraActivePixel> active_pixels;
    for (int y = 0; y < h_; y++) {
      for (int x = 0; x < w_; x++) {
        if (DijkstraPixelIsUnset(get_pixel(x, y))) {
          for (int i = 0; i < 8; i++) {
            int xx = x - directions[i][0];
            int yy = y - directions[i][1];

            if (xx >= 0 && xx < w_ && yy >= 0 && yy < w_ && !IsDijkstraPixel(get_pixel(xx, yy))) {
              set_pixel(x, y, PackDijkstraPixel(distances[i], i));
              active_pixels.append(DijkstraActivePixel(distances[i], x, y));
              break;
            }
          }
        }
      }
    }

    /* Not strictly needed because at this point it already is a heap. */
#if 0
    std::make_heap(active_pixels.begin(), active_pixels.end(), cmp_dijkstrapixel_fun);
#endif

    while (active_pixels.size()) {
      std::pop_heap(active_pixels.begin(), active_pixels.end(), cmp_dijkstrapixel_fun);
      DijkstraActivePixel p = active_pixels.pop_last();

      int dist = p.distance;

      if (dist < 2 * (margin + 1)) {
        for (int i = 0; i < 8; i++) {
          int x = p.x + directions[i][0];
          int y = p.y + directions[i][1];
          if (x >= 0 && x < w_ && y >= 0 && y < h_) {
            uint32_t dp = get_pixel(x, y);
            if (IsDijkstraPixel(dp) && (DijkstraPixelGetDistance(dp) > dist + distances[i])) {
              BLI_assert(DijkstraPixelGetDirection(dp) != i);
              set_pixel(x, y, PackDijkstraPixel(dist + distances[i], i));
              active_pixels.append(DijkstraActivePixel(dist + distances[i], x, y));
              std::push_heap(active_pixels.begin(), active_pixels.end(), cmp_dijkstrapixel_fun);
            }
          }
        }
      }
    }
  }

  /**
   * Walk over the map and for margin pixels follow the direction stored in the bottom 3
   * bits back to the polygon.
   * Then look up the pixel from the next polygon.
   */
  void lookup_pixels(ImBuf *ibuf, char *mask, int maxPolygonSteps)
  {
    for (int y = 0; y < h_; y++) {
      for (int x = 0; x < w_; x++) {
        uint32_t dp = get_pixel(x, y);
        if (IsDijkstraPixel(dp) && !DijkstraPixelIsUnset(dp)) {
          int dist = DijkstraPixelGetDistance(dp);
          int direction = DijkstraPixelGetDirection(dp);

          int xx = x;
          int yy = y;

          /* Follow the dijkstra directions to find the polygon this margin pixels belongs to. */
          while (dist > 0) {
            xx -= directions[direction][0];
            yy -= directions[direction][1];
            dp = get_pixel(xx, yy);
            dist -= distances[direction];
            BLI_assert(!dist || (dist == DijkstraPixelGetDistance(dp)));
            direction = DijkstraPixelGetDirection(dp);
          }

          uint32_t poly = get_pixel(xx, yy);

          BLI_assert(!IsDijkstraPixel(poly));

          float destX, destY;

          int other_poly;
          bool found_pixel_in_polygon = false;
          if (lookup_pixel_polygon_neighbourhood(x, y, &poly, &destX, &destY, &other_poly)) {

            for (int i = 0; i < maxPolygonSteps; i++) {
              /* Force to pixel grid. */
              int nx = int(round(destX));
              int ny = int(round(destY));
              uint32_t polygon_from_map = get_pixel(nx, ny);
              if (other_poly == polygon_from_map) {
                found_pixel_in_polygon = true;
                break;
              }

              float dist_to_edge;
              /* Look up again, but starting from the polygon we were expected to land in. */
              if (!lookup_pixel(nx, ny, other_poly, &destX, &destY, &other_poly, &dist_to_edge)) {
                found_pixel_in_polygon = false;
                break;
              }
            }

            if (found_pixel_in_polygon) {
              bilinear_interpolation(ibuf, ibuf, destX, destY, x, y);
              /* Add our new pixels to the assigned pixel map. */
              mask[y * w_ + x] = 1;
            }
          }
        }
        else if (DijkstraPixelIsUnset(dp) || !IsDijkstraPixel(dp)) {
          /* These are not margin pixels, make sure the extend filter which is run after this step
           * leaves them alone.
           */
          mask[y * w_ + x] = 1;
        }
      }
    }
  }

 private:
  float2 uv_to_xy(MLoopUV const &mloopuv) const
  {
    float2 ret;
    ret.x = (((mloopuv.uv[0] - uv_offset_[0]) * w_) - (0.5f + 0.001f));
    ret.y = (((mloopuv.uv[1] - uv_offset_[1]) * h_) - (0.5f + 0.001f));
    return ret;
  }

  void build_tables()
  {
    loop_to_poly_map_.resize(totloop_);
    for(int p=0;p<totpoly_;p++)for(int l=0;l<mpoly_[p].totloop;l++)loop_to_poly_map_[mpoly_[p].loopstart+l]=p;

    loop_adjacency_map_.resize(totloop_, -1);

    Vector<int> tmpmap;
    tmpmap.resize(totedge_, -1);

    for (size_t i = 0; i < totloop_; i++) {
      int edge = mloop_[i].e;
      if (tmpmap[edge] == -1) {
        loop_adjacency_map_[i] = -1;
        tmpmap[edge] = i;
      }
      else {
        BLI_assert(tmpmap[edge] >= 0);
        loop_adjacency_map_[i] = tmpmap[edge];
        loop_adjacency_map_[tmpmap[edge]] = i;
      }
    }
  }

  /**
   * Call lookup_pixel for the start_poly. If that fails, try the adjacent polygons as well.
   * Because the Dijkstra is not very exact in determining which polygon is the closest, the
   * polygon we need can be the one next to the one the Dijkstra map provides. To prevent missing
   * pixels also check the neighboring polygons.
   */
  bool lookup_pixel_polygon_neighbourhood(
      float x, float y, uint32_t *r_start_poly, float *r_destx, float *r_desty, int *r_other_poly)
  {
    float found_dist;
    if (lookup_pixel(x, y, *r_start_poly, r_destx, r_desty, r_other_poly, &found_dist)) {
      return true;
    }

    int loopstart = mpoly_[*r_start_poly].loopstart;
    int totloop = mpoly_[*r_start_poly].totloop;

    float destx, desty;
    int foundpoly;

    float mindist = -1.0f;

    /* Loop over all adjacent polygons and determine which edge is closest.
     * This could be optimized by only inspecting neighbors which are on the edge of an island.
     * But it seems fast enough for now and that would add a lot of complexity. */
    for (int i = 0; i < totloop; i++) {
      int otherloop = loop_adjacency_map_[i + loopstart];

      if (otherloop < 0) {
        continue;
      }

      uint32_t poly = loop_to_poly_map_[otherloop];

      if (lookup_pixel(x, y, poly, &destx, &desty, &foundpoly, &found_dist)) {
        if (mindist < 0.0f || found_dist < mindist) {
          mindist = found_dist;
          *r_other_poly = foundpoly;
          *r_destx = destx;
          *r_desty = desty;
          *r_start_poly = poly;
        }
      }
    }

    return mindist >= 0.0f;
  }

  /**
   * Find which edge of the src_poly is closest to x,y. Look up its adjacent UV-edge and polygon.
   * Then return the location of the equivalent pixel in the other polygon.
   * Returns true if a new pixel location was found, false if it wasn't, which can happen if the
   * margin pixel is on a corner, or the UV-edge doesn't have an adjacent polygon.
   */
  bool lookup_pixel(float x,
                    float y,
                    int src_poly,
                    float *r_destx,
                    float *r_desty,
                    int *r_other_poly,
                    float *r_dist_to_edge)
  {
    float2 point(x, y);

    *r_destx = *r_desty = 0;

    int found_edge = -1;
    float found_dist = -1;
    float found_t = 0;

    /* Find the closest edge on which the point x,y can be projected.
     */
    for (size_t i = 0; i < mpoly_[src_poly].totloop; i++) {
      int l1 = mpoly_[src_poly].loopstart + i;
      int l2 = l1 + 1;
      if (l2 >= mpoly_[src_poly].loopstart + mpoly_[src_poly].totloop) {
        l2 = mpoly_[src_poly].loopstart;
      }
      /* edge points */
      float2 edgepoint1 = uv_to_xy(mloopuv_[l1]);
      float2 edgepoint2 = uv_to_xy(mloopuv_[l2]);
      /* Vector AB is the vector from the first edge point to the second edge point.
       * Vector AP is the vector from the first edge point to our point under investigation. */
      float2 ab = edgepoint2 - edgepoint1;
      float2 ap = point - edgepoint1;

      /* Project ap onto ab. */
      float dotv = math::dot(ab, ap);

      float ablensq = math::length_squared(ab);

      float t = dotv / ablensq;

      if (t >= 0.0 && t <= 1.0) {

        /* Find the point on the edge closest to P */
        float2 reflect_point = edgepoint1 + (t * ab);
        /* This is the vector to P, so 90 degrees out from the edge. */
        float2 reflect_vec = reflect_point - point;

        float reflectLen = sqrt(reflect_vec[0] * reflect_vec[0] + reflect_vec[1] * reflect_vec[1]);
        float cross = ab[0] * reflect_vec[1] - ab[1] * reflect_vec[0];
        /* Only if P is on the outside of the edge, which means the cross product is positive,
         * we consider this edge.
         */
        bool valid = (cross > 0.0);

        if (valid && (found_dist < 0 || reflectLen < found_dist)) {
          /* Stother_ab the info of the closest edge so far. */
          found_dist = reflectLen;
          found_t = t;
          found_edge = i + mpoly_[src_poly].loopstart;
        }
      }
    }

    if (found_edge < 0) {
      return false;
    }

    *r_dist_to_edge = found_dist;

    /* Get the 'other' edge. I.E. the UV edge from the neighbor polygon. */
    int other_edge = loop_adjacency_map_[found_edge];

    if (other_edge < 0) {
      return false;
    }

    int dst_poly = loop_to_poly_map_[other_edge];

    if (r_other_poly) {
      *r_other_poly = dst_poly;
    }

    int other_edge2 = other_edge + 1;
    if (other_edge2 >= mpoly_[dst_poly].loopstart + mpoly_[dst_poly].totloop) {
      other_edge2 = mpoly_[dst_poly].loopstart;
    }

    float2 other_edgepoint1 = uv_to_xy(mloopuv_[other_edge]);
    float2 other_edgepoint2 = uv_to_xy(mloopuv_[other_edge2]);

    /* Calculate the vector from the order edges last point to its first point. */
    float2 other_ab = other_edgepoint1 - other_edgepoint2;
    float2 other_reflect_point = other_edgepoint2 + (found_t * other_ab);
    float2 perpendicular_other_ab;
    perpendicular_other_ab.x = other_ab.y;
    perpendicular_other_ab.y = -other_ab.x;

    /* The new point is dound_dist distance from other_reflect_point at a 90 degree angle to
     * other_ab */
    float2 new_point = other_reflect_point + (found_dist / math::length(perpendicular_other_ab)) *
                                                 perpendicular_other_ab;

    *r_destx = new_point.x;
    *r_desty = new_point.y;

    return true;
  }
};  // class TextureMarginMap

const int TextureMarginMap::directions[8][2] = {
    {-1, 0}, {-1, -1}, {0, -1}, {1, -1}, {1, 0}, {1, 1}, {0, 1}, {-1, 1}};
const int TextureMarginMap::distances[8] = {2, 3, 2, 3, 2, 3, 2, 3};


int main(int argc,char**argv){
 assert(argc==3);FILE*f=fopen(argv[1],"rb");assert(f);auto read=[&](void*p,size_t n){assert(fread(p,1,n,f)==n);};
 int dim[5];read(dim,sizeof(dim));int w=dim[0],h=dim[1],np=dim[2],nl=dim[3],ne=dim[4];assert(w==1024&&h==1024&&np>0&&nl>0);
 std::vector<MPoly>poly(np);std::vector<MLoop>loops(nl);std::vector<MLoopUV>uv(nl);std::vector<uint32_t>pixels(w*h);std::vector<char>mask(w*h);
 read(poly.data(),np*sizeof(MPoly));read(loops.data(),nl*sizeof(MLoop));read(uv.data(),nl*sizeof(MLoopUV));read(pixels.data(),w*h*4);read(mask.data(),w*h);fclose(f);
 float offset[2]={0,0};TextureMarginMap map(w,h,offset,poly.data(),loops.data(),uv.data(),np,nl,ne);
 for(int y=0;y<h;y++)for(int x=0;x<w;x++)map.set_pixel(x,y,pixels[y*w+x]);
 auto temporary=mask;extend(temporary.data(),w,h,2);map.grow_dijkstra(4);ImBuf ibuf{w,h};map.lookup_pixels(&ibuf,mask.data(),3);extend(mask.data(),w,h,4);
 f=fopen(argv[2],"wb");assert(f);fwrite("MRG1",1,4,f);fwrite(commands.data(),4,commands.size(),f);fclose(f);printf("Margin recipe: %zu bytes\n",commands.size()*4+4);
}
