"""Procedural animation definitions for the Characters_1 pack.

Each entry in ANIMATIONS maps a name to (builder, frames, loop). A builder
takes (ctx, frames) and returns pose_fn(frame) -> rigkit.Pose. Names ending
in "-loop" are imported by Godot as looping animations, matching the pack.

Armature space: +Z up, character faces -Y (FWD), its left is +X (LEFT).
Rotation sign cheat sheet (rotate(bone, axis, deg)):
    rotate(b, LEFT, +)  pitches an upright bone forward (look down / bow)
    rotate(b, UP, +)    turns toward the character's left
    rotate(b, FWD, +)   rolls an upright bone toward the character's right
"""
import math

from mathutils import Matrix, Vector

from rigkit import (BACK, DOWN, FWD, LEFT, RIGHT, UP, V, blend_bones, clamp, ease_in_out, lerp, ramp,
                    rot, smooth, wave)

ARM = {s: [f"Shoulder.{s}", f"Arm.{s}", f"ForeArm.{s}", f"Hand.{s}"] for s in "LR"}
LEG = {s: [f"UpperLeg.{s}", f"Leg.{s}", f"Foot.{s}", f"Toes.{s}"] for s in "LR"}
SIDE = {"L": 1.0, "R": -1.0}  # multiply x by this to mirror


class Ctx:
    def __init__(self, rig, src):
        self.rig = rig
        self.src = src
        self.idle = rig.pose(rig.sample_action(src["Idle_A_Pose"], 0))
        idle = self.idle
        # symmetric, relaxed stance derived from the idle pose
        self.ankle = {s: V(0.19 * SIDE[s], -0.03, idle.head(f"Foot.{s}").z) for s in "LR"}
        self.hip_joint_offset = (idle.head("UpperLeg.L") - idle.head("Hips")).z
        self.hips_z = idle.head("Hips").z
        self._offsets = {}

    def prop_offset(self, name):
        """(bone, matrix) attachment offset of a prop, measured on its reference frame."""
        if name not in self._offsets:
            bone, anim, frame, fn = PROP_REFS[name]
            if bone is None:  # placed relative to the character root
                self._offsets[name] = (None, fn(self, None))
                return self._offsets[name]
            if anim in REF_POSES:
                p = REF_POSES[anim](self)
            else:
                builder, frames, _ = ANIMATIONS[anim]
                p = builder(self, frames)(frame)
            self._offsets[name] = (bone, p.world(bone).inverted() @ fn(self, p))
        return self._offsets[name]

    def stand(self):
        p = self.idle.copy()
        return p

    def sample(self, name, frame):
        act = self.src[name]
        f0, f1 = act.frame_range
        length = f1 - f0
        fr = f0 + (frame % length) if length > 0 else f0
        return self.rig.pose(self.rig.sample_action(act, fr))

    def walk(self, frame, name="Walk_C-loop"):
        return self.sample(name, frame)

    def walk_frames(self, name="Walk_C-loop"):
        f0, f1 = self.src[name].frame_range
        return int(round(f1 - f0))


# ---------------------------------------------------------------------------
# shared pose helpers


def plant_feet(ctx, p, ankles=None, knee_out=0.25, feet_yaw=8.0):
    """Leg IK so the ankles stay at `ankles` with flat feet."""
    ankles = ankles or ctx.ankle
    for s in "LR":
        p.leg_ik(s, ankles[s], V(knee_out * SIDE[s], -1, 0))
        fwd = rot(UP, feet_yaw * SIDE[s]) @ FWD
        p.foot_flat(s, fwd)


def breathe(p, phase, amount=1.0):
    b = wave(phase)
    p.rotate("Torso", LEFT, -1.2 * amount * b)
    p.rotate("Head", LEFT, 0.9 * amount * b)
    for s in "LR":
        p.rotate(f"Shoulder.{s}", BACK, 1.5 * amount * b * -SIDE[s])


def look(p, yaw=0.0, pitch=0.0, roll=0.0, torso_share=0.0):
    """Turn the head (and optionally torso). pitch + = look down."""
    if torso_share:
        p.rotate("Torso", UP, yaw * torso_share)
        p.rotate("Torso", LEFT, pitch * torso_share * 0.5)
    p.rotate("Head", UP, yaw * (1 - torso_share))
    p.rotate("Head", LEFT, pitch * (1 - torso_share * 0.5))
    if roll:
        p.rotate("Head", FWD, roll)


def hips_to(ctx, p, pos=None, pitch=0.0, yaw=0.0, roll=0.0):
    """Move/rotate the hips in armature space (absolute position)."""
    if pos is not None:
        p.translate("Hips", Vector(pos) - p.head("Hips"))
    if pitch:
        p.rotate("Hips", LEFT, pitch)
    if yaw:
        p.rotate("Hips", UP, yaw)
    if roll:
        p.rotate("Hips", FWD, roll)


def arm_to(p, side, palm, fingers, palm_dir, elbow_dir, straight=False):
    """Place a hand: palm centre position, finger direction, palm normal.

    The forearm is twisted to follow the hand (pronation happens in the
    forearm), so the IteamSlot (a forearm child) turns with the palm.
    straight=True keeps the wrist straight (fingers along the forearm), which
    keeps held props at a constant spot on the palm.
    """
    s = side.upper()
    off = p.hand_grip_offset(s)
    pn = Vector(palm_dir).normalized()
    f = Vector(fingers).normalized()
    f = (f - pn * f.dot(pn)).normalized()
    for _ in range(4 if straight else 1):
        wrist = Vector(palm) - f * off
        p.arm_ik(s, wrist, elbow_dir)
        if straight:
            f = p.axis(f"ForeArm.{s}", 1)
    p.hand(s, f, pn)
    fy = p.axis(f"ForeArm.{s}", 1)
    p.orient(f"ForeArm.{s}", fy, p.axis(f"Hand.{s}", 2))
    p.hand(s, f, pn)


def relaxed_arms(ctx, p, sway=0.0):
    """Idle arms (basis copied from the idle pose) with optional swing."""
    for s in "LR":
        for b in ARM[s]:
            p.basis[b] = ctx.idle.basis[b].copy()
        if sway:
            p.rotate(f"Arm.{s}", LEFT, -sway * SIDE[s])


def body_frame(p, bone="Torso"):
    """Bone-relative helper: offsets/directions given in armature axes as if
    the bone were at rest, measured from the bone head."""
    w = p.world(bone)
    rest = p.rig.rest[bone]
    # express offsets in a torso frame whose axes match armature axes at rest
    m = w @ rest.inverted()

    def f(x, y, z):
        return m @ (rest.translation + V(x, y, z))

    def d(v):
        return (m.to_3x3() @ Vector(v)).normalized()

    return f, d


# ---------------------------------------------------------------------------
# Wave


def _wave_arm(p, amount, swing, side="R"):
    """Raise one arm and wave. amount 0..1 blends from idle, swing in -1..1."""
    sx = SIDE[side]
    f, d = body_frame(p)
    up_pose = p.copy()
    up_pose.rotate(f"Shoulder.{side}", BACK, 10 * -sx)
    wrist_x = (0.47 + 0.06 * swing) * sx
    palm = f(wrist_x, -0.10, 0.68 - 0.02 * swing * swing)  # relative to torso head
    fingers = rot(FWD, 28 * swing * -sx) @ d(UP)
    arm_to(up_pose, side, palm, fingers, d(FWD), d(V(sx, 0.35, -0.7)))
    blend_bones(p, up_pose, amount, ARM[side])


def wave_loop(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, roll=-1.5)
        breathe(p, ph)
        p.rotate("Torso", FWD, 3)  # lean away from the waving arm
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        _wave_arm(p, 1.0, wave(ph * 2))
        look(p, yaw=-6, pitch=-4, roll=-4 + 2 * wave(ph * 2))
        return p
    return pose


def wave_once(ctx, frames):
    def pose(fr):
        t = fr / frames
        p = ctx.stand()
        up = ramp(t, 0.0, 0.16) * (1 - ramp(t, 0.82, 1.0))
        hips_to(ctx, p, roll=-1.5 * up)
        breathe(p, t)
        p.rotate("Torso", FWD, 3 * up)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        # three waves between 16% and 82%
        wt = clamp((t - 0.16) / 0.66)
        swing = math.sin(wt * 3 * 2 * math.pi) * ramp(t, 0.1, 0.22) * (1 - ramp(t, 0.76, 0.84))
        _wave_arm(p, up, swing)
        look(p, yaw=-6 * up, pitch=-4 * up, roll=-4 * up)
        return p
    return pose


# ---------------------------------------------------------------------------
# Hailing a taxi: standing at the kerb facing the street, right arm straight up
# and out toward the traffic, leaning out a little, watching the cars come.


def taxi_hail(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        # weight on the forward (right) foot, leaning out into the street
        ankles = {"L": ctx.ankle["L"] + V(0.0, 0.05, 0.0), "R": ctx.ankle["R"] + V(-0.02, -0.07, 0.0)}
        hips_to(ctx, p, V(-0.02, -0.025, ctx.hips_z - 0.012), roll=2.5, yaw=-6)
        p.rotate("Torso", LEFT, 6)
        p.rotate("Torso", FWD, 5)  # lean toward the raised arm
        breathe(p, ph * 2)
        plant_feet(ctx, p, ankles)
        relaxed_arms(ctx, p)
        p.rotate("Arm.L", BACK, -4)
        # arm up and out, with an eager little pump now and then
        pump = max(0.0, math.sin(ph * 2 * math.pi * 3)) * (1.0 if ph < 0.5 else 0.4)
        f, d = body_frame(p)
        reach = d(RIGHT * 0.55 + FWD * 0.35 + UP * (0.85 + 0.12 * pump))
        palm = p.head("Arm.R") + reach * 0.615
        fingers = rot(FWD, -6 * pump) @ reach
        arm_to(p, "R", palm, fingers, d(FWD + RIGHT * 0.3), d(V(-0.6, 0.5, -1)))
        # watch the traffic coming from the right, scanning a little
        look(p, yaw=-34 + 10 * wave(ph, 0.1), pitch=-6 + 3 * wave(ph * 2), roll=-3, torso_share=0.15)
        return p
    return pose


# ---------------------------------------------------------------------------
# Sitting on a chair
#
# The seat is behind the character's root: sitting moves the hips back by
# ~0.17 and down so the thighs rest at about z=0.33. Chair seat surface: ~0.27.

CHAIR_HIPS = V(0, 0.17, 0.215)


def _sit_chair(ctx, p, s, lean_extra=0.0, spread=0.0):
    """s: 0 standing .. 1 seated. spread widens the knees (e.g. to hold a cello)."""
    stand = V(0, 0, ctx.hips_z)
    # hips path: go down/back with a slight forward bulge so it reads as weight shifting
    pos = stand.lerp(CHAIR_HIPS, smooth(s))
    pos.y -= 0.03 * math.sin(math.pi * s)
    lean = 32 * math.sin(math.pi * clamp(s * 1.1)) + lean_extra
    hips_to(ctx, p, pos, pitch=lean * 0.5)
    p.rotate("Torso", LEFT, lean * 0.5)
    p.rotate("Head", LEFT, -lean * 0.6)
    ankles = {k: ctx.ankle[k] + V(spread * SIDE[k], -0.06 * s, 0) for k in "LR"}
    plant_feet(ctx, p, ankles, knee_out=0.2 + spread * 6, feet_yaw=8 + spread * 120)


def _hands_on_thighs(p, ph=0.0):
    f, d = body_frame(p)
    for s in "LR":
        sx = SIDE[s]
        knee = p.head(f"Leg.{s}")
        hip = p.head(f"UpperLeg.{s}")
        palm = hip.lerp(knee, 0.62) + V(-0.015 * sx, 0, 0.085)
        arm_to(p, s, palm, FWD + DOWN * 0.35 + RIGHT * sx * 0.15, DOWN, V(0.7 * sx, 1, 0))


def sit_chair_down(ctx, frames):
    def pose(fr):
        t = fr / frames
        s = ease_in_out(clamp(t / 0.9))
        p = ctx.stand()
        _sit_chair(ctx, p, s)
        seated = p.copy()
        _hands_on_thighs(seated)
        relaxed_arms(ctx, p)
        k = ramp(t, 0.35, 1.0)
        for side in "LR":
            blend_bones(p, seated, k, ARM[side])
        return p
    return pose


def sit_chair_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _sit_chair(ctx, p, 1.0)
        breathe(p, ph * 2, 1.2)
        _hands_on_thighs(p)
        look(p, yaw=10 * wave(ph), pitch=2 * wave(ph, 0.25))
        return p
    return pose


def sit_chair_up(ctx, frames):
    down = sit_chair_down(ctx, frames)

    def pose(fr):
        return down(frames - fr)
    return pose


# ---------------------------------------------------------------------------
# Carrying a box (Props/Box.glb on a BoneAttachment3D for "Torso")

BOX_CENTER_AT_REST = V(0, -0.27, 0.80)
BOX_HALF = V(0.18, 0.14, 0.15)


def box_world(ctx, p):
    local = ctx.rig.rest["Torso"].inverted() @ Matrix.Translation(BOX_CENTER_AT_REST)
    return p.world("Torso") @ local


def _hold_box(ctx, p, squeeze=0.0):
    m = box_world(ctx, p)
    r = m.to_3x3()
    for s in "LR":
        sx = SIDE[s]
        palm = m @ V((BOX_HALF.x + 0.012 - squeeze) * sx, -0.01, -0.03)
        arm_to(p, s, palm, r @ (FWD + DOWN * 0.45 - LEFT * sx * 0.1), r @ V(-sx, 0, 0),
               r @ V(sx * 0.9, 0.7, -0.5))


def carry_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.01, ctx.hips_z - 0.01))
        p.rotate("Torso", LEFT, -5)
        breathe(p, ph * 2, 1.3)
        plant_feet(ctx, p)
        _hold_box(ctx, p)
        look(p, yaw=8 * wave(ph), pitch=4)
        return p
    return pose


def carry_walk(ctx, frames):
    def pose(fr):
        p = ctx.walk(fr)
        p.rotate("Torso", LEFT, -6)
        _hold_box(ctx, p)
        look(p, pitch=5)
        return p
    return pose


# ---------------------------------------------------------------------------
# Umbrella (Props/Umbrella.glb on a BoneAttachment3D for "IteamSlot.R")


# fist position (torso-relative) and backward tilt of the shaft; tuned with
# tools/check_clearance.py so the shaft clears every character's head
UMBRELLA = {"x": -0.25, "y": -0.42, "z": 0.18, "tilt": 0.0}


def _hold_umbrella(p, bob=0.0):
    f, d = body_frame(p)
    palm = f(UMBRELLA["x"], UMBRELLA["y"], UMBRELLA["z"] + bob)
    arm_to(p, "R", palm, d(FWD + LEFT * 0.2 + UP * 0.25), d(LEFT + UP * 0.15), d(V(-0.45, 0.5, -1)),
           straight=True)


def umbrella_world(ctx, p):
    """Desired umbrella placement in the reference pose: shaft through the fist, tilted back a bit."""
    palm = p.head("Hand.R") + p.axis("Hand.R", 1) * p.hand_grip_offset("R")
    palm += p.axis("Hand.R", 0) * 0.02  # toward the curled fingers
    y = rot(LEFT, -UMBRELLA["tilt"]) @ UP
    z = FWD
    x = y.cross(z).normalized()
    z = x.cross(y)
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = palm
    return m


def umbrella_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        breathe(p, ph * 2)
        p.rotate("Torso", UP, -4)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        p.rotate("Arm.L", BACK, -6)
        _hold_umbrella(p, 0.006 * wave(ph * 2))
        look(p, yaw=12 * wave(ph), pitch=-3 + 3 * wave(ph, 0.3))
        return p
    return pose


def umbrella_walk(ctx, frames):
    def pose(fr):
        p = ctx.walk(fr)
        _hold_umbrella(p)
        return p
    return pose


# ---------------------------------------------------------------------------
# Phone (Props/Phone.glb on a BoneAttachment3D for "IteamSlot.R")


def _phone_hands(p, two_hands=True, tap=0.0):
    f, d = body_frame(p)
    arm_to(p, "R", f(-0.05, -0.40, 0.21), d(FWD + LEFT * 0.4 + UP * 0.35), d(UP + BACK * 0.8 + LEFT * 0.2),
           d(V(-0.6, 0.4, -1)), straight=True)
    p.basis["Hand.R"] = Matrix.Identity(4)  # straight wrist: the phone (on the forearm slot) stays on the palm
    if two_hands:
        arm_to(p, "L", f(0.07, -0.39, 0.205 + tap), d(FWD + RIGHT * 0.45 + UP * 0.35),
               d(UP + BACK * 0.8 + RIGHT * 0.25), d(V(0.6, 0.4, -1)))


# mitten hand: the flat palm face is ~0.05 from the hand bone along +X (right hand)
PALM_SURFACE = 0.05
PHONE_HALF_THICK = 0.008


