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


def bike_track_for(anim):
    return next((t for t, (a, _) in BIKE_TRACKS.items() if a == anim), None)


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


# Poses used only as references for prop offsets
REF_POSES = {"_ref_grip": _ref_grip}


# Reference frames used to derive prop attachment offsets: name -> (bone, anim, frame, fn)
PROP_REFS = {
    "Cello_Carried": ("IteamSlot.R", "Cello_Carry_Idle-loop", 0, cello_carry_world),
    "Cello_Played": (None, None, 0, cello_play_matrix),
    "Bow": ("IteamSlot.R", "_ref_grip", 0, bow_world),
    "Box": ("Torso", "Carry_Box_Idle-loop", 0, box_world),
    "Umbrella": ("IteamSlot.R", "Umbrella_Idle-loop", 0, umbrella_world),
    "Phone": ("IteamSlot.R", "Phone_Idle-loop", 0, phone_world),
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
}
