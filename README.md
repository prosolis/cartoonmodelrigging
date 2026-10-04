# Cartoon model rigging

`Characters_1_Godot/` is the Characters_1 pack for Godot 4 (308 characters sharing one
21-bone skeleton). The original zip is kept as `Characters_1_Godot.zip`.

This repo adds **35 new animations** to the pack's shared animation library, plus a few
props that go with them. All 47 original animations are untouched.

![New animations](Characters_1_Godot/Preview_NewAnimations.png)

## New animations

All of them are in `Characters_1_Godot/Animations/Characters_1_Animations.glb`, so they work on
every character, the same way the original ones do. Names ending in `-loop` loop; Godot drops
that suffix on import (`Wave_B-loop` shows up as `Wave_B`).

| Group | Animations (Godot name) | Prop |
|---|---|---|
| Wave | `Wave_A` (raise, wave 3x, lower), `Wave_B` (loop) | |
| Sit on a chair | `Sit_Chair_Down`, `Sit_Chair_Idle`, `Sit_Chair_Talk`, `Sit_Chair_StandUp` | your own chair |
| Sit on the ground | `Sit_Floor_Down`, `Sit_Floor_Idle`, `Sit_Floor_StandUp` | |
| Carry a box | `Carry_Box_Idle`, `Carry_Box_Walk` | `Props/Box.glb` on `Torso` |
| Umbrella | `Umbrella_Idle`, `Umbrella_Walk` | `Props/Umbrella.glb` on `IteamSlot.R` |
| Look up | `LookUp_A` (scanning the sky), `LookUp_B` (shading the eyes) | |
| Phone | `Phone_Walk` (walking, looking down), `Phone_Idle` (texting), `Phone_Talk` (call at the ear) | `Props/Phone.glb` on `IteamSlot.R` |
| Hot | `Fan_Hot` (fanning the face, hand on hip) | |
| Cold | `Shiver_Cold` (hunched, shaking, rubbing hands) | |
| Cycling | `Cycling` (pedalling), `Cycling_Coast` | `Props/Bicycle.glb` at the character root |
| Cello | `Cello_Carry_Idle`, `Cello_Carry_Walk` (holding it by the neck), `Cello_Play` (seated, bowing), `Cello_Rest` (seated, bow down, swaying) | carrying: `Props/Cello_Carried.glb` on `IteamSlot.R`; playing: `Props/Cello_Played.glb` + `Props/Chair.glb` at the root, `Props/Bow.glb` on `IteamSlot.R` |
| Extras | `Talk`, `Clap`, `Cheer`, `Point_A`, `Nod_Yes`, `Shake_No`, `Shrug`, `Dance_A`, `Wait_HandsBehind` | |

The walking variants are built on the pack's `Walk_C` cycle (1.03 s, in place), so they line up
with it.

## Using the props in Godot

Bone props have their offset baked into the file, so you attach them with an identity transform:

```gdscript
var att := BoneAttachment3D.new()
att.bone_name = "IteamSlot.R"            # "Torso" for the box
skeleton.add_child(att)                    # the character's Armature/Skeleton3D
att.add_child(preload("res://Characters_1_Godot/Props/Umbrella.glb").instantiate())
```

Root props (`Bicycle`, `Chair`, `Cello_Played`) are added as children of the character root
with an identity transform; their placement is baked in too.

The bicycle is a child of the character root (not a bone). Its own `AnimationPlayer` has
`Pedal` (0.8 s, matches `Cycling`) and `Coast` (2 s, matches `Cycling_Coast`). Start it together
with the rider so the cranks line up with the feet.

`Test/AnimationTest.tscn` attaches all of this automatically: pick an animation and the matching
prop appears.

**Cello:** there is deliberately no animation between carrying and playing. Cut from
`Cello_Carry_*` to `Cello_Play`/`Cello_Rest` behind a visual transition (a puff, fade or similar),
swapping `Cello_Carried.glb` for `Cello_Played.glb` + `Bow.glb` + a chair at the same moment. The
played cello stands at a fixed spot in front of the seated character, so it lines up with the chair
pose (and `Props/Chair.glb`) as long as the character's root stays put. It is held the way a cellist
holds it: the back of the upper bout against the chest (below the shoulders), the lower bout
between the knees, the endpin forward on the floor, and the left hand on the neck at about
shoulder height.

