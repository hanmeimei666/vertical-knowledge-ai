"""Checkpointed, locally governed Request → Evidence → Blueprint → Artifact workflow."""
from datetime import datetime, timezone
from html import escape
import multiprocessing
from pathlib import Path
import re
import tempfile
import uuid

from pipeline import digest, encode, inside, load, now
from platform_tools import _worker
from readiness_media import run_media
from readiness_model import SyntheticModel, validate_plan
from readiness_retrieval import search
from readiness_store import sha256, timestamp

TRACKS = Path(__file__).resolve().parents[1] / 'config/course_tracks.json'
FILES = {'lesson.md', 'lesson.html', 'lesson.json', 'activity.html', 'timeline.json', 'clip.mp4'}


def check_domain_rules(query, rules):
    """Reject out-of-scope medical/diagnostic/device-control phrases defined in domain_rules.json."""
    if not isinstance(rules, dict):
        return None
    for phrase in rules.get('excluded_request_phrases', []):
        if phrase and phrase in query:
            return phrase
    return None


def invoke_model(provider, request, evidence, timeout):
    if not 0 < timeout <= 120:
        raise ValueError('Model timeout outside supported range')
    ctx = multiprocessing.get_context('spawn')
    rx, tx = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(tx, provider_plan, (provider, request, evidence)))
    try:
        proc.start()
        tx.close()
        if not rx.poll(timeout):
            raise TimeoutError('Model provider timed out')
        result = rx.recv()
        if not result['ok']:
            raise RuntimeError(result['error'])
        return result['output']
    finally:
        rx.close()
        tx.close()
        if proc.pid:
            proc.join(.25)
            if proc.is_alive():
                proc.terminate()
                proc.join()


def provider_plan(payload):
    provider, request, evidence = payload
    return provider.plan(request, evidence)


