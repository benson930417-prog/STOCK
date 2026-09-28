"""Bedroom A ensuite. Source plan geometry, explicitly schematic verticals."""
import json
from pathlib import Path

def build_bathroom(box,slab,material,wall,floor,frame,glass):
    import bpy
    d=json.loads((Path(__file__).parent/'context-p63.json').read_text())['bathroom']
    white=material('12 plain bathroom ceramic',(.82,.82,.78),.34)
    grey=material('13 plain bathroom floor',(.57,.58,.55),.85)
    h=d['illustrative_heights']['ceiling']
    def item(name,loc,size,mat=wall,g='Bathroom',bevel=0):
        o=box(name,loc,size,mat,g,bevel)
        o['dimension_basis']='p61/p63 plan; vertical dimensions schematic. '+d['height_status']
        return o
    slab('bathroom A floor',[(0,3.504),(2.7,3.504),(2.7,5.3),(.97,5.3),(.97,4.9),(0,4.9)],-.12,.12,grey,'Bathroom')
    # Bedroom-facing south wall and east vestibule boundary already exist in Shell.
    item('bathroom A north wall',(1.35,5.35,h/2),(2.7,.10,h))
    item('bathroom A east upper wall',(2.752,4.7355,h/2),(.104,1.129,h))
    item('bathroom A northwest service enclosure',(.485,5.1,h/2),(.97,.4,h))
    item('bathroom A west lower pier',(-.075,3.902,h/2),(.15,.796,h))
    item('bathroom A west sill',(-.075,4.6,.6),(.15,.6,1.2))
    item('bathroom A west lintel',(-.075,4.6,(2.2+h)/2),(.15,.6,h-2.2))
    item('bathroom A west upper pier',(-.075,5.1,h/2),(.15,.4,h))
    item('bathroom A ceiling SCHEMATIC height',(1.35,4.402,h+.04),(2.7,1.796,.08),wall,'Ceiling')
    for y in [4.325,4.875]:item('bathroom A window jamb',(-.06,y,1.7),(.10,.05,1),frame)
    for z in [1.225,2.175]:item('bathroom A window rail',(-.06,4.6,z),(.10,.55,.05),frame)
    item('bathroom A window glass',(-.06,4.6,1.7),(.012,.5,.90),glass)
    # Closed shower door plus fixed screen; no decorative tile scheme.
    item('bathroom A shower fixed screen',(.97,4.55,1.025),(.012,.7,2.05),glass)
    item('bathroom A shower closed glass door',(.97,3.852,1.025),(.012,.69,2.05),glass)
    item('bathroom A shower upper rail',(.97,4.202,2.05),(.027,1.396,.027),frame)
    item('bathroom A shower handle',(1.0,3.62,1.02),(.035,.035,.22),frame,bevel=.012)
    item('bathroom A shower fitting schematic',(.48,4.87,1.05),(.18,.035,.08),frame,bevel=.01)
    item('bathroom A shower rail schematic',(.48,4.865,1.52),(.023,.025,.92),frame,bevel=.008)
    item('bathroom A shower head schematic',(.48,4.77,1.94),(.13,.16,.024),frame,bevel=.02)
    item('bathroom A vanity plain support',(2.24,4.97,.405),(.88,.58,.77),white,bevel=.025)
    item('bathroom A vanity rim',(2.24,4.97,.8),(.9,.6,.04),white,bevel=.03)
    item('bathroom A basin inset',(2.24,4.96,.825),(.56,.35,.016),grey,bevel=.10)
    item('bathroom A basin tap',(2.24,5.2,.93),(.032,.04,.21),frame,bevel=.01)
    item('bathroom A toilet base',(1.375,4.91,.17),(.29,.53,.34),white,bevel=.10)
    item('bathroom A toilet bowl',(1.375,4.88,.35),(.39,.65,.18),white,bevel=.16)
    item('bathroom A toilet seat opening',(1.375,4.83,.446),(.23,.35,.008),grey,bevel=.10)
    item('bathroom A toilet cistern',(1.375,5.16,.57),(.39,.18,.35),white,bevel=.045)
    return d
