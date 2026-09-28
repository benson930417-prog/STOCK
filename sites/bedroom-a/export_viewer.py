import bpy
from pathlib import Path
root = Path(__file__).resolve().parent
bpy.ops.wm.open_mainfile(filepath=str(root / 'bedroom-a-v1.blend'))
bpy.ops.object.select_all(action='DESELECT')
for obj in bpy.context.scene.objects:
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.hide_render = False
    if obj.type == 'MESH':
        obj['viewer_group'] = obj.users_collection[0].name if obj.users_collection else 'Shell'
        obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=str(root / 'bedroom-a-v1.glb'), export_format='GLB', use_selection=True, export_extras=True, export_apply=True, export_cameras=False, export_lights=False)
print('EXPORT COMPLETE')
import runpy
runpy.run_path(str(root / 'build_edge_profiles.py'))['build_edge_profiles'](root)
