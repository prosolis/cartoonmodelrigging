extends Node3D

# Test scene for the pack: pick a character or a crowd, then pick an animation.
# Every character uses the same AnimationLibrary (Characters_1_Animations.glb).

const CROWD_SIZE := 12
const Hats := preload("../Props/santa_hat.gd")
# Rider animation -> Bicycle.glb animation to play with it
const BIKE_ANIMS := {
	"Cycling": "Pedal",
	"Cycling_Coast": "Coast",
	"Cycling_Stop": "Stop",
	"Cycling_Rest": "Rest",
	"Cycling_Start": "Start",
	"Cycling_Signal_Left": "Coast",
	"Cycling_Signal_Right": "Coast",
	"Cycling_Signal_Stop": "Coast",
}

# Props for some of the added animations: animation name prefix -> list of [prop scene, bone].
# An empty bone means the prop is placed at the character root (bicycle, chair, played cello).
const PROPS := {
	"Carry_Box": [["Props/Box.glb", "Torso"]],
	"Umbrella": [["Props/Umbrella.glb", "IteamSlot.R"]],
	"Phone": [["Props/Phone.glb", "IteamSlot.R"]],
	"Cycling": [["Props/Bicycle.glb", ""]],
	"Sit_Chair": [["Props/Chair.glb", ""]],
	"Cello_Carry": [["Props/Cello_Carried.glb", "IteamSlot.R"]],
	"Cello_Play": [["Props/Cello_Played.glb", ""], ["Props/Bow.glb", "IteamSlot.R"], ["Props/Chair.glb", ""]],
	"Cello_Rest": [["Props/Cello_Played.glb", ""], ["Props/Bow.glb", "IteamSlot.R"], ["Props/Chair.glb", ""]],
	"Police_Radio": [["Props/Radio_Mic.glb", "Torso"]],
	"Police_Ticket": [["Props/TicketBook.glb", "IteamSlot.L"], ["Props/Pen.glb", "IteamSlot.R"]],
}

var pack_dir := ""
var library: AnimationLibrary
var character_paths: PackedStringArray = []
var shown: Array[Node3D] = []
var players: Array[AnimationPlayer] = []
var props: Array[Node] = []
var prop_players: Array[AnimationPlayer] = []
var current_anim := ""
var speed := 1.0
var paused := false

var char_filter: LineEdit
var char_list: ItemList
var anim_list: ItemList
var speed_label: Label
var info_label: Label

var cam_pivot: Node3D
var camera: Camera3D
var cam_yaw := -25.0
var cam_pitch := -10.0
var cam_dist := 4.5
var dragging := false


func _ready() -> void:
	# Pack root is one folder up from this script, so the pack can live anywhere in the project
	pack_dir = (get_script() as Script).resource_path.get_base_dir().get_base_dir()
	library = load(pack_dir + "/Animations/Characters_1_Animations.glb") as AnimationLibrary
	if library == null:
		push_error("Characters_1_Animations.glb is not imported as Animation Library. Select it, set Import As: Animation Library, Reimport.")
		return
	_collect_characters(pack_dir + "/Characters")
	character_paths.sort()
	_build_world()
	_build_ui()

	var args := _parse_args()
	var anims := library.get_animation_list()
	current_anim = args.get("anim", "Idle_A" if library.has_animation("Idle_A") else String(anims[0]))
	if args.has("crowd"):
		_show_crowd(int(args["crowd"]))
	else:
		_show_single(character_paths[0])
	_select_anim_in_list()


func _collect_characters(dir: String) -> void:
	for entry in ResourceLoader.list_directory(dir):
		if entry.ends_with("/"):
			_collect_characters(dir + "/" + entry.trim_suffix("/"))
		elif entry.ends_with(".glb"):
			character_paths.append(dir + "/" + entry)


func _build_world() -> void:
	var env := WorldEnvironment.new()
	# the pack ships an Environment, use it so the test looks like the rest of the pack
	var e := load(pack_dir + "/Environment/Characters_1_Environment.tres") as Environment
	if e == null:
		e = Environment.new()
		var sky := Sky.new()
		sky.sky_material = ProceduralSkyMaterial.new()
		e.background_mode = Environment.BG_SKY
		e.sky = sky
		e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
		e.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.environment = e
	add_child(env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -35, 0)
	sun.shadow_enabled = true
	add_child(sun)

	var floor_mesh := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(40, 40)
	var floor_mat := StandardMaterial3D.new()
	floor_mat.albedo_color = Color(0.32, 0.34, 0.37)
	plane.material = floor_mat
	floor_mesh.mesh = plane
	add_child(floor_mesh)

	cam_pivot = Node3D.new()
	cam_pivot.position = Vector3(0, 0.9, 0)
	add_child(cam_pivot)
	camera = Camera3D.new()
	camera.fov = 40
	cam_pivot.add_child(camera)
	_update_camera()


