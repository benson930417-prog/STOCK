"""New design proposal: standard double bed and wardrobe parallel to aisle."""
import math
def build_platform_proposal(box,material,slab,linen,cloth,frame,lightmat,area):
    import bpy
    wood=material('14 platform natural wood',(.48,.32,.19),.73)
    grey=material('15 warm grey panel',(.52,.54,.51),.82)
    base=material('16 inset plinth',(.22,.24,.21),.82)
    # A framed platform with removable support panels; no decorative bed frame.
    box('platform inset fixed plinth',(1.14,1.0,.055),(2.16,1.52,.11),base,'Furniture')
    box('platform storage head compartment',(.91,1.0,.1775),(1.82,1.68,.135),wood,'Furniture',.008)
    for y in [.16,1.0,1.84]:box('platform foot fixed divider',(2.10,y,.172),(.56,.02,.146),wood,'Furniture')
    box('platform top 238 x 170 H28',(1.19,1.0,.2625),(2.38,1.70,.035),wood,'Furniture',.008)
    # Two visible foot drawers, each with body; no drawer opens toward the wardrobe aisle.
    for idx,(ya,yb) in enumerate([(.19,.975),(1.015,1.8)],1):
        yc=(ya+yb)/2
        parts=[('front',(2.377,yc,.167),(.016,yb-ya,.146)),
               ('bottom',(2.105,yc,.108),(.53,yb-ya-.035,.015)),
               ('side A',(2.105,ya+.016,.169),(.53,.015,.12)),
               ('side B',(2.105,yb-.016,.169),(.53,.015,.12)),
               ('back',(1.843,yc,.169),(.015,yb-ya-.035,.12))]
        for suffix,loc,size in parts:
            o=box(f'foot drawer {idx} {suffix}',loc,size,wood,'Furniture',.004)
            o['drawer_travel']=.55
            o['dimension_basis']='Design proposal: foot drawer opens toward balcony, travel allowance 55 cm; hardware unselected.'
    box('mattress Taiwan standard 152 x 188', (1.04,.96,.39),(1.88,1.52,.22),linen,'Furniture',.075)
    for y in [.58,1.32]:box('pillow',(.43,y,.555),(.48,.60,.12),linen,'Furniture',.055)
    box('folded bed blanket',(1.67,.96,.517),(.45,1.53,.045),cloth,'Furniture',.02)
    # Along the bathroom wall; 20 cm clear before the 75 cm door opening starts.
    box('parallel wardrobe body 170 x 65 H235',(.85,3.075,1.175),(1.70,.65,2.35),grey,'Furniture',.012)
    box('parallel wardrobe sliding leaf A',(.434,2.739,1.18),(.83,.025,2.28),grey,'Furniture',.005)
    box('parallel wardrobe sliding leaf B',(1.27,2.713,1.18),(.85,.025,2.28),grey,'Furniture',.005)
    # Door leaves included in the 65 cm planning envelope, body trimmed accordingly below.
    body=bpy.data.objects['parallel wardrobe body 170 x 65 H235']
    body.location.y=3.10;body.dimensions.y=.60
    for name in ['parallel wardrobe sliding leaf A','parallel wardrobe sliding leaf B']:
        o=bpy.data.objects[name];o.location.y += .05
    # Warm grey head wall with one raised plane and one rising concealed light edge.
    box('head wall grey backdrop',(.0175,1.0,1.35),(.035,1.7,2.10),grey,'Furniture')
    yz=[(.15,.28),(1.85,.28),(1.85,1.56),(1.34,1.19),(.15,1.19)]
    vs=[(x,y,z) for x in [.035,.065] for y,z in yz];n=len(yz)
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new('head panel single angled profile');mesh.from_pydata(vs,[],faces);mesh.update()
    obj=bpy.data.objects.new('head panel single angled profile',mesh);bpy.data.collections['Furniture'].objects.link(obj);obj.data.materials.append(grey)
    for a,b in [((.18,1.20),(1.34,1.20)),((1.34,1.20),(1.82,1.548))]:
        dy=b[0]-a[0];dz=b[1]-a[1]
        o=box('LED diffuser head panel concealed',(.042,(a[0]+b[0])/2,(a[1]+b[1])/2),(.012,math.hypot(dy,dz),.007),lightmat,'Furniture')
        o.rotation_euler.x=math.atan2(dz,dy)
    box('LED diffuser platform foot recessed',(2.27,1.0,.064),(.016,1.50,.008),lightmat,'Furniture')
    box('LED diffuser platform side recessed',(1.13,1.76,.064),(2.16,.016,.008),lightmat,'Furniture')
    lamp=area('headboard wash proposal',(.095,1.05,1.35),(.005,1.05,1.65),3,1.1,(1,.78,.52));lamp.data.shape='RECTANGLE';lamp.data.size_y=.03
    for o in bpy.data.collections['Furniture'].objects:
        if not o.get('dimension_basis'):o['dimension_basis']='New proposal after 28-reference review; 152 x 188 mattress, parallel 170 cm wardrobe, not existing construction.'
