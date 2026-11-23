"""Loopback-only synthetic workbench. Run with Python 3.11 or newer."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

from pipeline import ROOT, load
from readiness_agent import artifact, ensure_current, produce, review
from readiness_demo_data import isolate, seed, evaluate
from readiness_parse import parse
from readiness_retrieval import search
from readiness_store import Readiness

STATIC = ROOT/'demo/readiness'
COURSES = load(ROOT/'config/course_tracks.json')
MEDIA = {'.png':'image/png','.wav':'audio/wav','.mp4':'video/mp4','.json':'application/json',
         '.js':'text/javascript','.css':'text/css','.html':'text/html','.md':'text/plain'}


class Workbench:
    def __init__(self, home, full=False):
        self.home = Path(home)
        self.full = full
        self.root = None
        self.reset()

    def reset(self):
        target = isolate(self.home/uuid.uuid4().hex)
        with Readiness(target) as app:
            seed(app,self.full)
        self.root = target
        return {'workspace':self.root.name}

    def overview(self, app):
        sources = []
        for row in app.records('ReadinessSource'):
            p = app.record('SourceAuthorization',row['id'])
            sources.append(dict(id=row['id'],version=row['body']['version'],title=row['body']['title'],
                                format=row['body']['source_type'],sha256=row['body']['sha256'],
                                status=app.source_status(row),authorization=p and p['body']))
        units = []
        permitted = set()
        for item in app.records('ReadinessUnit'):
            try:
                for ref in item['body']['references']:
                    app.evidence_span(ref)
            except (PermissionError, ValueError, OSError):
                item = {'id':item['id'],'state':item['state'],'revision':item['revision'],
                        'body':{'title':'受限条目 / '+item['id']}}
            else:
                permitted.add(item['id'])
            units.append(item)
        relations = [r for r in app.records('ReadinessRelation') if
                     {r['body']['subject'],r['body']['object']} <= permitted]
        runs = []
        for row in app.db.execute('SELECT id FROM runs ORDER BY created DESC'):
            run = app.get_run(row['id'],check=False)
            if 'evidence' in run['steps']:
                try:
                    ensure_current(app,run)
                except (PermissionError, ValueError, OSError):
                    run['steps'] = {}
                    if run['state'] in {'draft_ready','approved','published'}:
                        run['state'] = 'invalidated'
            runs.append(run)
        return {'workspace':self.root.name,'synthetic':True,'courses':COURSES,'sources':sources,
                'units':units,'relations':relations,
                'parses':app.records('ParseRun'),'runs':runs,
                'events':[dict(r) for r in app.db.execute('SELECT * FROM audit ORDER BY seq DESC LIMIT 30')]}


def serve(workbench, port=8766):
    key = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,body,mime='application/json; charset=utf-8',activity=False):
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            scripts = "'self' 'unsafe-inline'" if activity else "'self'"
            self.send_header('Content-Security-Policy',f"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src {scripts}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self,status,data):
            self.reply(status,json.dumps(data,ensure_ascii=False).encode('utf8'))

        def check_host(self):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host:
                raise PermissionError('Use the loopback workbench address')
            return 'http://' + host

        def do_GET(self):
            try:
                self.check_host()
                url = urlsplit(self.path)
                static = {'/':'index.html','/style.css':'style.css','/app.js':'app.js'}
                if url.path in static:
                    file = STATIC/static[url.path]
                    return self.reply(200,file.read_bytes(),MEDIA[file.suffix]+'; charset=utf-8')
                with Readiness(workbench.root) as app:
                    if url.path=='/api/state':
                        return self.json(200,dict(workbench.overview(app),session=key + '.' + workbench.root.name,full=workbench.full))
                    if url.path=='/api/search':
                        query = parse_qs(url.query,keep_blank_values=True)
                        method = query.get('method',['lexical'])[0]
                        facet = query.get('domain',[''])[0]
                        return self.json(200,search(app,query.get('q',[''])[0],
                                                    {'domain':[facet]} if facet else None,method=method,expand=True))
                    if url.path=='/api/benchmark':
                        return self.json(200,evaluate(app))
                    pieces = url.path.split('/')
                    if len(pieces)==4 and pieces[1]=='artifacts':
                        file = artifact(app,pieces[2],pieces[3])
                        mime = MEDIA.get(file.suffix,'application/octet-stream')
                        return self.reply(200,file.read_bytes(),mime,activity=file.name=='activity.html')
                self.json(404,{'error':'Unknown route'})
            except PermissionError as exc:
                self.json(403,{'error':str(exc)})
            except (ValueError, OSError) as exc:
                self.json(400,{'error':str(exc)})

        def do_POST(self):
            try:
                origin = self.check_host()
                if (self.headers.get('Origin')!=origin
                        or not secrets.compare_digest(self.headers.get('X-Workbench-Session',''),key + '.' + workbench.root.name)
                        or self.headers.get_content_type()!='application/json'):
                    raise PermissionError('Same-origin operator session required')
                length = int(self.headers.get('Content-Length',0))
                if not 1 <= length <= 16384:
                    raise ValueError('Request size outside local limit')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body,dict):
                    raise ValueError('Expected an object')
                if self.path=='/api/reset' and not body:
                    return self.json(200,workbench.reset())
                with Readiness(workbench.root) as app:
                    if self.path=='/api/run' and set(body)<= {'request','key','retry'} and {'request','key'}<=body.keys():
                        run = produce(app,body['request'],body['key'],retry=body.get('retry') is True)
                        return self.json(200,run)
                    if self.path=='/api/parse' and set(body)=={'id'} and isinstance(body['id'],str):
                        existing = next((r for r in app.records('ParseRun') if r['body']['source']==body['id']),None)
                        if not existing:
                            raise ValueError('Unknown generated sample')
                        result = parse(app,body['id'],existing['id'],existing['body']['stages'],retry=True)
                        return self.json(200,result)
                    if self.path=='/api/review' and set(body)=={'run','decision'} and body['decision'] in {'approved','withdrawn','rejected'}:
                        result = review(app,body['run'],reviewer='synthetic-local-operator',decision=body['decision'],
                                        note='Local demonstration review of generated fixtures')
                        return self.json(200,result)
                self.json(404,{'error':'Unknown local action'})
            except PermissionError as exc:
                self.json(403,{'error':str(exc)})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.json(400,{'error':str(exc)})

    return HTTPServer(('127.0.0.1',port),Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--full',action='store_true',help='Include PDF, image, FFmpeg and media timeline')
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    app = Workbench(ROOT/'data/v04-demo',args.full)
    with serve(app,args.port) as http:
        address = f'http://127.0.0.1:{http.server_port}'
        print(f'Synthetic workbench {address} | {app.root}',flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__=='__main__':
    main()
