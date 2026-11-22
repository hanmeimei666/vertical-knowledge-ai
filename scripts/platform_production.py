"""Recoverable deterministic single workflow; no live model or auto approval."""
import uuid
from pipeline import Pipeline, encode, digest, now
from platform_contracts import contract
from platform_tools import definition
from platform_models import ExtractivePlanner


def export_draft(payload):
    with Pipeline(payload['root'],payload['config']) as renderer:
        return {'files':renderer.render(payload['run'],'draft')}


def register_exporter(app):
    app.tools.register_adapter('export_draft',export_draft)
    d=definition('export.draft','export_draft','markdown_html_json',['adapt'])
    d['input_schema']={'type':'object','required':['root','config','run'],'properties':{'root':{'type':'string'},'config':{'type':'string'},'run':{'type':'object'}},'additionalProperties':False}
    d['output_schema']={'type':'object','required':['files']}
    app.tools.register(d)


def produce(app,request,key,retry=False):
    contract('Request',request)
    if not set(request['outputs'])<= {'markdown','html','json'}: raise ValueError('Exporter planned for requested format')
    if 'export.draft' not in request['available_tools']: raise ValueError('Exporter not allowed by task')
    if request['evidence'].get('entries'):
        raise ValueError('Caller evidence is untrusted; supply empty evidence, server re-retrieves')
    register_exporter(app)
    specification={'goal':request['goal'],'audience':','.join(request['populations']) or 'unspecified',
                   'kind':'teaching_draft','platform_request':request}
    old=app.db.execute('SELECT id,request_hash FROM runs WHERE request_key=?',(key,)).fetchone()
    if old:
        if old['request_hash']!=digest(specification): raise ValueError('Run key reused')
        run=app.get_run(old['id'])
        if run['state'] in ('draft_ready','invalidated','blocked'): return run
        if run['state']=='failed' and not retry: raise ValueError('Explicit resume required for failed run')
    else:
        id_=uuid.uuid4().hex
        with app.db:
            app.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (id_,key,digest(specification),encode(specification),'created',app.fingerprint(),encode({'config':app.config,'rules':app.rules,'platform':'0.3'}),'{}',None,None,now(),now()))
        run=app.get_run(id_)
    if 'evidence' not in run['steps']:
        filters=dict(request['project'].get('filters',{}))
        for facet,field in (('domain','domains'),('population','populations'),('scenario','scenarios')):
            if request[field]:filters.setdefault(facet,request[field])
        pack=app.retrieve(request['goal'],filters,purpose='adapt')
        if not pack['entries']:
            run.update(state='blocked',error='no_evidence');app.save_run(run);return run
        entries=[]
        for e in pack['entries']:
            u=e['knowledge']
            refs=[{'source':r['source_asset_id'],'source_revision':r['source_revision'],'locator':encode(r['locator']),'verification':'synthetic_candidate','url':''} for r in e['references']]
            entries.append({'id':u['id'],'title':u['title'],'summary':u['statement'],'origin':'curator_summary','limits':encode(u['applicability']),'teaching_application':'Synthetic structural arrangement','references':refs})
        run['steps']['evidence']={'entries':entries,'gaps':pack['gaps'],'platform_evidence':pack}
        blueprint={'id':run['id']+'-bp','schema_version':'0.3','version':1,'goal':request['goal'],
            'population':request['populations'],'scenarios':request['scenarios'],'objectives':[],
            'units':[{'knowledge_id':e['id'],'teaching_goal':'Synthetic structural arrangement'} for e in entries],
            'media_references':[],'interactions':[],'formats':request['outputs'],'quality':{},
            'safety_constraints':['review required'],'review_state':'draft','mode':'deterministic_extractive'}
        if not set(request['outputs'])<= {'markdown','html','json'}: raise ValueError('Exporter planned for requested format')
        contract('ContentBlueprint',blueprint)
        run['steps']['blueprint']=blueprint
        run['steps']['agent_plan']=ExtractivePlanner().invoke('content_planning',request,pack)
        run.update(state='blueprint_ready',error=None);app.save_run(run)
    attempt=app.db.execute('SELECT count(*) FROM tool_invocations WHERE id LIKE ?',(run['id']+'-export%',)).fetchone()[0]
    result=app.tools.invoke('export.draft',{'root':str(app.root),'config':str(app.config_path.relative_to(app.root)),'run':run},{'adapt':True},run['id']+'-export-'+str(attempt))
    if result['state']!='completed':
        run.update(state='failed',error=result.get('error'));app.save_run(run);return run
    run['steps']['artifacts']=result['output']['files']
    run['steps']['artifact_contracts']=[contract('Artifact',{'id':run['id']+'-'+name,'schema_version':'0.3','version':1,
        'artifact_type':name.rsplit('.',1)[-1],'source_blueprint':run['steps']['blueprint']['id'],
        'source_assets':sorted({r['source'] for e in run['steps']['evidence']['entries'] for r in e['references']}),
        'generation_tools':[{'tool_id':'export.draft','version':1,'invocation':result['id']}],
        'file_path':info['path'],'file_hash':info['sha256'],'review_state':'review_pending','created_at':now()}) for name,info in run['steps']['artifacts'].items()]
    run.update(state='draft_ready',error=None);app.save_run(run)
    return run
