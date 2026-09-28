"""Room geometry from dimension-audit.json. Coordinates are INNER faces."""
import json, math
from pathlib import Path

def build_shell(box, slab, wall, floor, frame, glass, joinery, balcony):
    D=json.loads((Path(__file__).parent/'dimension-audit.json').read_text(encoding='utf-8'))
    r=D['room']; L=r['length']; W=r['width']; A=r['vestibule_x0']; B=r['cabinet_x0']
    C=r['cabinet_x1']; N=r['vestibule_y1']; F=r['cabinet_front_y']
    outline=[(0,0),(L,0),(L,W),(C,W),(C,F),(B,F),(B,N),(A,N),(A,W),(0,W)]
    slab('room floor - traced finished boundary',outline,-.15,.15,floor,'Shell')
    def facewall(name,a,b,z0=0,z1=3.15,th=.15,g='Shell',mat=None):
        # Interior on left of segment; thickness outside. 3.15 is hidden closure only.
        dx,dy=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dy)
        o=box(name,((a[0]+b[0])/2+dy/length*th/2,(a[1]+b[1])/2-dx/length*th/2,(z0+z1)/2),
              (length,th,z1-z0),mat or wall,g)
        o.rotation_euler.z=math.atan2(dy,dx)
        o['dimension_basis']='Traced interior face; thickness outside; hidden top not structural'
        return o
    facewall('left headboard wall',(0,W),(0,0),g='LeftWall')
    bath,entry=D['doors']
    facewall('bath wall solid',(bath['x0'],W),(0,W),th=.104)
    facewall('bath wall pier',(A,W),(bath['x1'],W),th=.104)
    facewall('bath door lintel',(bath['x1'],W),(bath['x0'],W),z0=2.4,th=.104)
    facewall('vestibule west bathroom boundary',(A,N),(A,W),th=.104)
    facewall('entry opening lintel',(entry['x1'],N),(A,N),z0=2.4,th=.104)
    facewall('entry right pier',(B,N),(entry['x1'],N),th=.104)
    for name,a,b in [('west side',(B,F),(B,N)),('front',(C,F),(B,F)),('east return',(C,W),(C,F))]:
        obj=facewall('original cabinet boundary - '+name,a,b,z1=2.5,th=.025,g='OriginalJoinery',mat=joinery)
        obj['dimension_basis']='p61 cabinet frontage, NOT verified building wall; backing unresolved; sheet thickness illustrative'
    facewall('northeast edge',(L,W),(C,W))
    for d in D['doors']:
        name=d['name']; xa,xb,y,h,t=d['x0'],d['x1'],d['y'],d['head'],d['frame']
        for side,x in [('left',xa+t/2),('right',xb-t/2)]:
            box(name+' door '+side+' frame',(x,y+.025,h/2),(t,.10,h),frame,'Openings')
        box(name+' door head frame',((xa+xb)/2,y+.025,h-t/2),(xb-xa,.10,t),frame,'Openings')
        door=box(name+' door closed',((xa+xb)/2,y+.025,(h-t+.01)/2),(xb-xa-2*t,.047,h-t-.01),joinery,'Openings',.004)
        door['dimension_basis']=d['status']
        handle=box(name+' door lever',(xb-t-.10,y-.025,1.05),(.12,.045,.02),frame,'Openings',.004)
        handle['dimension_basis']='Hardware and handle height illustrative'
    windows=D['windows']
    for i,(xa,xb) in enumerate([(0,windows[0]['x0']),(windows[0]['x1'],windows[1]['x0']),(windows[1]['x1'],L)]):
        facewall('front wall pier '+str(i),(xa,0),(xb,0),g='FrontWall')
    for d in windows:
        xa,xb=d['x0'],d['x1']
        facewall(d['name']+' sill',(xa,0),(xb,0),z1=d['sill'],g='FrontWall')
        facewall(d['name']+' lintel',(xa,0),(xb,0),z0=d['head'],g='FrontWall')
    door=D['balcony_door']; low,high,t=door['bottom'],door['head'],door['frame']
    facewall('balcony header',(L,0),(L,W),z0=high)
    box('balcony door threshold',(L+.075,W/2,low/2),(.15,W,low),frame,'Openings')
    for label,y in [('left',t/2),('right',W-t/2)]:
        box('balcony '+label+' outer frame',(L+.075,y,(low+high)/2),(.15,t,high-low),frame,'Openings')
    for label,z in [('bottom',low+t/2),('top',high-t/2)]:
        box('balcony '+label+' outer rail',(L+.075,W/2,z),(.15,W,t),frame,'Openings')
    obj=box('balcony glazing - internal division unconfirmed',(L+.075,W/2,(low+high)/2),(.012,W-2*t,high-low-2*t),glass,'Openings')
    obj['dimension_basis']=door['status']
    obj=slab('balcony context floor - level unconfirmed',[(L,0),(r['balcony_outer_x'],0),(r['balcony_outer_x'],W),(L,W)],-.08,.08,balcony,'Balcony')
    obj['dimension_basis']='p61 extent; floor level and railing elevation unconfirmed'
    return D
