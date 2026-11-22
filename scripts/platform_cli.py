"""V0.3 workspace entry point; legacy workflow.py remains compatible."""
import argparse
import json
import sys
from platform_store import Platform
from platform_production import produce, register_exporter
from pipeline import load


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config/runtime.json')
    sub=parser.add_subparsers(dest='command',required=True)
    tools=sub.add_parser('tools');tools.add_argument('--capability')
    health=sub.add_parser('health');health.add_argument('id')
    put=sub.add_parser('put');put.add_argument('kind');put.add_argument('file');put.add_argument('--confirmed',action='store_true')
    ex=sub.add_parser('extract');ex.add_argument('manifest');ex.add_argument('--key',required=True)
    query=sub.add_parser('query');query.add_argument('text',nargs='?',default='');query.add_argument('--filters',default='{}');query.add_argument('--mode',choices=['AND','OR'],default='AND')
    run=sub.add_parser('run');run.add_argument('request');run.add_argument('--key',required=True)
    resume=sub.add_parser('resume');resume.add_argument('id')
    args=parser.parse_args()
    try:
        with Platform(config=args.config) as app:
            if args.command in ('tools','health'):
                register_exporter(app)
                result=app.tools.discover(args.capability) if args.command=='tools' else app.tools.health(args.id)
            elif args.command=='put':
                value=load(args.file)
                result={'revision':app.confirm(value,args.confirmed) if args.kind=='KnowledgeUnit' else app.save_object(args.kind,value)}
            elif args.command=='extract':result=app.extract(load(args.manifest),args.key)
            elif args.command=='query':result=app.retrieve(args.text,json.loads(args.filters),args.mode)
            elif args.command=='resume':
                saved=app.get_run(args.id)
                run=produce(app,saved['request']['platform_request'],saved['request_key'],retry=True)
                result={k:run[k] for k in ('id','state','error')}
            else:
                run=produce(app,load(args.request),args.key)
                result={k:run[k] for k in ('id','state','error')}
            print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError) as exc:
        print(json.dumps({'status':'ERROR','error':str(exc)},ensure_ascii=False));return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
