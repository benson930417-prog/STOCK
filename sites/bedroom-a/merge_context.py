from pathlib import Path
import json

def merge_context(data):
    root=Path(__file__).parent
    c=json.loads((root/'context-p63.json').read_text())
    data['pages']=sorted(set(data['pages']+c['source_pages']))
    data['north_boundary']=c['north_boundary']
    data['bathroom']=c['bathroom']
    data['uncertain']=[s for s in data['uncertain'] if not s.startswith('North stepped boundary')]
    note=c['bathroom']['height_status']
    if note not in data['uncertain']:data['uncertain'].append(note)
    data['method']+=' p63 confirms fixed bedroom B wardrobe partition; bathroom footprint traced in the same registered plan coordinates.' if 'p63 confirms' not in data['method'] else ''
    return data

if __name__=='__main__':
    p=Path(__file__).parent/'dimension-audit.json'
    p.write_text(json.dumps(merge_context(json.loads(p.read_text(encoding='utf-8'))),ensure_ascii=False,indent=2),encoding='utf-8')
