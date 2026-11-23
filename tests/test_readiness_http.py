"""End-to-end loopback HTTP acceptance with the actual server and isolated fixtures."""
from contextlib import closing
import http.client
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from readiness_demo import Workbench, serve


class WorkbenchTests(unittest.TestCase):
    def test_browser_contract_and_review_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbench=Workbench(Path(tmp)/'runs')
            with serve(workbench,0) as server:
                thread=Thread(target=server.serve_forever,daemon=True)
                thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                def send(route,body=None,headers=None):
                    with closing(http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=15)) as client:
                        client.request('GET' if body is None else 'POST',route,
                                       None if body is None else json.dumps(body),headers or {})
                        response=client.getresponse()
                        return response.status,response.read().decode('utf8')
                try:
                    status, page=send('/')
                    self.assertEqual(status,200)
                    self.assertIn('蓝图与产出',page)
                    self.assertEqual(send('/api/state',headers={'Host':'other.invalid'})[0],403)
                    state=json.loads(send('/api/state')[1])
                    revoked=next(u['body'] for u in state['units'] if u['id']=='revoked')
                    self.assertEqual(set(revoked),{'title'})
                    self.assertFalse(json.loads(send('/api/search?q=revoked')[1])['entries'])
                    request={'request':{'track':'football','query':'shared'},'key':'http-test'}
                    self.assertEqual(send('/api/run',request,{'Content-Type':'application/json'})[0],403)
                    headers={'Content-Type':'application/json','Origin':base,'X-Workbench-Session':state['session']}
                    status, payload=send('/api/run',request,headers)
                    self.assertEqual(status,200)
                    run=json.loads(payload)
                    self.assertEqual(run['state'],'draft_ready',payload)
                    route=f"/artifacts/{run['id']}/activity.html"
                    self.assertIn('data-answer="locator"',send(route)[1])
                    self.assertEqual(send(f"/artifacts/{run['id']}/lesson.json")[0],200)
                    self.assertEqual(send('/api/review',{'run':run['id'],'decision':'approved'},headers)[0],200)
                    self.assertIn('approved',send(route)[1])
                    self.assertEqual(send('/api/review',{'run':run['id'],'decision':'withdrawn'},headers)[0],200)
                    self.assertEqual(send(route)[0],403)
                    self.assertEqual(send('/api/reset',{},headers)[0],200)
                    self.assertEqual(send('/api/run',request,headers)[0],403)
                    self.assertEqual(json.loads(send('/api/state')[1])['runs'],[])
                finally:
                    server.shutdown()
                    thread.join()