def phone_world(ctx, p):
    """Phone lying flat on the right palm (screen away from the palm)."""
    w = p.world("Hand.R").to_3x3()
    hx, hy, hz = (w.col[i].normalized() for i in range(3))
    c = p.head("Hand.R") + hy * 0.11 + hx * (PALM_SURFACE + PHONE_HALF_THICK + 0.002) - hz * 0.015
    m = Matrix((-hz, hy, hx)).transposed().to_4x4()
    m.translation = c
    return m


def phone_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        breathe(p, ph * 2)
        p.rotate("Torso", LEFT, 5)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        tap = 0.008 * max(0.0, wave(ph * 9)) * (1 if (int(ph * 3) % 3) != 2 else 0)
        _phone_hands(p, True, tap)
        look(p, pitch=30, yaw=3 * wave(ph))
        return p
    return pose


def phone_walk(ctx, frames):
    def pose(fr):
        p = ctx.walk(fr)
        p.rotate("Torso", LEFT, 4)
        relaxed_left = p.copy()
        relaxed_arms(ctx, relaxed_left)
        blend_bones(p, relaxed_left, 0.5, ARM["L"])
        _phone_hands(p, False)
        look(p, pitch=30)
        return p
    return pose


def hold_slot_prop(ctx, p, side, prop, desired, slide=None, spin=False):
    """Pose an arm so a slot-attached prop lands exactly at `desired` (world matrix).

    The forearm orientation follows from the prop. To let the upper arm reach
    the elbow either the prop is slid along `slide`, or (spin=True, for
    round props like a bow) rolled about its own Y axis, picking the lowest
    elbow. The wrist is kept straight so the prop stays in the palm.
    """
    s = side.upper()
    rig = ctx.rig
    bone, off = ctx.prop_offset(prop)
    slot_rel = rig.offset[bone]  # slot relative to forearm (slot basis is identity)
    sh = p.head(f"Arm.{s}")
    l1 = (rig.rest[f"ForeArm.{s}"].translation - rig.rest[f"Arm.{s}"].translation).length
    lam = 0.0
    if spin:
        def fore_at(phi):
            return desired @ Matrix.Rotation(phi, 4, "Y") @ off.inverted() @ slot_rel.inverted()

        def err(phi):
            return (fore_at(phi).translation - sh).length - l1

        steps = 72
        phis = [2 * math.pi * i / steps for i in range(steps + 1)]
        errs = [err(a) for a in phis]
        roots = []
        for a0, a1, e0_, e1_ in zip(phis, phis[1:], errs, errs[1:]):
            if e0_ == 0 or e0_ * e1_ < 0:
                lo, hi = a0, a1
                for _ in range(30):
                    mid = (lo + hi) / 2
                    if err(lo) * err(mid) <= 0:
                        hi = mid
                    else:
                        lo = mid
                roots.append((lo + hi) / 2)
        if roots:
            phi = min(roots, key=lambda a: fore_at(a).translation.z)
        else:
            phi = min(phis, key=lambda a: abs(err(a)))
        fore = fore_at(phi)
    else:
        fore0 = desired @ off.inverted() @ slot_rel.inverted()
        e0 = fore0.translation
        u = Vector(slide).normalized()
        dv = e0 - sh
        bq, cq = 2 * dv.dot(u), dv.length_squared - l1 * l1
        disc = bq * bq - 4 * cq
        if disc >= 0:
            r1, r2 = (-bq - math.sqrt(disc)) / 2, (-bq + math.sqrt(disc)) / 2
            lam = r1 if abs(r1) < abs(r2) else r2
        else:
            lam = -bq / 2  # closest approach
        fore = Matrix.Translation(u * lam) @ fore0
    elbow = fore.translation
    wrist = elbow + fore.to_3x3().col[1].normalized() * rig.length[f"ForeArm.{s}"]
    tip = elbow - (sh + wrist) * 0.5
    up_dir = (elbow - sh).normalized()
    tip = tip - up_dir * tip.dot(up_dir)
    p.orient(f"Arm.{s}", elbow - sh, tip.normalized() if tip.length > 1e-6 else DOWN)
    p.set_world(f"ForeArm.{s}", fore)
    p.basis[f"Hand.{s}"] = Matrix.Identity(4)
    return lam


# phone beside the ear: distance from the head bone to the phone centre (head-relative x)
PHONE_EAR_X = 0.345


def phone_talk(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        breathe(p, ph * 2)
        hips_to(ctx, p, roll=1.5 * wave(ph))
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        look(p, yaw=-10 + 14 * wave(ph), pitch=-2 + 4 * wave(ph * 2), roll=8)
        hf, hd = body_frame(p, "Head")
        # phone flat against the side of the head: screen toward the ear, top tilted back
        z = hd(LEFT)
        y = hd(UP + BACK * 0.45)
        y = (y - z * y.dot(z)).normalized()
        m = Matrix((y.cross(z), y, z)).transposed().to_4x4()
        m.translation = hf(-PHONE_EAR_X, 0.0, 0.17)
        hold_slot_prop(ctx, p, "R", "Phone", m, y)
        # free hand gestures a little
        gest = 0.5 + 0.5 * wave(ph * 2, 0.1)
        f, d = body_frame(p)
        arm_to(p, "L", f(0.24, -0.22, 0.02 + 0.06 * gest), d(FWD + LEFT * 0.4), d(UP + RIGHT * 0.5),
               d(V(0.7, 0.6, -0.6)))
        return p
    return pose


# ---------------------------------------------------------------------------
# Looking up


def look_up_a(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.015, ctx.hips_z))
        p.rotate("Torso", LEFT, -8)
        breathe(p, ph * 3)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        for s in "LR":
            p.rotate(f"Arm.{s}", LEFT, 6)  # arms hang slightly forward of the leaned-back body
        look(p, yaw=22 * wave(ph), pitch=-32 + 4 * wave(ph * 2), torso_share=0.25)
        return p
    return pose


def look_up_b(ctx, frames):
    """Looking up, shading the eyes with the right hand."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        p.rotate("Torso", LEFT, -6)
        breathe(p, ph * 2)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        look(p, yaw=-14 * wave(ph), pitch=-17, torso_share=0.2)
        hf, hd = body_frame(p, "Head")
        arm_to(p, "R", hf(-0.07, -0.43, 0.25), hd(LEFT + FWD * 0.7), hd(DOWN + BACK * 0.15), hd(V(-1, 0.3, -0.4)))
        return p
    return pose


# ---------------------------------------------------------------------------
# Hot: fanning the face with a hand


def keyed(t, keys):
    """Piecewise smooth interpolation through [(t, value), ...] (floats or Vectors)."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            k = smooth((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
            return v0 + (v1 - v0) * k
    return keys[-1][1]


def _hand_on_hip(p, side):
    sx = SIDE[side]
    f, d = body_frame(p)
    arm_to(p, side, f(0.255 * sx, 0.01, -0.10), d(FWD * 0.5 + DOWN + RIGHT * sx * 0.3), d(V(-sx, 0.2, 0)),
           d(V(sx, 0.5, 0.1)))


def fan_hot(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.01, ctx.hips_z - 0.005), roll=2)
        p.rotate("Torso", LEFT, 5)
        p.rotate("Torso", FWD, -2)
        breathe(p, ph * 3, 1.8)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        _hand_on_hip(p, "L")
        look(p, yaw=-8, pitch=-10 + 2 * wave(ph * 3), roll=5)
        hf, hd = body_frame(p, "Head")
        stroke = wave(ph * 8)
        q = rot(hd(LEFT), 38 * stroke)
        palm = hf(-0.36, -0.36, 0.08) + hd(FWD) * 0.03 * stroke
        arm_to(p, "R", palm, q @ hd(UP + LEFT * 0.35), q @ hd(LEFT + BACK * 0.5), hd(V(-1, 0.1, -0.8)))
        return p
    return pose


# ---------------------------------------------------------------------------
# Cold: hunched, rubbing hands in front of the mouth, shaking


def shiver_cold(ctx, frames):
    def pose(fr):
        ph = fr / frames
        jit = 1.4 * wave(ph * 17) + 0.7 * wave(ph * 29, 0.3)
        p = ctx.stand()
        ankles = {s: ctx.ankle[s] + V(-0.05 * SIDE[s], 0, 0) for s in "LR"}
        hips_to(ctx, p, V(0.004 * wave(ph * 17), 0.01, ctx.hips_z - 0.025), yaw=0.8 * jit)
        p.rotate("Torso", LEFT, 12)
        p.rotate("Torso", UP, -jit)
        breathe(p, ph * 4, 1.4)
        for s in "LR":
            p.rotate(f"Shoulder.{s}", BACK, -14 * SIDE[s])
        plant_feet(ctx, p, ankles, knee_out=-0.25)
        relaxed_arms(ctx, p)
        look(p, pitch=4 + 0.6 * jit, yaw=0.8 * jit)
        hf, hd = body_frame(p, "Head")
        rub = 0.035 * wave(ph * 4)
        arm_to(p, "R", hf(-0.045, -0.34, 0.06 + rub), hd(UP + FWD * 0.4 + LEFT * 0.2), hd(LEFT + FWD * 0.1),
               hd(V(-0.35, 0.1, -1)))
        arm_to(p, "L", hf(0.045, -0.34, 0.06 - rub), hd(UP + FWD * 0.4 + RIGHT * 0.2), hd(RIGHT + FWD * 0.1),
               hd(V(0.35, 0.1, -1)))
        return p
    return pose


# ---------------------------------------------------------------------------
# Cycling (Props/Bicycle.glb placed at the character root). The bicycle has its
# own animation for each riding clip (BIKE_TRACKS): play it alongside so the
# crank, wheels and lean match the rider. Both are driven by the same bike
# state functions below: (lean in degrees, crank angle, wheel angle) per frame.
#
# Stopping: the rider stays on the saddle (their legs are too short to
# straddle the top tube), the bike leans onto the left foot and the right foot
# waits on the raised pedal. Cycling_Stop starts from the Cycling_Coast pose;
# Cycling_Start ends on frame 0 of Cycling.

BIKE_LEAN = 18.0  # degrees the stopped bike leans to the rider's left (so the toe reaches the ground)
CRANK_READY = math.pi / 4  # crank angle at rest: right pedal raised forward, ready to push
STOP_FRAMES, START_FRAMES = 45, 36
_PEDAL_FRAMES, _COAST_FRAMES = 24, 60
# wheel turns per Pedal loop: a whole number of spoke steps (45deg), so the loop has no jump
PEDAL_WHEEL_TURNS = 1.625
_W_PEDAL = 2 * math.pi * PEDAL_WHEEL_TURNS / _PEDAL_FRAMES  # wheel speed (rad/frame) while pedalling
_W_COAST = 2 * math.pi * 2.5 / _COAST_FRAMES
_C_PEDAL = 2 * math.pi / _PEDAL_FRAMES  # crank speed while pedalling
_STOP_BRAKE = 30  # frames the wheels take to stop
# wheel angle while stopped, chosen so Cycling_Start ends where Pedal starts (spokes repeat every 45deg)
_W_REST = -(_W_PEDAL * START_FRAMES / 2) % (math.pi / 4)


def _bike():
    import props
    return props.BIKE


def bike_pedal(f, frames):
    return 0.0, 2 * math.pi * f / frames, 2 * math.pi * PEDAL_WHEEL_TURNS * f / frames


def bike_coast(f, frames):
    return 0.0, math.pi / 2, 2 * math.pi * 2.5 * f / frames


def bike_stop(f, frames):
    t = min(f, _STOP_BRAKE)
    wheel = _W_REST - _W_COAST * _STOP_BRAKE / 2 + _W_COAST * (t - t * t / (2 * _STOP_BRAKE))
    crank = math.pi / 2 - (math.pi / 2 - CRANK_READY) * ramp(f, 4, 22)  # back-pedal to the ready position
    return BIKE_LEAN * ramp(f, 16, 36), crank, wheel


def bike_rest(f, frames):
    return BIKE_LEAN, CRANK_READY, _W_REST


def bike_start(f, frames):
    # crank accelerates from the ready position to pedalling speed, reaching a full turn (0) at the end
    c0 = (2 * (2 * math.pi - CRANK_READY) / frames) - _C_PEDAL
    crank = CRANK_READY + c0 * f + (_C_PEDAL - c0) * f * f / (2 * frames)
    wheel = _W_REST + _W_PEDAL * f * f / (2 * frames)
    return BIKE_LEAN * (1 - ramp(f, 2, 22)), crank, wheel


# bicycle track -> (rider animation, bike state function)
BIKE_TRACKS = {
    "Pedal-loop": ("Cycling-loop", bike_pedal),
    "Coast-loop": ("Cycling_Coast-loop", bike_coast),
    "Stop": ("Cycling_Stop", bike_stop),
    "Rest-loop": ("Cycling_Rest-loop", bike_rest),
    "Start": ("Cycling_Start", bike_start),
}


def _ride(ctx, p, alpha, pedal=1.0, bob=0.0, lean=0.0, foot_down=0.0):
    """Rider on the bike. lean: bike lean (deg, + = to the rider's left);
    foot_down: 0 = left foot on its pedal .. 1 = left foot on the ground."""
    b = _bike()
    bike = Matrix.Rotation(math.radians(lean), 4, "Y")  # the bike pivots about the ground line
    seat_top = b["seat"].z + 0.02
    hips = bike @ V(0, b["seat"].y - 0.005, seat_top - 0.05 + bob)
    roll = 2.0 * math.sin(alpha) * pedal
    hips_to(ctx, p, hips, pitch=12, roll=roll - lean)
    p.rotate("Torso", LEFT, 14)
    p.rotate("Torso", FWD, -roll * 0.8 + lean * 0.85)  # stay upright while the bike leans
    r = b["crank_r"]
    for s, a in (("L", alpha), ("R", alpha + math.pi)):
        sx = SIDE[s]
        pedal_pos = bike @ (b["crank"] + V(b["pedal_x"] * sx + 0.01 * sx, r * math.sin(a), -r * math.cos(a)))
        ankle = pedal_pos + V(0, 0.05, 0.075)
        pitch = 12 + 10 * math.sin(a + 0.6)
        k = foot_down if s == "L" else 0.0
        if k > 0:
            ground = V(hips.x + 0.20, b["crank"].y + 0.03, ctx.ankle["L"].z)
            ankle = ankle.lerp(ground, smooth(k)) + V(0.02, 0, 0.07) * math.sin(math.pi * k)
            pitch = lerp(pitch, 4.0, smooth(k))  # toe just down, touching the ground
        p.leg_ik(s, ankle, V(0.12 * sx + 0.25 * k, -1, 0.3))
        p.foot_flat(s, FWD, pitch=pitch)
    for s in "LR":
        sx = SIDE[s]
        grip = bike @ V(b["grips"] * sx, b["bar"].y, b["bar"].z + 0.03)
        arm_to(p, s, grip, FWD + DOWN * 0.6 - LEFT * sx * 0.2, DOWN + FWD * 0.5 - LEFT * sx * 0.3,
               V(sx * 0.9, 0.6, -0.2))
    look(p, pitch=-26, yaw=0)


def cycling(ctx, frames):
    def pose(fr):
        _, alpha, _ = bike_pedal(fr, frames)
        p = ctx.stand()
        _ride(ctx, p, alpha, bob=0.004 * math.cos(2 * alpha))
        return p
    return pose


