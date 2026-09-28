import bpy, json, runpy
from pathlib import Path
from mathutils import Vector
root=Path(__file__).resolve().parent
bpy.ops.wm.open_mainfile(filepath=str(root/'bedroom-a-v1.blend'))
def bounds(objects):
    pts=[o.matrix_world@Vector(v) for o in objects for v in o.bound_box]
    return [min(p[i] for p in pts) for i in range(3)]+[max(p[i] for p in pts) for i in range(3)]
def obj(name):return bpy.data.objects[name]
def near(actual,expected):
    assert abs(actual-expected)<.0002,(actual,expected)
for name,xa,xb,lo,hi in [('wide window',1.07,2.57,.5,2.3),('narrow window',4.44,5.37,.5,2.5)]:
    frames=[o for o in bpy.data.objects if o.name.startswith(name) and any(t in o.name for t in ['jamb','rail','transom','mullion'])]
    b=bounds(frames)
    for actual,expected in zip([b[0],b[3],b[2],b[5]],[xa,xb,lo,hi]):near(actual,expected)
near(bounds([obj('wide window transom')])[2],1.0)
near(bounds([obj('wide window transom')])[5],1.05)
near(bounds([obj('left headboard wall')])[3],0)
near(bounds([obj('bath wall solid')])[1],3.4)
near(bounds([obj('front wall pier 0')])[4],0)
for name,xa,xb in [('bathroom',1.9,2.65),('entrance',2.804,3.804)]:
    b=bounds([o for o in bpy.data.objects if o.name.startswith(name+' door') and 'frame' in o.name])
    near(b[0],xa);near(b[3],xb);near(b[5],2.4)
assert not any('parapet' in o.name for o in bpy.data.objects)
assert not any('balcony vertical frame' in o.name for o in bpy.data.objects)
assert not any('north stepped boundary' in o.name for o in bpy.data.objects)
inventory=[]
for o in bpy.context.scene.objects:
    if o.type!='MESH':continue
    group=o.users_collection[0].name
    basis=o.get('dimension_basis')
    if not basis:
        if group=='Furniture':basis='Design proposal; not measured existing furniture'
        elif group=='OriginalJoinery':basis='Original cabinet boundary; wall construction unresolved'
        elif group=='Ceiling':
            basis='p69 traced visible ceiling / p65 transverse band' if any(m and m.name=='01 chalk walls' for m in o.data.materials) else 'Illustrative grille/LED hardware; recess position from p69'
        elif group=='FrontWall':basis='p61/p69 opening geometry; simplified frame cross-section'
        elif group=='Openings':basis='p61/p67/p69 opening, simplified frame/hardware; entrance conflict recorded'
        else:basis='Drawing footprint, context/hidden closure thickness not surveyed'
    inventory.append({'name':o.name,'group':group,'bounds_m':[round(v,5) for v in bounds([o])],'basis':basis})
(root/'model-dimension-inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: openings, sill/transom/head elevations, inner wall faces and removal of unsupported parapets;',len(inventory),'meshes classified')
runpy.run_path(str(root/'export_viewer.py'))
