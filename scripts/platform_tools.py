"""Allowlisted adapters, dependency/health checks, schemas, policy and timed execution."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import time
import uuid

from platform_contracts import validate, contract
from platform_parsers import parse_file, probe
from pipeline import inside, encode, now


def native_parse(payload):
    path=inside(Path(payload['root']),payload['path'])
    if not path.is_relative_to(inside(Path(payload['root']),payload['raw_directory'])):
        raise ValueError('Outside raw directory')
    return parse_file(path,payload['source'],payload['revision'])


def echo(payload):
    return {'value':payload['value']}


def _worker(connection, adapter, payload):
    try: connection.send({'ok':True,'output':adapter(payload)})
    except Exception as exc: connection.send({'ok':False,'error':type(exc).__name__ + ': ' + str(exc)[:200]})
    finally: connection.close()


class ToolRegistry:
    def __init__(self, db, authorizer=None):
        self.db=db
        self._authorize=authorizer
        self.adapters={'native_parse':native_parse,'echo':echo}
        self._definitions={}

    @property
    def definitions(self):
        return deepcopy(self._definitions)

    def register_adapter(self,key,adapter):
        if key in self.adapters and self.adapters[key] is not adapter: raise ValueError('Duplicate adapter')
        self.adapters[key]=adapter  # Code-only extension; never loaded from model strings.

    def register(self,definition):
        definition=deepcopy(definition)
        contract('ToolDefinition',definition)
        if definition['status'] not in ('implemented','available','unavailable_dependency','disabled_by_policy','planned','failed_health_check'):
            raise ValueError('Unknown tool state')
        if not 0 < definition['timeout'] <= 120: raise ValueError('Invalid timeout')
        id_=definition['tool_id']
        if id_ in self._definitions:
            if self._definitions[id_]!=definition: raise ValueError('Conflicting tool definition: '+id_)
            return
        self._definitions[id_]=definition

    def health(self,id_):
        d=self._definitions[id_]
        state=d['status']; reason='implementation reserved; not executable' if state=='planned' else 'registered policy state'
        if state in ('implemented','available'):
            missing=[m for m in d['dependencies'] if importlib.util.find_spec(m) is None]
            if missing: state,reason='unavailable_dependency',','.join(missing)
            elif d['adapter_key'] not in self.adapters: state,reason='failed_health_check','adapter not allowlisted'
            else: state,reason='available','adapter and dependencies available'
        return {'tool_id':id_,'status':state,'reason':reason,'definition':deepcopy(d)}

    def discover(self,capability=None):
        return [self.health(k) for k,d in self._definitions.items() if capability is None or capability in d['capabilities']]

    def invoke(self,id_,payload,rights,key,approved=False,source_ids=None):
        payload=deepcopy(payload);rights=deepcopy(rights);source_ids=deepcopy(source_ids)
        contract('ToolInvocation',{'id':key,'schema_version':'0.3','version':1,'tool_id':id_,
            'input':payload,'rights':rights,'idempotency_key':key})
        d=self._definitions[id_]
        request_hash=hashlib.sha256(encode({'tool':d,'input':payload,'rights':rights,'approved':approved,'sources':source_ids}).encode()).hexdigest()
        previous=self.db.execute('SELECT request_hash,result FROM tool_invocations WHERE id=?',(key,)).fetchone()
        if previous:
            if previous[0]!=request_hash: raise ValueError('Idempotency key reused')
            saved=json.loads(previous[1])
            if saved['state']=='running': raise ValueError('Uncertain/interrupted attempt; use a new explicit attempt key')
        result={'id':key,'schema_version':'0.3','version':1,'tool_id':id_,'state':'running','output':{}}
        def save():
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO tool_invocations VALUES(?,?,?,?,?,?,?)',
                    (key,id_,str(d['version']),result['state'],request_hash,encode(result),now()))
        if not previous:save()
        try:
            health=self.health(id_)
            if health['status']!='available': raise ValueError(health['status']+': '+health['reason'])
            if any(rights.get(p) is not True for p in d['permission_requirements']): raise PermissionError('Missing explicit tool permission')
            if d['network_access'] or d['external_processing']: raise PermissionError('Network/external tools disabled in V0.3')
            if d['human_approval_required'] and not approved: raise PermissionError('Approval required')
            validate(payload,d['input_schema'])
            if d['data_sensitivity']!='synthetic_only': raise PermissionError('Unknown/unsupported tool data sensitivity')
            if self._authorize is None: raise PermissionError('Trusted source authorizer required')
            self._authorize(d,payload,source_ids)
            # Re-authorize current records before returning any cached output.
            if previous:return saved
            context=multiprocessing.get_context('spawn')
            parent,child=context.Pipe(duplex=False)
            process=context.Process(target=_worker,args=(child,self.adapters[d['adapter_key']],payload))
            process.start(); child.close()
            try:
                if not parent.poll(d['timeout']):
                    process.terminate(); raise TimeoutError('Tool timeout')
                received=parent.recv()
                if not received['ok']: raise ValueError(received['error'])
                validate(received['output'],d['output_schema'])
                result.update(state='completed',output=received['output'])
            finally:
                parent.close(); process.join(timeout=2)
                if process.is_alive(): process.terminate(); process.join()
        except Exception as exc:
            result.update(state='failed',error=type(exc).__name__ + ': ' + str(exc)[:300])
        contract('ToolResult',result)
        save()
        return result


def definition(id_,adapter,capability,permissions=None,status='implemented',dependencies=None):
    parse=adapter=='native_parse'
    return {'id':id_,'schema_version':'0.3','version':1,'tool_id':id_,'name':id_,
        'category':'parser' if parse else 'integration','description':capability,
        'capabilities':[capability],'adapter_key':adapter,'status':status,
        'input_schema':{'type':'object','required':['root','path','raw_directory','source','revision'] if parse else ['value'],
            'properties':{k:{'type':'string'} for k in ('root','path','raw_directory','source')} | {'revision':{'type':'integer'}} if parse else {'value':{}},'additionalProperties':False},
        'output_schema':{'type':'object','required':['segments','status','parser_version'] if parse else ['value']},
        'dependencies':dependencies or [],'runtime_type':'spawned_local_process',
        'permission_requirements':permissions or [],'data_sensitivity':'synthetic_only',
        'network_access':False,'external_processing':False,'deterministic':True,
        'side_effects':['local_audit'],'timeout':30,'idempotency':'explicit_attempt_key',
        'human_approval_required':False,'health_check':'adapter_and_dependency_probe','configuration_schema':{'type':'object'}}


class ParserRegistry:
    def __init__(self,tools):
        self.tools=tools
        self.routes={}
        for suffix in ('.txt','.md','.docx','.html','.htm','.csv','.json','.pptx','.xlsx','.pdf','.png','.jpg','.jpeg','.wav'):
            id_='parse'+suffix
            deps=['pypdf'] if suffix=='.pdf' else ['PIL'] if suffix in ('.png','.jpg','.jpeg') else []
            tools.register(definition(id_,'native_parse',suffix,['internal_parse'],dependencies=deps))
            self.routes[suffix]=id_
        for name,cap,permission in [('ocr','image_text','ocr'),('asr','audio_text','asr'),('ffmpeg','video_processing','video_processing'),('semantic','semantic_search','retrieve'),('mcp','external_tools','external_model_processing')]:
            tools.register(definition(name,name,cap,[permission],status='planned'))

    def resolve(self,path):
        suffix=Path(path).suffix.lower()
        if suffix not in self.routes: raise ValueError('unavailable_dependency: no parser registered for '+suffix)
        return self.routes[suffix]
