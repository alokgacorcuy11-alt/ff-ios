#include "ff_math.h"

namespace ff {

Vec3 transform_point(const Mat4& m, const Vec3& p) {
    // Column-major: column j is m[4j .. 4j+3], and the result is sum of
    // columns scaled by the components of p, plus the translation column.
    Vec3 out;
    out.x = m.m[0] * p.x + m.m[4] * p.y + m.m[8] * p.z + m.m[12];
    out.y = m.m[1] * p.x + m.m[5] * p.y + m.m[9] * p.z + m.m[13];
    out.z = m.m[2] * p.x + m.m[6] * p.y + m.m[10] * p.z + m.m[14];
    return out;
}

bool world_to_screen(const Mat4& vp, const Vec3& world, int screen_w, int screen_h,
                     Vec2& out) {
    if (screen_w <= 0 || screen_h <= 0) {
        return false;
    }
    const Vec3 clip = transform_point(vp, world);

    // Unity's clip space: +z is forward. A point at or behind the near plane
    // has no projection, and the perspective divide below would produce a
    // mirrored position that looks almost right - the worst kind of wrong.
    if (clip.z <= 0.0001f) {
        return false;
    }

    const float ndc_x = clip.x / clip.z;
    const float ndc_y = clip.y / clip.z;

    // Off-screen points are rejected rather than clamped: clamping draws a
    // column of boxes along the screen edge, which is what a badly written ESP
    // looks like.
    if (ndc_x < -1.5f || ndc_x > 1.5f || ndc_y < -1.5f || ndc_y > 1.5f) {
        return false;
    }

    out.x = (ndc_x * 0.5f + 0.5f) * static_cast<float>(screen_w);
    // Screen y grows downward, clip y grows upward.
    out.y = (1.0f - (ndc_y * 0.5f + 0.5f)) * static_cast<float>(screen_h);
    return true;
}

bool player_box(const Mat4& vp, const Vec3& feet, const Silhouette& shape,
                int screen_w, int screen_h, Box& out) {
    const Vec3 head = {feet.x, feet.y + shape.height_m, feet.z};
    const Vec3 left = {feet.x - shape.half_width_m, feet.y, feet.z};
    const Vec3 right = {feet.x + shape.half_width_m, feet.y, feet.z};

    Vec2 feet_px, head_px, left_px, right_px;
    if (!world_to_screen(vp, feet, screen_w, screen_h, feet_px)) {
        return false;
    }
    // The head is what decides whether the box is drawable: an enemy whose feet
    // are just off the bottom of the screen still has a head worth marking.
    if (!world_to_screen(vp, head, screen_w, screen_h, head_px)) {
        return false;
    }

    if (world_to_screen(vp, left, screen_w, screen_h, left_px)
            && world_to_screen(vp, right, screen_w, screen_h, right_px)) {
        out.left = left_px.x;
        out.right = right_px.x;
    } else {
        // Fall back to the vertical extent, which is proportional to distance
        // the same way the width is.
        const float h = head_px.y - feet_px.y;
        out.left = feet_px.x - h * 0.25f;
        out.right = feet_px.x + h * 0.25f;
    }
    out.top = head_px.y;
    out.bottom = feet_px.y;

    if (!out.valid()) {
        return false;
    }
    // Clamp to the screen. A box that runs off the panel is fine; one that
    // runs to a million pixels is a matrix that was read wrong.
    if (out.left < -4096 || out.right > screen_w + 4096
            || out.top < -4096 || out.bottom > screen_h + 4096) {
        return false;
    }
    return true;
}

float distance_m(const Vec3& a, const Vec3& b) {
    return (a - b).length();
}

Vec2 label_anchor(const Box& box) {
    return {box.left, box.top - 2.0f};
}

}  // namespace ff
