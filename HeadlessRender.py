"""HeadlessRender.py: render the sprite sheets of a Clonk without the Blender UI (batch jobs, scripted tools).

Builds Graphics.png + Overlay.png + ActMap.txt + DefCore.txt of a Clonk character from the Clonk content files
(https://github.com/RoboClonk/RenderClonkAddon/releases/tag/Content) with this add-on, like the buttons
"Import Action List..." + "Import Object..." + "Render Spritesheet" do. Tested with Blender 5.2.

Run (the add-on must be installed in Blender, or its parent "addons" folder be on BLENDER_USER_SCRIPTS):
  blender -b --factory-startup -P HeadlessRender.py -- --content <content folder> --clonk Clonk --out <dir>
      [--actions Walk,Jump] [--samples 256] [--setup-only] [--blend X.blend]

--clonk: name of Clonks/<name>.mesh + .act in the content folder (Clonk, Knight, Mage, King, ...). Its .act decides
         which actions are rendered (+ tool meshes). With --blend an existing .blend (made by --setup-only and then
         modified, e.g. new materials/props) is rendered instead.
Output in --out: Graphics.png, Overlay.png, ActMap.txt, DefCore.txt, <name>.blend; single sprites in output_render/.

How it works: the add-on renders with a modal timer operator (needs a window). Its execute()/modal() are plain
functions, so they are called here with a stand-in operator object, a fake window manager and TIMER events; the
add-on's own follow-up call for the overlay pass is redirected to the same driver.
"""
import argparse
import os
import shutil
import sys
import time

import addon_utils
import bpy


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--clonk", default="Clonk")
    ap.add_argument("--content", default="", help="Clonk content folder (with Clonks/, Actions/, Tools/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--actions", default="", help="comma separated subset of actions (default: all of the .act)")
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--setup-only", action="store_true", help="only build and save the .blend")
    ap.add_argument("--blend", default="", help="render this .blend instead of importing")
    ap.add_argument("--face", default="", help="face texture (path, or file name in Clonks/Textures) instead of the mesh's")
    ap.add_argument("--picture-face", default="", help="face texture only for the title picture, e.g. the detailed "
                    "Face.png (the original sprites: FaceGob.png in the animations, Face.png in the picture)")
    return ap.parse_args(argv)


A = args()
if not A.blend and not A.content:
    raise SystemExit("--content (Clonk content folder) is required unless --blend is given")
OUT = os.path.abspath(A.out)
os.makedirs(OUT, exist_ok=True)


def find_addon():
    """Module name of this add-on (the folder name depends on how it was installed)."""
    for mod in addon_utils.modules():
        if getattr(mod, "bl_info", {}).get("name") == "RenderClonk":
            return mod.__name__
    return "RenderClonkAddon"


ADDON = find_addon()
addon_utils.enable(ADDON, default_set=True, handle_error=None)
_pkg = __import__(ADDON, fromlist=["ClonkPort", "MetaData", "SpritesheetMaker", "PathUtilities"])
ClonkPort, MetaData, SM = _pkg.ClonkPort, _pkg.MetaData, _pkg.SpritesheetMaker


def setup():
    """Fresh scene with the Clonk rig, actions, tools and body mesh, saved as <out>/<clonk>.blend."""
    for ob in list(bpy.data.objects):          # factory scene: cube, camera, light would end up in the renders
        bpy.data.objects.remove(ob)
    bpy.context.preferences.addons[ADDON].preferences.content_folder = A.content
    ClonkPort.SetOptimalRenderingSettings()
    clonks = os.path.join(A.content, "Clonks")
    r = bpy.ops.act.open_filebrowser(filepath=os.path.join(clonks, A.clonk + ".act"))
    print("act import:", r)
    r = bpy.ops.mesh.open_filebrowser(filepath=os.path.join(clonks, A.clonk + ".mesh"))
    print("mesh import:", r)
    add_picture(os.path.join(clonks, A.clonk + ".act"))
    if A.face:
        set_face(A.face if os.path.isabs(A.face) else os.path.join(clonks, "Textures", A.face))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, A.clonk + ".blend"))