def cycling_coast(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _ride(ctx, p, bike_coast(fr, frames)[1], pedal=0.0, bob=0.003 * wave(ph * 2))
        look(p, yaw=10 * wave(ph), pitch=2 * wave(ph * 2))
        return p
    return pose


def cycling_stop(ctx, frames):
    """From coasting: brake, back-pedal to the ready position, put the left foot down."""
    def pose(fr):
        lean, alpha, _ = bike_stop(fr, frames)
        p = ctx.stand()
        brake = math.sin(math.pi * ramp(fr, 2, 26))  # weight pitches forward under braking
        _ride(ctx, p, alpha, pedal=0.0, lean=lean, foot_down=ramp(fr, 12, 32))
        p.rotate("Torso", LEFT, 5 * brake)
        look(p, pitch=-6 * brake + 14 * ramp(fr, 20, 44), yaw=-8 * ramp(fr, 26, 44))
        return p
    return pose


def cycling_rest(ctx, frames):
    """Stopped, left foot on the ground, looking around (as if at a crossing)."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _ride(ctx, p, CRANK_READY, pedal=0.0, lean=BIKE_LEAN, foot_down=1.0)
        breathe(p, ph * 3)
        # look left and right, a little longer to the left
        yaw = 30 * wave(ph) * (1.0 if wave(ph) > 0 else 0.8) - 8 * (1 - abs(wave(ph)))
        look(p, pitch=14 + 3 * wave(ph * 2), yaw=yaw)
        return p
    return pose


def cycling_start(ctx, frames):
    """Push off from the stopped pose and pedal away; ends on frame 0 of Cycling."""
    def pose(fr):
        lean, alpha, _ = bike_start(fr, frames)
        p = ctx.stand()
        k = ramp(fr, 8, frames)
        _ride(ctx, p, alpha, pedal=k, bob=0.004 * math.cos(2 * alpha) * k, lean=lean,
              foot_down=1 - ramp(fr, 3, 18))
        push = math.sin(math.pi * ramp(fr, 0, 20))
        p.rotate("Torso", LEFT, 6 * push)  # lean into the first pedal stroke
        look(p, pitch=14 * (1 - ramp(fr, 0, 16)), yaw=-8 * (1 - ramp(fr, 0, 12)))
        return p
    return pose


# Hand signals, given while coasting. Each clip is as long as Cycling_Coast and
# starts and ends on its frame 0, so coast -> signal -> coast has no seam; the
# bicycle plays its Coast animation underneath (BIKE_SIGNAL_TRACK).
SIGNAL_FRAMES = _COAST_FRAMES
SIGNAL_KEYS = [(0.0, 0.0), (0.07, 0.0), (0.22, 1.0), (0.72, 1.0), (0.92, 0.0), (1.0, 0.0)]


def _signal_arm(p, kind):
    """Pose the signalling arm: "Left"/"Right" = arm straight out, "Stop" = left arm bent down."""
    f, d = body_frame(p)
    if kind == "Stop":
        s, sx = "L", 1.0
        sh = p.head("Arm.L")
        arm_to(p, s, sh + d(LEFT) * 0.27 + d(DOWN) * 0.33 + d(FWD) * 0.02, d(DOWN), d(BACK),
               d(V(1, 0.2, 0.4)))
        return s
    s = "L" if kind == "Left" else "R"
    sx = SIDE[s]
    sh = p.head(f"Arm.{s}")
    arm_to(p, s, sh + d(LEFT * sx) * 0.60 + d(DOWN) * 0.06 + d(FWD) * 0.04, d(LEFT * sx + DOWN * 0.1), d(DOWN),
           d(V(0, 1, -0.3)))
    return s


def cycling_signal(kind):
    def build(ctx, frames):
        coast = cycling_coast(ctx, frames)

        def pose(fr):
            ph = fr / frames
            k = keyed(ph, SIGNAL_KEYS)
            p = coast(fr)
            sig = p.copy()
            s = _signal_arm(sig, kind)
            blend_bones(p, sig, k, ARM[s])
            # check over the shoulder toward the turn first, then look ahead
            glance = math.sin(math.pi * ramp(ph, 0.05, 0.5))
            look(p, yaw=34 * SIDE[s] * glance, pitch=-4 * glance)
            return p
        return pose
    return build


BIKE_SIGNAL_TRACK = "Coast-loop"


def bike_track_for(anim):
    if anim.startswith("Cycling_Signal_"):
        return BIKE_SIGNAL_TRACK
    return next((t for t, (a, _) in BIKE_TRACKS.items() if a == anim), None)


# ---------------------------------------------------------------------------
# Sitting on the floor (legs out, leaning back on the hands)

FLOOR_KEYS = [
    # t, hips position, torso pitch, ankle y, foot pitch, hands-on-floor blend
    (0.00, V(0, 0.00, 0.406), 0.0, -0.03, 0.0, 0.0),
    (0.38, V(0, -0.03, 0.22), 32.0, -0.03, 0.0, 0.0),
    (0.66, V(0, 0.08, 0.07), 12.0, -0.17, -20.0, 1.0),
    (1.00, V(0, 0.07, 0.015), -14.0, -0.36, -62.0, 1.0),
]


def _sit_floor(ctx, p, t, ph=0.0, idle=False):
    keys = FLOOR_KEYS
    hips = keyed(t, [(k[0], k[1]) for k in keys])
    torso = keyed(t, [(k[0], k[2]) for k in keys])
    ankle_y = keyed(t, [(k[0], k[3]) for k in keys])
    foot_pitch = keyed(t, [(k[0], k[4]) for k in keys])
    hands = keyed(t, [(k[0], k[5]) for k in keys])
    hips_to(ctx, p, hips, pitch=torso * 0.4)
    p.rotate("Torso", LEFT, torso * 0.6)
    p.rotate("Head", LEFT, -torso * 0.7)
    if idle:
        breathe(p, ph * 2, 1.3)
    for s in "LR":
        sx = SIDE[s]
        ankle = V(0.19 * sx + 0.03 * sx * hands, ankle_y, ctx.ankle[s].z)
        wig = 0.0
        if idle and s == "R":
            wig = 8 * max(0.0, wave(ph * 3)) * (1 if int(ph * 3) == 1 else 0)
        p.leg_ik(s, ankle, V(0.25 * sx, -0.6, 1.0 * (1 - smooth(clamp(t)) * 0.4)))
        p.foot_flat(s, rot(UP, 14 * sx * smooth(t)) @ FWD, pitch=foot_pitch - wig)
    relaxed_arms(ctx, p)
    if hands > 0:
        floor = p.copy()
        for s in "LR":
            sx = SIDE[s]
            arm_to(floor, s, V(0.25 * sx, hips.y + 0.24, 0.04), BACK + LEFT * sx * 0.5, DOWN, V(0.5 * sx, 1, 0.3))
        for s in "LR":
            blend_bones(p, floor, hands, ARM[s])


def sit_floor_down(ctx, frames):
    def pose(fr):
        p = ctx.stand()
        _sit_floor(ctx, p, fr / frames)
        return p
    return pose


def sit_floor_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _sit_floor(ctx, p, 1.0, ph, idle=True)
        look(p, yaw=16 * wave(ph), pitch=-4 + 3 * wave(ph * 2, 0.2))
        return p
    return pose


def sit_floor_up(ctx, frames):
    down = sit_floor_down(ctx, frames)

    def pose(fr):
        return down(frames - fr)
    return pose


# ---------------------------------------------------------------------------
# Extras: clap, cheer, talk, point, nod, head shake, shrug, dance, waiting


def _clap_hands(p, sep, y=-0.40, z=0.20):
    f, d = body_frame(p)
    arm_to(p, "R", f(-sep / 2, y, z), d(FWD + UP * 0.6 + LEFT * 0.2), d(LEFT), d(V(-0.8, 0.3, -1)))
    arm_to(p, "L", f(sep / 2, y, z), d(FWD + UP * 0.6 + RIGHT * 0.2), d(RIGHT), d(V(0.8, 0.3, -1)))


def clap(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0, ctx.hips_z - 0.008 * abs(math.sin(math.pi * 3 * ph))))
        breathe(p, ph)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        sep = 0.035 + 0.15 * abs(math.sin(math.pi * 3 * ph)) ** 0.7
        _clap_hands(p, sep)
        look(p, pitch=-4, yaw=6 * wave(ph))
        return p
    return pose


def cheer(ctx, frames):
    def pose(fr):
        ph = fr / frames
        bounce = abs(math.sin(math.pi * 2 * ph))
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0, ctx.hips_z - 0.04 + 0.04 * bounce))
        p.rotate("Torso", LEFT, -6)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        f, d = body_frame(p)
        for s in "LR":
            sx = SIDE[s]
            pump = 0.06 * math.sin(math.pi * 2 * ph * 2 + (0 if s == "L" else 0.5))
            arm_to(p, s, f(0.44 * sx, -0.08, 0.80 + pump), d(UP + LEFT * sx * 0.4), d(FWD), d(V(sx, 0.3, -0.5)))
        look(p, pitch=-12 + 4 * bounce, roll=6 * wave(ph))
        return p
    return pose


def _talk_hands(p, ph, amount=1.0):
    f, d = body_frame(p)
    for s, off in (("R", 0.0), ("L", 0.37)):
        sx = SIDE[s]
        g = 0.5 + 0.5 * wave(ph * 2, off) * wave(ph * 3, off * 0.5)
        palm = f(0.22 * sx, -0.30 - 0.06 * g, 0.03 + 0.10 * g * amount)
        arm_to(p, s, palm, d(FWD + LEFT * sx * 0.5), d(UP * 1.0 + RIGHT * sx * 0.6 + BACK * 0.2 * g),
               d(V(0.8 * sx, 0.6, -0.5)))


def talk(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, roll=1.2 * wave(ph))
        p.rotate("Torso", UP, 5 * wave(ph, 0.1))
        breathe(p, ph * 3)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        _talk_hands(p, ph)
        look(p, yaw=-6 * wave(ph, 0.1), pitch=4 * wave(ph * 6), roll=4 * wave(ph * 2))
        return p
    return pose


def sit_chair_talk(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _sit_chair(ctx, p, 1.0, lean_extra=6)
        breathe(p, ph * 3, 1.2)
        _hands_on_thighs(p)
        relaxed = p.copy()
        _talk_hands(p, ph, 0.8)
        # the left hand mostly stays on the thigh
        blend_bones(p, relaxed, 0.75, ARM["L"])
        look(p, yaw=12 + 6 * wave(ph, 0.1), pitch=4 * wave(ph * 6), roll=3 * wave(ph * 2))
        return p
    return pose


def point(ctx, frames):
    def pose(fr):
        t = fr / frames
        k = ramp(t, 0.05, 0.25) * (1 - ramp(t, 0.78, 1.0))
        p = ctx.stand()
        p.rotate("Torso", UP, -10 * k)
        breathe(p, t)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        target = p.copy()
        f, d = body_frame(target)
        arm_to(target, "R", f(-0.30, -0.60, 0.30 + 0.01 * math.sin(t * 20)), d(FWD + UP * 0.1), d(DOWN + LEFT * 0.3),
               d(V(-1, 0.3, -0.6)))
        blend_bones(p, target, k, ARM["R"])
        look(p, yaw=-14 * k, pitch=-3 * k)
        return p
    return pose


def nod_yes(ctx, frames):
    def pose(fr):
        t = fr / frames
        p = ctx.stand()
        breathe(p, t)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        env = math.sin(math.pi * clamp(t / 0.9)) ** 0.8
        look(p, pitch=13 * env * max(0.0, math.sin(2 * math.pi * 3 * t)) - 3 * env)
        p.rotate("Torso", LEFT, 2 * env)
        return p
    return pose


def shake_no(ctx, frames):
    def pose(fr):
        t = fr / frames
        p = ctx.stand()
        breathe(p, t)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        env = math.sin(math.pi * clamp(t / 0.92)) ** 0.7
        look(p, yaw=20 * env * math.sin(2 * math.pi * 3 * t), pitch=4 * env, torso_share=0.15)
        return p
    return pose


def shrug(ctx, frames):
    def pose(fr):
        t = fr / frames
        k = ramp(t, 0.0, 0.3) * (1 - ramp(t, 0.68, 1.0))
        p = ctx.stand()
        breathe(p, t)
        for s in "LR":
            p.rotate(f"Shoulder.{s}", BACK, -16 * k * SIDE[s])
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        target = p.copy()
        f, d = body_frame(target)
        for s in "LR":
            sx = SIDE[s]
            arm_to(target, s, f(0.40 * sx, -0.20, 0.02), d(FWD * 0.6 + LEFT * sx), d(UP + LEFT * sx * 0.3),
                   d(V(0.3 * sx, 0.3, -1)))
        for s in "LR":
            blend_bones(p, target, k, ARM[s])
        look(p, roll=10 * k, pitch=-4 * k)
        return p
    return pose


def dance(ctx, frames):
    def pose(fr):
        ph = fr / frames
        beat = abs(math.sin(math.pi * 2 * ph))
        sway = math.sin(math.pi * 2 * ph)
        p = ctx.stand()
        hips_to(ctx, p, V(0.045 * sway, 0, ctx.hips_z - 0.05 + 0.035 * beat), roll=-5 * sway, yaw=6 * sway)
        p.rotate("Torso", FWD, 7 * sway)
        p.rotate("Torso", UP, -8 * sway)
        ankles = {s: ctx.ankle[s] + V(0.04 * SIDE[s], 0, 0) for s in "LR"}
        plant_feet(ctx, p, ankles, knee_out=0.4)
        relaxed_arms(ctx, p)
        f, d = body_frame(p)
        for s in "LR":
            sx = SIDE[s]
            pump = math.sin(math.pi * 2 * ph * 2 + (0.0 if s == "L" else math.pi))
            arm_to(p, s, f(0.30 * sx, -0.30, 0.15 + 0.10 * pump), d(FWD + UP * 0.5), d(RIGHT * sx + DOWN * 0.3),
                   d(V(0.9 * sx, 0.3, -0.6)))
        look(p, roll=-8 * sway, pitch=-3 + 6 * beat)
        return p
    return pose


def wait_hands_behind(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, roll=1.5 * wave(ph))
        p.rotate("Torso", LEFT, -3)
        breathe(p, ph * 3)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        f, d = body_frame(p)
        for s in "LR":
            sx = SIDE[s]
            arm_to(p, s, f(0.07 * sx, 0.29, -0.08), d(RIGHT * sx + DOWN * 0.4), d(BACK * 0.6 + RIGHT * sx),
                   d(V(sx, 0.4, -0.3)))
        look(p, yaw=20 * wave(ph), pitch=-2 + 3 * wave(ph * 2, 0.25))
        return p
    return pose


# ---------------------------------------------------------------------------
# Cello: carried by the neck (Props/Cello_Carried.glb on "IteamSlot.R") and
# played seated (Props/Cello_Played.glb at the character root, Props/Bow.glb on
# "IteamSlot.R", Props/Chair.glb at the root). There is no transition clip:
# cut between carrying and playing with a visual effect.
#
# Cello model space: +Y up the instrument, origin where the endpin leaves the
# body (endpin goes to y=-CELLO_ENDPIN), strings on the +Z side, width along X.

from props import CELLO_ENDPIN, CELLO_SCALE as CELLO_S  # noqa: E402  (cello is modelled at unit size)
CELLO_GRIP_Y = 0.62 * CELLO_S   # carrying hand: on the neck just above the body
CELLO_STRINGS_Z = 0.075 * CELLO_S  # string height above the body centre plane
CELLO_CARRY = {"x": -0.27, "y": -0.46, "z": 0.21}
# Played: the back of the upper bout rests against the chest (top of the body
# below the shoulders), the lower bout between the knees, the endpin forward on
# the floor. The cello leans back toward the player and, because the heads are
# so big, further to the player's left than a real one so the neck clears them.
CELLO_PLAY_ENDPIN = V(-0.25, -0.35, 0.0)  # endpin tip on the floor, right of centre
CELLO_PLAY_LEAN = (10.0, 35.0)  # degrees: back toward the player, to the player's left


def cello_play_matrix(ctx=None, p=None):
    back, left = (math.radians(a) for a in CELLO_PLAY_LEAN)
    u = V(math.tan(left), math.tan(back), 1).normalized()
    z = FWD - u * FWD.dot(u)
    z.normalize()
    m = Matrix((u.cross(z), u, z)).transposed().to_4x4()
    m.translation = CELLO_PLAY_ENDPIN + u * CELLO_ENDPIN * CELLO_S
    return m


def _fist_centre(p, side="R"):
    s = side.upper()
    w = p.world(f"Hand.{s}").to_3x3()
    palm_sign = 1 if s == "R" else -1
    return p.head(f"Hand.{s}") + w.col[1].normalized() * p.hand_grip_offset(s) + w.col[0].normalized() * 0.02 * palm_sign


def _carry_cello_arm(p, bob=0.0):
    f, d = body_frame(p)
    palm = f(CELLO_CARRY["x"], CELLO_CARRY["y"], CELLO_CARRY["z"] + bob)
    arm_to(p, "R", palm, d(FWD + LEFT * 0.2 + UP * 0.25), d(LEFT + UP * 0.15), d(V(-0.45, 0.5, -1)), straight=True)
    p.basis["Hand.R"] = Matrix.Identity(4)


def cello_carry_world(ctx, p):
    """Reference: cello upright, front facing forward, neck through the right fist."""
    y, z = UP, FWD
    m = Matrix((y.cross(z), y, z)).transposed().to_4x4()
    m.translation = _fist_centre(p) - y * CELLO_GRIP_Y
    return m


def cello_carry_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        breathe(p, ph * 2)
        p.rotate("Torso", LEFT, -3)
        p.rotate("Torso", UP, -5)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        p.rotate("Arm.L", BACK, -6)
        _carry_cello_arm(p, 0.004 * wave(ph * 2))
        look(p, yaw=10 * wave(ph), pitch=-2 + 2 * wave(ph, 0.3))
        return p
    return pose


def cello_carry_walk(ctx, frames):
    def pose(fr):
        p = ctx.walk(fr)
        p.rotate("Torso", LEFT, -3)
        _carry_cello_arm(p)
        return p
    return pose


def _ref_grip(ctx):
    """Reference pose for the bow grip: straight wrist, fist in front of the body."""
    p = ctx.stand()
    plant_feet(ctx, p)
    relaxed_arms(ctx, p)
    f, d = body_frame(p)
    arm_to(p, "R", f(-0.20, -0.35, 0.15), d(FWD), d(LEFT), d(V(-0.4, 0.5, -1)), straight=True)
    p.basis["Hand.R"] = Matrix.Identity(4)
    return p


def bow_world(ctx, p):
    """Bow stick through the fist along the thumb direction, frog at the fist."""
    w = p.world("Hand.R").to_3x3()
    hx, hy, hz = (w.col[i].normalized() for i in range(3))
    m = Matrix((-hy, -hz, hx)).transposed().to_4x4()
    m.translation = _fist_centre(p)
    return m


def _cello_seated(ctx, p, ph, sway=1.0):
    _sit_chair(ctx, p, 1.0, lean_extra=10, spread=0.08)
    p.rotate("Torso", FWD, -2.5 * sway * wave(ph))
    p.rotate("Torso", UP, 4)
    breathe(p, ph * 3)
    look(p, yaw=10, pitch=10 + 3 * sway * wave(ph * 2), roll=-2 * sway * wave(ph))  # upright: clear of the neck
    cm = cello_play_matrix()
    cx, cy, cz = (cm.to_3x3().col[i].normalized() for i in range(3))
    return cm, cx, cy, cz


def _cello_left_hand(p, cm, cx, cy, cz, ph, shifts=True):
    pos_y = (0.72 + (0.07 * smooth(0.5 + 0.5 * wave(ph * 2, 0.15)) if shifts else 0.0)) * CELLO_S
    vib = 0.006 * wave(ph * 24)
    neck = cm @ V(0, pos_y + vib, 0.02 * CELLO_S)
    palm = neck + cx * 0.075 - cz * 0.01
    arm_to(p, "L", palm, cz + RIGHT * 0.6, -cx + cz * 0.2, cx + DOWN * 0.4 + BACK * 0.3)


def cello_play(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        cm, cx, cy, cz = _cello_seated(ctx, p, ph)
        _cello_left_hand(p, cm, cx, cy, cz, ph)
        # bow: two long strokes per loop, eased at the turnarounds
        stroke = 0.5 - 0.5 * math.cos(2 * math.pi * 2 * ph)
        s = 0.12 + 0.44 * stroke
        string_tilt = 6 * wave(ph, 0.2)
        d = (rot(cz, string_tilt) @ cx).normalized()
        contact = cm @ V(0, 0.40 * CELLO_S, CELLO_STRINGS_Z)
        origin = contact - d * s + cz * 0.022
        z = cz - d * cz.dot(d)
        z.normalize()
        m = Matrix((d.cross(z), d, z)).transposed().to_4x4()
        m.translation = origin
        hold_slot_prop(ctx, p, "R", "Bow", m, spin=True)
        return p
    return pose


def cello_rest(ctx, frames):
    """Seated with the cello, bow resting on the right knee, swaying to the music."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        cm, cx, cy, cz = _cello_seated(ctx, p, ph, sway=1.4)
        _cello_left_hand(p, cm, cx, cy, cz, ph, shifts=False)
        # bow held at the frog just above the right knee, tip down toward the floor
        knee = p.head("Leg.R")
        frog = knee + V(-0.04, 0.02, 0.16)
        d = (FWD * 0.45 + DOWN + RIGHT * 0.25).normalized()
        z = FWD - d * FWD.dot(d)
        z.normalize()
        m = Matrix((d.cross(z), d, z)).transposed().to_4x4()
        m.translation = frog
        hold_slot_prop(ctx, p, "R", "Bow", m, spin=True)
        return p
    return pose


# ---------------------------------------------------------------------------
# Police (made for the PoliceMan models): talking into a shoulder radio
# (Props/Radio_Mic.glb on "Torso") and writing a ticket (Props/TicketBook.glb
# on "IteamSlot.L", Props/Pen.glb on "IteamSlot.R").

# speaker mic clipped to the left chest by the shoulder seam (beside the badge), under the beard (rest pose, armature space)
RADIO_MIC_AT_REST = V(0.205, -0.212, 1.025)
RADIO_MIC_SIZE = V(0.06, 0.03, 0.08)


def radio_mic_world(ctx, p):
    local = ctx.rig.rest["Torso"].inverted() @ Matrix.Translation(RADIO_MIC_AT_REST)
    return p.world("Torso") @ local


def _key_radio(ctx, p, press):
    """Left hand on the outer side of the mic, fingers up; press 1 = squeezing the key."""
    m = radio_mic_world(ctx, p)
    r = m.to_3x3()
    off = 1.0 - press
    palm = m @ V(RADIO_MIC_SIZE.x / 2 + PALM_SURFACE + 0.004 + 0.035 * off, -0.01 - 0.03 * off, -0.005 - 0.05 * off)
    arm_to(p, "L", palm, r @ (UP + FWD * 0.25 + RIGHT * 0.15 * press), r @ (RIGHT + FWD * 0.15 * off),
           r @ V(1, 0.5, -0.9))


def police_radio(ctx, frames):
    # talk (keyed) -> let go and listen -> key again and answer
    press_keys = [(0.0, 1.0), (0.38, 1.0), (0.46, 0.0), (0.84, 0.0), (0.92, 1.0), (1.0, 1.0)]

    def pose(fr):
        ph = fr / frames
        press = keyed(ph, press_keys)
        talking = press * (0.5 + 0.5 * wave(ph * 9))  # little nods while speaking
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.005, ctx.hips_z - 0.004), roll=-1.5 + 1.0 * wave(ph))
        breathe(p, ph * 2)
        p.rotate("Torso", UP, 4 * press)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        _hand_on_hip(p, "R")
        _key_radio(ctx, p, press)
        look(p, yaw=lerp(-6 + 10 * wave(ph * 2, 0.2), 24, press), pitch=lerp(-2, 13, press) + 3 * talking,
             roll=lerp(0, -10, press))
        return p
    return pose


