"""Static p61/p69 window frames; frame edges stay inside traced openings."""
from pathlib import Path
import json

def build_window_frames(box, frame, glass):
    windows=json.loads((Path(__file__).parent/'dimension-audit.json').read_text(encoding='utf-8'))['windows']
    for d in windows:
        name,xa,xb,sill,head=d['name'],d['x0'],d['x1'],d['sill'],d['head']
        t=d['frame']; mid=(xa+xb)/2; y=-.075
        for side,x in [('left',xa+t/2),('right',xb-t/2)]:
            box(name+' '+side+' jamb',(x,y,(sill+head)/2),(t,.12,head-sill),frame,'FrontWall')
        for side,z in [('bottom',sill+t/2),('top',head-t/2)]:
            box(name+' '+side+' rail',(mid,y,z),(xb-xa,.12,t),frame,'FrontWall')
        if 'transom_bottom' in d:
            a,b=d['transom_bottom'],d['transom_top']
            box(name+' transom',(mid,y,(a+b)/2),(xb-xa,.12,b-a),frame,'FrontWall')
            box(name+' upper mullion',(mid,y,(b+head-t)/2),(t,.12,head-t-b),frame,'FrontWall')
            panes=[('lower single',xa+t,xb-t,sill+t,a),('upper left',xa+t,mid-t/2,b,head-t),('upper right',mid+t/2,xb-t,b,head-t)]
        else:
            panes=[('single',xa+t,xb-t,sill+t,head-t)]
        for label,x0,x1,z0,z1 in panes:
            o=box(name+' '+label+' glass',((x0+x1)/2,y,(z0+z1)/2),(x1-x0,.01,z1-z0),glass,'FrontWall')
            o['dimension_basis']=d['status']
