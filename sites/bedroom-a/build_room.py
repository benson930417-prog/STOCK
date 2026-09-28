"""Bedroom A v1. Run with Blender 4.5: blender -b --python build_room.py
Audited drawing geometry, meters. Source: dimension-audit.json; not a site survey.
X points from original left wardrobe wall toward balcony. Y points up source plan.
"""
import bpy, math, json
from pathlib import Path
from mathutils import Vector

OUT=Path(__file__).resolve().parent
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
for d in list(bpy.data.materials): bpy.data.materials.remove(d)
scene=bpy.context.scene
scene.unit_settings.system='METRIC'
scene.render.engine='CYCLES'
scene.cycles.samples=48
scene.cycles.use_denoising=True
try:
    pref=bpy.context.preferences.addons['cycles'].preferences
    pref.compute_device_type='OPTIX'; pref.get_devices()
    for d in pref.devices: d.use=d.type=='OPTIX'
    scene.cycles.device='GPU'
except Exception: pass
scene.render.resolution_x=1500; scene.render.resolution_y=1000; scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.world.color=(.65,.65,.65)
scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.78,.85,.94,1)
scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.4
scene.view_settings.view_transform='AgX'
scene.view_settings.look='AgX - Medium High Contrast'
scene.render.film_transparent=False
scene.view_settings.exposure=-0.25
scene.render.image_settings.color_mode='RGB'
bpy.context.preferences.filepaths.save_version=0

def material(name,c,rough=.65,metal=0,emission=0):
    m=bpy.data.materials.new(name);m.diffuse_color=(*c,1);m.use_nodes=True
    p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1)
    p.inputs['Roughness'].default_value=rough;p.inputs['Metallic'].default_value=metal
    if emission:p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emission
    return m
wall=material('01 chalk walls',(.78,.77,.74)); floor=material('02 warm grey floor',(.5,.48,.44))
linen=material('03 ivory linen',(.78,.77,.71)); cloth=material('04 warm grey upholstery',(.45,.43,.39))
joinery=material('05 clay joinery placeholders',(.61,.59,.54));frame=material('06 soft graphite window frames',(.17,.19,.19),.4)
dark=material('07 shadow gaps',(.06,.07,.07)); balcony=material('08 balcony grey',(.48,.49,.47))
lightmat=material('09 warm LED', (1,.78,.48),emission=1.25)
grillemat=material('11 satin grey vent grille',(.48,.49,.47),.48,.12)
glass=material('10 glass',(.85,.92,.94),.08)
pg=glass.node_tree.nodes['Principled BSDF'];pg.inputs['Transmission Weight'].default_value=1;pg.inputs['IOR'].default_value=1.45
groups={}
for name in ['Shell','FrontWall','LeftWall','Ceiling','Openings','Furniture','OriginalJoinery','Bathroom','Balcony','Lights','Cameras']:
    c=bpy.data.collections.new(name);scene.collection.children.link(c);groups[name]=c
def group(o,g):
    for c in list(o.users_collection):c.objects.unlink(o)
    groups[g].objects.link(o)
