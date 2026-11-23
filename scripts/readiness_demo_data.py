"""Code-generated dataset, benchmark queries and the three course templates."""
from pathlib import Path
import shutil

from pipeline import ROOT, load
from readiness_agent import produce
from readiness_media import ffmpeg, Unavailable
from readiness_parse import parse, span_ref
from readiness_retrieval import search
from readiness_store import Readiness, sha256

EXPIRY = '2099-01-01T00:00:00+00:00'
GRANT = dict(reviewer='synthetic-operator',proof='synthetic-permission-fixture-v1',expires_at=EXPIRY)
DOMAINS = ('conditioning','rehabilitation','football')
RECORDS = [
    ('gold','conditioning','educator','youth','classroom'),
    ('green','rehabilitation','educator','youth','lab'),
    ('blue','football','coach','youth','field'),
    ('red','conditioning','educator','adult','classroom'),
    ('shared','all','all','youth','classroom'),
    ('denied','football','coach','youth','field'),
    ('revoked','football','coach','youth','field'),
    ('expired','football','coach','youth','field'),
    ('conflict-upper','conditioning','educator','youth','classroom'),
    ('conflict-lower','conditioning','educator','youth','classroom'),
]


def isolate(root):
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=False)
    (root/'config').mkdir()
    for name in ('readiness.json','domain_rules.json'):
        shutil.copyfile(ROOT/'config'/name,root/'config'/name)
    return root


def grant(app, source, scoped=(), state='admitted'):
    return app.record_grant(source,**GRANT,scopes={k:True for k in ('internal_parse',*scoped)},state=state)


def seed(app, optional=False):
    inputs = []
    for id_,domain,learner,population,scenario in RECORDS:
        source = 'source-'+id_
        title = 'Synthetic conflict witness ' + id_ if id_.startswith('conflict') else f'Synthetic {id_} sequence'
        entry = app.generate_source(source,'.txt',title)
        grant(app,source,('retrieve','adapt'))
        sr = parse(app,source,'parse-'+id_)
        first = sr['parts'][0]
        inputs.append(dict(entry['body'],id=source,extraction=sr['id']))
        unit = {'id':id_, 'title':title,'statement':'Structure demonstration only. Synthetic material.',
                'facets':{'domain':list(DOMAINS) if domain=='all' else [domain],
                          'learner':['educator','coach'] if learner=='all' else [learner],
                          'training_population':[population],'scenario':[scenario],'ability':['fixture-basic']},
                'references':[span_ref(sr['id'],first,first['segments'][0])],
                'objectives':['Trace a synthetic evidence span'],'prerequisites':[],
                'risks':['Synthetic data'] }
        if id_.startswith('conflict'):
            unit.update(claim_key='ordering',claim_value=id_.split('-')[1])
        if id_=='expired':
            unit['valid_until']='2000-01-01T00:00:00+00:00'
        app.save_unit(unit)
        app.review_unit(id_,'synthetic-operator','approved','Structural fixtures only')
        if id_=='revoked':
            grant(app,source,(),state='revoked')
        elif id_=='denied':
            grant(app,source,())
    app.relate('gold','related_to','shared')
    app.relate('gold','related_to','blue')
    app.relate('conflict-upper','contradicts','conflict-lower')
    for domain in DOMAINS:
        track = load(ROOT/'config/course_tracks.json')[domain]
        app.put('ReadinessTrainingUnit','training-'+domain,
                dict(domain=domain,objectives=['Navigate synthetic evidence'],knowledge=['shared'],
                     learner=track['learner'],training_population=track['training_population']), 'template')
    suffixes = ['.txt','.md','.html','.csv','.json','.docx','.pptx','.xlsx','.wav']
    if optional:
        suffixes += ['.pdf','.png','.jpg','.mp3','.m4a','.mp4','.mov']
    parsed = []
    for ext in suffixes:
        id_ = 'media-'+ext[1:]
        source = app.generate_source(id_,ext,'Synthetic parser mix ' + ext)
        scopes = ('retrieve','adapt','ocr','asr','video_processing')
        grant(app,id_,scopes)
        stages = ['native']
        if ext in {'.png','.jpg'}:
            stages += ['ocr']
        if ext in {'.wav','.mp3','.m4a','.mp4','.mov'}:
            stages += ['asr']
        if ext in {'.mp4','.mov'}:
            stages += ['tracks']
        extracted = parse(app,id_,'extract-'+ext[1:],stages)
        row = dict(id=id_,path=source['body']['path'],sha256=source['body']['sha256'],
                   result=extracted)
        parsed.append(row)
        if ext in {'.mp4','.mov'}:
            duration = extracted['parts'][0]['segments'][0]['locator']['end_ms']
            app.put('ReadinessMediaAsset',id_,dict(source=id_,revision=source['revision'],
                    duration_ms=duration,extraction=extracted['id']),'available')
    return {'inputs':inputs,'parsed':parsed}


