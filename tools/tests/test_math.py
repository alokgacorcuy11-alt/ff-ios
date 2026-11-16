#!/usr/bin/env python3
"""test_math.py - verify the maths that Core/Game.m ships, offline.

The quaternion code in the world-to-screen fallback is the part of this project
that is most likely to be wrong and least likely to be noticed: a wrong handness
or a wrong euler order does not crash, it draws the ESP in the wrong place - and
you would blame the offsets.

This mirrors that maths in Python and checks the invariants that a wrong
formula breaks. It verifies the *formulas*, which is where the bugs live; it
cannot execute the Objective-C, and it does not pretend to.

  python tools/tests/test_math.py
"""

from __future__ import annotations

import math
import sys

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


# ---------------------------------------------------------------- quaternions
def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q, v):
    """The exact expression Core/Game.m uses in quatRotate()."""
    ux, uy, uz, uw = q
    vx, vy, vz = v
    tx = 2.0 * (uy * vz - uz * vy)
    ty = 2.0 * (uz * vx - ux * vz)
    tz = 2.0 * (ux * vy - uy * vx)
    cx = uy * tz - uz * ty
    cy = uz * tx - ux * tz
    cz = ux * ty - uy * tx
    return (vx + uw * tx + cx, vy + uw * ty + cy, vz + uw * tz + cz)


def quat_from_axis_angle(axis, deg):
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / n, y / n, z / n
    h = math.radians(deg) * 0.5
    s = math.sin(h)
    return (x * s, y * s, z * s, math.cos(h))


def quat_from_euler(xd, yd, zd):
    """Core/Game.m quatFromEuler: Unity's ZXY order."""
    h = math.pi / 360.0
    x, y, z = xd * h, yd * h, zd * h
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def quat_from_matrix(m):
    """Core/Game.m quatFromMatrix. `m` is a flat column-major 16-float array,
    element (row r, col c) at index c*4 + r."""
    m00, m10, m20 = m[0], m[1], m[2]
    m01, m11, m21 = m[4], m[5], m[6]
    m02, m12, m22 = m[8], m[9], m[10]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def matrix_from_quat(q):
    x, y, z, w = q
    """Column-major Unity Matrix4x4 rotation part, same indexing as above."""
    return [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
        0, 0, 0, 1,
    ]


def close(a, b, eps=1e-5):
    return abs(a - b) <= eps


def vclose(a, b, eps=1e-5):
    return all(close(x, y, eps) for x, y in zip(a, b))


# ---------------------------------------------------------------- the tests
print("=" * 66)
print(" maths audit - the formulas Core/Game.m ships")
print("=" * 66)

# 1. quatRotate must be a pure rotation: length preserving, identity for w=1
v = (3.0, -4.0, 12.0)
identity = (0.0, 0.0, 0.0, 1.0)
check("identity quaternion leaves a vector alone", vclose(quat_rotate(identity, v), v))

for axis, deg in ((("1", "0", "0"), 90), (("0", "1", "0"), 90), (("0", "0", "1"), 90),
                  ((1, 1, 0), 137), ((0.3, -0.8, 0.5), 41)):
    q = quat_from_axis_angle([float(a) for a in axis], deg)
    r = quat_rotate(q, v)
    ln = math.sqrt(sum(c * c for c in r))
    src = math.sqrt(sum(c * c for c in v))
    check(f"length preserved for axis={axis} {deg}deg", close(ln, src, 1e-4),
          f"({ln:.6f} vs {src:.6f})")

# 2. the handness check. In Unity a +90 degrees rotation about +Y maps +Z to +X.
#    Get this backwards and the ESP mirrors horizontally, which looks exactly
#    like a wrong field offset.
q = quat_from_axis_angle((0, 1, 0), 90)
r = quat_rotate(q, (0, 0, 1))
check("+90 about Y sends +Z to +X (Unity is left-handed)",
      vclose(r, (1, 0, 0), 1e-5), f"-> {tuple(round(c, 5) for c in r)}")

q = quat_from_axis_angle((1, 0, 0), 90)
r = quat_rotate(q, (0, 1, 0))
check("+90 about X sends +Y to +Z", vclose(r, (0, 0, 1), 1e-5),
      f"-> {tuple(round(c, 5) for c in r)}")

# 3. composition order: Unity applies Z, then X, then Y
qx = quat_from_axis_angle((1, 0, 0), 30)
qy = quat_from_axis_angle((0, 1, 0), 40)
qz = quat_from_axis_angle((0, 0, 1), 50)
qw = quat_mul(quat_mul(qz, qx), qy)          # wait: ZXY means (Y * X * Z) applied left-to-right
manual = quat_mul(qy, quat_mul(qx, qz))
e = quat_from_euler(30, 40, 50)
check("euler matches Y*X*Z composition", vclose(e, manual, 1e-4),
      f"euler={tuple(round(c,4) for c in e)} manual={tuple(round(c,4) for c in manual)}")
