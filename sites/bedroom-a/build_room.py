"""Bedroom A v1. Run with Blender 4.5: blender -b --python build_room.py
Concept geometry, meters. Source: PDF pp.61,65,67-70; dimensions approximate.
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
for name in ['Shell','FrontWall','LeftWall','Ceiling','Openings','Furniture','Balcony','Lights','Cameras']:
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

# Main outline estimated by calibrating p.61 proportions against readable p.67 dimensions.
outline=[(0,0),(7.15,0),(7.15,3.4),(6.55,3.4),(6.55,2.95),(4.03,2.95),(4.03,4.25),(2.82,4.25),(2.82,3.4),(0,3.4)]
slab('room floor, approximate outline',outline,-.15,.15,floor,'Shell')
wallseg('left headboard wall',(0,0),(0,3.4),g='LeftWall')
wallseg('bath wall solid',(0,3.4),(1.86,3.4))
wallseg('bath wall pier',(2.7,3.4),(2.82,3.4))
wallseg('bath door lintel',(1.86,3.4),(2.7,3.4),.85,base=2.2)
box('bathroom door placeholder',(2.28,3.42,1.07),(.80,.055,2.14),joinery,'Openings',.012)
box('bathroom door lever',(2.55,3.365,1.02),(.14,.055,.025),frame,'Openings',.01)
# PDF p61: entrance is in the north end of the vestibule, hinged at its west jamb.
wallseg('vestibule west bathroom boundary',(2.82,3.4),(2.82,4.25))
wallseg('entry opening lintel',(2.82,4.25),(3.80,4.25),.85,base=2.2)
wallseg('entry right pier',(3.80,4.25),(4.03,4.25))
box('room entrance door closed',(3.31,4.25,1.07),(.94,.055,2.14),joinery,'Openings',.012)
box('room entrance door lever',(3.65,4.195,1.02),(.14,.055,.025),frame,'Openings',.01)
wallseg('neighbor boundary',(4.03,4.25),(4.03,2.95))
wallseg('north stepped boundary',(4.03,2.95),(6.55,2.95))
wallseg('north short return',(6.55,2.95),(6.55,3.4))
wallseg('northeast edge',(6.55,3.4),(7.15,3.4))

# Two exterior windows, positions from plan; vertical sizes from elevation proportions only.
for i,(xa,xb) in enumerate([(0,1.1),(2.6,4.45),(5.38,7.15)]):wallseg('front wall pier '+str(i),(xa,0),(xb,0),g='FrontWall')
for name,xa,xb,sill,head in [('wide window',1.1,2.6,.65,2.15),('narrow window',4.45,5.38,.7,2.35)]:
    wallseg(name+' sill',(xa,0),(xb,0),sill,g='FrontWall')
    wallseg(name+' lintel',(xa,0),(xb,0),3.05-head,g='FrontWall',base=head)
    for x in [xa,xb,(xa+xb)/2]:box(name+' jamb',(x,0,(sill+head)/2),(.045,.12,head-sill),frame,'FrontWall')
    for z in [sill,head]:box(name+' rail',((xa+xb)/2,0,z),(xb-xa,.12,.045),frame,'FrontWall')
    box(name+' glass',((xa+xb)/2,0,(sill+head)/2),(xb-xa-.05,.01,head-sill-.05),glass,'FrontWall')

# Balcony opening: retain full-height glazed boundary as a schematic sliding assembly.
wallseg('balcony header',(7.15,0),(7.15,3.4),.65,base=2.4)
for y in [0,1.13,2.26,3.4]:box('balcony vertical frame',(7.15,y,1.2),(.1,.045,2.4),frame,'Openings')
for z in [.035,2.4]:box('balcony horizontal frame',(7.15,1.7,z),(.1,3.4,.05),frame,'Openings')
for y in [.565,1.695,2.83]:box('balcony glass',(7.15,y,1.22),(.012,1.085,2.3),glass,'Openings')
slab('balcony floor',[(7.15,0),(8.65,0),(8.65,3.4),(7.15,3.4)],-.08,.08,balcony,'Balcony')
for y in [0,3.4]:wallseg('balcony side return',(7.2,y),(8.65,y),1.1,g='Balcony')
box('balcony parapet',(8.65,1.7,.55),(.14,3.4,1.1),wall,'Balcony')
for x in [7.4,7.8,8.2]:box('balcony floor joint',(x,1.7,.004),(.006,3.35,.004),dark,'Balcony')

# Bed axis +X, head at original left wall. Mattress is an assumed 1.8 x 2.0 m.
box('upholstered bed base',(1.23,1.68,.22),(2.15,1.92,.32),cloth,'Furniture',.075)
box('mattress 180 x 200',(1.23,1.68,.48),(2,1.8,.28),linen,'Furniture',.11)
box('headboard left wall',(.11,1.68,.71),(.18,2.12,1.32),cloth,'Furniture',.065)
for y in [1.23,2.13]:box('pillow',(.54,y,.69),(.57,.72,.17),linen,'Furniture',.08)
box('folded neutral blanket',(1.88,1.68,.645),(.55,1.81,.08),joinery,'Furniture',.03)
for y in [.40,2.98]:
    box('bedside table',(.46,y,.245),(.56,.44,.49),joinery,'Furniture',.025)
    box('bedside tray',(.46,y,.505),(.38,.28,.025),wall,'Furniture',.02)

# Optional furniture blocks. They are proposals, not inherited fixed joinery.
box('proposed wardrobe north',(5.25,2.625,1.16),(2.34,.60,2.32),joinery,'Furniture',.025)
for x in [4.67,5.25,5.83]:box('wardrobe door reveal',(x,2.316,1.16),(.009,.006,2.24),dark,'Furniture')
box('desk proposal',(5.92,.40,.74),(1.55,.65,.055),joinery,'Furniture',.02)
for x in [5.24,6.60]:box('desk support',(x,.40,.36),(.055,.54,.70),frame,'Furniture',.01)
box('chair seat',(5.92,1.01,.46),(.5,.47,.08),cloth,'Furniture',.06)
box('chair back',(5.92,1.24,.76),(.5,.06,.53),cloth,'Furniture',.05)
for x in [5.72,6.12]:
    for y in [.85,1.17]:box('chair leg',(x,y,.23),(.032,.032,.43),frame,'Furniture',.007)

# Visible S3 ceiling contour traced from repaired vector PDF p69.
# Scale uses explicit 105 cm dimension; Z uses explicit H250 plus 20 / 10 cm steps.
# X is reversed because the drawing shows the balcony on the left.
P69_SCALE=97.163/1.05
P69_RIGHT=833.050
P69_H250_Y=410.571
profile_pdf=[
 (833.050,350.422),(833.050,410.571),(735.887,410.571),(735.887,382.810),
 (550.444,382.810),(544.198,382.810),(544.198,392.064),(550.444,392.064),
 (550.444,398.541),(572.652,398.541),(572.652,396.691),(573.578,396.691),
 (573.578,401.317),(228.324,401.317),(228.324,396.691),(229.250,396.691),
 (229.250,398.541),(237.578,398.541),(237.578,382.810),(214.444,382.810),
 (214.444,376.564),(205.190,376.564),(205.190,410.571),(182.056,410.571),
 (182.056,401.317),(170.952,401.317),(170.952,350.422)]
profile_xz=[(max(0,min(7.15,(P69_RIGHT-x)/P69_SCALE)),2.5+(P69_H250_Y-y)/P69_SCALE) for x,y in profile_pdf]
def extruded_ceiling_profile(name,profile,y0,y1):
    import bmesh
    n=len(profile);verts=[(x,y0,z) for x,z in profile]+[(x,y1,z) for x,z in profile]
    faces=[tuple(range(n)),tuple(reversed(range(n,2*n)))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    bm=bmesh.new();bm.from_mesh(mesh);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
    o=bpy.data.objects.new(name,mesh);groups['Ceiling'].objects.link(o);o.data.materials.append(wall);return o
extruded_ceiling_profile('P69 traced ceiling including projecting shelves and lips',profile_xz,.47,2.95)
def ceiling_zone(name,xa,xb,ya,yb,bottom):
    top=3.15
    return box(name,((xa+xb)/2,(ya+yb)/2,(bottom+top)/2),(xb-xa,yb-ya,top-bottom),wall,'Ceiling')
# Front window wall: explicit 12 cm recess and adjacent 35 cm band.
ceiling_zone('front curtain recess roof',0,7.15,0,.12,2.6)
ceiling_zone('front perimeter low band',0,7.15,.12,.47,2.5)
ceiling_zone('north low strip left',0,2.82,2.95,3.4,2.5)
ceiling_zone('vestibule ceiling',2.82,4.03,2.95,4.25,2.5)
ceiling_zone('northeast ceiling',6.55,7.15,2.95,3.4,2.6)
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
 'scale_anchor_cm':105,'scale_anchor_pdf_points':97.163,
 'height_anchor_cm':250,'height_anchor_pdf_y':P69_H250_Y,
 'x_origin_pdf':P69_RIGHT,'profile_pdf_xy':profile_pdf,'profile_model_xz_m':profile_xz,
 'note':'Contour copied from vector drawing; unlabelled details inferred by scale. The last wall coordinate is clipped by 5 mm to match existing schematic room width.'
},ensure_ascii=False,indent=2),encoding='utf-8')

area('daylight balcony',(10,1.7,4.2),(3,1.7,1),1250,5,(.86,.92,1))
area('daylight exterior windows',(3.1,-3,3),(3,2,1),900,4,(.91,.95,1))
area('soft interior bounce',(3.6,1.7,2.43),(3.6,1.7,0),170,3,(1,.94,.84))
area('left high ceiling fill',(1.5,1.7,2.7),(1.5,1.7,0),95,2,(1,.94,.84))
area('review studio light',(2,-1,7),(3.5,1.7,0),700,6,(1,1,1))

c_top=camera('01 TOP plan',(4.15,1.9,12),(4.15,1.9,0),ortho=10.1)
c_over=camera('02 OVERVIEW cutaway',(-4,-7.2,8.2),(3.7,1.8,1.15),ortho=11.8)
c_bed=camera('03 BED toward balcony',(.70,1.7,1.35),(7.6,1.70,1.35),lens=19)
c_entry=camera('04 ENTRY toward bed',(3.45,2.88,1.60),(.8,1.60,1.1),lens=22)
c_rev=camera('05 BALCONY toward bed',(6.94,1.35,1.60),(1.2,1.68,1.1),lens=22)
scene['concept_notes']='Estimated geometry, not a measured survey. Original left cabinetry removed. Bed head left, feet toward balcony. Ceiling scheme chosen from PDF p65. New desk and wardrobe are movable proposals.'
scene['source_pdf_pages']='61,65,67,68,69,70'
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
for cam,filename,hidden in jobs:
    visibility(hidden);scene.camera=cam;scene.render.filepath=str(OUT/filename)
    print('RENDERING',filename,flush=True);bpy.ops.render.render(write_still=True)
visibility([])
print('MODEL_AND_RENDERS_COMPLETE',flush=True)