**Chair:** sitting moves the hips back about 0.17 from the character's root, and the seat surface
is at a height of about 0.27. Put the character's root at the front edge of the chair, facing away
from it.

**Notes / limits**
- The hands are mittens and the rig has no finger or face bones, so the poses are stylised.
- Props sit at a placement that clears the heads of all characters (the umbrella shaft passes at
  least 6 cm and the cello neck at least 4.8 cm from every head; checked with
  `tools/check_clearance.py`). Because the heads are big, the cello is scaled up (it reads as a
  viola at "true" size next to them) and the played cello leans further to the player's left
  than a real one would, so its neck passes beside the head instead of through it. The phone at the ear is set
  for a typical head width, so on the two widest heads (Character_3 and Character_4) it can
  overlap the hair a little.

## Model fixes

- `Character_2_2_5` and `Character_2_2_11` shipped with their head and hair skinned 50/50 to the
  Hips and Head bones, so the head lagged behind in every animation. Their skin weights now match
  `Character_2_2_1` (same mesh): fully on Head. Nothing else in those files changed.

## Santa hats

![Santa hats](Characters_1_Godot/Preview_SantaHats.png)

`Props/SantaHat.glb` is a Santa hat with a floppy tip that bounces (Godot's
`SpringBoneSimulator3D`). `Props/santa_hat.gd` (`class_name SantaHat`) puts it on a character:

```gdscript
SantaHat.apply(character)          # when spawning: hat on during the holiday season, off otherwise
SantaHat.mode = SantaHat.Mode.ON   # or OFF, or AUTO (the default: Dec 1 - Jan 6)
SantaHat.update_all(get_tree())    # re-apply to every character passed to apply()
SantaHat.put_on(character)         # or control it directly
SantaHat.take_off(character)
```

`character` is an instance of one of the character `.glb` scenes. Each model has its own fit
(size and position on its head and hair) in `Props/santa_hat_fits.gd`, which is generated
for every model, so it just works on all of them. The season dates (`season_start`/`season_end`) and
the tip's springiness (`tip_stiffness`, `tip_drag`, `tip_gravity`) are static vars you can change.

- **Who gets one:** 256 of the 308 models. The police officers, the hard hats (`Character_B_*`), and
  `Character_7` (who already wears a hat) are skipped, and so are the zombies (`Character_Z_*`),
  which are kept for Halloween. `SantaHat.can_wear(character)` tells you.
- **Umbrella:** the umbrella goes through the hat, so hide the hat while an `Umbrella_*` clip
  plays: `SantaHat.set_hidden(character, SantaHat.clashes_with(anim))`. The test scene does this.
- The test scene has a **Santa hats** switch.

## Regenerating / tweaking

The animations are procedural Python (Blender's `bpy` module), so they can be tuned and rebuilt:

```sh
python3.11 -m venv .venv && .venv/bin/pip install bpy==4.2.* numpy pillow
.venv/bin/python tools/build_animations.py              # rebuild all new animations + props
.venv/bin/python tools/build_animations.py Wave_A       # rebuild just one
.venv/bin/python tools/preview.py out/ Wave_A --props   # render a contact sheet to check it
.venv/bin/python tools/santa_hat.py                     # rebuild the Santa hat + per-model fits
.venv/bin/python tools/santa_hat.py --check out/ Character_6_1_1   # render fitted hats
```

- `tools/animations.py`: the animation definitions (poses as functions of time)
- `tools/rigkit.py`: posing helpers (FK, two-bone IK, keyframe baking)
- `tools/props.py`: prop geometry and export
- `tools/merge_glb.py`: appends animations to the library without touching the originals
- `tools/check_clearance.py`: umbrella / cello neck vs head clearance check across characters
- `tools/santa_hat.py`: Santa hat model, per-model fitting (`OVERRIDES` for hand tweaks, skip list)