check("and not the other order", not vclose(e, qw, 1e-3))

# 4. pure-axis euler must equal the axis quaternion
for ax, de, comp in ((1, 33, 0), (2, -71, 1), (3, 12, 2)):
    e = quat_from_euler(*[de if i == comp else 0 for i in range(3)])
    a = quat_from_axis_angle(tuple(1 if i == comp else 0 for i in range(3)), de)
    same = vclose(e, a, 1e-5) or vclose(e, tuple(-c for c in a), 1e-5)
    check(f"single-axis euler {de}deg about axis {ax} equals axis-angle", same)

# 5. matrix -> quaternion round trip, including the 180 degree branch that
#    naive implementations get wrong (trace <= 0 picks another branch)
for name, q0 in (("identity", (0, 0, 0, 1)),
                 ("90 about Y", quat_from_axis_angle((0, 1, 0), 90)),
                 ("180 about X", quat_from_axis_angle((1, 0, 0), 180)),
                 ("180 about Y", quat_from_axis_angle((0, 1, 0), 180)),
                 ("180 about Z", quat_from_axis_angle((0, 0, 1), 180)),
                 ("arbitrary", quat_from_axis_angle((0.2, 0.9, -0.4), 123))):
    m = matrix_from_quat(q0)
    q1 = quat_from_matrix(m)
    ok = vclose(q1, q0, 1e-4) or vclose(q1, tuple(-c for c in q0), 1e-4)
    check(f"matrix round trip: {name}", ok,
          f"-> got {tuple(round(c, 4) for c in q1)} want {tuple(round(c, 4) for c in q0)}")

# 6. the round trip must preserve what the quaternion DOES, even if the sign
#    flips (q and -q are the same rotation)
probe = (1.5, -2.5, 0.75)
m = matrix_from_quat(quat_from_axis_angle((0.1, 0.2, 0.97), 62))
q1 = quat_from_matrix(m)
a = quat_rotate(quat_from_axis_angle((0.1, 0.2, 0.97), 62), probe)
b = quat_rotate(q1, probe)
check("round trip preserves the rotation's effect on a vector", vclose(a, b, 1e-4),
      f"-> {tuple(round(c,4) for c in a)} vs {tuple(round(c,4) for c in b)}")

# 7. the view matrix used by the W2S fallback: inverse of a rigid transform.
#    Camera in Unity looks down its local +Z.
def view_space(cam_pos, cam_rot, world):
    d = tuple(w - c for w, c in zip(world, cam_pos))
    inv = (-cam_rot[0], -cam_rot[1], -cam_rot[2], cam_rot[3])
    return quat_rotate(inv, d)

cam_rot = quat_from_euler(0, 0, 0)                 # looking down +Z, no yaw
p = view_space((0, 0, 0), cam_rot, (0, 0, 10))
check("point 10m down +Z lands at +Z in view space", vclose(p, (0, 0, 10)),
      f"-> {tuple(round(c,4) for c in p)}")

cam_rot = quat_from_euler(0, 90, 0)                # yawed 90 to the right
p = view_space((0, 0, 0), cam_rot, (10, 0, 0))
check("after a 90 yaw, +X world is straight ahead", vclose(p, (0, 0, 10), 1e-4),
      f"-> {tuple(round(c,4) for c in p)}")

# 8. projection: a point on the axis projects to the screen centre, and a point
#    to the right of the axis projects to the right of centre.
def project(cam_pos, cam_rot, world, w, h, fov_deg):
    c = view_space(cam_pos, cam_rot, world)
    if c[2] <= 0.05:
        return None
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    aspect = w / h
    sx = (c[0] * f / aspect) / c[2]
    sy = (c[1] * f) / c[2]
    return ((sx * 0.5 + 0.5) * w, (1.0 - (sy * 0.5 + 0.5)) * h)

W, H = 1170.0, 2532.0
centre = project((0, 0, 0), quat_from_euler(0, 0, 0), (0, 0, 10), W, H, 60)
check("on-axis point projects to the screen centre",
      vclose(centre, (W / 2, H / 2), 1e-3), f"-> {tuple(round(c,2) for c in centre)}")

right = project((0, 0, 0), quat_from_euler(0, 0, 0), (1, 0, 10), W, H, 60)
check("a point at +X projects right of centre", right[0] > W / 2,
      f"-> x={right[0]:.1f}")