func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var panel := PanelContainer.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_LEFT_WIDE)
	panel.custom_minimum_size = Vector2(280, 0)
	layer.add_child(panel)
	var box := VBoxContainer.new()
	panel.add_child(box)

	box.add_child(_label("Character (%d)" % character_paths.size()))
	char_filter = LineEdit.new()
	char_filter.placeholder_text = "Filter, e.g. Police"
	char_filter.text_changed.connect(func(_t): _fill_char_list())
	box.add_child(char_filter)
	char_list = ItemList.new()
	char_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	char_list.item_selected.connect(func(i): _show_single(char_list.get_item_metadata(i)))
	box.add_child(char_list)
	_fill_char_list()

	var crowd_btn := Button.new()
	crowd_btn.text = "Random crowd (%d)" % CROWD_SIZE
	crowd_btn.pressed.connect(func(): _show_crowd(CROWD_SIZE))
	box.add_child(crowd_btn)

	var hat_btn := CheckButton.new()
	hat_btn.text = "Santa hats"
	hat_btn.button_pressed = Hats.is_active()
	hat_btn.toggled.connect(func(on: bool):
		Hats.mode = Hats.Mode.ON if on else Hats.Mode.OFF
		Hats.update_all(get_tree())
		_update_hats())
	box.add_child(hat_btn)

	box.add_child(_label("Animation (%d)" % library.get_animation_list().size()))
	anim_list = ItemList.new()
	anim_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	anim_list.size_flags_stretch_ratio = 1.4
	for n in library.get_animation_list():
		var a := library.get_animation(n)
		var idx := anim_list.add_item("%s%s" % [n, "  (loop)" if a.loop_mode != Animation.LOOP_NONE else ""])
		anim_list.set_item_metadata(idx, String(n))
	anim_list.item_selected.connect(func(i): _play(anim_list.get_item_metadata(i)))
	box.add_child(anim_list)

	speed_label = _label("Speed 1.00x")
	box.add_child(speed_label)
	var slider := HSlider.new()
	slider.min_value = 0.1
	slider.max_value = 2.0
	slider.step = 0.05
	slider.value = 1.0
	slider.value_changed.connect(_set_speed)
	box.add_child(slider)

	info_label = _label("")
	box.add_child(info_label)
	box.add_child(_label("Drag: orbit, Wheel: zoom\nSpace: pause, Up/Down: animation"))


func _label(text: String) -> Label:
	var l := Label.new()
	l.text = text
	return l


func _fill_char_list() -> void:
	char_list.clear()
	var f := char_filter.text.to_lower()
	for p in character_paths:
		var n := p.get_file().get_basename()
		if f.is_empty() or n.to_lower().contains(f) or p.to_lower().contains(f):
			var idx := char_list.add_item(n)
			char_list.set_item_metadata(idx, p)


func _clear() -> void:
	_clear_props()
	for c in shown:
		c.queue_free()
	shown.clear()
	players.clear()


func _clear_props() -> void:
	for p in props:
		if is_instance_valid(p):
			p.queue_free()
	props.clear()
	prop_players.clear()


func _attach_props(anim: String) -> void:
	_clear_props()
	var entries: Array = []
	for prefix in PROPS:
		if anim.begins_with(prefix):
			entries = PROPS[prefix]
	for entry in entries:
		var scene := load(pack_dir + "/" + String(entry[0])) as PackedScene
		if scene == null:
			push_warning("Prop not found: " + String(entry[0]))
			continue
		for i in shown.size():
			_attach_prop(scene, String(entry[1]), i, anim)


func _attach_prop(scene: PackedScene, bone: String, i: int, anim: String) -> void:
	var ch := shown[i]
	var prop := scene.instantiate() as Node3D
	if bone.is_empty():
		# root prop: the placement is baked into the scene
		ch.add_child(prop)
		props.append(prop)
		# the bicycle has its own animation per riding clip (wheels, crank, lean): play it in sync
		var prop_ap := prop.find_child("AnimationPlayer", true, false) as AnimationPlayer
		var prop_anim: String = BIKE_ANIMS.get(anim, "Pedal")
		if prop_ap and prop_ap.has_animation(prop_anim):
			prop_ap.play(prop_anim)
			prop_ap.seek(players[i].current_animation_position, true)
			prop_ap.speed_scale = players[i].speed_scale
			prop_players.append(prop_ap)
		return
	# bone prop: the offset is baked into the prop scene, so identity under the attachment
	var skel := ch.find_child("Skeleton3D", true, false) as Skeleton3D
	if skel == null:
		prop.queue_free()
		return
	var att := BoneAttachment3D.new()
	att.bone_name = bone
	skel.add_child(att)
	att.add_child(prop)
	props.append(att)


