"""Read-only extraction of selected, visually identified drawing coordinates.
No nominal PDF paper scale is assumed. Source PDFs are never rewritten.
"""
from pathlib import Path
import sys, json
sys.path.insert(0, str(Path('work/python_deps').resolve()))
import pymupdf as fitz

root = Path(__file__).resolve().parent
pdf_path = Path(r'C:/Users/benso/Downloads/1150905順天吳宅_中文修正版.pdf')
pdf = fitz.open(pdf_path)
s69 = (833.0501098632812 - 735.8867797851562) / 1.05
s61 = (354.8678894042969 - 271.5850830078125) / 1.5
x0, y0 = 212.17666625976562, 520.6893920898438
X = lambda x: round((x-x0)/s61, 4)
Y = lambda y: round((y0-y)/s61, 4)
Z = lambda y: round((641.9121704101562-y)/s69, 4)

# Check cited coordinates really occur in the source drawing paths.
for page, expected in [(69, [(833.05011,299.06451),(735.88678,299.06451),
                            (595.23132,429.07828),(336.12921,595.64392)]),
                       (61, [(212.17667,520.68939),(609.43573,331.91498),
                             (367.86002,289.10699),(423.38190,289.10699)])]:
    points=[]
    for d in pdf[page-1].get_drawings():
        for item in d['items']:
            for p in item[1:]:
                if isinstance(p,fitz.Point): points.append(p)
    for x,y in expected:
        assert any(abs(p.x-x)<.003 and abs(p.y-y)<.003 for p in points),(page,x,y)

data = {
 'source':str(pdf_path),'pages':[61,64,65,67,68,69,70],
 'method':'p69 explicit 105 cm anchors horizontal scale; explicit 250 cm checks vertical scale. p61 is independently transferred through the same 150 cm window opening, cross-checked with the 93 cm opening.',
 'scale_points_per_m':{'p69':s69,'p61':s61},
 'origin':'X=original wardrobe/headboard inner wall; Y=window wall inner face; Z=finished room floor.',
 'room':{'length':X(609.4357299804688),'width':Y(331.91497802734375),
         'vestibule_x0':X(367.8600158691406),'vestibule_y1':Y(289.10699462890625),
         'cabinet_x0':X(435.3750305175781),'cabinet_x1':X(577.1220703125),
         'cabinet_front_y':Y(358.0102844238281),'balcony_outer_x':X(693.5513916015625)},
 'windows':[
  {'name':'wide window','x0':1.07,'x1':2.57,'sill':Z(595.6439208984375),'head':Z(429.0782775878906),
   'transom_bottom':Z(549.375732421875),'transom_top':Z(544.7489013671875),'frame':.05,
   'status':'p69 traced dimensions; upper pair / lower single confirmed by owner; overall opening width checked in p61 and p69 plan.'},
  {'name':'narrow window','x0':4.44,'x1':5.37,'sill':Z(595.6439208984375),'head':2.5,'frame':.05,
   'status':'p69 visible portion ends at H250 ceiling; true concealed head height is not given. Model shows visible opening only.'}],
 'doors':[
  {'name':'bathroom','x0':X(317.6682434082031),'x1':X(359.3096618652344),'y':Y(331.91497802734375),
   'head':2.4,'frame':.05,'status':'p61 75 cm plan opening; p67 75 cm outer frame and 240 cm head agree.'},
  {'name':'entrance','x0':X(367.8600158691406),'x1':X(423.38189697265625),'y':Y(289.10699462890625),
   'head':2.4,'frame':.03,'status':'CONFLICT: p61 plan and p67 S1 show 100 cm; p68 S2 shows 90 cm. Use p61 footprint temporarily; do not claim site confirmation.'}],
 'balcony_door':{'width':3.4,'bottom':.07,'head':2.4,'frame':.05,'depth':.15,
                 'status':'p61 plan width; p67/p69 side section H7 to H240. Internal sash division not established; outer frame only.'},
 'ceiling':{'low':2.5,'main':2.6,'high':2.8,'shelf_top':2.63,'lip_top':2.65,
            'window_recess_width':.12,'window_low_band_width':.35,
            'status':'p69 authoritative traced profile, p65/p68 transverse 12+35 cm band. Grille/LED longitudinal lengths and manufactured profiles are illustrative.'},
 'uncertain':[
  'North stepped boundary follows original cabinet frontage, NOT a verified building wall. Kept as a separate original-cabinet volume/datum pending removal/redesign decisions.',
  'Entrance width disagreement 100 vs 90 cm; source dates p67 older than p61/p68/p69.',
  'Narrow window concealed head, window installation tolerances, exact sash/track sections.',
  'Balcony railing height/material and floor level difference lack usable elevation dimensions; fabricated parapets removed, floor shown as a context plane.',
  'Bare slab/structural beam heights, pipe dimensions and positions cannot be recovered from the finished-ceiling section/photo alone.',
  'Outlet mounting heights and hardware dimensions unknown; markers are plan projections only.',
  'New bed, desk, wardrobe, chair and side tables are design proposal dimensions, not measured existing furniture.',
  'Wall/ceiling closure above visible finished surfaces is a modeling envelope, not a structural section.'
 ]
}
assert abs((641.9121704101562-410.57098388671875)/s69-2.5)<.00001
assert abs((510.3291931152344-458.6938171386719)/s61-.93)<.00001
import runpy
data=runpy.run_path(str(root/'merge_context.py'))['merge_context'](data)
(root/'dimension-audit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(data,ensure_ascii=False,indent=2))