up = project((0, 0, 0), quat_from_euler(0, 0, 0), (0, 1, 10), W, H, 60)
check("a point at +Y projects above centre (y is flipped for UIKit)",
      up[1] < H / 2, f"-> y={up[1]:.1f}")

behind = project((0, 0, 0), quat_from_euler(0, 0, 0), (0, 0, -5), W, H, 60)
check("a point behind the camera is rejected", behind is None)

# 9. a wider FOV must push the same point closer to the centre
narrow = project((0, 0, 0), quat_from_euler(0, 0, 0), (1, 0, 10), W, H, 30)
wide = project((0, 0, 0), quat_from_euler(0, 0, 0), (1, 0, 10), W, H, 90)
check("wider FOV moves the point toward the centre",
      abs(wide[0] - W / 2) < abs(narrow[0] - W / 2),
      f"-> 30deg x={narrow[0]:.1f}  90deg x={wide[0]:.1f}")

# ---------------------------------------------------------------- aim loop
# The aimbot is a discrete control loop, and its sign conventions are the part
# that fails invisibly: a flipped sign does not crash, it drives the crosshair
# AWAY from the target, which on a device looks like "the aimbot does nothing".
#
# Mirrored exactly from +aimAt:bone:smooth: in Core/Game.m.
#
# The model has TWO gains and that is the whole point:
#
#   gain_used   what the loop divides by - the value the calibration measured
#   gain_true   how the camera actually responds on this device
#
# Modelling them as one number makes the loop look unconditionally stable,
# because dx = err/gain and the response gain*dx cancel regardless of sign. That
# is a fake: it proves nothing. Writing the test that way was the first attempt,
# and the sign checks below failed for the right reason - the model, not the
# code, was wrong. With the two separated, stability depends on the measured
# gain actually matching the device, which is precisely what the calibration
# step exists to guarantee.
#
# This still cannot verify the game's own sensitivity curve or look
# acceleration. It verifies that our loop converges when the measurement is
# right and fails loudly when it is not.
DEADZONE = 0.0058          # rad
MAX_STEP = 24.0            # points per frame


def aim_loop_step(err, gain_used, smooth):
    """One frame of the aim loop: angular error in, finger delta out."""
    if abs(err) < DEADZONE:
        return 0.0
    steps = max(2, min(int(smooth), 20))
    k = 1.0 / steps
    dx_full = err / gain_used
    return max(-MAX_STEP, min(MAX_STEP, dx_full * k))


def simulate(err0, gain_used, gain_true, smooth, frames=80):
    """Error trajectory: the loop budgets with gain_used, the camera answers
    with gain_true."""
    err = err0
    traj = [err]
    for _ in range(frames):
        dx = aim_loop_step(err, gain_used, smooth)
        err = err - gain_true * dx
        traj.append(err)
    return traj


def frames_to_settle(traj):
    """How many frames until the deadzone is reached."""
    for i, e in enumerate(traj):
        if abs(e) <= DEADZONE:
            return i
    return len(traj)


GAIN = 0.005                                   # rad per point, a real value
POS = math.radians(10.0)                       # 10 degrees off target

traj = simulate(POS, GAIN, GAIN, smooth=4)
check("a correct gain converges from 10 deg at smooth=4",
      frames_to_settle(traj) < len(traj) - 1,
      f"-> settled in {frames_to_settle(traj)} frames, final "
      f"{math.degrees(traj[-1]):.3f} deg")
check("a correct gain never overshoots",
      all(abs(traj[i + 1]) <= abs(traj[i]) + 1e-12 for i in range(len(traj) - 1)),
      f"-> peak {math.degrees(max(abs(e) for e in traj)):.2f} deg")

# geometric convergence with factor (1 - k), so 1/k * ln(100) frames
n4 = frames_to_settle(simulate(POS, GAIN, GAIN, smooth=4))
check("smooth=4 settles in roughly 2/k frames", 8 <= n4 <= 24,
      f"-> {n4} frames, {n4 * 0.05:.2f} s at 20 Hz")

# the slider must actually do something, in the right direction
n2 = frames_to_settle(simulate(POS, GAIN, GAIN, smooth=2))
n20 = frames_to_settle(simulate(POS, GAIN, GAIN, smooth=20))
check("smooth=20 is slower than smooth=2", n20 > n2, f"-> {n20} vs {n2}")

# the per-frame cap: a 90-degree error must not produce a snap
dx90 = aim_loop_step(math.radians(90.0), GAIN, smooth=2)
check("a 90 deg error is capped at MAX_STEP",
      abs(dx90) <= MAX_STEP + 1e-9, f"-> {dx90:.2f} points")