def box(name,loc,size,mat,g='Shell',bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.dimensions=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    o.data.materials.append(mat);group(o,g)
    if bevel:
        m=o.modifiers.new('soft edges','BEVEL');m.width=bevel;m.segments=3
        m=o.modifiers.new('weighted normals','WEIGHTED_NORMAL')
    return o
def slab(name,poly,z,thickness,mat,g):
    verts=[(x,y,z) for x,y in poly]+[(x,y,z+thickness) for x,y in poly];n=len(poly)
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    o=bpy.data.objects.new(name,mesh);groups[g].objects.link(o);o.data.materials.append(mat);return o
def wallseg(name,a,b,h=3.05,g='Shell',base=0,th=.15):
    dx=b[0]-a[0];dy=b[1]-a[1]
    o=box(name,((a[0]+b[0])/2,(a[1]+b[1])/2,base+h/2),(math.hypot(dx,dy),th,h),wall,g)
    o.rotation_euler.z=math.atan2(dy,dx);return o
def area(name,loc,target,power,size,color=(1,.91,.8)):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
    o=bpy.data.objects.new(name,data);groups['Lights'].objects.link(o);o.visible_camera=False;o.visible_glossy=False;o.visible_transmission=False;o.location=loc;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();return o
def camera(name,loc,target,lens=24,ortho=None):
    d=bpy.data.cameras.new(name);o=bpy.data.objects.new(name,d);groups['Cameras'].objects.link(o)
    o.location=loc;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();d.lens=lens;d.clip_start=.05
    if ortho:d.type='ORTHO';d.ortho_scale=ortho
    return o

# Room enclosure and windows use the audited source-coordinate manifest.
import runpy
D=runpy.run_path(str(OUT/'audited_shell.py'))['build_shell'](box,slab,wall,floor,frame,glass,joinery,balcony)
runpy.run_path(str(OUT/'window_frames.py'))['build_window_frames'](box,frame,glass)
runpy.run_path(str(OUT/'bathroom.py'))['build_bathroom'](box,slab,material,wall,floor,frame,glass)

runpy.run_path(str(OUT/'platform_proposal.py'))['build_platform_proposal'](box,material,slab,linen,cloth,frame,lightmat,area)

# Optional furniture blocks. They are proposals, not inherited fixed joinery.
box('desk proposal',(5.92,.40,.74),(1.55,.65,.055),joinery,'Furniture',.02)
for x in [5.24,6.60]:box('desk support',(x,.40,.36),(.055,.54,.70),frame,'Furniture',.01)
box('chair seat',(5.92,1.01,.46),(.5,.47,.08),cloth,'Furniture',.06)
box('chair back',(5.92,1.24,.76),(.5,.06,.53),cloth,'Furniture',.05)
for x in [5.72,6.12]:
    for y in [.85,1.17]:box('chair leg',(x,y,.23),(.032,.032,.43),frame,'Furniture',.007)

# Visible S3 ceiling contour traced from repaired vector PDF p69.
# Scale uses explicit 105 cm dimension; Z uses explicit H250 plus 20 / 10 cm steps.
# X is reversed because the drawing shows the balcony on the left.
P69_SCALE=92.53650483630952
P69_RIGHT=833.0501098632812
P69_H250_Y=410.571
profile_pdf=[
 (833.050,350.422),(833.050,410.571),(735.887,410.571),(735.887,382.810),
 (550.444,382.810),(544.198,382.810),(544.198,392.064),(550.444,392.064),
 (550.444,398.541),(572.652,398.541),(572.652,396.691),(573.578,396.691),
 (573.578,401.317),(228.324,401.317),(228.324,396.691),(229.250,396.691),
 (229.250,398.541),(237.578,398.541),(237.578,382.810),(214.444,382.810),
 (214.444,376.564),(205.190,376.564),(205.190,410.571),(182.056,410.571),
 (182.056,401.317),(170.952,401.317),(170.952,350.422)]
profile_xz=[(max(0,min(7.155,(P69_RIGHT-x)/P69_SCALE)),2.5+(P69_H250_Y-y)/P69_SCALE) for x,y in profile_pdf]
def extruded_ceiling_profile(name,profile,y0,y1):
    import bmesh
    n=len(profile);verts=[(x,y0,z) for x,z in profile]+[(x,y1,z) for x,z in profile]
    faces=[tuple(range(n)),tuple(reversed(range(n,2*n)))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    bm=bmesh.new();bm.from_mesh(mesh);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
    o=bpy.data.objects.new(name,mesh);groups['Ceiling'].objects.link(o);o.data.materials.append(wall);return o
extruded_ceiling_profile('P69 traced ceiling including projecting shelves and lips',profile_xz,.47,2.93)
def ceiling_zone(name,xa,xb,ya,yb,bottom):
    top=3.15
    return box(name,((xa+xb)/2,(ya+yb)/2,(bottom+top)/2),(xb-xa,yb-ya,top-bottom),wall,'Ceiling')
# Front window wall: explicit 12 cm recess and adjacent 35 cm band.
ceiling_zone('front curtain recess roof',0,7.155,0,.12,2.6)
ceiling_zone('front perimeter low band',0,7.155,.12,.47,2.5)
ceiling_zone('north low strip left',0,2.804,2.93,3.4,2.5)
ceiling_zone('vestibule ceiling',2.804,4.02,2.93,4.171,2.5)
ceiling_zone('northeast ceiling',6.573,7.155,2.93,3.4,2.6)
def grille(name,center,length,width,vertical=False):
    # local A is the grille cross-axis; local B is its longitudinal Y direction.
    # depth positive goes inside the housing. All blades follow the long edge.
    x,y,z=center
    def part(suffix,a,b,d,sa,sb,sd,mat,bevel=0):
        if vertical:loc=(x+d,y+b,z+a);size=(sd,sb,sa)
        else:loc=(x+a,y+b,z+d);size=(sa,sb,sd)
        return box(name+suffix,loc,size,mat,'Ceiling',bevel)
    part(' recessed backing',0,0,.018,width,length,.006,dark)
    rail=.008
    for side in [-1,1]:
        part(' long frame '+str(side),side*(width-rail)/2,0,0,rail,length,.012,grillemat,.001)
        part(' end frame '+str(side),0,side*(length-rail)/2,0,width,rail,.012,grillemat,.001)
    for i in range(3):
        a=-width/2+rail+(i+.5)*(width-2*rail)/3
        part(' longitudinal blade '+str(i+1),a,0,-.004,.008,length-2*rail,.01,grillemat,.001)

# Grille openings sit INSIDE the two traced recesses, not on the same underside.
grille('central VERTICAL AC grille',(3.089,1.72,2.745),1.9,.10,vertical=True)
grille('balcony HORIZONTAL AC grille',(6.735,1.70,2.835),2.25,.09)
# Strips sit on top of the traced shelf (H263), behind the H265 concealing lip.
box('LED diffuser central concealed',(3.041,1.72,2.635),(.019,2.25,.006),lightmat,'Ceiling',.001)
box('LED diffuser balcony concealed',(6.449,1.72,2.635),(.019,2.25,.006),lightmat,'Ceiling',.001)
for name,loc,target in [
    ('LED central cove wash',(3.036,1.72,2.642),(2.90,1.72,2.81)),
    ('LED balcony cove wash',(6.452,1.72,2.642),(6.60,1.72,2.81))]:
    strip=area(name,loc,target,8,2.25,(1,.78,.52))
    strip.data.shape='RECTANGLE';strip.data.size=2.25;strip.data.size_y=.025
(OUT/'ceiling-profile-p69.json').write_text(json.dumps({
 'source':'original PDF p69, repaired PDF page9',
 'scale_anchor_cm':105,'scale_anchor_pdf_points':97.163330078125,
 'height_anchor_cm':250,'height_anchor_pdf_y':P69_H250_Y,
 'x_origin_pdf':P69_RIGHT,'profile_pdf_xy':profile_pdf,'profile_model_xz_m':profile_xz,
 'note':'Contour copied from vector drawing; unlabelled details inferred by scale. Horizontal extent now matches the traced 7.155 m inner-face span; closure above visible finish is not structural.'
},ensure_ascii=False,indent=2),encoding='utf-8')

area('daylight balcony',(10,1.7,4.2),(3,1.7,1),1250,5,(.86,.92,1))
area('daylight exterior windows',(3.1,-3,3),(3,2,1),900,4,(.91,.95,1))
area('soft interior bounce',(3.6,1.7,2.43),(3.6,1.7,0),170,3,(1,.94,.84))
area('left high ceiling fill',(1.5,1.7,2.7),(1.5,1.7,0),95,2,(1,.94,.84))
area('review studio light',(2,-1,7),(3.5,1.7,0),700,6,(1,1,1))

c_top=camera('01 TOP plan',(4.15,2.65,12),(4.15,2.65,0),ortho=10.4)
c_over=camera('02 OVERVIEW cutaway',(-4,-7.2,8.2),(3.7,1.8,1.15),ortho=11.8)
c_bed=camera('03 BED toward balcony',(.70,1.0,1.35),(7.6,1.0,1.35),lens=19)
c_entry=camera('04 ENTRY toward bed',(3.45,2.88,1.60),(.8,1.60,1.1),lens=22)
c_rev=camera('05 BALCONY toward bed',(6.94,1.35,1.60),(1.2,1.68,1.1),lens=22)
scene['concept_notes']='p61/p63 fixed partition enclosing bedroom B wardrobe. Bedroom A ensuite included; bathroom heights schematic per p65 unspecified developer ceiling. P69 controls bedroom ceiling. Standard-double platform and parallel wardrobe are new proposals.'
scene['source_pdf_pages']='61,63,65,67,68,69,70,73,74'
scene.camera=c_over
for a in bpy.context.screen.areas:
    if a.type=='VIEW_3D':
        a.spaces.active.region_3d.view_distance=11
        a.spaces.active.region_3d.view_location=(3.7,1.7,1)
        a.spaces.active.region_3d.view_rotation=c_over.rotation_euler.to_quaternion()

def visibility(hidden):
    for name in ['FrontWall','LeftWall','Ceiling']:
        for o in groups[name].objects:o.hide_render=name in hidden

# Save the complete editable model with a cutaway viewport; rendering uses full geometry.
for name in ['Ceiling','FrontWall','LeftWall']:
    for ob in groups[name].objects: ob.hide_set(True)
visibility([])
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'bedroom-a-v1.blend'))
jobs=[(c_top,'01-top.png',['Ceiling']),(c_over,'02-overview.png',['Ceiling','FrontWall','LeftWall']),
      (c_bed,'03-bed-to-balcony.png',[]),(c_entry,'04-entry.png',[]),(c_rev,'05-balcony-to-bed.png',[])]
import os
if os.environ.get('BEDROOM_AUDIT_FAST')=='1':
    jobs=[(c_bed,'03-bed-to-balcony.png',[])]
    scene.cycles.samples=24
    scene.render.resolution_x=1200;scene.render.resolution_y=800
if os.environ.get('BEDROOM_NO_RENDER')=='1':jobs=[]
for cam,filename,hidden in jobs:
    visibility(hidden);scene.camera=cam;scene.render.filepath=str(OUT/filename)
    print('RENDERING',filename,flush=True);bpy.ops.render.render(write_still=True)
visibility([])
print('MODEL_AND_RENDERS_COMPLETE',flush=True)