def set_face(path):
    """Use another face texture: every image texture of the "Face" material(s) gets this image."""
    image = bpy.data.images.load(path, check_existing=True)
    count = 0
    for mat in bpy.data.materials:
        if mat.name.startswith("Face") and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE":
                    node.image = image
                    count += 1
    print(f"face: {os.path.basename(path)} ({count} texture node(s))")


def add_picture(act_path):
    """The .act's "Picture=<anim>" (menu/inventory picture, e.g. PictClonk 32x40) is ignored by the add-on's
    import; load it as a "Picture" entry with special placement (lands in a free spot, like the originals: 192,100)."""
    name = ""
    for line in open(act_path, encoding="utf-8", errors="replace"):
        if line.startswith("Picture="):
            name = line.split("=", 1)[1].strip()
    if not name:
        return
    anim = next((os.path.join(A.content, sub, name + ".anim") for sub in ("Actions", "Clonks", "Tools")
                 if os.path.exists(os.path.join(A.content, sub, name + ".anim"))), None)
    if not anim:
        print("picture animation not found:", name)
        return
    data = ClonkPort.LoadAction(anim, bpy.context.scene.anim_target)
    entry = MetaData.MakeActionEntry(data)
    ClonkPort._ImportToolsIfAnyLegacy(entry, data, ClonkPort.found_meshes)   # e.g. PictKnight: Tool1=Sword, Tool2=Shield
    if A.picture_face:
        # the add-on's per-action material swap: materials named "Face..." use this copy while the picture renders
        path = A.picture_face if os.path.isabs(A.picture_face) else os.path.join(A.content, "Clonks", "Textures", A.picture_face)
        face = next((m for m in bpy.data.materials if m.name.startswith("Face") and m.node_tree), None)
        if face:
            pict_face = face.copy()
            pict_face.name = "PictureFace"
            image = bpy.data.images.load(path, check_existing=True)
            for node in pict_face.node_tree.nodes:
                if node.type == "TEX_IMAGE":
                    node.image = image
            entry.find_material_name = face.name
            entry.replace_material = pict_face
            print(f"picture face: {os.path.basename(path)}")
    entry.render_type_enum = "Picture"
    entry.use_normal_action_placement = False
    print(f"picture: {name} {entry.width}x{entry.height}")


class FakeWM:
    def event_timer_add(self, *a, **k):
        return None

    def modal_handler_add(self, *a, **k):
        return True

    def event_timer_remove(self, *a, **k):
        pass


class FakeContext:
    window = None
    window_manager = FakeWM()

    def __getattr__(self, name):
        return getattr(bpy.context, name)


class TimerEvent:
    type = "TIMER"


class Driver:
    """Stand-in for SpritesheetMaker.TIMER_OT (same methods, plain attributes)."""
    execute = SM.TIMER_OT.execute
    modal = SM.TIMER_OT.modal
    cancel = SM.TIMER_OT.cancel
    reset_ortho_scale = SM.TIMER_OT.reset_ortho_scale
    reset_camera_shift = SM.TIMER_OT.reset_camera_shift
    store_camera_shift = SM.TIMER_OT.store_camera_shift

    def __init__(self, output_image_name, set_overlay_material=False, replace_overlay_material=False):
        for k, v in vars(SM.TIMER_OT).items():
            if not k.startswith("__") and not callable(v) and not isinstance(v, (staticmethod, classmethod)):
                setattr(self, k, v)
        self.output_image_name = output_image_name
        self.set_overlay_material = set_overlay_material
        self.replace_overlay_material = replace_overlay_material
        self.current_action_index = 0
        self.default_camera_zoom, self.default_camera_shift = {}, {}
        self.default_anim_target_locations, self.default_anim_target_rotations = {}, {}
        self.messages = []

    def report(self, kind, message):
        print(f"[{', '.join(kind)}] {message}")
        self.messages.append((kind, message))

    def run(self):
        ctx = FakeContext()
        self.execute(ctx)
        if self.has_been_cancelled:
            raise RuntimeError(f"render did not start: {self.cancel_message_type} {self.cancel_message}")
        while True:
            state = self.modal(ctx, TimerEvent())
            if state != {"RUNNING_MODAL"}:
                return state