# ticket book: long axis +Y, cover (pages) facing +Z, origin at its centre
TICKET_BOOK_SIZE = V(0.16, 0.24, 0.026)
PEN_TIP = 0.115  # pen tip distance from the fist centre (pen origin)


def _hold_book(p, lift=0.0):
    f, d = body_frame(p)
    arm_to(p, "L", f(0.10, -0.43, 0.10 + lift), d(FWD + RIGHT * 0.25 + UP * 0.9), d(UP * 0.8 + BACK + RIGHT * 0.1),
           d(V(0.8, 0.5, -1)), straight=True)
    p.basis["Hand.L"] = Matrix.Identity(4)


def _ref_book(ctx):
    p = ctx.stand()
    plant_feet(ctx, p)
    relaxed_arms(ctx, p)
    _hold_book(p)
    return p


def ticket_book_world(ctx, p):
    """Book lying on the left palm, long axis along the fingers."""
    w = p.world("Hand.L").to_3x3()
    hx, hy, hz = (w.col[i].normalized() for i in range(3))
    c = p.head("Hand.L") + hy * 0.13 - hx * (PALM_SURFACE + TICKET_BOOK_SIZE.z / 2 + 0.002)
    m = Matrix((hz, hy, -hx)).transposed().to_4x4()
    m.translation = c
    return m


def pen_world(ctx, p):
    """Pen through the right fist, tip out of the little-finger side."""
    w = p.world("Hand.R").to_3x3()
    hx, hy, hz = (w.col[i].normalized() for i in range(3))
    m = Matrix((-hy, hz, -hx)).transposed().to_4x4()
    m.translation = _fist_centre(p)
    return m


def _write(ctx, p, u, v, lift):
    """Put the pen tip at (u, v) on the open page (book coordinates), `lift` above it."""
    bone, off = ctx.prop_offset("TicketBook")
    book = p.world(bone) @ off
    r = book.to_3x3()
    bx, by, bz = (r.col[i].normalized() for i in range(3))
    tip = book @ V(u, v, TICKET_BOOK_SIZE.z / 2 + 0.002 + lift)
    # pen leaning back toward the writer's right shoulder
    axis = (-bz + bx * -0.45 + by * -0.35).normalized()  # pen +Y points at the page
    y = axis
    z = (bx - y * bx.dot(y)).normalized()
    m = Matrix((y.cross(z), y, z)).transposed().to_4x4()
    m.translation = tip - y * PEN_TIP
    hold_slot_prop(ctx, p, "R", "Pen", m, spin=True)


def police_ticket(ctx, frames):
    lines = 3
    w0, w1 = 0.12, 0.88  # writing time span

    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.0, ctx.hips_z - 0.006), roll=1.5 * wave(ph))
        p.rotate("Torso", LEFT, 4)
        breathe(p, ph * 2)
        plant_feet(ctx, p)
        relaxed_arms(ctx, p)
        _hold_book(p, 0.006 * wave(ph * 2))
        writing = ramp(ph, w0 - 0.06, w0) * (1 - ramp(ph, w1, w1 + 0.06))
        t = clamp((ph - w0) / (w1 - w0)) * lines
        line = min(int(t), lines - 1)
        s = t - line
        # across the line with a scribble, short lift between lines
        u = lerp(-0.045, 0.05, s) if writing > 0 else 0.0
        v = 0.065 - 0.05 * line + 0.007 * math.sin(s * math.pi * 14)
        between = max(ramp(s, 0.9, 1.0), 1 - ramp(s, 0.0, 0.1)) if 0 < t < lines else 0.0
        _write(ctx, p, u * writing + 0.02 * (1 - writing), v * writing - 0.07 * (1 - writing),
               0.012 * between + 0.06 * (1 - writing))
        # glance up at the car between writing
        up = 1 - writing
        look(p, yaw=-14 * up + 50 * u * writing, pitch=lerp(32, -4, up) + 1.5 * wave(ph * 6) * writing,
             roll=-3 * up, torso_share=0.15)
        return p
    return pose


# ---------------------------------------------------------------------------
# Taxi (Props/Taxi.glb at the character root; see props.TAXI). The character
# starts Taxi_Enter standing on the kerb facing the car's right side, opens the
# rear door with the right hand, walks round it to the opening, hops up
# backwards onto the (for these short legs, high) seat, swings the legs in over
# the sill (ending facing forward, -X) and pulls the door shut. Taxi_Ride loops
# seated; Taxi_Exit reverses the trip, hopping down, and ends where Taxi_Enter
# started. The taxi's door has an animation per clip (TAXI_TRACKS) to play
# alongside, like the bicycle.

from props import TAXI, TAXI_FRAME_IN, TAXI_GRIP_IN, TAXI_HANDLE_OUT  # noqa: E402

TAXI_ENTER_FRAMES = TAXI_EXIT_FRAMES = 165
# The cabin narrows toward the roof (the windows lean in), so the big heads only
# clear it toward the middle of the bench: the passenger shuts the door sitting
# next to it (TAXI_BY_DOOR), then slides over (TAXI_SEATED).
TAXI_SEATED = V(-0.60, -1.20, TAXI["seat_z"] - 0.025)  # riding: hips, facing -X
TAXI_BY_DOOR = V(-0.60, -0.95, TAXI["seat_z"] - 0.025)  # seated next to the door
TAXI_KERB = V(-0.55, -0.39, 0.0)  # hips xy standing in front of the opening, facing +Y (back to the seat)
TAXI_EDGE = V(-0.55, -0.78, TAXI["seat_z"])  # hips perched on the seat edge, facing +Y
TAXI_HANG = 0.40  # perched, the feet hang this far below the hips (they don't reach the ground)
_GROUND = 0.0


def _yaw_m(yaw):
    return Matrix.Rotation(math.radians(yaw), 3, "Z")


def taxi_door_point(angle, local):
    return TAXI["hinge"] + _yaw_m(angle) @ local


def _door_normal(angle):
    return _yaw_m(angle) @ BACK  # outward face of the door


# door angle keys (frame, degrees); "follow" spans are where a hand drives the door
ENTER_DOOR = [(0, 0.0), (10, 0.0), (28, 40.0), (44, TAXI["door_open"]), (138, TAXI["door_open"]), (150, 24.0),
              (156, 0.0), (TAXI_ENTER_FRAMES, 0.0)]
EXIT_DOOR = [(0, 0.0), (10, 0.0), (26, 36.0), (42, TAXI["door_open"]), (114, TAXI["door_open"]), (136, 30.0),
             (144, 0.0), (TAXI_EXIT_FRAMES, 0.0)]


def taxi_door_enter(f, frames):
    return keyed(f, ENTER_DOOR)


def taxi_door_exit(f, frames):
    return keyed(f, EXIT_DOOR)


def _ankle_z(floor):
    return floor + 0.071


def _foot_keys(keys):
    """keys: [(frame, V(x, y, floor), yaw, bulge)] for one ankle -> fn(f) -> (ankle, yaw, lifting).
    Moving segments arc up (and sideways by `bulge`) so the feet step instead of sliding."""
    def at(f):
        if f <= keys[0][0]:
            k = keys[0]
            return V(k[1].x, k[1].y, _ankle_z(k[1].z)), k[2], 0.0
        for k0, k1 in zip(keys, keys[1:]):
            if f <= k1[0]:
                s = (f - k0[0]) / (k1[0] - k0[0])
                e = smooth(s)
                a = V(k0[1].x, k0[1].y, _ankle_z(k0[1].z))
                b = V(k1[1].x, k1[1].y, _ankle_z(k1[1].z))
                pos = a.lerp(b, e)
                d = b - a
                lift = 0.0
                if d.length > 0.01:
                    lift = math.sin(math.pi * s)
                    side = V(-d.y, d.x, 0).normalized() if d.xy.length > 1e-6 else V(0, 0, 0)
                    pos += V(0, 0, max(0.06, abs(d.z) * 0.5 + k1[3][1]) * lift) + side * k1[3][0] * lift
                return pos, lerp(k0[2], k1[2], e), lift
        k = keys[-1]
        return V(k[1].x, k[1].y, _ankle_z(k[1].z)), k[2], 0.0
    return at


def _taxi_body(ctx, p, hips, yaw, feet, pitch=0.0, torso=0.0, torso_roll=0.0, seated=0.0):
    """Hips at `hips` turned by `yaw` (deg, 0 = facing -Y); feet = {side: (ankle, foot yaw)}."""
    p.translate("Hips", hips - p.head("Hips"))
    if pitch:
        p.rotate("Hips", LEFT, pitch)
    p.rotate("Hips", UP, yaw)
    r = _yaw_m(yaw)
    if torso:
        p.rotate("Torso", r @ LEFT, torso)
    if torso_roll:
        p.rotate("Torso", r @ FWD, torso_roll)
    for s in "LR":
        ankle, fyaw = feet[s]
        knee = r @ V(0.25 * SIDE[s], -1, 0.6 * seated)
        p.leg_ik(s, ankle, knee)
        p.foot_flat(s, _yaw_m(fyaw) @ FWD)
    return r


def _look_yawed(p, r, yaw=0.0, pitch=0.0, roll=0.0):
    p.rotate("Head", UP, yaw)
    p.rotate("Head", r @ LEFT, pitch)
    if roll:
        p.rotate("Head", r @ FWD, roll)


def _thigh_hands(p, r, tuck=0.0):
    """tuck 0..1 brings the elbows down and in (to pass the backrest and door frame)."""
    for s in "LR":
        sx = SIDE[s]
        knee = p.head(f"Leg.{s}")
        hip = p.head(f"UpperLeg.{s}")
        palm = hip.lerp(knee, 0.62) + r @ V(-0.015 * sx, 0, 0) + V(0, 0, 0.085)
        arm_to(p, s, palm, r @ (FWD + DOWN * 0.35 + RIGHT * sx * 0.15), DOWN,
               r @ V(0.7 * sx, 1, 0).lerp(V(0.25 * sx, 0.3, -1), tuck))


def _hand_on_door(p, side, angle, local, inside, fingers_local=None):
    """Hand flat on the door at door-local point `local` (inside or outside face)."""
    n = _door_normal(angle)
    pt = taxi_door_point(angle, local)
    palm_dir = n if inside else -n  # palm faces the door
    palm = pt - palm_dir * (PALM_SURFACE + (0.012 if inside else 0.05))
    along = _yaw_m(angle) @ (fingers_local or V(-1, 0, -0.6))
    arm_to(p, side, palm, along, palm_dir, V(-0.6 * SIDE[side], 0.2, -1) if side == "R" else V(1, 0.2, -1))


def _taxi_seated(ctx, p, ph=0.0, sway=0.0):
    """Riding pose: facing -X, hands on the thighs (used by all three clips)."""
    yaw = -90.0
    r = _yaw_m(yaw)
    floor = TAXI["floor_z"]
    feet = {s: (V(TAXI_SEATED.x - 0.30, TAXI_SEATED.y + 0.17 * -SIDE[s], _ankle_z(floor)), yaw + 8 * SIDE[s])
            for s in "LR"}
    hips = TAXI_SEATED + V(0, 0.006 * sway, 0)
    _taxi_body(ctx, p, hips, yaw, feet, seated=1.0)
    p.rotate("Torso", r @ FWD, 1.5 * sway)
    _thigh_hands(p, r)
    return r


