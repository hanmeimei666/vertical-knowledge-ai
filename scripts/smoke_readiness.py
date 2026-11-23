"""Run complete synthetic evidence and keep the input and output ledger locally."""
import argparse
import json
import uuid

from pipeline import ROOT
from readiness_demo_data import demo_run, isolate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full',action='store_true',help='require PDF, image and FFmpeg dependencies')
    args = parser.parse_args()
    root = isolate(ROOT/'data/v04-smoke'/uuid.uuid4().hex)
    report = demo_run(root,args.full)
    path = root/'report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'report':str(path),'status':report['status'],
                      'formats':[row['id'] for row in report['inputs']['parsed']],
                      'runs':[(r['id'],r['state']) for r in report['runs']],
                      'metrics':{k:v for k,v in report['benchmark'].items() if k!='rows'}},ensure_ascii=False))
    return int(report['status']!='PASS')


if __name__=='__main__':
    raise SystemExit(main())
