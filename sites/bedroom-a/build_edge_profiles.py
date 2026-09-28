"""Generate display edges from unions of touching wall/ceiling pieces.

Temporary copies only: the room's meshes and .blend are not modified.
Called by export_viewer.py after GLB export.
"""
import bpy, bmesh, json, math
from pathlib import Path
from mathutils import Matrix

def build_edge_profiles(root):
    groups = {}
    for obj in list(bpy.context.scene.objects):
        if obj.type == 'MESH' and any(m and m.name == '01 chalk walls' for m in obj.data.materials):
            name = obj.users_collection[0].name
            groups.setdefault(name, []).append(obj)
    result = []
    for group, sources in groups.items():
        copies = []
        for source in sources:
            mesh = source.data.copy()
            mesh.transform(source.matrix_world)
            # Cube transforms can leave micron-scale gaps at nominally identical joins.
            # Snap only these temporary copies to a 0.01 mm grid before the union.
            for vertex in mesh.vertices:
                vertex.co = tuple(round(c, 5) for c in vertex.co)
            obj = bpy.data.objects.new('_edge_union', mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.matrix_world = Matrix.Identity(4)
            copies.append(obj)
        merged = copies[0]
        bpy.context.view_layer.objects.active = merged
        merged.select_set(True)
        for other in copies[1:]:
            modifier = merged.modifiers.new('Join adjoining solids for outlines', 'BOOLEAN')
            modifier.operation = 'UNION'
            modifier.solver = 'EXACT'
            modifier.object = other
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            bpy.data.objects.remove(other, do_unlink=True)
        bm = bmesh.new()
        bm.from_mesh(merged.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        positions = []
        for edge in bm.edges:
            if edge.calc_length() > 0.00002 and (edge.is_boundary or (edge.is_manifold and edge.calc_face_angle() > math.radians(35))):
                for vertex in edge.verts:
                    x, y, z = vertex.co
                    positions.extend([round(x, 6), round(z, 6), round(-y, 6)])
        result.append({'group': group, 'positions': positions})
        print('UNION EDGES', group, len(sources), 'solids;', len(positions)//6, 'edges')
        bm.free()
        bpy.data.objects.remove(merged, do_unlink=True)
    (Path(root) / 'architectural-edges.json').write_text(json.dumps(result), encoding='utf-8')

if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    bpy.ops.wm.open_mainfile(filepath=str(root / 'bedroom-a-v1.blend'))
    build_edge_profiles(root)
