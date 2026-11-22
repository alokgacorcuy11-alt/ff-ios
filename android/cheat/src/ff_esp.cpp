#include "ff_esp.h"

namespace ff {

bool read_vec3(const Memory& mem, uintptr_t base, int32_t offset, Vec3& out) {
    if (offset <= 0) {
        return false;
    }
    const uintptr_t at = base + static_cast<uintptr_t>(offset);
    if (!mem.get_f32(at, out.x) || !mem.get_f32(at + 4, out.y)
            || !mem.get_f32(at + 8, out.z)) {
        return false;
    }
    // A position on a real map is a few thousand metres at most, and a NaN or a
    // denormal here means the offset moved after a patch rather than that the
    // player is somewhere strange.
    if (!(out.x == out.x) || !(out.y == out.y) || !(out.z == out.z)) {
        return false;
    }
    if (out.x < -100000.f || out.x > 100000.f
            || out.y < -10000.f || out.y > 10000.f
            || out.z < -100000.f || out.z > 100000.f) {
        return false;
    }
    const float len = out.length();
    return len >= 0.f && len < 200000.f;
}

bool read_transform_object(const Memory& mem, uintptr_t player, uintptr_t& out) {
    out = 0;
    if (!mem.get_ptr(player + Offsets::kTransformObject, out)) {
        return false;
    }
    if (out == 0 || !mem.contains(out, 0x40)) {
        out = 0;
        return false;
    }
    return true;
}

bool read_player(const Memory& mem, uintptr_t object, PlayerView& out) {
    out = PlayerView{};
    out.object = object;
    if (object == 0 || !mem.contains(object, 0x100)) {
        return false;
    }

    // m_GroundHitPos is a position stored inline, which makes it one read and no
    // pointer chase. It is the ground point under the player, so the box built
    // from it stands on the floor where the player is standing.
    out.has_position = read_vec3(mem, object, Offsets::kGroundHitPos, out.ground);
    if (!out.has_position) {
        uintptr_t transform = 0;
        if (read_transform_object(mem, object, transform)) {
            // Fall back to the component's own position, further into the
            // struct and one indirection away.
            out.has_position = read_vec3(mem, transform, 0x90, out.ground);
        }
    }

    int32_t team = -1;
    if (mem.get_i32(object + Offsets::kTeamModeId, team)) {
        // Team ids are small; anything else means the field moved.
        out.team = (team >= 0 && team < 64) ? team : -1;
    }
    return out.has_position;
}

Frame build_frame(const Memory& mem, PlayerSource& source, const Mat4& view_projection,
                  const Vec3& camera, int screen_w, int screen_h, float max_distance_m) {
    Frame frame;

    uintptr_t local = 0;
    int32_t local_team = -1;
    if (source.local_player(local) && local != 0) {
        PlayerView me;
        if (read_player(mem, local, me)) {
            local_team = me.team;
        }
    }

    std::vector<uintptr_t> objects;
    source.players(objects);

    const Silhouette shape;
    for (uintptr_t obj : objects) {
        if (obj == 0 || obj == local) {
            continue;
        }
        frame.considered++;

        PlayerView view;
        if (!read_player(mem, obj, view) || !view.has_position) {
            continue;
        }
        view.local_team = local_team;

        // Range check before projecting: projecting first and filtering after
        // costs the same and draws enemies across the whole map when the
        // distance filter is what should have stopped them.
        const float distance = distance_m(view.ground, camera);
        if (distance > max_distance_m) {
            continue;
        }

        Box box;
        if (!player_box(view_projection, view.ground, shape, screen_w, screen_h, box)) {
            continue;
        }
        frame.projected++;
        frame.boxes.push_back(box);
        frame.distances.push_back(distance);
    }
    return frame;
}

}  // namespace ff