check("the cap, not the fraction, is what bounds a large error",
      abs(dx90) < abs(math.radians(90.0) / GAIN) / 2)

# inside the deadzone the loop must not touch the finger at all
check("inside the deadzone nothing moves",
      aim_loop_step(DEADZONE * 0.9, GAIN, 4) == 0.0)
check("just outside the deadzone it does move",
      aim_loop_step(DEADZONE * 1.1, GAIN, 4) != 0.0)

# ------------------------------------------------------------- gain mismatch
# These four are the reason the calibration exists.

flipped = simulate(POS, -GAIN, GAIN, smooth=4)
check("a measured gain with the wrong sign diverges",
      abs(flipped[-1]) > abs(POS) * 10,
      f"-> grew to {math.degrees(flipped[-1]):.1f} deg")

toosmall = simulate(POS, GAIN / 10.0, GAIN, smooth=4, frames=200)
check("a gain far too small is saved from overshoot by the cap",
      abs(toosmall[1]) <= abs(POS),
      f"-> first frame {math.degrees(toosmall[1]):.1f} deg, cap holds it")
# ...but the cap only binds while the error is large. Near the target it stops
# binding and a too-small gain overshoots and then diverges. That is the real
# failure mode, and it is why the gain is refined online rather than trusted.
near = 0.02
step = -GAIN * aim_loop_step(near, GAIN / 10.0, 4)          # what the camera does
check("close in, a gain far too small overshoots the target",
      abs(near + step) > near, f"-> {math.degrees(near):.1f} deg becomes "
                               f"{math.degrees(near + step):.1f} deg")

# ------------------------------------------------------- adaptive refinement
# Mirrors the observation block in +aimAt: the loop measures how far the camera
# actually turned in response to the correction it applied, and blends that into
# the gain. In the model the observed estimate is exactly gain_true, so the
# question is only how fast it gets there and whether it is stable on the way.

def simulate_adaptive(err0, gain_used0, gain_true, smooth, frames=200, alpha=0.25):
    gain = gain_used0
    err = err0
    traj = [err]
    last_dx = None
    settled_at = None
    for i in range(frames):
        if last_dx is not None and abs(last_dx) > 0.25:
            observed = gain_true * last_dx
            if abs(observed) > 1e-4:
                est = observed / last_dx
                if 1e-4 <= abs(est) <= 0.5 and est * gain > 0.0:
                    gain += (est - gain) * alpha
        dx = aim_loop_step(err, gain, smooth)
        last_dx = dx
        err = err - gain_true * dx
        traj.append(err)
        if settled_at is None and abs(err) <= DEADZONE:
            settled_at = i
    return traj, gain, settled_at


for bad, label in ((GAIN / 20.0, "20x too small"), (GAIN * 20.0, "20x too big")):
    t, g, settled = simulate_adaptive(POS, bad, GAIN, smooth=4)
    # Inside the deadzone no correction is applied, so no further observation is
    # made and the gain stops moving. Converging on the TARGET is the claim;
    # "the gain equals the true value exactly" is not, and asserting it would
    # have been wrong.
    check(f"adaptive reaches the target from a {label} starting gain",
          settled is not None,
          f"-> settled at frame {settled}, final {math.degrees(t[-1]):.3f} deg")
    check(f"adaptive recovers the gain's magnitude from {label}",
          abs(abs(g) - GAIN) / GAIN < 2.0,
          f"-> {g:.6f} vs true {GAIN}")

t_adapt, _, _ = simulate_adaptive(POS, GAIN / 20.0, GAIN, smooth=4)
check("adaptive refinement still never overshoots from 10 deg",
      all(abs(t_adapt[i + 1]) <= abs(t_adapt[i]) + 1e-12
          for i in range(len(t_adapt) - 1)))

# Without the refinement the same starting gain is not merely slow: the per-frame
# cap turns it into a limit cycle, so the crosshair swings back and forth by
# several degrees and never settles. That is the observable symptom, and it is
# what the observation block exists to remove.
t_raw = simulate(POS, GAIN / 20.0, GAIN, smooth=4, frames=200)
flips = sum(1 for i in range(len(t_raw) - 1) if t_raw[i] * t_raw[i + 1] < 0)
check("without refinement the same gain limit-cycles",
      frames_to_settle(t_raw) == len(t_raw) and flips >= 3,
      f"-> never settles, {flips} sign flips, peak "
      f"{math.degrees(max(abs(e) for e in t_raw)):.1f} deg")

print()
if FAIL:
    print(f"{FAIL} check(s) FAILED")
    sys.exit(1)
print("all maths checks passed")
