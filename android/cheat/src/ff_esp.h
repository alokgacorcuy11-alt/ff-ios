// ff_esp.h - reading a player, and what it costs to find one.
//
// Reading a player is solved: the offsets are known and verified, and the code
// below is a straight sequence of reads against them.
//
// Finding them is not. The list of live players hangs off something the game
// builds at match start, and which object holds it is not visible in a static
// dump - a field of type List<T> is just a pointer and a count, and which of
// Player's 1,689 fields or GameFacade's 196 is the one that holds the list
// cannot be read off the metadata alone. It has to be found against a running
// match, by walking the candidates and keeping the one that is non-empty before
// the first shot and empty after the last.
//
// So PlayerSource is an interface with no implementation here on purpose.
// Faking it would produce a build that compiles and finds nothing, which is
// worse than one that admits the gap.
#pragma once

#include <cstdint>
#include <vector>

#include "ff_mem.h"
#include "ff_math.h"
#include "ff_offsets.h"

namespace ff {

struct PlayerView {
    uintptr_t object = 0;
    Vec3 ground;             // world position, metres
    int32_t team = -1;
    int32_t local_team = -1;
    bool has_position = false;

    bool enemy() const { return local_team >= 0 && team >= 0 && team != local_team; }
};

// The offsets, by name, straight out of the generated header. Nothing here is
// typed twice.
struct Offsets {
    static constexpr int32_t kTransformObject = player::m_TransformObject;
    static constexpr int32_t kGroundHitPos = player::m_GroundHitPos;
    static constexpr int32_t kTeamModeId = player::TeamModeID;
    static constexpr int32_t kSpeed = player::Speed;
    static constexpr int32_t kAimCount = player::AimCount;
};

// Reads one player object. Returns false when the object is not readable or has
// no position yet, which is normal for a player who has not spawned.
bool read_player(const Memory& mem, uintptr_t object, PlayerView& out);

// The transform component a player points at, which carries the live position
// between physics steps. Used when m_GroundHitPos lags a moving target.
bool read_transform_object(const Memory& mem, uintptr_t player, uintptr_t& out);

// A world position out of a Vector3 stored inline at an offset.
bool read_vec3(const Memory& mem, uintptr_t base, int32_t offset, Vec3& out);

// Where the live players come from. One implementation per game version, found
// against a running match - see the note at the top of this file.
class PlayerSource {
public:
    virtual ~PlayerSource() = default;
    virtual bool local_player(uintptr_t& out) = 0;
    virtual void players(std::vector<uintptr_t>& out) = 0;
};

// Everything one frame needs: the matrix, the players, and the boxes.
struct Frame {
    std::vector<Box> boxes;
    std::vector<float> distances;
    int considered = 0;
    int projected = 0;
};

// Builds the frame from a source. Pure once the reads are done, so the drawing
// decision can be tested without a game.
Frame build_frame(const Memory& mem, PlayerSource& source, const Mat4& view_projection,
                  const Vec3& camera, int screen_w, int screen_h, float max_distance_m);

}  // namespace ff
