// SPDX-License-Identifier: Apache-2.0
// Copyright 2011-2022 Blender Foundation
// Pinned Cycles PMJ asset generator, derived from Blender 3.4.1.
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <vector>
#include <string>
#include <algorithm>
using uint=uint32_t;
#define ccl_device_inline inline
#define ccl_device_forceinline inline
#define ccl_device inline
using std::min;using std::max;
struct float2{float x,y;};
uint reverse_integer_bits(uint x){return __builtin_bitreverse32(x);}
int count_leading_zeros(uint x){return __builtin_clz(x);}
uint hash_uint(uint k){uint a=0xdeadbf00,b=a,c=a;a+=k;
#define R(x,n) ((x<<n)|(x>>(32-n)))
c^=b;c-=R(b,14);a^=c;a-=R(c,11);b^=a;b-=R(a,25);c^=b;c-=R(b,16);a^=c;a-=R(c,4);b^=a;b-=R(a,14);c^=b;c-=R(b,24);return c;}
ccl_device_forceinline float uint_to_float_excl(uint n)
{
  // Note: we divide by 4294967808 instead of 2^32 because the latter
  // leads to a [0.0, 1.0] mapping instead of [0.0, 1.0) due to floating
  // point rounding error. 4294967808 unfortunately leaves (precisely)
  // one unused ulp between the max number this outputs and 1.0, but
  // that's the best you can do with this construction.
  return (float)n * (1.0f / 4294967808.0f);
}
ccl_device_inline uint hash_hp_uint(uint i)
{
  // The actual mixing function from Hash Prospector.
  i ^= i >> 16;
  i *= 0x21f0aaad;
  i ^= i >> 15;
  i *= 0xd35a2d97;
  i ^= i >> 15;

  // The xor is just to make input zero not map to output zero.
  // The number is randomly selected and isn't special.
  return i ^ 0xe6fe3beb;
}
ccl_device_inline float hash_hp_float(uint i)
{
  return uint_to_float_excl(hash_hp_uint(i));
}
ccl_device_inline uint hash_wang_seeded_uint(uint i, uint seed)
{
  i = (i ^ 61) ^ seed;
  i += i << 3;
  i ^= i >> 4;
  i *= 0x27d4eb2d;
  return i;
}
ccl_device_inline uint hash_shuffle_uint(uint i, uint length, uint seed)
{
  i = i % length;
  uint mask = (1 << (32 - count_leading_zeros(length - 1))) - 1;

  do {
    i ^= seed;
    i *= 0xe170893d;
    i ^= seed >> 16;
    i ^= (i & mask) >> 4;
    i ^= seed >> 8;
    i *= 0x0929eb3f;
    i ^= seed >> 23;
    i ^= (i & mask) >> 1;
    i *= 1 | seed >> 27;
    i *= 0x6935fa69;
    i ^= (i & mask) >> 11;
    i *= 0x74dcb303;
    i ^= (i & mask) >> 2;
    i *= 0x9e501cc3;
    i ^= (i & mask) >> 2;
    i *= 0xc860a3df;
    i &= mask;
    i ^= i >> 5;
  } while (i >= length);

  return i;
}
ccl_device_inline uint reversed_bit_owen(uint n, uint seed)
{
  n ^= n * 0x3d20adea;
  n += seed;
  n *= (seed >> 16) | 1;
  n ^= n * 0x05526c56;
  n ^= n * 0x53a22864;

  return n;
}
ccl_device_inline uint nested_uniform_scramble(uint i, uint seed)
{
  return reverse_integer_bits(reversed_bit_owen(reverse_integer_bits(i), seed));
}
void progressive_multi_jitter_02_generate_2D(float2 points[], int size, int rng_seed)
{
  /* Xor values for generating the PMJ02 sequence.  These permute the
   * order we visit the strata in, which is what makes the code below
   * produce the PMJ02 sequence.  Other choices are also possible, but
   * result in different sequences. */
  static uint xors[2][32] = {
      {0x00000000, 0x00000000, 0x00000002, 0x00000006, 0x00000006, 0x0000000e, 0x00000036,
       0x0000004e, 0x00000016, 0x0000002e, 0x00000276, 0x000006ce, 0x00000716, 0x00000c2e,
       0x00003076, 0x000040ce, 0x00000116, 0x0000022e, 0x00020676, 0x00060ece, 0x00061716,
       0x000e2c2e, 0x00367076, 0x004ec0ce, 0x00170116, 0x002c022e, 0x02700676, 0x06c00ece,
       0x07001716, 0x0c002c2e, 0x30007076, 0x4000c0ce},
      {0x00000000, 0x00000001, 0x00000003, 0x00000003, 0x00000007, 0x0000001b, 0x00000027,
       0x0000000b, 0x00000017, 0x0000013b, 0x00000367, 0x0000038b, 0x00000617, 0x0000183b,
       0x00002067, 0x0000008b, 0x00000117, 0x0001033b, 0x00030767, 0x00030b8b, 0x00071617,
       0x001b383b, 0x00276067, 0x000b808b, 0x00160117, 0x0138033b, 0x03600767, 0x03800b8b,
       0x06001617, 0x1800383b, 0x20006067, 0x0000808b}};

  uint rng_i = rng_seed;

  points[0].x = hash_hp_float(rng_i++);
  points[0].y = hash_hp_float(rng_i++);

  /* Subdivide the domain into smaller and smaller strata, filling in new
   * points as we go. */
  for (int log_N = 0, N = 1; N < size; log_N++, N *= 2) {
    float strata_count = (float)(N * 2);
    for (int i = 0; i < N && (N + i) < size; i++) {
      /* Find the strata that are already occupied in this cell. */
      uint occupied_x_stratum = (uint)(points[i ^ xors[0][log_N]].x * strata_count);
      uint occupied_y_stratum = (uint)(points[i ^ xors[1][log_N]].y * strata_count);

      /* Generate a new point in the unoccupied strata. */
      points[N + i].x = ((float)(occupied_x_stratum ^ 1) + hash_hp_float(rng_i++)) / strata_count;
      points[N + i].y = ((float)(occupied_y_stratum ^ 1) + hash_hp_float(rng_i++)) / strata_count;
    }
  }
}

int main(int argc,char**argv){if(argc!=2)return 2;std::vector<float2>pmj(256*256);for(int j=0;j<256;j++)progressive_multi_jitter_02_generate_2D(pmj.data()+j*256,256,j);FILE*f=fopen(argv[1],"wb");if(!f)return 2;fwrite(pmj.data(),sizeof(float2),pmj.size(),f);return fclose(f);}