def all_queries():
    cases = []
    def add(group,q,want,**kwargs):
        cases.append(dict(group=group,query=q,expected=want,parameters=kwargs))
    for term,domain,learner,scenario,alias in [
        ('gold','conditioning','educator','classroom','amber'),
        ('green','rehabilitation','educator','lab','emerald'),
        ('blue','football','coach','field','azure'),
        ('red','conditioning','educator','classroom','crimson')]:
        population = 'adult' if term=='red' else 'youth'
        add('lexical',term,[term])
        add('multi_condition',term,[term],filters={'domain':[domain],'learner':[learner],
             'training_population':[population],'scenario':[scenario]})
        add('synonym',alias,[term],method='semantic',filters={'domain':[domain],'training_population':[population]})
        add('hybrid',term+' '+alias,[term],method='hybrid',filters={'domain':[domain]})
        add('inapplicable',term,[],filters={'training_population':['youth' if term=='red' else 'adult']})
        add('wrong_learner',term,[],filters={'learner':['coach' if learner=='educator' else 'educator']})
    add('cross_domain','gold',['gold','shared','blue'],expand=True)
    add('relationship_filtered','gold',['gold','shared'],filters={'domain':['conditioning']},expand=True)
    add('conflict','conflict',['conflict-upper','conflict-lower'])
    add('denied','denied',[])
    add('revoked','revoked',[])
    add('expired','expired',[])
    add('no_answer','zpxqk',[])
    add('no_answer','never-recorded-098711',[])
    add('version','blue',[],versions={'blue':1})
    add('version','blue',['blue'],versions={'blue':2})
    add('domain_or','blue',['blue'],filters={'domain':['rehabilitation'],'scenario':['field']},mode='OR')
    add('domain_and','blue',[],filters={'domain':['rehabilitation'],'scenario':['field']},mode='AND')
    return cases


def evaluate(app,k=5):
    rows = []
    recalls, hits, first = [], [], []
    leaks = {'denied','revoked','expired'}
    for item in all_queries():
        pack = search(app,item['query'],limit=k,**item['parameters'])
        actual = [e['knowledge']['id'] for e in pack['entries']]
        expected = set(item['expected'])
        if expected:
            recalls.append(len(set(actual)&expected)/len(expected))
            rank = next((i for i,v in enumerate(actual,1) if v in expected),None)
            hits.append(int(rank is not None))
            first.append(1/rank if rank else 0)
        rows.append(dict(item,actual=actual,unexpected=sorted(set(actual)-expected),
                         leak=sorted(leaks&set(actual)),conflict_detected=bool(pack['conflicts'])))
    return dict(k=k,queries=len(rows),recall=sum(recalls)/len(recalls),hit=sum(hits)/len(hits),
                mrr=sum(first)/len(first),unauthorized=sum(len(r['leak']) for r in rows),
                false_retrievals=sum(len(r['unexpected']) for r in rows),
                answerless_false_hits=sum(bool(r['actual']) and not r['expected'] for r in rows),rows=rows)


def demo_run(root,optional=False):
    with Readiness(root) as app:
        data = seed(app,optional)
        metrics = evaluate(app)
        runs = []
        for track in DOMAINS:
            request = {'track':track,'query':'shared'}
            if track=='football' and optional:
                request['timeline']=[{'asset':'media-mp4','start_ms':0,'end_ms':320},
                                     {'asset':'media-mov','start_ms':160,'end_ms':560}]
            runs.append(produce(app,request,'demo-'+track))
        report = dict(synthetic=True,root=str(root),inputs=data,benchmark=metrics,
                      runs=runs,status='PASS' if all(r['state']=='draft_ready' for r in runs) and not metrics['unauthorized'] else 'FAIL')
    return report