class OpsProxy:
    """bpy stand-in for SpritesheetMaker: redirects its bpy.ops.timer.progress(...) (the overlay pass)."""
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name == "ops":
            return _OpsRedirect(self._real.ops)
        return getattr(self._real, name)


class _OpsRedirect:
    def __init__(self, ops):
        self._ops = ops

    def __getattr__(self, name):
        if name == "timer":
            return _Timer()
        return getattr(self._ops, name)


class _Timer:
    @staticmethod
    def progress(**kw):
        print("overlay pass:", kw)
        return Driver(**kw).run()


def broken_overlay_sprites():
    """Overlay sprites (*_o.png) in which most opaque pixels are black."""
    import glob
    import numpy as np
    bad = []
    for path in glob.glob(os.path.join(_pkg.PathUtilities.GetOutputPath(), "sprites", "*_o.png")):
        image = bpy.data.images.load(path)
        px = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(px)
        bpy.data.images.remove(image)
        px = px.reshape(-1, 4)
        opaque = px[px[:, 3] > 0.8]
        if len(opaque) > 20 and (opaque[:, :3].sum(axis=1) < 0.15).mean() > 0.5:
            bad.append(path)
    return bad


def render():
    scene = bpy.context.scene
    scene.cycles.device = "CPU"
    scene.cycles.samples = A.samples
    scene.custom_output_dir = OUT
    wanted = {a.strip() for a in A.actions.split(",") if a.strip()}
    if wanted:
        for entry in scene.animlist:
            entry.is_used = MetaData.GetActionName(entry) in wanted
    entries = MetaData.GetValidActionEntries()
    frames = sum(e.max_frames for e in entries)
    print(f"rendering {len(entries)} actions, {frames} frames x 2 passes, {A.samples} samples")
    SM.bpy = OpsProxy(bpy)
    t = time.time()
    SM.current_rerender_state = ""
    Overlay, Holdout, Fill = ClonkPort.GetOrAppendOverlayMaterials()

    def render_sheets():
        if scene.spritesheet_settings.overlay_rendering_enum == "Separate":
            Driver("Graphics", set_overlay_material=True, replace_overlay_material=False).run()
        else:
            Driver("Graphics").run()

    render_sheets()
    if os.environ.get("RC_TEST_BREAK"):   # test hook: blacken one overlay sprite to exercise the repair below
        import numpy as np
        path = os.path.join(_pkg.PathUtilities.GetOutputPath(), "sprites", os.environ["RC_TEST_BREAK"])
        image = bpy.data.images.load(path)
        px = np.zeros(len(image.pixels), dtype=np.float32)
        px[3::4] = 1.0
        image.pixels.foreach_set(px)
        image.save()
        bpy.data.images.remove(image)
        print("test: broke", os.path.basename(path))
    # Rarely (seen once in ~10 Knight renders, not reproducible) a frame of the overlay pass comes out as an opaque
    # black silhouette: the Holdout material swap did not take effect for that frame. Re-render such frames with
    # the add-on's "render missing sprites and repack" mode.
    for attempt in range(3):
        bad = broken_overlay_sprites()
        if not bad:
            break
        print(f"re-rendering {len(bad)} broken overlay sprite(s): {', '.join(os.path.basename(p) for p in bad)}")
        for path in bad:
            os.remove(path)
        SM.current_rerender_state = "RepackSpriteSheet"
        render_sheets()
        SM.current_rerender_state = ""
    SM.bpy = bpy
    print(f"render time {time.time() - t:.0f} s")
    print(ClonkPort.PrintActmap(OUT))
    print(ClonkPort.PrintDefCore(OUT))


if A.blend:
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(A.blend))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, os.path.basename(A.blend)))
else:
    setup()
if not A.setup_only:
    render()
    bpy.ops.wm.save_mainfile()
print("DONE", OUT, sorted(os.listdir(OUT)))