def request_contract(request, *, max_clip_ms=60_000):
    if not isinstance(request, dict) or set(request) - {'track', 'query', 'timeline'}:
        raise ValueError('Invalid production request fields')
    tracks = load(TRACKS)
    if (request.get('track') not in tracks or not isinstance(request.get('query'), str)
            or not 1 <= len(request['query'].strip()) <= 2000):
        raise ValueError('A known track and query are required')
    timeline = request.get('timeline', [])
    if not isinstance(timeline, list) or len(timeline) > 8:
        raise ValueError('Timeline requires at most eight clips')
    for clip in timeline:
        if (not isinstance(clip, dict) or set(clip) != {'asset', 'start_ms', 'end_ms'}
                or not isinstance(clip['asset'], str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', clip['asset'])
                or type(clip['start_ms']) is not int or type(clip['end_ms']) is not int
                or not 0 <= clip['start_ms'] < clip['end_ms'] <= max_clip_ms):
            raise ValueError('Invalid milliseconds or asset ID')
    return tracks[request['track']]


def media_inputs(app, timeline):
    result = []
    output_time = 0
    for clip in timeline:
        item = app.record('ReadinessMediaAsset', clip['asset'])
        if not item or item['state'] != 'available' or item['body']['duration_ms'] < clip['end_ms']:
            raise ValueError('Unknown media asset or clip exceeds duration')
        asset = item['body']
        source = app.authorize(asset['source'], 'retrieve', 'adapt', 'video_processing', revision=asset['revision'])
        duration = clip['end_ms'] - clip['start_ms']
        result.append(dict(clip, source=source['id'], revision=source['revision'], source_sha256=source['body']['sha256'],
                           output_start_ms=output_time, output_end_ms=output_time + duration))
        output_time += duration
    return result


def _has_audio_stream(path):
    info = run_media('-i', path, '-map', '0', '-t', '0.01', '-f', 'null', '-').stderr.decode('utf8', 'replace')
    return bool(re.search(r'Stream #0:\d+.*Audio:', info))


def render_media(app, directory, timeline):
    media_cfg = app.config.get('media', {}) if isinstance(app.config, dict) else {}
    scale = media_cfg.get('video_scale', '320:180')
    if not isinstance(scale, str) or not re.fullmatch(r'\d{2,4}:\d{2,4}', scale):
        raise ValueError('Invalid media.video_scale configuration')
    args = []
    commands = []
    for i, clip in enumerate(timeline):
        source = app.authorize(clip['source'], 'retrieve', 'adapt', 'video_processing', revision=clip['revision'])
        path = app.path(source['body'])
        dur_s = (clip['end_ms'] - clip['start_ms']) / 1000
        args += ['-ss', str(clip['start_ms'] / 1000), '-t', str(dur_s), '-i', path]
        commands.append(f'[{i}:v]scale={scale},setsar=1,fps=25,format=yuv420p,setpts=PTS-STARTPTS[v{i}]')
        if _has_audio_stream(path):
            commands.append(f'[{i}:a]aresample=16000,aformat=channel_layouts=mono,asetpts=PTS-STARTPTS[a{i}]')
        else:
            commands.append(f'anullsrc=r=16000:cl=mono,atrim=duration={dur_s:.3f},asetpts=PTS-STARTPTS[a{i}]')
    inputs = ''.join(f'[v{i}][a{i}]' for i in range(len(timeline)))
    commands.append(f'{inputs}concat=n={len(timeline)}:v=1:a=1[v][a]')
    run_media('-loglevel', 'error', *args, '-filter_complex_threads', '1', '-filter_complex', ';'.join(commands),
              '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', '-movflags', '+faststart',
              '-y', directory / 'clip.mp4')
    (directory / 'timeline.json').write_text(encode(timeline) + '\n', encoding='utf8')


def lesson_html(title, body):
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>body{{font:17px/1.7 system-ui;max-width:54rem;margin:3rem auto;padding:0 1.3rem;color:#203032;background:#f7faf8}} details,section{{padding:1rem;border:1px solid #b5c7c1;margin:1rem 0;border-radius:.5rem}} code{{overflow-wrap:anywhere}} button,input{{font:inherit}} .notice{{background:#fff8eb;border-color:#d4a756}} </style></head><body>{body}</body></html>"""


def render_documents(directory, run, review_status, timeline, *, limitations=()):
    steps = run['steps']
    blueprint = steps['blueprint']
    by_id = {e['knowledge']['id']: e for e in steps['evidence']['entries']}
    entries = [by_id[id_] for id_ in blueprint['units']]
    mode = run['request']['input_mode']
    title = blueprint['course']['title'] + (' | Synthetic' if mode == 'synthetic' else ' | Local authorized input')
    planner = blueprint.get('model_provider', 'legacy-provider-unrecorded')
    plain = [f'# {title}', f'Run {run["id"]} · {review_status}', f'Planner: {planner} · input mode: {mode}']

    safety_items = list(blueprint.get('safety_notes', [])) + list(limitations or [])
    safety_html = ''
    if safety_items:
        plain += ['## 安全边界与使用限制', *(f'- {s}' for s in safety_items)]
        safety_html = ('<section class="notice"><h2>安全边界与使用限制</h2><ul>'
                       + ''.join(f'<li>{escape(s)}</li>' for s in safety_items) + '</ul></section>')

    phases_html = ''
    if blueprint.get('phases'):
        plain.append('## 教学流程安排')
        phase_cards = []
        for phase in blueprint['phases']:
            cues = phase.get('coaching_cues', [])
            plain.append(f"### {phase['name']}（{phase['duration_minutes']} 分钟）")
            plain.extend(f'- {act}' for act in phase['activities'])
            if cues:
                plain.append('要点: ' + '；'.join(cues))
            plain.append('依据单元: ' + ', '.join(phase['unit_ids']))
            cues_html = ('<p><strong>执教要点：</strong>' + escape('；'.join(cues)) + '</p>') if cues else ''
            phase_cards.append(
                f"<section><h3>{escape(phase['name'])}（{phase['duration_minutes']} 分钟）</h3>"
                f"<ul>{''.join(f'<li>{escape(a)}</li>' for a in phase['activities'])}</ul>"
                f"{cues_html}<p><code>依据单元: {escape(', '.join(phase['unit_ids']))}</code></p></section>"
            )
        phases_html = ''.join(phase_cards)

    blocks = []
    for e in entries:
        unit = e['knowledge']
        refs = [f"{r['source']} v{r['revision']} · {encode(r['locator'])}" for r in e['references']]
        label = f"{unit['title']} · v{e['version']} · {e['review_state']}"
        plain += ['## ' + escape(label), escape(unit['statement']), 'Evidence: ' + escape(' / '.join(refs))]
        blocks.append(f'<details><summary>{escape(label)}</summary><p>{escape(unit["statement"])}</p><code>{escape(" / ".join(refs))}</code></details>')

    citations_html = ''
    if blueprint.get('citations'):
        plain += ['## 关键结论溯源', *(f"- {c['claim']} [{c['unit_id']}]" for c in blueprint['citations'])]
        citations_html = ('<section><h2>关键结论溯源</h2><ul>'
                          + ''.join(f"<li>{escape(c['claim'])} <code>[{escape(c['unit_id'])}]</code></li>" for c in blueprint['citations'])
                          + '</ul></section>')

    shell = f'<header><h1>{escape(title)}</h1><p>Run {escape(run["id"])} · {escape(review_status)}</p><p>Planner: {escape(planner)} · input mode: {escape(mode)}</p></header>'
    sections = safety_html + phases_html + ''.join(blocks) + citations_html
    exercise = blueprint['interaction']
    extra_exercises = ''
    for idx, ex in enumerate(blueprint.get('exercises', []), 1):
        if ex['type'] == 'single_choice':
            opts = ''.join(f'<label><input type="radio" name="ex-{idx}" value="{escape(o, quote=True)}" required> {escape(o)}</label><br>' for o in ex.get('options', []))
            control = opts
        else:
            control = '<label>答案 <input required></label>'
        extra_exercises += (
            f'<form class="quiz" data-type="{escape(ex["type"])}" data-answer="{escape(ex["answer"], quote=True)}" '
            f'data-feedback="{escape(ex["feedback"], quote=True)}"><p><strong>练习 {idx}：</strong>{escape(ex["prompt"])}</p>'
            f'{control} <button>检查</button><p class="quiz-status" role="status"></p></form>'
        )

    activity = f"""<section aria-labelledby="practice"><h2 id="practice">结构练习</h2>
<p>{escape(exercise["prompt"])}</p><form data-answer="{escape(exercise["answer"],quote=True)}" data-feedback="{escape(exercise["feedback"],quote=True)}"><label>答案 <input required></label> <button>检查</button></form><p role="status"></p>{extra_exercises}</section>
<script>const form=document.querySelector('form:not(.quiz)');form.addEventListener('submit',function(e){{e.preventDefault();const correct=form.querySelector('input').value.trim().toLowerCase()===form.dataset.answer.toLowerCase();document.querySelector('[role=status]').textContent=correct?form.dataset.feedback:'再试一次';}});
document.querySelectorAll('form.quiz').forEach(function(qf){{qf.addEventListener('submit',function(e){{e.preventDefault();const picked=qf.dataset.type==='single_choice'?(qf.querySelector('input:checked')||{{value:''}}).value:qf.querySelector('input').value;const ok=picked.trim().toLowerCase()===qf.dataset.answer.toLowerCase();qf.querySelector('.quiz-status').textContent=ok?qf.dataset.feedback:'再试一次';}});}});</script>"""
    modules = ''.join(f'<li>{escape(m)}</li>' for m in blueprint['course']['modules'])
    (directory / 'lesson.md').write_text('\n\n'.join(plain) + '\n', encoding='utf8')
    (directory / 'lesson.html').write_text(lesson_html(title, shell + sections), encoding='utf8')
    (directory / 'activity.html').write_text(lesson_html(title, shell + '<ol>' + modules + '</ol>' + sections + activity), encoding='utf8')
    (directory / 'lesson.json').write_text(encode(dict(run=run['id'], mode=mode, review_state=review_status,
                                                       limitations=list(limitations or []),
                                                       evidence=steps['evidence'], blueprint=blueprint, timeline=timeline)) + '\n', encoding='utf8')


def ensure_current(app, run, *, public=False):
    steps = run['steps']
    if run['state'] in {'invalidated', 'withdrawn', 'blocked', 'rejected'} or 'evidence' not in steps:
        raise PermissionError('Run not current')
    for e in steps['evidence']['entries']:
        current = app.record('ReadinessUnit', e['knowledge']['id'])
        if not current or current['state'] != 'approved' or current['revision'] != e['version']:
            raise PermissionError('Evidence unit version changed')
        expiry = current['body'].get('valid_until')
        if expiry and timestamp(expiry) <= datetime.now(timezone.utc):
            raise PermissionError('Evidence unit expired')
        for ref in e['references']:
            app.evidence_span(ref, 'adapt')
            if public:
                app.authorize(ref['source'], 'redistribute', revision=ref['revision'])
    for clip in steps.get('timeline', []):
        app.authorize(clip['source'], 'retrieve', 'adapt', 'video_processing',
                      *(['redistribute'] if public else []), revision=clip['revision'])


def publish_files(app, run, review_status):
    ensure_current(app, run)
    output = inside(app.root, app.config['output_directory']) / 'lessons' / run['id']
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        staging = Path(temporary)
        timeline = run['steps']['timeline']
        limitations = app.rules.get('limitations', []) if isinstance(getattr(app, 'rules', None), dict) else []
        render_documents(staging, run, review_status, timeline, limitations=limitations)
        if timeline:
            render_media(app, staging, timeline)
        ensure_current(app, run)
        folder = output / review_status
        folder.mkdir(exist_ok=True)
        files = {}
        for path in sorted(staging.iterdir()):
            final = folder / path.name
            path.replace(final)
            files[path.name] = {'path': final.relative_to(app.root).as_posix(),
                                'sha256': sha256(final), 'review_state': review_status}
        return files


def produce(app, request, key, *, provider=None, retry=False, timeout=20):
    media_cfg = app.config.get('media', {}) if isinstance(getattr(app, 'config', None), dict) else {}
    course = request_contract(request, max_clip_ms=media_cfg.get('max_clip_ms', 60_000))
    if not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', key):
        raise ValueError('An explicit idempotency key is required')
    full_request = dict(request, version='0.4', input_mode='authorized-local' if app.authorized_local_intake else 'synthetic')
    previous = app.db.execute('SELECT id,request_hash FROM runs WHERE request_key=?', (key,)).fetchone()
    if previous:
        if previous['request_hash'] != digest(full_request):
            raise ValueError('Idempotency key reused')
        run = app.get_run(previous['id'], check=False)
        if run['state'] in {'draft_ready', 'approved', 'published'}:
            ensure_current(app, run)
            for name in run['steps']['artifacts']:
                artifact(app, run['id'], name)
            return run
        if run['state'] in {'invalidated', 'withdrawn', 'blocked', 'rejected'} or run['state'] == 'failed' and not retry:
            raise ValueError('New key or explicit failure recovery required')
    else:
        id_ = uuid.uuid4().hex
        with app.db:
            app.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (id_, key, digest(full_request), encode(full_request), 'created', 'revision-checked',
                            encode({'platform': '0.4'}), '{}', None, None, now(), now()))
            app.audit('run_created', id_, {'key': key})
        run = app.get_run(id_, check=False)
    try:
        excluded = check_domain_rules(request['query'], getattr(app, 'rules', None))
        if excluded:
            run.update(state='blocked', error='excluded_domain_request: ' + excluded)
            app.audit('domain_rule_blocked', run['id'], {'phrase': excluded})
            app.save_run(run)
            return app.get_run(run['id'], check=False)
        steps = run['steps']
        if 'evidence' not in steps:
            pack = search(app, request['query'], {'domain': [request['track']],
                                                  'learner': [course['learner']], 'training_population': [course['training_population']]}, purpose='adapt')
            if not pack['entries'] or pack['conflicts']:
                steps['evidence'] = pack
                run.update(state='blocked', error='no_evidence_or_unresolved_conflict')
                app.save_run(run)
                return run
            modes = {'synthetic', 'authorized-local'} if app.authorized_local_intake else {'synthetic'}
            if any(app.record('ReadinessSource', ref['source'])['body']['mode'] not in modes
                   for e in pack['entries'] for ref in e['references']):
                raise PermissionError('Source mode is not enabled by the local operator')
            steps['timeline'] = media_inputs(app, request.get('timeline', []))
            steps['evidence'] = pack
            run.update(state='evidence_ready', error=None)
            app.save_run(run)
        ensure_current(app, run)
        if 'blueprint' not in steps:
            active_provider = provider or SyntheticModel()
            if getattr(active_provider, 'requires_external_processing', False):
                for e in steps['evidence']['entries']:
                    for ref in e['references']:
                        app.authorize(ref['source'], 'external_model_processing', revision=ref['revision'])
            allowed = {'documents', 'interactive'} | ({'media'} if steps['timeline'] else set())
            plan = validate_plan(invoke_model(active_provider, request, steps['evidence'], timeout),
                                 steps['evidence'], allowed)
            steps['blueprint'] = dict(plan, course=course, synthetic=full_request['input_mode'] == 'synthetic',
                                      model_provider=type(active_provider).__name__,
                                      source_evidence=steps['evidence']['id'])
            run.update(state='blueprint_ready', error=None)
            app.save_run(run)
        steps['artifacts'] = publish_files(app, run, 'review_pending')
        run.update(state='draft_ready', error=None)
    except Exception as exc:
        run.update(state='failed', error=type(exc).__name__ + ': ' + str(exc)[:300])
    app.save_run(run)
    return app.get_run(run['id'], check=False)


def review(app, id_, *, reviewer, decision, note):
    run = app.get_run(id_, check=False)
    if (run['snapshot'].get('platform') != '0.4' or not reviewer.strip() or not note.strip()
            or decision not in {'approved', 'rejected', 'withdrawn'}):
        raise ValueError('An operator decision and rationale are required')
    if decision == 'approved':
        if run['state'] != 'draft_ready':
            raise ValueError('Review requires a current draft')
        ensure_current(app, run)
        for name in run['steps']['artifacts']:
            artifact(app, id_, name)
        run['steps']['artifacts'] = publish_files(app, run, 'approved')
    run['review'] = dict(reviewer=reviewer, decision=decision, note=note, at=now())
    run['state'] = decision
    app.save_run(run)
    return app.get_run(id_, check=False)


def publish(app, id_, *, public=False):
    run = app.get_run(id_, check=False)
    if run['state'] != 'approved' or not run['review']:
        raise PermissionError('Operator approval is required')
    ensure_current(app, run, public=public)
    if public:
        raise PermissionError('Synthetic workspace cannot publish externally')
    for name in run['steps']['artifacts']:
        artifact(app, id_, name)
    run['state'] = 'published'
    app.save_run(run)
    return app.get_run(id_, check=False)


def artifact(app, id_, name):
    run = app.get_run(id_, check=False)
    if (name not in FILES or run['snapshot'].get('platform') != '0.4'
            or run['state'] not in {'draft_ready', 'approved', 'published'}):
        raise PermissionError('Artifact state or name unavailable')
    ensure_current(app, run)
    item = run['steps']['artifacts'].get(name)
    if not item:
        raise ValueError('Unknown artifact')
    path = inside(app.root, item['path'])
    if not path.is_file() or sha256(path) != item['sha256']:
        raise PermissionError('Artifact fingerprint changed')
    return path
