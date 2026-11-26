"""Revision-bound extraction checkpoints and separate human corrections."""
import hashlib
import re

from pipeline import digest, inside
from readiness_media import ParserRegistry, chunk_document, chunk_segments, native, pdf_pages, pdf_parts
from readiness_store import sha256


def read_pdf_chunks(app, source, path, part_id):
    segments = []
    try:
        chunks = list(pdf_parts(path))
    except ImportError:
        from platform_parsers import Unavailable
        raise Unavailable('pypdf is not installed') from None
    for start, end in chunks:
        app.authorize(source['id'],'internal_parse',revision=source['revision'])
        key = digest([part_id,start,end])
        saved = app.record('ParseChunk',key)
        if saved:
            blocks = saved['body']['segments']
        else:
            blocks = pdf_pages(path,start,end)
            app.put('ParseChunk',key,dict(source=source['id'],revision=source['revision'],
                                        first_page=start+1,last_page=end,segments=blocks),'completed')
        segments.extend(blocks)
    return segments


def parse(app, source_id, key, stages=('native',), retry=False, registry=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key) or not stages or len(set(stages)) != len(stages):
        raise ValueError('Invalid extraction key or stages')
    registry = registry or ParserRegistry()
    source = app.authorize(source_id,'internal_parse')
    path = app.path(source['body'])
    routes = {name: registry.route(path.suffix,name) for name in stages}
    for _, permissions in routes.values():
        app.authorize(source_id,'internal_parse',*permissions)
    previous = app.record('ParseRun',key)
    binding = dict(source=source_id, revision=source['revision'], stages=list(stages))
    if previous and any(previous['body'][k] != v for k,v in binding.items()):
        raise ValueError('Extraction key reused with different inputs')
    if previous and previous['state'] == 'failed' and not retry:
        raise ValueError('Explicit extraction retry required')
    state = dict(binding,completed=[]) if not previous else dict(previous['body'])
    location = inside(app.root, app.config['output_directory']) / 'media' / key
    location.mkdir(parents=True,exist_ok=True)
    for name, (adapter, permissions) in routes.items():
        app.authorize(source_id,'internal_parse',*permissions,revision=source['revision'])
        part_id = digest([key,name])
        part = app.record('ParseRevision',part_id)
        if part and part['state'] == 'completed':
            for span in part['body']['segments']:
                if span.get('file'):
                    file = inside(app.root,span['file'])
                    if not file.is_file() or sha256(file) != span['sha256']:
                        raise ValueError('Cached media fingerprint changed')
        else:
            try:
                directory = location / name
                directory.mkdir(exist_ok=True)
                if adapter is native and path.suffix=='.pdf':
                    segments = read_pdf_chunks(app,source,path,part_id)
                elif adapter is chunk_document:
                    native_part = app.record('ParseRevision', digest([key, 'native']))
                    if native_part and native_part['state'] == 'completed':
                        segments = chunk_segments(native_part['body']['segments'])
                    else:
                        segments = adapter(path,source_id,source['revision'],directory)
                else:
                    segments = adapter(path,source_id,source['revision'],directory)
                app.authorize(source_id,'internal_parse',*permissions,revision=source['revision'])
                for i,span in enumerate(segments):
                    span['segment_id'] = f'{part_id[:16]}-{i+1}'
                    span['content_hash'] = hashlib.sha256(span['extracted_content'].encode()).hexdigest()
                    if span.get('file'):
                        artifact = inside(directory, span['file'])
                        span['file'] = artifact.relative_to(app.root).as_posix()
                        span['sha256'] = sha256(artifact)
                app.put('ParseRevision',part_id,dict(binding,stage=name,permissions=list(permissions),segments=segments), 'completed')
            except Exception as exc:
                app.put('ParseRun',key,dict(state,error=type(exc).__name__+': '+str(exc)[:300]),'failed')
                raise
        state.pop('error',None)
        if part_id not in state['completed']:
            state['completed'].append(part_id)
        app.put('ParseRun',key,state,'running' if len(state['completed']) < len(stages) else 'completed')
    return dict(state, id=key, state='completed', parts=[app.record('ParseRevision',id_)['body'] for id_ in state['completed']])


def correct_span(app,id_,ref,content,editor,note):
    if not all(isinstance(v,str) and v.strip() for v in (content,editor,note)):
        raise ValueError('A correction requires content, editor and reason')
    span = app.evidence_span(ref)
    extraction = app.record('ParseRevision',ref['extraction'])
    if ref.get('layer','machine') != 'machine' or not extraction:
        raise ValueError('Corrections require an original machine span')
    row = dict(span, extracted_content=content, extraction_method='human-correction',
               content_hash=hashlib.sha256(content.encode()).hexdigest())
    app.put('SpanCorrection',id_,dict(source=ref['source'],revision=ref['revision'],segments=[row],
                                    machine_ref=ref,editor=editor,note=note,permissions=extraction['body']['permissions']),'completed')
    return dict(ref,extraction=id_,layer='human')


def span_ref(key, part, segment):
    return dict(source=part['source'],revision=part['revision'],extraction=digest([key,part['stage']]),
                segment=segment['segment_id'],locator=segment['locator'])
