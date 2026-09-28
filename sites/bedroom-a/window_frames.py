"""Static window frames: photo + owner's clarification, 2026-09-29.

Opening bounds retain the schematic model dimensions. The wide window's
transom is approximately one third above the sill, not a measured height.
No sliding tracks, overlapping sashes or opening animation are modeled.
"""

def build_window_frames(box, frame, glass):
    for name, xa, xb, sill, head in [
        ('wide window', 1.1, 2.6, .65, 2.15),
        ('narrow window', 4.45, 5.38, .7, 2.35),
    ]:
        mid = (xa + xb) / 2
        thickness = .045
        for side, x in [('left', xa), ('right', xb)]:
            box(name+' '+side+' jamb', (x, 0, (sill+head)/2),
                (thickness, .12, head-sill), frame, 'FrontWall')
        for side, z in [('bottom', sill), ('top', head)]:
            box(name+' '+side+' rail', (mid, 0, z),
                (xb-xa, .12, thickness), frame, 'FrontWall')
        if name == 'wide window':
            transom = sill + (head-sill)/3
            box(name+' transom', (mid, 0, transom),
                (xb-xa, .12, thickness), frame, 'FrontWall')
            box(name+' upper mullion', (mid, 0, (transom+head)/2),
                (thickness, .12, head-transom), frame, 'FrontWall')
            panes = [('lower single', xa, xb, sill, transom),
                     ('upper left', xa, mid, transom, head),
                     ('upper right', mid, xb, transom, head)]
        else:
            panes = [('single', xa, xb, sill, head)]
        for label, x0, x1, z0, z1 in panes:
            box(name+' '+label+' glass', ((x0+x1)/2, 0, (z0+z1)/2),
                (x1-x0-thickness, .01, z1-z0-thickness), glass, 'FrontWall')
