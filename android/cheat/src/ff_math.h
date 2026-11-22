// ff_math.h - turning a world position into a pixel, and a pixel into a box.
//
// Deliberately free of anything Android: it takes numbers and returns numbers,
// so the projection can be tested against a known matrix on a desktop. The part
// of an ESP that is easy to get subtly wrong is exactly this, and a box drawn a
// hundred pixels off looks like a broken detector rather than a broken matrix.
#pragma once

#include <cmath>
#include <cstdint>

namespace ff {

struct Vec3 {
    float x = 0, y = 0, z = 0;

    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }

    float length() const { return std::sqrt(x * x + y * y + z * z); }
};

struct Vec2 {
    float x = 0, y = 0;
};

// Column-major, the order Unity hands out. m[12..14] is the translation.
struct Mat4 {
    float m[16] = {1, 0, 0, 0,
                   0, 1, 0, 0,
                   0, 0, 1, 0,
                   0, 0, 0, 1};

    static Mat4 identity() { return Mat4{}; }
};

struct Box {
    float left = 0, top = 0, right = 0, bottom = 0;

    float width() const { return right - left; }
    float height() const { return bottom - top; }
    bool valid() const { return right > left && bottom > top; }
};

// The full transform: view * projection, as the camera holds it.
Vec3 transform_point(const Mat4& m, const Vec3& p);

// Projects a world point onto the screen.
//
// Returns false when the point is behind the camera or outside the frustum,
// which is the normal case for most of the world on most frames. A caller that
// ignores the return value draws boxes for enemies behind it.
bool world_to_screen(const Mat4& view_projection, const Vec3& world,
                     int screen_w, int screen_h, Vec2& out);

// A box around a player, sized from how far away they are: a distant enemy is a
// few pixels tall and a near one fills a third of the screen. Both cut-offs are
// in world units so the shape holds at any resolution.
struct Silhouette {
    float half_width_m = 0.45f;   // shoulders
    float height_m = 1.8f;        // head to feet
};

bool player_box(const Mat4& view_projection, const Vec3& feet, const Silhouette& shape,
                int screen_w, int screen_h, Box& out);

// Distance in metres between two world points, for the label.
float distance_m(const Vec3& a, const Vec3& b);

// Where to draw a label above the box.
Vec2 label_anchor(const Box& box);

}  // namespace ff
