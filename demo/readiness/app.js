'use strict';
const by = id => document.getElementById(id);
let session, workspace;
const labels = {pending_review:'待准入',admitted:'已准入',expired:'已过期',revoked:'已撤回',
  approved:'已批准',review_pending:'待审核',withdrawn:'已撤回',completed:'已完成',draft_ready:'草稿完成',
  published:'已发布',invalidated:'已失效',failed:'失败',rejected:'已驳回',blocked:'已阻断'};
function el(tag,text) {const node=document.createElement(tag);if(text!==undefined) node.textContent=text;return node;}
function detail(title,data) {const d=el('details');d.append(el('summary',title),el('pre',JSON.stringify(data,null,2)));return d;}
function message(text,bad=false) {by('status').textContent=text;by('status').classList.toggle('bad',bad);}
async function api(path,body) {
  const options = body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Session':session},body:JSON.stringify(body)};
  const r=await fetch(path,options);const obj=await r.json();if(!r.ok) throw new Error(obj.error);return obj;
}
function handle(node, event, task) {node.addEventListener(event, async e => {
  e.preventDefault();try {message('本地执行中…');await task(e);message('已更新 · Synthetic');}
  catch(error) {message(error.message,true);}
});}
function option(id,title) {const opt=el('option',title);opt.value=id;return opt;}
function listRun(run) {
  const card=el('article'), name=run.request.track;
  const heading=el('strong',name+' · '+(labels[run.state]||run.state));card.append(heading);
  card.append(el('p',run.id+(run.error?' · '+run.error:'')));
  if (['draft_ready','approved','published'].includes(run.state)) {
    const links=el('div');links.className='links';
    Object.keys(run.steps.artifacts||{}).forEach(file=>{const link=el('a',file);
      link.href='/artifacts/'+encodeURIComponent(run.id)+'/'+encodeURIComponent(file);link.target='_blank';link.rel='noopener';links.append(link);});
    card.append(links);
  }
  if (run.state==='draft_ready'||run.state==='approved') {
    const decisions=run.state==='draft_ready'?[['approved','批准合成草稿'],['rejected','驳回']]:[['withdrawn','撤回']];
    decisions.forEach(([decision,label])=>{const button=el('button',label);button.className='quiet';
      card.append(button);handle(button,'click',async()=>{await api('/api/review',{run:run.id,decision});await refresh();});});
  }
  if (run.state==='failed') {
    const button=el('button','恢复执行');card.append(button);handle(button,'click',async()=>{
      const {version,input_mode,...request}=run.request;
      await api('/api/run',{request,key:run.request_key,retry:true});await refresh();
    });
  }
  card.append(detail('Request · EvidencePack · CourseBlueprint', {request:run.request,evidence:run.steps.evidence,
    blueprint:run.steps.blueprint,review:run.review,timeline:run.steps.timeline}));
  return card;
}
async function refresh() {
  const state=await api('/api/state');session=state.session;workspace=state.workspace;
  by('counts').replaceChildren();
  [['来源',state.sources.length],['知识',state.units.length],['运行',state.runs.length]].forEach(([label,count])=>by('counts').append(el('dt',label),el('dd',count)));
  const rows=state.sources.map(s=>{const row=el('tr');[s.title,'v'+s.version,labels[s.status]||s.status].forEach(v=>row.append(el('td',v)));
    const meta=el('td');meta.append(detail(s.sha256.slice(0,12)+'…', {sha256:s.sha256,format:s.format,authorization:s.authorization}));row.append(meta);return row;});
  by('source-rows').replaceChildren(...rows);
  by('parse-source').replaceChildren(...state.parses.map(r=>option(r.body.source,r.body.source+' · '+r.body.stages.join(' / '))));
  by('units').replaceChildren(...state.units.map(u=>detail(u.body.title+' · '+(labels[u.state]||u.state),u)));
  by('relations').textContent=JSON.stringify(state.relations,null,2);
  by('search-domain').replaceChildren(option('','全部'),...Object.entries(state.courses).map(([id,course])=>option(id,course.title)));
  const previousTrack=by('track').value;
  by('track').replaceChildren(...Object.entries(state.courses).map(([id,course])=>option(id,course.title)));
  if(previousTrack) by('track').value=previousTrack;
  by('media-label').hidden=!state.full;
  by('run-list').replaceChildren(...state.runs.map(listRun));
  by('events').textContent=JSON.stringify(state.events,null,2);
}
handle(by('search-form'),'submit',async()=>{
  const result=await api('/api/search?'+new URLSearchParams(new FormData(by('search-form'))));
  by('search-results').replaceChildren(el('p','命中 '+result.entries.length+' 个知识单元'),
    ...result.entries.map(e=>detail(e.knowledge.title,e)),...(result.conflicts.length?[detail('冲突证据',result.conflicts)]:[]));
});
handle(by('production-form'),'submit',async()=>{
  const data=new FormData(by('production-form'));
  const request={track:data.get('track'),query:data.get('query')};
  if(data.has('media')) request.timeline=[{asset:'media-mp4',start_ms:0,end_ms:320},{asset:'media-mov',start_ms:160,end_ms:560}];
  const run=await api('/api/run',{request,key:'ui-'+crypto.randomUUID()});
  by('output').replaceChildren(el('p','产出见任务与审核，运行 ID '+run.id));
  await refresh();
});
handle(by('parse-check'),'click',async()=>{
  const result=await api('/api/parse',{id:by('parse-source').value});
  by('parsed').replaceChildren(...result.parts.map(p=>{const d=detail(p.stage,p.segments);d.open=true;return d;}));
});
handle(by('bench'),'click',async()=>{
  const {rows,...metrics}=await api('/api/benchmark');
  by('metrics').textContent=JSON.stringify(metrics,null,2);
});
handle(by('reset'),'click',async()=>{
  if(!window.confirm('用一套新的合成资料替换当前展示？旧运行目录将保留。')) return;
  await api('/api/reset',{});await refresh();by('output').replaceChildren();by('parsed').replaceChildren();
});
refresh().then(()=>message('本地工作区 '+workspace+' · Synthetic')).catch(error=>message(error.message,true));