def taxi_ride(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        sway = wave(ph * 2) * 0.6 + wave(ph * 5, 0.3) * 0.4  # the car's motion
        r = _taxi_seated(ctx, p, ph, sway)
        # watch the city go by out of the right window, now and then ahead
        _look_yawed(p, r, yaw=-38 * max(0.0, wave(ph, 0.15)) + 6 * wave(ph * 3), pitch=-2 + 2 * wave(ph * 2))
        return p
    return pose


def _blend_arm(p, other, k, side):
    if k > 0:
        blend_bones(p, other, k, ARM[side])


# Taxi_Enter keys --------------------------------------------------------------
_KERB_R = V(TAXI_KERB.x + 0.19, TAXI_KERB.y + 0.03, _GROUND)
_KERB_L = V(TAXI_KERB.x - 0.19, TAXI_KERB.y + 0.03, _GROUND)
NO = (0.0, 0.0)  # step arc: (sideways bulge, extra height)

ENTER_FEET = {
    "R": _foot_keys([(0, V(-0.19, -0.03, 0), -8, NO), (36, V(-0.19, -0.03, 0), -8, NO),
                     (48, V(-0.36, -0.08, 0), -60, NO), (58, V(-0.36, -0.08, 0), -60, NO),
                     (72, _KERB_R, -188, NO), (TAXI_ENTER_FRAMES, _KERB_R, -188, NO)]),
    "L": _foot_keys([(0, V(0.19, -0.03, 0), 8, NO), (46, V(0.19, -0.03, 0), 8, NO),
                     (60, V(-0.60, 0.05, 0), -125, (-0.10, 0.0)), (68, V(-0.60, 0.05, 0), -125, NO),
                     (84, _KERB_L, -172, NO), (TAXI_ENTER_FRAMES, _KERB_L, -172, NO)]),
}


def _standing_hips(ctx, feet, lifts):
    """Hips over the feet (as in the idle stance), shifted over the planted foot during a step."""
    (al, yl), (ar, yr) = feet["L"], feet["R"]
    yaw = (yl + yr) / 2
    mid = (al + ar) / 2
    hips = mid + _yaw_m(yaw) @ V(0, 0.03, 0)
    hips.z = ctx.hips_z
    for s, other in (("L", "R"), ("R", "L")):
        if lifts[s] > 0:
            hips += (feet[other][0] - mid) * V(0.35, 0.35, 0) * lifts[s]
            hips.z -= 0.012 * lifts[s]
    return hips, yaw


def _hanging_feet(hips, yaw):
    """Feet hanging from the perched hips (facing +Y, toes forward), resting on the step below the door."""
    r = _yaw_m(yaw)
    out = {}
    for s in "LR":
        a = hips + r @ V(0.12 * SIDE[s], -0.20, 0)
        floor = TAXI["sill_z"] if a.y < TAXI["side_y"] + 0.02 else _GROUND
        a.z = max(hips.z - TAXI_HANG, _ankle_z(floor))
        out[s] = (a, yaw + 8 * SIDE[s])
    return out


def _seated_feet(hips=TAXI_BY_DOOR):
    return {s: (V(hips.x - 0.30, hips.y + 0.17 * -SIDE[s], _ankle_z(TAXI["floor_z"])), -90 + 8 * SIDE[s])
            for s in "LR"}


def _bezier(a, b, c, d, t):
    u = 1 - t
    return a * u ** 3 + b * (3 * u * u * t) + c * (3 * u * t * t) + d * t ** 3


def _swing_feet(s, inward):
    """Legs swung over the sill: from hanging outside (perched, facing +Y) to the cabin floor, or back."""
    t = s if inward else 1 - s
    hang = _hanging_feet(TAXI_EDGE, -180.0)
    seat = _seated_feet()
    feet = {}
    for side in "LR":
        a, d = hang[side][0], seat[side][0]
        lift = V(0, 0, 0.42)
        pos = _bezier(a, a + lift, d + lift * 0.8, d, smooth(t))
        feet[side] = (pos, lerp(hang[side][1], seat[side][1], smooth(t)))
    return feet


def _hands_on_seat(p, r, hips):
    """Both palms on the cushion beside the hips, pushing (for hopping up or down)."""
    for s in "LR":
        sx = SIDE[s]
        palm = hips + r @ V(0.27 * sx, 0.06, 0)
        palm.z = TAXI["seat_z"] + PALM_SURFACE + 0.01
        arm_to(p, s, palm, r @ (FWD + RIGHT * sx * 0.2), DOWN, r @ V(0.3 * sx, 1, 0.2))


def _hop(ctx, p, k, t_hop, t_duck, hands, thighs):
    """Kerb (standing, back to the car) <-> perched on the seat edge. k: 0 standing .. 1 perched."""
    yaw = -180.0
    stand = V(TAXI_KERB.x, TAXI_KERB.y, ctx.hips_z)
    e = ease_in_out(k)
    hips = stand.lerp(TAXI_EDGE, e)
    hips.z += 0.05 * math.sin(math.pi * e)  # arc of the hop
    hips.z -= 0.05 * t_hop  # crouch before pushing off / landing
    planted = {"R": (_KERB_R + V(0, 0, _ankle_z(_GROUND)), -188.0), "L": (_KERB_L + V(0, 0, _ankle_z(_GROUND)), -172.0)}
    hang = _hanging_feet(hips, yaw)
    off = smooth(clamp((e - 0.15) / 0.5))  # feet leave the ground early in the hop
    feet = {s: (planted[s][0].lerp(hang[s][0], off), lerp(planted[s][1], hang[s][1], off)) for s in "LR"}
    duck = 30 * t_duck
    r = _taxi_body(ctx, p, hips, yaw, feet, pitch=duck * 0.5, torso=duck * 0.6, seated=e)
    p.rotate("Head", r @ LEFT, -duck * 0.5)
    relaxed_arms(ctx, p)
    if hands > 0:
        h = p.copy()
        _hands_on_seat(h, r, hips)
        for side in "LR":
            blend_bones(p, h, smooth(hands), ARM[side])
    if thighs > 0:
        h = p.copy()
        _thigh_hands(h, r)
        for side in "LR":
            blend_bones(p, h, smooth(thighs), ARM[side])
    return r


def taxi_enter(ctx, frames):
    seated_ref = taxi_ride(ctx, 120)(0)

    def pose(fr):
        p = ctx.stand()
        door = taxi_door_enter(fr, frames)
        if fr <= 86:
            # open the door, walk round it and turn to stand with the back to the seat
            feet, lifts = {}, {}
            for s in "LR":
                a, y, l = ENTER_FEET[s](fr)
                feet[s], lifts[s] = (a, y), l
            hips, yaw = _standing_hips(ctx, feet, lifts)
            lean = -4 * math.sin(math.pi * ramp(fr, 8, 30))  # leaning back while pulling the door
            r = _taxi_body(ctx, p, hips, yaw, feet, torso=lean)
            breathe(p, fr / 60)
            relaxed_arms(ctx, p)
            reach = ramp(fr, 0, 10) * (1 - ramp(fr, 28, 42))
            if reach > 0:
                hand = p.copy()
                _hand_on_door(hand, "R", min(door, 40.0), TAXI_HANDLE_OUT, inside=False, fingers_local=V(-0.3, 0, -1))
                _blend_arm(p, hand, smooth(reach), "R")
            k = ramp(fr, 76, 92)
            if k > 0:  # reach back for the seat
                h = p.copy()
                _hands_on_seat(h, r, hips)
                for side in "LR":
                    blend_bones(p, h, smooth(k), ARM[side])
            _look_yawed(p, r, yaw=-20 * ramp(fr, 30, 50) * (1 - ramp(fr, 60, 80)), pitch=8 * (1 - ramp(fr, 30, 60)))
            return p
        if fr <= 114:
            # hop up backwards onto the seat edge, ducking under the roof
            k = ramp(fr, 92, 108)
            crouch = math.sin(math.pi * ramp(fr, 86, 98))
            _hop(ctx, p, k, crouch, math.sin(math.pi * ramp(fr, 90, 120)),
                 hands=1 - ramp(fr, 106, 114), thighs=ramp(fr, 106, 114))
            return p
        # lift the legs over the sill and swing them in, turning to face forward; then pull the door shut
        s = ease_in_out(ramp(fr, 114, 138))
        yaw = lerp(-180.0, -90.0, s)
        hips = TAXI_EDGE.lerp(TAXI_BY_DOOR, s)
        hips.z += 0.03 * math.sin(math.pi * s)  # lift a little off the seat while turning
        slide = ease_in_out(ramp(fr, 150, 162))  # over to the middle once the door is shut
        feet = _swing_feet(s, True)
        if slide > 0:
            hips = hips.lerp(TAXI_SEATED, slide)
            feet = _seated_feet(hips)
        duck = 30 * math.sin(math.pi * ramp(fr, 90, 120))
        r = _taxi_body(ctx, p, hips, yaw, feet, pitch=duck * 0.5, torso=duck * 0.6 - 6 * math.sin(math.pi * s),
                       seated=1.0)
        if duck:
            p.rotate("Head", r @ LEFT, -duck * 0.5)
        _thigh_hands(p, r, math.sin(math.pi * s))
        reach = ramp(fr, 128, 138) * (1 - ramp(fr, 150, 158))
        lean = 26 * smooth(reach)
        if lean:
            p.rotate("Torso", r @ FWD, -lean)  # lean out toward the door (rider's right)
            p.rotate("Torso", r @ LEFT, lean * 0.4)
            _thigh_hands(p, r)
            hand = p.copy()
            _hand_on_door(hand, "R", max(door, 24.0), TAXI_GRIP_IN, inside=True, fingers_local=V(-1, 0, -0.2))
            _blend_arm(p, hand, smooth(reach), "R")
        _look_yawed(p, r, yaw=-30 * smooth(reach), pitch=6 * smooth(reach))
        k = ramp(fr, 156, frames)  # settle into the ride pose
        if k > 0:
            blend_bones(p, seated_ref, smooth(k), p.rig.names)
        return p
    return pose


EXIT_FEET = {
    "R": _foot_keys([(88, _KERB_R, -188, NO), (100, V(-0.42, 0.14, 0), -120, NO),
                     (112, V(-0.42, 0.14, 0), -120, NO), (124, V(-0.22, 0.06, 0), -45, NO),
                     (136, V(-0.22, 0.06, 0), -45, NO), (150, V(-0.19, -0.03, 0), -8, NO),
                     (TAXI_EXIT_FRAMES, V(-0.19, -0.03, 0), -8, NO)]),
    "L": _foot_keys([(88, _KERB_L, -172, NO), (96, _KERB_L, -172, NO), (108, V(-0.50, -0.10, 0), -84, NO),
                     (118, V(-0.50, -0.10, 0), -84, NO), (132, V(0.02, -0.06, 0), -20, (0.08, 0.0)),
                     (142, V(0.02, -0.06, 0), -20, NO), (154, V(0.19, -0.03, 0), 8, NO),
                     (TAXI_EXIT_FRAMES, V(0.19, -0.03, 0), 8, NO)]),
}


def taxi_exit(ctx, frames):
    seated_ref = taxi_ride(ctx, 120)(0)

    def pose(fr):
        p = ctx.stand()
        door = taxi_door_exit(fr, frames)
        if fr <= 56:
            # push the door open, then lift the legs over the sill, turning to face out
            s = ease_in_out(ramp(fr, 32, 56))
            yaw = lerp(-90.0, -180.0, s)
            slide = ease_in_out(ramp(fr, 14, 30))  # over to the door once it is open
            hips = TAXI_SEATED.lerp(TAXI_BY_DOOR, slide).lerp(TAXI_EDGE, s)
            hips.z += 0.03 * math.sin(math.pi * s)
            feet = _swing_feet(s, False) if s > 0 else _seated_feet(hips)
            reach = ramp(fr, 0, 10) * (1 - ramp(fr, 24, 34))
            lean = 14 * smooth(reach)
            duck = 30 * math.sin(math.pi * ramp(fr, 44, 84))
            r = _taxi_body(ctx, p, hips, yaw, feet, pitch=duck * 0.5,
                           torso=-6 * math.sin(math.pi * s) + lean * 0.4 + duck * 0.6, torso_roll=-lean, seated=1.0)
            if duck:
                p.rotate("Head", r @ LEFT, -duck * 0.5)
            _thigh_hands(p, r, math.sin(math.pi * s))
            if reach > 0:
                hand = p.copy()
                _hand_on_door(hand, "R", min(door, 36.0), TAXI_GRIP_IN, inside=True, fingers_local=V(-1, 0, -0.2))
                _blend_arm(p, hand, smooth(reach), "R")
            _look_yawed(p, r, yaw=-25 * smooth(reach), pitch=6 * smooth(reach))
            k = 1 - ramp(fr, 0, 8)
            if k > 0:
                blend_bones(p, seated_ref, smooth(k), p.rig.names)
            return p
        if fr <= 88:
            # hop down onto the kerb, landing with bent knees
            k = 1 - ramp(fr, 60, 76)
            land = math.sin(math.pi * ramp(fr, 70, 88))
            _hop(ctx, p, k, land, math.sin(math.pi * ramp(fr, 44, 84)),
                 hands=ramp(fr, 54, 62) * (1 - ramp(fr, 72, 80)), thighs=1 - ramp(fr, 54, 62))
            return p
        # step out, turn toward the door and pull it shut while backing onto the kerb
        feet, lifts = {}, {}
        for s in "LR":
            a, y, l = EXIT_FEET[s](fr)
            feet[s], lifts[s] = (a, y), l
        hips, yaw = _standing_hips(ctx, feet, lifts)
        r = _taxi_body(ctx, p, hips, yaw, feet)
        breathe(p, fr / 60)
        relaxed_arms(ctx, p)
        reach = ramp(fr, 102, 114) * (1 - ramp(fr, 134, 140))
        if reach > 0:
            hand = p.copy()
            _hand_on_door(hand, "R", max(door, 30.0), TAXI_FRAME_IN, inside=True, fingers_local=V(1, 0, 0.2))
            _blend_arm(p, hand, smooth(reach), "R")
        _look_yawed(p, r, yaw=-15 * smooth(reach), pitch=4 * smooth(reach))
        return p
    return pose


TAXI_TRACKS = {
    "Enter": ("Taxi_Enter", taxi_door_enter),
    "Ride-loop": ("Taxi_Ride-loop", lambda f, frames: 0.0),
    "Exit": ("Taxi_Exit", taxi_door_exit),
}


def taxi_track_for(anim):
    return next((t for t, (a, _) in TAXI_TRACKS.items() if a == anim), None)


# ---------------------------------------------------------------------------
# Walk variants: an in-place walk cycle (like the pack's walks) with its own
# stride, cadence, bounce, posture and arm swing. Move the character at
# walk_speed(name) m/s so the planted foot doesn't slide.

WALK_DUTY = 0.6  # fraction of the cycle each foot is on the ground
FOOT_BALL = 0.11  # ankle to the ball of the foot (heel lift)

WALKS = {
    # cycle: frames per cycle (two steps); cycles: cycles per clip; stride: foot travel (m)
    "Walk_Brisk-loop": dict(cycle=24, cycles=1, stride=0.62, lift=0.12, width=0.155, bounce=0.012, lean=9,
                            arm=34, elbow=72, hip_yaw=7, sway=0.012, head_pitch=-2),
    "Walk_Stroll-loop": dict(cycle=40, cycles=2, stride=0.42, lift=0.08, width=0.175, bounce=0.010, lean=-1,
                             arm=12, elbow=12, hip_yaw=4, sway=0.015, head_pitch=-3, look=(14, 4, 1)),
    "Walk_Tired-loop": dict(cycle=44, cycles=1, stride=0.34, lift=0.045, width=0.19, bounce=0.022, lean=17,
                            arm=7, elbow=6, hip_yaw=3, sway=0.03, head_pitch=22, slump=1.0),
    "Walk_Happy-loop": dict(cycle=26, cycles=2, stride=0.50, lift=0.17, width=0.17, bounce=0.035, lean=-3,
                            arm=36, elbow=34, hip_yaw=6, sway=0.018, head_pitch=-6, bob=7),
    "Walk_Sightseeing-loop": dict(cycle=34, cycles=4, stride=0.46, lift=0.09, width=0.175, bounce=0.012, lean=0,
                                  arm=12, elbow=14, hip_yaw=4, sway=0.015, head_pitch=-3, sightsee=True),
}


def walk_speed(name):
    """Ground speed (m/s) that matches the planted foot of a walk variant."""
    w = WALKS[name]
    return w["stride"] / (WALK_DUTY * w["cycle"] / 30.0)


def _walk_foot(t, w):
    """Ankle offset (y, lift) and foot pitch at foot phase t (0 = heel strike, front)."""
    s = w["stride"]
    if t < WALK_DUTY:  # stance: the foot slides back under the body (the world moves, not the foot)
        k = t / WALK_DUTY
        y = lerp(-s / 2, s / 2, k)
        heel = ramp(k, 0.7, 1.0)  # heel comes up before toe-off
        pitch = -12 * (1 - ramp(k, 0.0, 0.15)) + 32 * heel
        lift = FOOT_BALL * math.sin(math.radians(max(0.0, pitch)))
        return y, lift, pitch
    k = (t - WALK_DUTY) / (1 - WALK_DUTY)  # swing: forward through the air
    y = lerp(s / 2, -s / 2, smooth(k))
    lift = w["lift"] * math.sin(math.pi * k) + FOOT_BALL * math.sin(math.radians(32)) * (1 - ramp(k, 0, 0.3))
    pitch = lerp(32, -12, smooth(ramp(k, 0.0, 0.8)))
    return y, lift, pitch


def walk_variant(name):
    w = WALKS[name]

    def build(ctx, frames):
        cyc = w["cycle"]

        def pose(fr):
            ph = (fr % cyc) / cyc  # cycle phase: left heel strike at 0
            clip = fr / frames      # clip phase (for slow head motion)
            p = ctx.stand()
            # hips: high at mid-stance, low at heel strike, over the planted foot
            up = math.cos(4 * math.pi * (ph - WALK_DUTY / 2))
            slump = w.get("slump", 0.0)
            hips = V(w["sway"] * math.sin(2 * math.pi * (ph - 0.05)), 0.0,
                     ctx.hips_z - 0.022 - 0.02 * slump + w["bounce"] * up)
            p.translate("Hips", hips - p.head("Hips"))
            twist = w["hip_yaw"] * math.sin(2 * math.pi * ph)
            p.rotate("Hips", UP, twist)
            p.rotate("Hips", FWD, -2.0 * math.sin(2 * math.pi * ph) * (1 + slump))
            # torso: lean, counter-twist
            p.rotate("Torso", LEFT, w["lean"] + 2 * slump * up)
            p.rotate("Torso", UP, -twist * 1.6)
            # legs
            for s, off in (("L", 0.0), ("R", 0.5)):
                sx = SIDE[s]
                y, lift, pitch = _walk_foot((ph + off) % 1.0, w)
                ankle = V(w["width"] * sx, y - 0.03, ctx.ankle[s].z + lift)
                p.leg_ik(s, ankle, V(0.1 * sx, -1, 0))
                p.foot_flat(s, rot(UP, 4 * sx) @ FWD, pitch=pitch)
            # arms swing against the legs (right arm forward with the left foot)
            relaxed_arms(ctx, p)
            for s in "LR":
                sx = SIDE[s]
                swing = w["arm"] * math.cos(2 * math.pi * ph) * (-1 if s == "R" else 1)
                p.rotate(f"Arm.{s}", LEFT, swing)
                p.rotate(f"Arm.{s}", FWD, -4 * sx * (1 - slump))  # a little away from the body
                # elbow bends forward (hands come up in front), a little more on the forward swing
                p.rotate(f"ForeArm.{s}", LEFT, -(w["elbow"] + 10 * max(0.0, -swing / max(1, w["arm"]))))
                if slump:
                    p.rotate(f"Shoulder.{s}", LEFT, 8 * slump)
            # head: steady, with the variant's attitude
            yaw = twist * 1.0
            pitch = w["head_pitch"] - w["lean"] * 0.5
            roll = 0.0
            if "bob" in w:  # happy: tilt from side to side with the steps
                roll = w["bob"] * math.sin(2 * math.pi * ph)
            if "look" in w:  # stroll: glance around now and then
                a, b, n = w["look"]
                yaw += a * math.sin(2 * math.pi * clip * n)
                pitch += b * math.sin(4 * math.pi * clip * n)
            if w.get("sightsee"):  # look up at the buildings, left then right
                look_l = math.sin(math.pi * ramp(clip, 0.05, 0.45))
                look_r = math.sin(math.pi * ramp(clip, 0.55, 0.95))
                yaw += 42 * look_l - 42 * look_r
                pitch -= 24 * max(look_l, look_r)
                p.rotate("Torso", UP, 10 * (look_l - look_r))
            look(p, yaw=yaw, pitch=pitch, roll=roll)
            breathe(p, clip * w["cycles"] * (2 if slump else 1), 1.0 + slump)
            return p
        return pose
    return build


# ---------------------------------------------------------------------------
# Water and beach. The surf and canoe clips put the character root on the water
# surface (z = 0); their board / canoe is a root prop that floats (bobs, rolls,
# carves) with its own animation per clip, and the rider follows it rigidly:
# the pose is built in the float's frame and the whole body is then moved with
# it (_on_float). The kite (reel, line, kite) is a root prop too; the fishing
# rod goes on "IteamSlot.R" and its line is a root prop keyed from the rod tip
# (props.LINE_TRACKS, written by the build).


def _float_matrix(z=0.0, pitch=0.0, roll=0.0, yaw=0.0, pivot=V(0, 0, 0)):
    """Float frame: rotate about `pivot` (pitch + = nose (-Y) down, roll + = to the character's right), lift by z."""
    r = (rot(UP, yaw) @ rot(FWD, roll) @ rot(LEFT, pitch)).to_matrix().to_4x4()
    return Matrix.Translation(Vector(pivot) + V(0, 0, z)) @ r @ Matrix.Translation(-Vector(pivot))


def _on_float(p, m):
    """Move the whole body rigidly with a float whose frame is m (the pose was built in float space)."""
    p.set_world("Hips", m @ p.world("Hips"))


def _foot_q(toes, sole_up=False):
    """World rotation for a foot whose toes point along `toes`; sole_up turns it over (lying face down)."""
    t = Vector(toes).normalized()
    up = UP if not sole_up else DOWN
    side = FWD.cross(UP)  # rest foot: toes FWD, top UP
    x = t.cross(up)
    if x.length < 1e-6:
        x = side
    x.normalize()
    z = x.cross(t).normalized()
    return Matrix((x, t, z)).transposed().to_quaternion() @ Matrix((side, FWD, UP)).transposed().to_quaternion().inverted()


# --- Surfing ---------------------------------------------------------------
# Surfboard model: deck-top centre at the origin, nose toward -Y (props.SURFBOARD).

SURF_PADDLE_FRAMES, SURF_SIT_FRAMES, SURF_RIDE_FRAMES = 40, 150, 90
SURF_PRONE_HIPS = V(0, 0.36, 0.31)   # lying on the board: hips (board space)
SURF_SIT_HIPS = V(0, 0.42, 0.07)     # sitting astride, toward the tail
SURF_RIDE = {"front": -0.27, "back": 0.25, "hips_z": 0.31}  # stance along the board, crouched hip height


def surf_board_paddle(f, frames):
    ph = f / frames
    return _float_matrix(z=0.03 + 0.008 * wave(ph * 2), roll=1.5 * math.sin(2 * math.pi * ph), pitch=0.6 * wave(ph * 2, 0.2))


def surf_board_sit(f, frames):
    ph = f / frames
    swell = math.sin(2 * math.pi * ph)
    return _float_matrix(z=0.005 + 0.03 * swell, pitch=-4 + 2.0 * math.sin(2 * math.pi * ph - 0.8),
                         roll=1.5 * math.sin(4 * math.pi * ph + 0.5), pivot=SURF_SIT_HIPS)


def _carve(f, frames):
    return math.sin(2 * math.pi * f / frames)  # one turn each way per loop


def surf_board_ride(f, frames):
    c = _carve(f, frames)
    c2 = math.sin(2 * math.pi * f / frames - 0.5)
    # roll toward the toe side (the character's facing side, root -X) and back
    return _float_matrix(z=0.03, roll=-9 * c, pitch=-2 + 1.5 * c2, yaw=-10 * c2)


SURF_TRACKS = {
    "Paddle-loop": ("Surf_Paddle-loop", surf_board_paddle),
    "Sit-loop": ("Surf_Sit-loop", surf_board_sit),
    "Ride-loop": ("Surf_Ride-loop", surf_board_ride),
}


def _paddle_hand(t, sh, sx):
    """Crawl stroke for one arm. t: stroke phase (0 = hand enters the water ahead). Returns palm, fingers, palm normal."""
    if t < 0.55:  # pull: in ahead, down under the board edge, back to the hip
        k = t / 0.55
        y = lerp(sh.y - 0.42, sh.y + 0.30, smooth(k))
        z = -0.06 - 0.22 * math.sin(math.pi * k)
        x = sh.x + sx * (0.10 + 0.05 * math.sin(math.pi * k))
        return V(x, y, z), DOWN + FWD * (0.6 - 1.2 * k), BACK + DOWN * 0.3
    k = (t - 0.55) / 0.45  # recovery: elbow up, hand forward over the water
    y = lerp(sh.y + 0.30, sh.y - 0.42, smooth(k))
    z = -0.06 + 0.20 * math.sin(math.pi * k)
    x = sh.x + sx * (0.16 + 0.06 * math.sin(math.pi * k))
    return V(x, y, z), FWD + DOWN * 0.4, DOWN + LEFT * sx * 0.4


def _prone(ctx, p, kick=0.0):
    """Lying face down on the board, chest up, looking ahead (board space)."""
    hips_to(ctx, p, SURF_PRONE_HIPS, pitch=90)
    p.rotate("Torso", LEFT, -8)  # chest up off the board
    p.rotate("Head", LEFT, -54)   # look ahead
    for s in "LR":
        sx = SIDE[s]
        k = kick * (1 if s == "L" else -1)
        ankle = V(0.14 * sx, SURF_PRONE_HIPS.y + 0.44, 0.16 + 0.03 * k)
        p.leg_ik(s, ankle, V(0.2 * sx, 0.3, -1))
        p.rest_orient(f"Foot.{s}", _foot_q(BACK + DOWN * 0.15, sole_up=True))
        p.basis[f"Toes.{s}"] = Matrix.Identity(4)


def surf_paddle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _prone(ctx, p, kick=math.sin(4 * math.pi * ph))
        p.rotate("Torso", UP, 6 * math.sin(2 * math.pi * ph))  # shoulders roll with the strokes
        for s, off in (("L", 0.0), ("R", 0.5)):
            sx = SIDE[s]
            sh = p.head(f"Arm.{s}")
            palm, fingers, pn = _paddle_hand((ph + off) % 1.0, sh, sx)
            arm_to(p, s, palm, fingers, pn, V(sx, 0.2, 1.0))
        look(p, yaw=4 * math.sin(2 * math.pi * ph))
        _on_float(p, surf_board_paddle(fr, frames))
        return p
    return pose


def surf_sit(ctx, frames):
    """Sitting astride the board, legs in the water, watching the horizon and looking back for a wave."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        board = surf_board_sit(fr, frames)
        board_pitch = math.degrees(math.asin(clamp((board.to_3x3() @ FWD).z, -1, 1)))  # + = nose up
        hips_to(ctx, p, SURF_SIT_HIPS, pitch=-2 + board_pitch)  # stay upright as the board rocks
        p.rotate("Torso", LEFT, 6)
        breathe(p, ph * 4)
        for s in "LR":
            sx = SIDE[s]
            a = 2 * math.pi * (ph * 3 + (0.0 if s == "L" else 0.5))  # slow sculling circles
            ankle = V(0.38 * sx + 0.03 * math.cos(a), SURF_SIT_HIPS.y - 0.06 + 0.05 * math.sin(a), -0.33)
            p.leg_ik(s, ankle, V(0.6 * sx, -1, 0.2))
            p.foot_flat(s, rot(UP, 10 * sx) @ FWD, pitch=45)
        # look out to sea, then over the right shoulder for the next set, then ahead again
        back = math.sin(math.pi * ramp(ph, 0.45, 0.78))
        p.rotate("Torso", UP, -28 * back)
        for s in "LR":
            sx = SIDE[s]
            hip, knee = p.head(f"UpperLeg.{s}"), p.head(f"Leg.{s}")
            palm = hip.lerp(knee, 0.55) + V(0, 0, 0.08)
            arm_to(p, s, palm, FWD + DOWN * 0.3 + RIGHT * sx * 0.2, DOWN + RIGHT * sx * 0.3, V(0.8 * sx, 1, 0))
        look(p, yaw=14 * wave(ph * 2) * (1 - back) - 62 * back, pitch=-4 + 2 * wave(ph * 3))
        _on_float(p, board)
        return p
    return pose


def surf_ride(ctx, frames):
    """Standing on the board (left foot forward), crouched, arms out, carving one way then the other.

    The board stays pointing along the character's -Y (the direction of travel);
    the body stands across it, chest toward root -X, head turned to the nose."""
    def pose(fr):
        ph = fr / frames
        c = _carve(fr, frames)
        p = ctx.stand()
        z = SURF_RIDE["hips_z"] - 0.035 * (0.5 + 0.5 * c * c) + 0.01 * wave(ph * 4)
        hips_to(ctx, p, V(0.02 * c, (SURF_RIDE["front"] + SURF_RIDE["back"]) / 2, z), yaw=-90, pitch=14)
        p.rotate("Torso", LEFT, 8)  # (the hips are turned: LEFT is now across the board)
        p.rotate("Torso", UP, 26)   # shoulders open toward the nose
        for s, y, toe in (("L", SURF_RIDE["front"], 24), ("R", SURF_RIDE["back"], -6)):
            ankle = V(0.0, y, 0.081)
            fwd = rot(UP, -90 + toe) @ FWD
            knee = rot(UP, -90) @ V(0.0, -1, 0) + V(0, 0.35 * (-1 if s == "L" else 1), 0)
            p.leg_ik(s, ankle, knee)
            p.foot_flat(s, fwd)
        # arms out for balance: front arm toward the nose, back arm low behind
        sh_l, sh_r = p.head("Arm.L"), p.head("Arm.R")
        arm_to(p, "L", sh_l + V(-0.08 + 0.05 * c, -0.50, -0.20 + 0.06 * c), FWD + DOWN * 0.2, DOWN, V(0.3, 0.2, -1))
        arm_to(p, "R", sh_r + V(-0.12 - 0.05 * c, 0.42, -0.34 - 0.05 * c), BACK + DOWN * 0.4, DOWN + LEFT * 0.3,
               V(-0.2, 0.3, -1))
        look(p, yaw=50, pitch=8 - 4 * c)
        _on_float(p, surf_board_ride(fr, frames))
        return p
    return pose


# --- Kite ------------------------------------------------------------------
# Props/Kite.glb at the character root: a two-handled winder in front of the
# chest (both hands on its grips), the line, and a diamond kite high in front.
from props import KITE_REEL  # noqa: E402

KITE_FRAMES = 120
KITE_AT = V(-1.2, -7.5, 8.0)   # kite position (root space) between tugs
TUGS = [(0.10, 1.0), (0.42, 0.6), (0.70, 1.0)]  # (clip phase, strength)


def _tug(ph):
    """0..1 pull: snaps in over 4 frames, eases back over ~18."""
    v = 0.0
    for t0, k in TUGS:
        d = (ph - t0) % 1.0 * KITE_FRAMES
        if d < 4:
            v = max(v, k * smooth(d / 4))
        elif d < 22:
            v = max(v, k * (1 - smooth((d - 4) / 18)))
    return v


def _tug_lift(ph):
    """The kite climbs a little after each tug and settles back."""
    v = 0.0
    for t0, k in TUGS:
        d = (ph - t0) % 1.0 * KITE_FRAMES
        v += k * math.sin(math.pi * clamp(d / 40)) if d < 40 else 0.0
    return v


def kite_reel_matrix(f, frames):
    ph = f / frames
    t = _tug(ph)
    c = V(0.01 * wave(ph * 2), -0.36 + 0.07 * t, 0.90 - 0.06 * t + 0.008 * wave(ph * 3))
    m = rot(LEFT, -18 - 10 * t).to_matrix().to_4x4()  # top tipped toward the kite
    m.translation = c
    return m


def kite_matrix(f, frames):
    """Kite: its face toward the flyer, nose up, swaying in a slow figure of eight."""
    ph = f / frames
    lift = _tug_lift(ph)
    pos = KITE_AT + V(1.1 * math.sin(2 * math.pi * ph), 0.2 * lift, 0.5 * math.sin(4 * math.pi * ph) + 0.45 * lift)
    to_flyer = (V(0, 0, 1.0) - pos).normalized()
    y = (UP - to_flyer * UP.dot(to_flyer)).normalized()
    y = rot(to_flyer, -14 * math.cos(2 * math.pi * ph)) @ y  # bank into the sway
    x = y.cross(to_flyer).normalized()
    m = Matrix((x, y, to_flyer)).transposed().to_4x4()
    m.translation = pos
    return m


def kite_state(f, frames):
    reel = kite_reel_matrix(f, frames)
    kite = kite_matrix(f, frames)
    return {"Kite_Reel": reel, "Kite_Body": kite,
            "Kite_Line": _line_matrix(reel @ KITE_REEL["line_out"], kite @ KITE_REEL["bridle"])}


def _line_matrix(a, b):
    """A unit line along +Y, from a to b (scaled along Y)."""
    d = b - a
    y = d.normalized()
    x = y.orthogonal().normalized()
    z = x.cross(y)
    m = Matrix((x * 1.0, y * d.length, z)).transposed().to_4x4()
    m.translation = a
    return m


KITE_TRACKS = {"Fly-loop": ("Kite_Fly-loop", kite_state)}


def kite_fly(ctx, frames):
    def pose(fr):
        ph = fr / frames
        t = _tug(ph)
        p = ctx.stand()
        hips_to(ctx, p, V(0, 0.02 + 0.015 * t, ctx.hips_z - 0.01 - 0.012 * t), roll=1.2 * wave(ph * 2))
        p.rotate("Torso", LEFT, -6 - 5 * t)  # leaning back against the pull, more on a tug
        breathe(p, ph * 3)
        ankles = {s: ctx.ankle[s] + V(0.03 * SIDE[s], -0.02, 0) for s in "LR"}
        plant_feet(ctx, p, ankles, knee_out=0.3)
        reel = kite_reel_matrix(fr, frames)
        for s in "LR":
            sx = SIDE[s]
            grip = reel @ KITE_REEL["grip"][s]
            inward = reel.to_3x3() @ V(-sx, 0, 0)
            palm = grip - inward * 0.035 + reel.to_3x3() @ V(0, 0.02, 0)
            arm_to(p, s, palm, reel.to_3x3() @ V(0, -1, 0), inward, V(0.9 * sx, 0.3, -0.7))
        kite = kite_matrix(fr, frames).translation
        to = kite - p.head("Head")
        yaw = math.degrees(math.atan2(-to.x, -to.y))
        pitch = -math.degrees(math.atan2(to.z, to.xy.length))
        look(p, yaw=yaw * 0.8, pitch=max(-40.0, pitch * 0.75), torso_share=0.15)
        return p
    return pose


# --- Fishing ---------------------------------------------------------------
# Props/Rod.glb on "IteamSlot.R" (rod along +Y from the fist, reel below it);
# Props/Fishing_Line.glb at the root, keyed per clip from the rod tip to the
# float (props.LINE_TRACKS). Standing at the end of a jetty: the water is
# FISH_WATER_Z below the feet and the float lands FISH_FLOAT ahead.
from props import ROD  # noqa: E402

FISH_IDLE_FRAMES, FISH_CAST_FRAMES = 150, 96
FISH_WATER_Z = -0.7
FISH_FLOAT = V(-0.45, -4.6, FISH_WATER_Z)
ROD_ANGLE = 60.0  # rod angle up from the forearm, toward the thumb


def rod_world(ctx, p):
    """Rod in the fist (reference pose: fist ahead, thumb up): tipped ROD_ANGLE up from the forearm, reel below."""
    fy = p.axis("ForeArm.R", 1)
    a = math.radians(ROD_ANGLE)
    y = (fy * math.cos(a) + UP * math.sin(a)).normalized()
    z = (DOWN - y * DOWN.dot(y)).normalized()
    m = Matrix((y.cross(z), y, z)).transposed().to_4x4()
    m.translation = _fist_centre(p)
    return m


def rod_matrix(ctx, p):
    bone, off = ctx.prop_offset("Rod")
    return p.world(bone) @ off


# right fist keys (torso-relative position, forearm direction, palm normal)
FISH_HOLD = (V(-0.19, -0.36, -0.02), FWD + UP * 0.10, LEFT + UP * 0.1)
FISH_CAST_KEYS = [
    # frame, fist, forearm direction, palm normal
    (0, *FISH_HOLD),
    (12, V(-0.20, -0.32, 0.12), FWD + UP * 0.8, LEFT + UP * 0.1),        # lift: line comes in
    (34, V(-0.36, -0.10, 0.34), UP + BACK * 0.9 + RIGHT * 0.25, LEFT),   # back cast over the right shoulder
    (44, V(-0.36, -0.08, 0.36), UP + BACK * 1.1 + RIGHT * 0.25, LEFT),   # pause: the float swings behind
    (53, V(-0.24, -0.36, 0.24), FWD + UP * 0.9, LEFT + UP * 0.1),        # forward: release
    (60, V(-0.20, -0.42, 0.10), FWD + UP * 0.25, LEFT + UP * 0.1),       # follow through, rod pointing out
    (80, *FISH_HOLD),
    (96, *FISH_HOLD),
]
FISH_RELEASE, FISH_LAND = 53, 76


def _fish_stance(ctx, p, ph, sway=1.0):
    hips_to(ctx, p, V(0, 0, ctx.hips_z - 0.01), yaw=-6, roll=sway * 1.0 * wave(ph))
    p.rotate("Torso", LEFT, 4)
    breathe(p, ph * 4)
    ankles = {"L": ctx.ankle["L"] + V(0.01, -0.06, 0), "R": ctx.ankle["R"] + V(-0.02, 0.05, 0)}
    plant_feet(ctx, p, ankles)
    relaxed_arms(ctx, p)


def _hold_rod(ctx, p, fist, fore, palm, jig=0.0):
    f, d = body_frame(p)
    fore = rot(d(LEFT), -jig) @ d(fore)
    arm_to(p, "R", f(*fist), fore, d(palm), d(V(-0.7, 0.4, -1)), straight=True)
    p.basis["Hand.R"] = Matrix.Identity(4)
    # left hand on the butt of the rod, below and behind the right
    m = rod_matrix(ctx, p)
    rx, ry, rz = (m.to_3x3().col[i].normalized() for i in range(3))
    butt = m @ V(0, ROD["butt"] + 0.05, 0)
    arm_to(p, "L", butt + rz * 0.03, rx + rz * 0.3, -rz, V(0.6, 0.3, -1))


def fish_idle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _fish_stance(ctx, p, ph)
        # a small twitch of the rod tip now and then
        jig = 0.0
        for t0 in (0.30, 0.36, 0.78):
            jig += 7 * math.sin(math.pi * clamp((ph - t0) * frames / 6))
        _hold_rod(ctx, p, *FISH_HOLD, jig=jig)
        look(p, yaw=-4 + 18 * wave(ph, 0.6) * ramp(ph, 0.5, 0.6) * (1 - ramp(ph, 0.9, 1.0)), pitch=10)
        return p
    return pose


def fish_cast(ctx, frames):
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _fish_stance(ctx, p, 0.0, sway=0.0)
        keys = FISH_CAST_KEYS
        fist = keyed(fr, [(k[0], k[1]) for k in keys])
        fore = keyed(fr, [(k[0], k[2].normalized()) for k in keys])
        palm = keyed(fr, [(k[0], k[3].normalized()) for k in keys])
        wind = math.sin(math.pi * ramp(fr, 10, 50))
        p.rotate("Torso", UP, -16 * wind + 8 * math.sin(math.pi * ramp(fr, 46, 70)))
        p.rotate("Torso", LEFT, -5 * wind + 6 * math.sin(math.pi * ramp(fr, 48, 66)))
        _hold_rod(ctx, p, fist, fore, palm)
        watch = ramp(fr, 50, 70)
        look(p, yaw=-4 - 30 * math.sin(math.pi * ramp(fr, 20, 48)) * (1 - watch), pitch=10 * watch - 6 * wind)
        return p
    return pose


def fishing_line_tracks(ctx):
    """Rod tip and float per frame for Fish_Idle and Fish_Cast (root space): {track: [(tip, float), ...]}."""
    out = {}
    for anim, track in (("Fish_Idle-loop", "Idle-loop"), ("Fish_Cast", "Cast")):
        builder, frames, _ = ANIMATIONS[anim]
        fn = builder(ctx, frames)
        tips = [rod_matrix(ctx, fn(f)) @ V(0, ROD["length"], 0) for f in range(frames + 1)]
        floats = []
        if anim == "Fish_Idle-loop":
            for f in range(frames + 1):
                ph = f / frames
                floats.append(FISH_FLOAT + V(0, 0, 0.012 * wave(ph * 5) - 0.02 * max(0.0, wave(ph * 2, 0.3)) ** 8))
        else:
            # reeled in during the lift, swings on a short line through the cast, then flies out to FISH_FLOAT
            hang = 0.45
            pos = vel = None
            g = V(0, 0, -9.8 / 30 ** 2)
            for f, tip in enumerate(tips):
                if f <= 12:
                    k = smooth(f / 12)
                    b = FISH_FLOAT.lerp(tip + V(0, 0, -hang), k) + V(0, 0, 0.6 * math.sin(math.pi * k))
                    if f == 12:
                        pos, vel = b.copy(), V(0, 0, 0)
                elif f <= FISH_RELEASE:
                    new = pos + vel * 0.96 + g  # pendulum on a short line from the tip
                    d = new - tip
                    new = tip + d.normalized() * hang
                    vel, pos = new - pos, new
                    b = pos.copy()
                elif f <= FISH_LAND:
                    k = (f - FISH_RELEASE) / (FISH_LAND - FISH_RELEASE)
                    a = pos.lerp(FISH_FLOAT, k)
                    b = a + V(0, 0, 1.6 * math.sin(math.pi * k) * (1 - 0.3 * k))
                else:
                    k = (f - FISH_LAND) / (frames - FISH_LAND)
                    b = FISH_FLOAT + V(0, 0, -0.05 * math.sin(math.pi * min(1.0, k * 3)) * (1 - k))
                floats.append(b)
        out[track] = [(list(t), list(b)) for t, b in zip(tips, floats)]
    return out


# --- Canoe -----------------------------------------------------------------
# Props/Canoe.glb at the character root: the canoe (floating, rocking with the
# strokes) and the double-bladed paddle, keyed per clip; the hands hold the
# paddle. Canoe space: waterline centre under the seat, bow toward -Y.
from props import CANOE  # noqa: E402

CANOE_PADDLE_FRAMES, CANOE_REST_FRAMES = 56, 150
CANOE_HIPS = V(0, 0.04, CANOE["seat_z"] + 0.03)


def _stroke(ph):
    """Paddle shaft yaw/elevation over one cycle: right stroke, switch, left stroke, switch."""
    keys_a = [(0.0, 34.0), (0.38, -22.0), (0.50, -34.0), (0.88, 22.0), (1.0, 34.0)]
    keys_e = [(0.0, 38.0), (0.36, 38.0), (0.50, -38.0), (0.86, -38.0), (1.0, 38.0)]
    return keyed(ph, keys_a), keyed(ph, keys_e)


def canoe_paddle_matrix(yaw, elev, centre, roll=0.0):
    """Paddle frame: +Y along the shaft toward the left blade, blade faces along local X (roll turns the blades)."""
    d = rot(UP, yaw) @ (rot(BACK, -elev) @ LEFT)  # elev + = left end up
    x = rot(d, roll) @ (BACK - d * BACK.dot(d)).normalized()
    m = Matrix((x, d, x.cross(d))).transposed().to_4x4()
    m.translation = centre
    return m


def _canoe_float(ph, strokes=1.0):
    s = math.sin(4 * math.pi * ph)
    return _float_matrix(z=0.006 * math.cos(4 * math.pi * ph) * strokes + 0.01 * wave(ph * 2) * (1 - strokes),
                         roll=1.8 * s * strokes + 1.2 * wave(ph * 2) * (1 - strokes), yaw=1.5 * s * strokes,
                         pivot=CANOE_HIPS)


def canoe_paddle_state(f, frames):
    ph = f / frames
    yaw, elev = _stroke(ph)
    pull = math.sin(math.pi * ((ph % 0.5) / 0.5))
    centre = CANOE_HIPS + V(0, -0.34 - 0.05 * pull, 0.50 - 0.03 * pull)
    m = _canoe_float(ph)
    return {"Canoe_Float": m, "Canoe_Paddle": m @ canoe_paddle_matrix(yaw, elev, centre)}


CANOE_REST_PADDLE = (0.0, 0.0, CANOE_HIPS + V(0, -0.27, 0.26), 90.0)  # blades flat


def canoe_rest_state(f, frames):
    m = _canoe_float(f / frames, strokes=0.0)
    return {"Canoe_Float": m, "Canoe_Paddle": m @ canoe_paddle_matrix(*CANOE_REST_PADDLE)}


CANOE_TRACKS = {
    "Paddle-loop": ("Canoe_Paddle-loop", canoe_paddle_state),
    "Rest-loop": ("Canoe_Rest-loop", canoe_rest_state),
}


def _canoe_seated(ctx, p, lean=0.0):
    hips_to(ctx, p, CANOE_HIPS, pitch=-8 + lean)
    p.rotate("Torso", LEFT, 10 + lean)
    p.rotate("Head", LEFT, -6 - lean)
    for s in "LR":
        sx = SIDE[s]
        ankle = V(0.12 * sx, CANOE_HIPS.y - 0.47, CANOE["floor_z"] + 0.10)
        p.leg_ik(s, ankle, V(0.5 * sx, -0.5, 1.0))
        p.foot_flat(s, rot(UP, 10 * sx) @ FWD, pitch=-28)


def _grip_paddle(p, pm, sep=0.30):
    """Both hands on the shaft (overhand), `sep` either side of the paddle centre."""
    px, py, pz = (pm.to_3x3().col[i].normalized() for i in range(3))
    for s, k in (("R", -1), ("L", 1)):
        sx = SIDE[s]
        grip = pm.translation + py * (sep * k)
        top = pz if pz.z > 0 else -pz  # hand on top of the shaft
        back = px if px.y > 0 else -px
        arm_to(p, s, grip + top * 0.035 + back * 0.01, -back + DOWN * 0.3, -top, V(0.9 * sx, 0.4, -0.6))


def canoe_paddle(ctx, frames):
    def pose(fr):
        ph = fr / frames
        yaw, elev = _stroke(ph)
        p = ctx.stand()
        _canoe_seated(ctx, p, lean=4)
        p.rotate("Torso", UP, yaw * 0.55)   # wind up: shoulders turn with the shaft
        p.rotate("Torso", FWD, -elev * 0.10)
        st = canoe_paddle_state(fr, frames)
        m = st["Canoe_Float"]
        _grip_paddle(p, m.inverted() @ st["Canoe_Paddle"])  # built in canoe space
        look(p, yaw=-yaw * 0.4, pitch=-2)
        _on_float(p, m)
        return p
    return pose


def canoe_rest(ctx, frames):
    """Coasting: paddle across the knees, looking around."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        _canoe_seated(ctx, p, lean=0)
        breathe(p, ph * 4)
        st = canoe_rest_state(fr, frames)
        m = st["Canoe_Float"]
        _grip_paddle(p, m.inverted() @ st["Canoe_Paddle"], sep=0.22)
        look(p, yaw=34 * wave(ph), pitch=-4 + 4 * wave(ph * 2, 0.3), torso_share=0.2)
        _on_float(p, m)
        return p
    return pose


# ---------------------------------------------------------------------------
# Ice skating. The ice is at the character root (z = 0). Each shoe wears a
# strap-on blade (Props/Skate_L.glb on "Foot.L", Skate_R.glb on "Foot.R");
# _skate_foot puts the blade's lowest point on the ice. All the clips except
# the spin are built from one parameter set (_skate_body), so Skate_Stop can
# blend from the Skate_Glide pose to the Skate_Idle pose. The clips skate in
# place: move the character yourself (Stride about 2.5 m/s, Glide coasting
# and slowing, Stop slowing to a halt over its first second).
from props import SKATE, SKATE_PROFILE, blade_bottom  # noqa: E402

SKATE_REST = {s: V(0.172 * SIDE[s], SKATE["centre_y"], 0.0) for s in "LR"}  # skate origin on the rest pose


def _ref_rest(ctx):
    return ctx.rig.pose()


def skate_world(side):
    return lambda ctx, p: Matrix.Translation(SKATE_REST[side])


def _skate_foot(ctx, p, s, x, y, toe_out=0.0, edge=0.0, pitch=0.0, lift=0.0, knee=0.25):
    """Put a skate's blade (its middle) at (x, y), its lowest point `lift` above the ice.

    toe_out: degrees the toes turn out; edge: degrees rolled onto the inside
    edge (- = outside edge); pitch: + = toes down."""
    sx = SIDE[s]
    q = rot(UP, toe_out * sx) @ rot(FWD, edge * sx) @ rot(LEFT, pitch)
    a_rest = ctx.rig.rest[f"Foot.{s}"].translation

    def rel(yy, zz):  # skate-local point relative to the ankle, posed
        return q @ (SKATE_REST[s] + V(0, yy, zz) - a_rest)

    low = min(rel(yy, zz).z for yy, zz in SKATE_PROFILE)
    c = rel(0.0, blade_bottom(0.0))
    p.leg_ik(s, V(x * sx - c.x, y - c.y, lift - low), V(knee * sx, -1, 0))
    p.rest_orient(f"Foot.{s}", q)
    p.basis[f"Toes.{s}"] = Matrix.Identity(4)


def _mix(a, b, t):
    """Blend two parameter sets (dicts of floats / tuples)."""
    if isinstance(a, dict):
        return {k: _mix(a[k], b[k], t) for k in a}
    if isinstance(a, tuple):
        return tuple(_mix(u, v, t) for u, v in zip(a, b))
    return a + (b - a) * t


def _keyed_mix(t, keys):
    """Smooth interpolation through [(t, params), ...]."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t <= t1:
            return _mix(a, b, smooth((t - t0) / (t1 - t0)))
    return keys[-1][1]


def _add(k, **d):
    """Copy of params k with offsets added (feet / arms: per-side tuples of offsets)."""
    out = dict(k)
    for key, v in d.items():
        out[key] = tuple(a + b for a, b in zip(k[key], v)) if isinstance(v, tuple) else k[key] + v
    return out


# Feet: (x out from the centre, y, toe_out, edge, pitch, lift, knee out); arms: (out, forward, elbow) in degrees.
SKATE_IDLE = dict(hx=0.0, hy=0.01, hz=-0.035, pitch=5.0, roll=0.0, yaw=0.0, torso=3.0, torso_yaw=0.0,
                  head_yaw=0.0, head_pitch=-5.0,
                  L=(0.15, 0.0, 9.0, 0.0, 0.0, 0.0, 0.35), R=(0.15, 0.0, 9.0, 0.0, 0.0, 0.0, 0.35),
                  aL=(14.0, 4.0, 18.0), aR=(14.0, 4.0, 18.0))
SKATE_GLIDE = dict(hx=0.0, hy=0.02, hz=-0.075, pitch=12.0, roll=0.0, yaw=0.0, torso=8.0, torso_yaw=0.0,
                   head_yaw=0.0, head_pitch=-16.0,
                   L=(0.12, -0.08, 3.0, 0.0, 0.0, 0.0, 0.25), R=(0.12, 0.06, 3.0, 0.0, 0.0, 0.0, 0.25),
                   aL=(30.0, 10.0, 24.0), aR=(30.0, 10.0, 24.0))
SKATE_PLOW = dict(hx=0.0, hy=0.07, hz=-0.12, pitch=16.0, roll=0.0, yaw=0.0, torso=12.0, torso_yaw=0.0,
                  head_yaw=0.0, head_pitch=-14.0,
                  L=(0.25, -0.02, -24.0, 20.0, 0.0, 0.0, -0.15), R=(0.25, -0.02, -24.0, 20.0, 0.0, 0.0, -0.15),
                  aL=(22.0, 38.0, 34.0), aR=(22.0, 38.0, 34.0))


def _skate_body(ctx, p, k):
    """Pose from a parameter set: hips, torso, skates on the ice, arms, head."""
    hips_to(ctx, p, V(k["hx"], k["hy"], ctx.hips_z + SKATE["lift"] + k["hz"]), pitch=k["pitch"], yaw=k["yaw"],
            roll=k["roll"])
    p.rotate("Torso", LEFT, k["torso"])
    p.rotate("Torso", UP, k["torso_yaw"])
    for s in "LR":
        _skate_foot(ctx, p, s, *k[s])
    relaxed_arms(ctx, p)
    for s in "LR":
        out, fwd, elbow = k["a" + s]
        p.rotate(f"Arm.{s}", FWD, out * SIDE[s])  # (a hanging arm: + FWD swings the left hand out)
        p.rotate(f"Arm.{s}", LEFT, -fwd)
        p.rotate(f"ForeArm.{s}", LEFT, -elbow)
    look(p, yaw=k["head_yaw"], pitch=k["head_pitch"])


def skate_idle(ctx, frames):
    """Standing on skates, knees soft, weight drifting from foot to foot; once a loop
    the skates slip a little and the arms come out to catch the balance."""
    def pose(fr):
        ph = fr / frames
        sw = math.sin(2 * math.pi * ph)
        c = math.sin(math.pi * ramp(ph, 0.55, 0.78)) ** 2  # balance catch
        slip = math.sin(math.pi * ramp(ph, 0.55, 0.70))
        k = _add(SKATE_IDLE, hx=0.022 * sw, hz=-0.02 * c, pitch=-6 * c, roll=-2.5 * sw, torso=5 * c,
                 head_yaw=10 * math.sin(4 * math.pi * ph) * (1 - c), head_pitch=8 * c,
                 L=(0, -0.05 * slip, 0, 4 * c, 0, 0, 0), R=(0, 0.04 * slip, 0, 4 * c, 0, 0, 0),
                 aL=(26 * c, 8 * c, 12 * c), aR=(30 * c, -4 * c, 14 * c))
        p = ctx.stand()
        _skate_body(ctx, p, k)
        breathe(p, ph * 2)
        return p
    return pose


def skate_glide(ctx, frames):
    """Coasting on both skates, arms out a little, leaning into a gentle curve one way then the other."""
    def pose(fr):
        ph = fr / frames
        e = math.sin(2 * math.pi * ph)  # + = curving to the left
        k = _add(SKATE_GLIDE, hx=0.03 * e, roll=-5 * e, torso_yaw=6 * e, head_yaw=22 * math.sin(2 * math.pi * ph),
                 L=(0, 0, 4 * e, -7 * e, 0, 0, 0), R=(0, 0, -4 * e, 7 * e, 0, 0, 0),
                 aL=(-6 * e, 0, 0), aR=(6 * e, 0, 0))
        p = ctx.stand()
        _skate_body(ctx, p, k)
        breathe(p, ph * 3)
        return p
    return pose


def _stride_foot(t):
    """One skate through a stride, foot phase t: glide under the body, push out and back, lift, bring it back."""
    if t < 0.35:  # glide
        u = t / 0.35
        return (lerp(0.06, 0.10, u), lerp(-0.06, 0.0, u), 6.0, lerp(-4.0, 0.0, u), 0.0, 0.0, 0.2)
    if t < 0.6:  # push: out to the side and back, onto the inside edge
        u = smooth((t - 0.35) / 0.25)
        return (lerp(0.10, 0.36, u), lerp(0.0, 0.16, u), lerp(6, 38, u), lerp(0, 24, u), 0.0, 0.0, lerp(0.2, 0.7, u))
    if t < 0.7:  # off the ice, toe down
        u = smooth((t - 0.6) / 0.1)
        return (lerp(0.36, 0.33, u), lerp(0.16, 0.18, u), 38.0, lerp(24, 18, u), 25 * u, 0.05 * u, 0.7)
    u = smooth((t - 0.7) / 0.3)  # recovery: back in under the body and set down
    return (lerp(0.33, 0.06, u), lerp(0.18, -0.06, u), lerp(38, 6, u), lerp(18, -4, u), 25 * (1 - u),
            0.05 * (1 - u) + 0.04 * math.sin(math.pi * u), lerp(0.7, 0.2, u))


def skate_stride(ctx, frames):
    """Skating forward: a push to the side with one skate and then the other, the body low and
    leaning, over the gliding skate, the arms swinging with the pushes."""
    def pose(fr):
        ph = fr / frames
        over = math.cos(2 * math.pi * (ph - 0.2))  # + = over the left skate
        swing = math.cos(2 * math.pi * (ph - 0.5))  # + = left arm forward (left leg pushing)
        k = dict(SKATE_IDLE, hx=0.07 * over, hy=0.03, hz=-0.10 + 0.012 * math.cos(4 * math.pi * (ph - 0.1)),
                 pitch=22.0, roll=-3 * over, yaw=6 * over, torso=12.0, torso_yaw=-8 * over,
                 head_yaw=-4 * over, head_pitch=-30.0,
                 L=_stride_foot(ph), R=_stride_foot((ph + 0.5) % 1.0),
                 aL=(12.0, 38 * swing, 22 + 14 * max(0.0, swing)), aR=(12.0, -38 * swing, 22 + 14 * max(0.0, -swing)))
        p = ctx.stand()
        _skate_body(ctx, p, k)
        return p
    return pose


SPIN_TURNS = 2  # turns per loop


def skate_spin(ctx, frames):
    """A scratch spin on the left skate: the right leg crossed in front, hands together at the chest.
    The whole body turns about the left skate, counter-clockwise seen from above."""
    def pose(fr):
        ph = fr / frames
        p = ctx.stand()
        k = dict(SKATE_IDLE, hx=0.0, hy=0.0, hz=-0.02, pitch=2.0, roll=0.0, torso=-2.0, head_pitch=-8.0,
                 L=(0.0, 0.0, 0.0, 0.0, 6.0, 0.0, 0.2), R=(0.0, -0.15, -12.0, 0.0, 50.0, 0.06, 0.4))
        _skate_body(ctx, p, k)
        f, d = body_frame(p, "Torso")
        for s in "LR":
            sx = SIDE[s]
            arm_to(p, s, f(0.035 * sx, -0.21, 0.24), d(UP + FWD * 0.4 + RIGHT * sx * 0.2), d(RIGHT * sx),
                   d(V(sx, 0.3, -0.6)))
        look(p, pitch=-4 + 3 * math.sin(2 * math.pi * ph))
        _on_float(p, Matrix.Rotation(math.radians(360 * SPIN_TURNS * ph), 4, "Z"))
        return p
    return pose


def skate_wobble(ctx, frames):
    """A beginner losing balance: knock-kneed, the skates sliding forward and back,
    the body tipping, the arms windmilling."""
    def pose(fr):
        ph = fr / frames
        sc = math.sin(2 * math.pi * ph)        # scissor: + = left skate forward
        tip = math.sin(4 * math.pi * ph + 0.6)  # + = tipping forward
        k = dict(SKATE_IDLE, hx=0.03 * math.sin(2 * math.pi * ph + 1.2), hy=0.02, hz=-0.09 + 0.015 * tip,
                 pitch=14 + 12 * tip, roll=4 * sc, yaw=-8 * sc, torso=6 - 10 * tip, torso_yaw=6 * sc,
                 head_yaw=-6 * sc, head_pitch=18 - 14 * tip,
                 L=(0.15, -0.11 * sc, 6 - 12 * sc, 12 + 4 * tip, 0.0, 0.0, -0.35),
                 R=(0.15, 0.11 * sc, 6 + 12 * sc, 12 - 4 * tip, 0.0, 0.0, -0.35))
        turn = 360 * 3 * ph  # windmill: three forward circles per loop, the arms half a turn apart
        k["aL"] = (38.0 + 8 * math.sin(math.radians(turn)), turn, 16.0)
        k["aR"] = (38.0 - 8 * math.sin(math.radians(turn)), turn + 180, 16.0)
        p = ctx.stand()
        _skate_body(ctx, p, k)
        return p
    return pose


SKATE_STOP_FRAMES = 60
SKATE_STOP_KEYS = [(0, SKATE_GLIDE), (6, SKATE_GLIDE), (18, SKATE_PLOW), (36, SKATE_PLOW), (56, SKATE_IDLE),
                   (60, SKATE_IDLE)]


def skate_stop(ctx, frames):
    """A snowplough stop: from the Skate_Glide pose the heels push out and the toes turn in, the
    knees bend and the skates scrape to a halt, then up into the Skate_Idle pose."""
    def pose(fr):
        k = dict(_keyed_mix(fr, SKATE_STOP_KEYS))
        scrape = math.sin(math.pi * ramp(fr, 14, 38))
        k["hz"] += 0.005 * math.sin(fr * 2.3) * scrape  # judder while the blades scrape
        k["head_pitch"] += 4 * math.sin(fr * 1.7) * scrape
        p = ctx.stand()
        _skate_body(ctx, p, k)
        return p
    return pose


# Poses used only as references for prop offsets
REF_POSES = {"_ref_grip": _ref_grip, "_ref_book": _ref_book, "_ref_rest": _ref_rest}


# Reference frames used to derive prop attachment offsets: name -> (bone, anim, frame, fn)
PROP_REFS = {
    "Cello_Carried": ("IteamSlot.R", "Cello_Carry_Idle-loop", 0, cello_carry_world),
    "Cello_Played": (None, None, 0, cello_play_matrix),
    "Bow": ("IteamSlot.R", "_ref_grip", 0, bow_world),
    "Box": ("Torso", "Carry_Box_Idle-loop", 0, box_world),
    "Umbrella": ("IteamSlot.R", "Umbrella_Idle-loop", 0, umbrella_world),
    "Phone": ("IteamSlot.R", "Phone_Idle-loop", 0, phone_world),
    "Radio_Mic": ("Torso", "Police_Radio-loop", 0, radio_mic_world),
    "TicketBook": ("IteamSlot.L", "_ref_book", 0, ticket_book_world),
    "Pen": ("IteamSlot.R", "_ref_grip", 0, pen_world),
    "Rod": ("IteamSlot.R", "_ref_grip", 0, rod_world),
    "Skate_L": ("Foot.L", "_ref_rest", 0, skate_world("L")),
    "Skate_R": ("Foot.R", "_ref_rest", 0, skate_world("R")),
}


ANIMATIONS = {
    "Wave_A": (wave_once, 66, False),
    "Wave_B-loop": (wave_loop, 32, True),
    "Sit_Chair_Down": (sit_chair_down, 36, False),
    "Sit_Chair_Idle-loop": (sit_chair_idle, 90, True),
    "Sit_Chair_StandUp": (sit_chair_up, 36, False),
    "Carry_Box_Idle-loop": (carry_idle, 60, True),
    "Carry_Box_Walk-loop": (carry_walk, 31, True),
    "Umbrella_Idle-loop": (umbrella_idle, 90, True),
    "Umbrella_Walk-loop": (umbrella_walk, 31, True),
    "Phone_Idle-loop": (phone_idle, 90, True),
    "Phone_Walk-loop": (phone_walk, 31, True),
    "Phone_Talk-loop": (phone_talk, 120, True),
    "LookUp_A-loop": (look_up_a, 120, True),
    "LookUp_B-loop": (look_up_b, 90, True),
    "Fan_Hot-loop": (fan_hot, 64, True),
    "Shiver_Cold-loop": (shiver_cold, 60, True),
    "Cycling-loop": (cycling, 24, True),
    "Cycling_Coast-loop": (cycling_coast, 60, True),
    "Cycling_Stop": (cycling_stop, STOP_FRAMES, False),
    "Cycling_Rest-loop": (cycling_rest, 90, True),
    "Cycling_Start": (cycling_start, START_FRAMES, False),
    "Cycling_Signal_Left": (cycling_signal("Left"), SIGNAL_FRAMES, False),
    "Cycling_Signal_Right": (cycling_signal("Right"), SIGNAL_FRAMES, False),
    "Cycling_Signal_Stop": (cycling_signal("Stop"), SIGNAL_FRAMES, False),
    "Sit_Floor_Down": (sit_floor_down, 54, False),
    "Sit_Floor_Idle-loop": (sit_floor_idle, 120, True),
    "Sit_Floor_StandUp": (sit_floor_up, 54, False),
    "Sit_Chair_Talk-loop": (sit_chair_talk, 120, True),
    "Clap-loop": (clap, 36, True),
    "Cheer-loop": (cheer, 32, True),
    "Talk-loop": (talk, 120, True),
    "Point_A": (point, 60, False),
    "Nod_Yes": (nod_yes, 36, False),
    "Shake_No": (shake_no, 40, False),
    "Shrug": (shrug, 40, False),
    "Dance_A-loop": (dance, 32, True),
    "Wait_HandsBehind-loop": (wait_hands_behind, 120, True),
    "Cello_Carry_Idle-loop": (cello_carry_idle, 90, True),
    "Cello_Carry_Walk-loop": (cello_carry_walk, 31, True),
    "Cello_Play-loop": (cello_play, 96, True),
    "Cello_Rest-loop": (cello_rest, 90, True),
    "Police_Radio-loop": (police_radio, 120, True),
    "Police_Ticket-loop": (police_ticket, 180, True),
    "Taxi_Hail-loop": (taxi_hail, 90, True),
    "Taxi_Enter": (taxi_enter, TAXI_ENTER_FRAMES, False),
    "Taxi_Ride-loop": (taxi_ride, 120, True),
    "Taxi_Exit": (taxi_exit, TAXI_EXIT_FRAMES, False),
    "Surf_Paddle-loop": (surf_paddle, SURF_PADDLE_FRAMES, True),
    "Surf_Sit-loop": (surf_sit, SURF_SIT_FRAMES, True),
    "Surf_Ride-loop": (surf_ride, SURF_RIDE_FRAMES, True),
    "Kite_Fly-loop": (kite_fly, KITE_FRAMES, True),
    "Fish_Idle-loop": (fish_idle, FISH_IDLE_FRAMES, True),
    "Fish_Cast": (fish_cast, FISH_CAST_FRAMES, False),
    "Canoe_Paddle-loop": (canoe_paddle, CANOE_PADDLE_FRAMES, True),
    "Canoe_Rest-loop": (canoe_rest, CANOE_REST_FRAMES, True),
    "Skate_Idle-loop": (skate_idle, 120, True),
    "Skate_Stride-loop": (skate_stride, 40, True),
    "Skate_Glide-loop": (skate_glide, 120, True),
    "Skate_Spin-loop": (skate_spin, 40, True),
    "Skate_Wobble-loop": (skate_wobble, 72, True),
    "Skate_Stop": (skate_stop, SKATE_STOP_FRAMES, False),
    **{name: (walk_variant(name), w["cycle"] * w["cycles"], True) for name, w in WALKS.items()},
}