func _spawn(path: String, pos: Vector3) -> void:
	var scene := load(path) as PackedScene
	var ch := scene.instantiate() as Node3D
	ch.position = pos
	add_child(ch)
	# AnimationPlayer as a child of the character root, tracks are "Armature/Skeleton3D:Bone"
	var ap := AnimationPlayer.new()
	ch.add_child(ap)
	ap.add_animation_library("", library)
	Hats.apply(ch)  # Santa hat in the holiday season (or when toggled on)
	shown.append(ch)
	players.append(ap)


func _show_single(path: String) -> void:
	_clear()
	_spawn(path, Vector3.ZERO)
	cam_dist = 4.5
	cam_pivot.position = Vector3(0, 0.9, 0)
	camera.h_offset = -0.55  # keep the character clear of the side panel
	_update_camera()
	_play(current_anim)


func _show_crowd(count: int) -> void:
	_clear()
	var cols := 6
	var rows := int(ceil(count / float(cols)))
	for i in count:
		var path := character_paths[randi() % character_paths.size()]
		var x := (i % cols - (cols - 1) / 2.0) * 1.3
		var z := (i / cols - (rows - 1) / 2.0) * 1.6
		_spawn(path, Vector3(x, 0, z))
	cam_dist = 11.0
	cam_pivot.position = Vector3(0, 0.8, 0)
	camera.h_offset = -1.3
	_update_camera()
	_play(current_anim)


func _play(anim: String) -> void:
	current_anim = anim
	for i in players.size():
		var ap := players[i]
		ap.play(anim)
		# Small offset so a crowd does not move in perfect sync
		if players.size() > 1:
			ap.seek(fmod(i * 0.37, max(ap.current_animation_length, 0.01)), true)
		ap.speed_scale = 0.0 if paused else speed
	_attach_props(anim)
	_update_hats()
	var a := library.get_animation(anim)
	info_label.text = "%s\n%.2f s, %s" % [anim, a.length, "loop" if a.loop_mode != Animation.LOOP_NONE else "once"]


func _update_hats() -> void:
	# the umbrella goes through the hat, so hide hats while it is out
	for ch in shown:
		Hats.set_hidden(ch, Hats.clashes_with(current_anim))


func _select_anim_in_list() -> void:
	for i in anim_list.item_count:
		if anim_list.get_item_metadata(i) == current_anim:
			anim_list.select(i)
			anim_list.ensure_current_is_visible()


func _set_speed(v: float) -> void:
	speed = v
	speed_label.text = "Speed %.2fx" % v
	for ap in players:
		ap.speed_scale = 0.0 if paused else speed
	for ap in prop_players:
		ap.speed_scale = 0.0 if paused else speed


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT or event.button_index == MOUSE_BUTTON_RIGHT:
			dragging = event.pressed
		elif event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			cam_dist = max(1.5, cam_dist * 0.9)
			_update_camera()
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			cam_dist = min(40.0, cam_dist * 1.1)
			_update_camera()
	elif event is InputEventMouseMotion and dragging:
		cam_yaw -= event.relative.x * 0.3
		cam_pitch = clamp(cam_pitch - event.relative.y * 0.3, -80.0, 10.0)
		_update_camera()
	elif event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_SPACE:
			paused = not paused
			_set_speed(speed)
		elif event.keycode == KEY_DOWN or event.keycode == KEY_UP:
			var names := library.get_animation_list()
			var i := names.find(StringName(current_anim))
			i = wrapi(i + (1 if event.keycode == KEY_DOWN else -1), 0, names.size())
			_play(String(names[i]))
			_select_anim_in_list()


func _update_camera() -> void:
	cam_pivot.rotation_degrees = Vector3(cam_pitch, cam_yaw, 0)
	camera.position = Vector3(0, 0, cam_dist)


# Command line: godot ... -- --anim=Walk_A --crowd=12
func _parse_args() -> Dictionary:
	var out := {}
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--") and a.contains("="):
			var kv := a.substr(2).split("=", true, 1)
			out[kv[0]] = kv[1]
	return out
