"""Code-owned, fixed non-professional samples; never accepts user content or files."""
import wave
import zipfile

GENERATOR='v03-fixed-fixtures/1'


def write_fixture(path):
    texts={'.txt':'Synthetic alpha record, no professional conclusion.',
        '.md':'# Synthetic alpha', '.html':'<h1>Synthetic alpha</h1>',
        '.csv':'label,value\nSynthetic alpha,1', '.json':'1'}
    if path.suffix in texts:
        path.write_text(texts[path.suffix],encoding='utf-8')
    elif path.suffix=='.docx':
        with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic alpha</w:t></w:r></w:p></w:body></w:document>')
    elif path.suffix=='.pptx':
        with zipfile.ZipFile(path,'w') as z:z.writestr('ppt/slides/slide1.xml','<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Synthetic alpha</a:t></p:sld>')
    elif path.suffix=='.xlsx':
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" r:id="r1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>Synthetic alpha</t></is></c></row></sheetData></worksheet>')
    elif path.suffix=='.wav':
        with wave.open(str(path),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*80)
    else:raise ValueError('No fixed fixture generator for this format')
