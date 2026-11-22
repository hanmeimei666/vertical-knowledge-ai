"""Local parsers. No network, macros, embedded-code execution or model download."""
import csv
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path
import re
import wave
import xml.etree.ElementTree as ET
import zipfile

from pipeline import parse_native


class Unavailable(ValueError):
    pass


def probe(suffix):
    if suffix == '.pdf' and not importlib.util.find_spec('pypdf'):
        return 'unavailable_dependency', 'pypdf not installed'
    if suffix in ('.png', '.jpg', '.jpeg') and not importlib.util.find_spec('PIL'):
        return 'unavailable_dependency', 'Pillow not installed'
    return 'available', 'local native parser'


def parse_file(path, source, revision):
    path = Path(path)
    suffix = path.suffix.lower()
    state, reason = probe(suffix)
    if state != 'available':
        raise Unavailable(reason)
    blocks = []
    def add(content, locator, modality='text', metadata=None):
        blocks.append({'source_asset_id': source, 'source_revision': revision,
            'segment_id': source + '-' + str(len(blocks)+1), 'modality': modality,
            'content_type': 'metadata' if modality in ('image','audio') else 'text',
            'extracted_content': content, 'locator': locator, 'extraction_method': 'local-native',
            'extraction_version': '0.3', 'language': 'und', 'confidence': None,
            'metadata': metadata or {}, 'content_hash': hashlib.sha256(content.encode()).hexdigest(),
            'review_state': 'candidate'})
    if path.stat().st_size > 50_000_000:
        raise ValueError('File exceeds 50MB local limit')
    if suffix in ('.txt','.md','.docx'):
        for b in parse_native(path):
            add(b['text'], {'type':'document_block', 'coordinate':b['locator']})
    elif suffix in ('.html','.htm'):
        class Reader(HTMLParser):
            hidden = 0
            def handle_starttag(self, tag, attrs):
                if tag in ('script','style'): self.hidden += 1
            def handle_endtag(self, tag):
                if tag in ('script','style'): self.hidden = max(0,self.hidden-1)
            def handle_data(self, text):
                if text.strip() and not self.hidden:
                    add(text.strip(), {'type':'html_position','line':self.getpos()[0], 'column':self.getpos()[1]})
        Reader().feed(path.read_text(encoding='utf-8-sig'))
    elif suffix == '.csv':
        for row, cells in enumerate(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))),1):
            for col, text in enumerate(cells,1):
                add(text, {'type':'table','row':row,'column':col})
    elif suffix == '.json':
        def walk(value, pointer=''):
            if isinstance(value, dict):
                for key, child in value.items(): walk(child,pointer+'/'+str(key).replace('~','~0').replace('/','~1'))
            elif isinstance(value,list):
                for i, child in enumerate(value): walk(child,pointer+'/'+str(i))
            else: add(json.dumps(value,ensure_ascii=False), {'type':'json_pointer','pointer':pointer})
        walk(json.loads(path.read_text(encoding='utf-8-sig')))
    elif suffix in ('.pptx','.xlsx'):
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 50_000_000:
                raise ValueError('Office XML exceeds limit')
            if suffix == '.pptx':
                for name in sorted((n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)),key=lambda n:int(re.search(r'(\d+)\.xml',n)[1])):
                    slide = int(re.search(r'(\d+)\.xml',name)[1])
                    tree = ET.fromstring(z.read(name))
                    for i, node in enumerate(tree.iter(),1):
                        if node.tag.endswith('}t') and node.text:
                            add(node.text, {'type':'slide','slide':slide,'xml_element':i},metadata={'part':name})
                    for node in tree.iter():
                        if node.tag.endswith('}blip'):
                            add(json.dumps(node.attrib), {'type':'slide','slide':slide,'missing_bbox':True},metadata={'image_relationship':node.attrib})
            else:
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                strings=[]
                if 'xl/sharedStrings.xml' in z.namelist():
                    strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
                book=ET.fromstring(z.read('xl/workbook.xml'))
                rels={e.attrib['Id']:e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
                for sheet in book.findall('s:sheets/s:sheet',ns):
                    rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
                    target=rels[rid]; name=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                    for cell in ET.fromstring(z.read(name)).findall('.//s:c',ns):
                        value=cell.find('s:v',ns)
                        text=value.text if value is not None else ''.join(cell.find('s:is',ns).itertext()) if cell.find('s:is',ns) is not None else ''
                        if cell.get('t')=='s': text=strings[int(text)]
                        add(text or '',{'type':'spreadsheet','sheet':sheet.attrib['name'],'cell':cell.attrib['r']}, metadata={'formula':cell.findtext('s:f',default='',namespaces=ns),'formula_evaluated':False})
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader=PdfReader(path)
        for i,page in enumerate(reader.pages,1):
            text=page.extract_text() or ''
            add(text,{'type':'page','page':i,'bbox_missing':True},metadata={'ocr_required':not bool(text.strip()),'layout_quality':'unverified'})
    elif suffix in ('.png','.jpg','.jpeg'):
        from PIL import Image
        with Image.open(path) as im:
            add(json.dumps({'width':im.width,'height':im.height,'format':im.format}),{'type':'image','bbox':[0,0,im.width,im.height]},'image',{'ocr':'unavailable','semantic_description':'not_implemented'})
    elif suffix == '.wav':
        with wave.open(str(path),'rb') as wav:
            duration=round(1000*wav.getnframes()/wav.getframerate())
            add(json.dumps({'channels':wav.getnchannels(),'sample_rate':wav.getframerate(),'duration_ms':duration}),{'type':'audio','start_ms':0,'end_ms':duration,'track':0},'audio',{'asr':'unavailable'})
    else:
        raise Unavailable('No installed parser for ' + suffix)
    return {'segments':blocks, 'status':'candidate', 'parser_version':'0.3'}
