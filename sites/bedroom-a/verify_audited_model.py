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
assert not any('original cabinet boundary' in o.name for o in bpy.data.objects)
assert not any('180 x 200' in o.name for o in bpy.data.objects)
b=bounds([obj('mattress Taiwan standard 152 x 188')])
for actual,expected in zip(b,[.1,.2,.28,1.98,1.72,.50]):near(actual,expected)
b=bounds([obj('platform top 238 x 170 H28')])
for actual,expected in zip(b,[0,.15,.245,2.38,1.85,.28]):near(actual,expected)
wardrobe=[o for o in bpy.data.objects if o.name.startswith('parallel wardrobe')]
b=bounds(wardrobe)
near(b[0],0);near(b[3],1.7);near(b[4],3.4);near(b[5],2.35)
assert b[1]-1.85 >= .899, b
assert b[3] < 1.9
near(bounds([obj('bedroom B fixed partition - front')])[1],2.93)
assert len([o for o in bpy.data.objects if o.name.startswith('bathroom A')])>=20
near(bounds([obj('bathroom A floor')])[3],2.7)
near(bounds([obj('bathroom A floor')])[4],5.3)
for o in bpy.data.objects:
    if o.get('drawer_travel'):
        b=bounds([o]);assert b[4]<1.85,'Drawer must not enter wardrobe / bathroom aisle'
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
print('PASS: source openings, fixed B partition, ensuite footprint, 152 x 188 bed, platform, 90 cm wardrobe aisle and drawers outside bathroom approach;',len(inventory),'meshes classified')
runpy.run_path(str(root/'export_viewer.py'))
