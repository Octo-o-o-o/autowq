"""Local, append-only research evidence. Shadow advice never authorizes a simulation."""
import datetime as dt
import json
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from . import util, store, research_dsl

VERSION = 'learning-v1'
SIMULATION_COMPLETED = frozenset(('passed','failed','quality_failed','unchecked'))

class RequestBudgetExhausted(ValueError):
    pass

DDL = '''CREATE TABLE IF NOT EXISTS learning_trials(
 trial_id TEXT PRIMARY KEY, cycle_id INTEGER, task_id TEXT,
 execution_id TEXT NOT NULL, structure_id TEXT, family_id TEXT,
 scope_id TEXT NOT NULL, document_json TEXT NOT NULL, available_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_outcomes(
 version_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL, document_json TEXT NOT NULL,
 available_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS learning_outcome_time ON learning_outcomes(trial_id,available_at);
CREATE TABLE IF NOT EXISTS learning_rules(
 rule_id TEXT NOT NULL, version_id TEXT PRIMARY KEY, document_json TEXT NOT NULL,
 available_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_selections(
 cycle_id INTEGER PRIMARY KEY, document_json TEXT NOT NULL, available_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_experiments(
 experiment_id TEXT PRIMARY KEY, document_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_assignments(
 cycle_id INTEGER PRIMARY KEY, experiment_id TEXT NOT NULL, arm TEXT NOT NULL,
 baseline_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_allocation_scopes(
 cycle_id INTEGER PRIMARY KEY, stratum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_resource_cycles(
 cycle_id INTEGER PRIMARY KEY, experiment_id TEXT NOT NULL, work_kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_stop_types(
 experiment_id TEXT NOT NULL, stop_type TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,stop_type));
CREATE TABLE IF NOT EXISTS learning_observations(
 observation_id TEXT PRIMARY KEY, alpha_id TEXT NOT NULL, document_json TEXT NOT NULL,
 available_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_experiment_stops(
 experiment_id TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_reuses(
 trial_id TEXT NOT NULL, cycle_id INTEGER NOT NULL, parent_id TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(trial_id,cycle_id));
CREATE TABLE IF NOT EXISTS learning_pools(
 pool_id TEXT PRIMARY KEY, document_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS learning_trial_task ON learning_trials(task_id);
CREATE INDEX IF NOT EXISTS learning_trial_cycle ON learning_trials(cycle_id)'''


def timestamp(value=None):
    if value is None: return util.now_iso()
    parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('Timezone required')
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec='milliseconds')


def current_baseline(cfg):
    from . import autopilot
    profile_path=Path(cfg.resolve(cfg.get('routing','profiles_file',default='config/profiles.json')))
    profile_hash=hashlib.sha256(profile_path.read_bytes()).hexdigest() if profile_path.is_file() else None
    workflow_path=cfg.get('workflow','file')
    workflow_hash=hashlib.sha256(Path(cfg.resolve(workflow_path)).read_bytes()).hexdigest() if workflow_path else None
    source={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.rglob('*.py')}
    return {'source':util.sha256_json(source),'policy':util.sha256_json(autopilot.policy(cfg)),
            'models':util.sha256_json({'routing':cfg.get('routing',default={}), 'profiles':profile_hash,'workflow':workflow_hash,'config':util.sha256_json({k:v for k,v in cfg.data.items() if k not in ('ui','notifications','desktop','brain_submission')})}),
            'budget':util.sha256_json({k:cfg.get(k,default={}) for k in ('brain_api','autopilot','limits')}),
            'usable_definition':'latest_complete_feedback_no_validation_gaps_no_platform_blockers_v2'}


def active_experiment(conn):
    setup(conn)
    for row in conn.execute('SELECT * FROM learning_experiments ORDER BY created_at DESC'):
        if conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(row['experiment_id'],)).fetchone():continue
        doc=json.loads(row['document_json'])
        used=conn.execute('SELECT COUNT(*) FROM learning_assignments WHERE experiment_id=?',(row['experiment_id'],)).fetchone()[0]
        pending=conn.execute("SELECT COUNT(*) FROM learning_assignments a JOIN research_cycles c ON c.cycle_id=a.cycle_id WHERE a.experiment_id=? AND c.state!='closed'",(row['experiment_id'],)).fetchone()[0]
        if used < doc['max_cycles'] or pending: return dict(row)
    return None


def arm_report(conn,trials,experiment_id,at=None):
    groups={arm:[] for arm in ('baseline','learning')}
    mapping={r['cycle_id']:r['arm'] for r in conn.execute('SELECT * FROM learning_assignments WHERE experiment_id=? AND created_at<=?',(experiment_id,timestamp(at)))}
    for trial in trials:
        if trial['task_id']: groups[mapping[trial['cycle_id']]].append(trial)
    result={}
    for arm,items in groups.items():
        counts=Counter((x['outcome'] or {}).get('execution','not_run') for x in items)
        families={x['family_id'] or x['execution_id'] for x in items if (x['outcome'] or {}).get('quality')=='usable' and x['document'].get('source')!='campaign'}
        result[arm]={'allocated_cycles':sum(v==arm for v in mapping.values()),'execution':dict(counts),
            'usable_families':len(families),'campaign_measurements':sum(x['document'].get('source')=='campaign' for x in items),'actual_requests':sum(bool((x['outcome'] or {}).get('request_started')) for x in items),
            'quality_failures':sum((x['outcome'] or {}).get('quality')=='weak' for x in items),
            'conclusion':'not_evidence_of_superiority','human_seconds':None}
    return result


def setup(conn):
    for sql in DDL.split(';'):
        if sql.strip(): conn.execute(sql)


def ast_diff(before, after, path='$'):
    """Ordered structural edits; neither commutative nor economic equivalence is assumed."""
    if before == after: return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [edit for key in sorted(set(before) | set(after))
                for edit in ast_diff(before.get(key), after.get(key), path+'.'+key)]
    return [{'path': path, 'before': before, 'after': after}]


def identities(ast, bindings, settings, profile='proposal'):
    expression, _, family = research_dsl.compile_ast(ast, bindings, profile)
    used = {name: bindings[name] for name in research_dsl.roles_used(ast)}
    return {'execution_id': util.sha256_json({'expression': expression, 'settings': settings}),
            'structure_id': util.sha256_json(ast), 'family_id': family,
            'scope_id': util.sha256_json({'bindings': used,
                'market': {k: settings.get(k) for k in ('region', 'universe', 'delay')}}),
            'expression': expression}


def measurement_checks(ast, bindings, settings):
    """Deterministic questions, not fabricated field coverage or economic verification."""
    questions = []
    def visit(node):
        op = node['op']
        if op in ('log', 'div'): questions.append('positive_domain_and_units')
        if op in ('add', 'sub', 'mul', 'div'): questions.append('missingness_and_measurement_alignment')
        if op in research_dsl.GROUP and settings.get('neutralization') not in (None, 'NONE'):
            questions.append('group_transform_is_not_simulation_neutralization')
        if op in research_dsl.TIMESERIES: questions.append('update_frequency_and_holding_period')
        if op == 'field' and not bindings[node['name']].get('measurement'):
            questions.append('measurement_metadata_unavailable')
        for key in ('arg', 'left', 'right'):
            if isinstance(node.get(key), dict): visit(node[key])
    visit(ast)
    return {'questions': sorted(set(questions)), 'coverage': None, 'economic_validity': 'requires_review'}


def capture_feedback(conn, aid, report, *, origin, data_through=None, content_hash=None):
    setup(conn)
    body = {'report': report, 'origin': origin, 'data_through': data_through,
            'content_hash': content_hash, 'supplier_version': None}
    digest = util.sha256_json({'alpha_id': aid, **body})
    conn.execute('INSERT OR IGNORE INTO learning_observations VALUES(?,?,?,?)',
                 (digest, aid, json.dumps(body, ensure_ascii=False), util.now_iso()))
    return digest


def record_trial(conn, trial_id, document, *, cycle_id=None, task_id=None):
    setup(conn)
    required = ('execution_id', 'scope_id')
    if any(not document.get(k) for k in required): raise ValueError('Trial identity required')
    old = conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?', (trial_id,)).fetchone()
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True)
    if old:
        if old[0] != encoded: raise ValueError('Immutable trial changed')
        return
    parent = document.get('parent_id')
    if parent:
        row = conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?', (parent,)).fetchone()
        if not row: raise ValueError('Unknown parent trial')
        base = json.loads(row[0])
        expected = ast_diff({'ast': base.get('ast'), 'settings': base.get('settings')},
                            {'ast': document.get('ast'), 'settings': document.get('settings')})
        if document.get('edit') != expected: raise ValueError('Edit must match actual AST/config difference')
    conn.execute('INSERT INTO learning_trials VALUES(?,?,?,?,?,?,?,?,?)',
                 (trial_id, cycle_id, task_id, document['execution_id'], document.get('structure_id'),
                  document.get('family_id'), document['scope_id'], encoded, util.now_iso()))


def append_outcome(conn, trial_id, outcome):
    setup(conn)
    if not conn.execute('SELECT 1 FROM learning_trials WHERE trial_id=?', (trial_id,)).fetchone():
        raise ValueError('Unknown trial')
    if outcome.get('execution') not in ('not_run', 'pending', 'unknown', 'failed', 'complete'):
        raise ValueError('Invalid execution outcome')
    if outcome.get('quality') not in ('unassessed', 'weak', 'usable'):
        raise ValueError('Invalid quality outcome')
    if outcome['execution'] != 'complete' and outcome['quality'] != 'unassessed':
        raise ValueError('Incomplete execution cannot provide quality evidence')
    if outcome['quality'] == 'usable' and not outcome.get('evidence_complete'):
        raise ValueError('Usable requires complete evidence')
    previous = conn.execute('SELECT version_id,document_json FROM learning_outcomes WHERE trial_id=? ORDER BY available_at DESC,rowid DESC LIMIT 1',(trial_id,)).fetchone()
    if previous and json.loads(previous['document_json']) == outcome: return
    digest = util.sha256_json({'trial': trial_id, 'outcome': outcome, 'previous':previous['version_id'] if previous else None})
    conn.execute('INSERT OR IGNORE INTO learning_outcomes VALUES(?,?,?,?)',
                 (digest, trial_id, json.dumps(outcome, ensure_ascii=False), util.now_iso()))


def as_of(conn, at=None):
    setup(conn)
    cutoff = timestamp(at)
    trials = []
    for row in conn.execute('SELECT * FROM learning_trials WHERE available_at<=? ORDER BY available_at,trial_id', (cutoff,)):
        outcome = conn.execute('SELECT * FROM learning_outcomes WHERE trial_id=? AND available_at<=? ORDER BY available_at DESC,rowid DESC LIMIT 1', (row['trial_id'], cutoff)).fetchone()
        trials.append({**dict(row), 'document': json.loads(row['document_json']),
                       'outcome': json.loads(outcome['document_json']) if outcome else None,
                       'outcome_version': outcome['version_id'] if outcome else None})
    return trials


def sync(conn):
    """Import existing local records at NOW, never pretend they were known historically."""
    from . import autopilot, brain_jobs, feedback
    setup(conn); autopilot.setup(conn); brain_jobs.setup(conn); feedback.setup(conn)
    skipped = []
    for row in conn.execute('SELECT * FROM research_cycles WHERE candidate_json IS NOT NULL ORDER BY cycle_id').fetchall():
        try:
            candidate = json.loads(row['candidate_json']); policy = json.loads(row['policy_json'])
            profile = 'combination' if conn.execute('SELECT 1 FROM combination_plans WHERE cycle_id=?', (row['cycle_id'],)).fetchone() else 'proposal'
            ids = identities(candidate['ast'], policy['bindings'], policy.get('settings', {}), profile)
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append(row['cycle_id'])
            key='cycle:'+str(row['cycle_id'])
            document={'execution_id':util.sha256_json({'rejected_candidate':row['candidate_json'],'policy_hash':row['policy_hash']}),
                      'scope_id':row['policy_hash'],'ast':None,'structure_id':None,'family_id':row['family_hash'],
                      'parent_id':None,'edit':[],'source':'rejected_ast','validation_error':str(exc),
                      'original_candidate_hash':util.sha256_json(row['candidate_json'])}
            record_trial(conn,key,document,cycle_id=row['cycle_id'])
            append_outcome(conn,key,{'execution':'not_run','quality':'unassessed','evidence_complete':False})
            continue
        key = 'cycle:'+str(row['cycle_id'])
        document = {**ids, 'ast': candidate['ast'], 'settings': policy.get('settings', {}),
                    'roles': research_dsl.roles_used(candidate['ast']), 'parent_id': None, 'edit': [],
                    'policy_hash': row['policy_hash'], 'source': 'automatic', 'version': VERSION}
        from . import research_campaign, research_campaign_v2
        allocation=research_campaign.assignment(conn,row['cycle_id'])
        if allocation and allocation.get('schema')==research_campaign_v2.SCHEMA:
            document['source']='campaign'
        selected=conn.execute('SELECT document_json FROM learning_selections WHERE cycle_id=?',(row['cycle_id'],)).fetchone()
        if selected and not conn.execute('SELECT 1 FROM learning_trials WHERE trial_id=?',(key,)).fetchone():
            parent_id=json.loads(selected[0]).get('selected_parent_id')
            parent=conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',(parent_id,)).fetchone()
            if parent:
                base=json.loads(parent[0]);document['parent_id']=parent_id
                document['edit']=ast_diff({'ast':base.get('ast'),'settings':base['settings']},
                                          {'ast':document['ast'],'settings':document['settings']})
        elif conn.execute('SELECT 1 FROM learning_trials WHERE trial_id=?',(key,)).fetchone():
            saved=json.loads(conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',(key,)).fetchone()[0])
            document['parent_id']=saved.get('parent_id');document['edit']=saved.get('edit',[])
        record_trial(conn, key, document, cycle_id=row['cycle_id'])
        if not conn.execute('SELECT 1 FROM learning_outcomes WHERE trial_id=?', (key,)).fetchone():
            append_outcome(conn, key, {'execution': 'not_run', 'quality': 'unassessed',
                                       'evidence_complete': False})
        tasks = [(row['simulation_task'], 'base')] if row['simulation_task'] else []
        tasks += [(r['task_id'], r['label']) for r in conn.execute('SELECT task_id,label FROM cycle_simulations WHERE cycle_id=?', (row['cycle_id'],))]
        for task_id, label in tasks:
            parent_key, parent_doc = key, document
            if label != 'base' and row['simulation_task']:
                base = conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?', ('task:'+row['simulation_task'],)).fetchone()
                if base: parent_key, parent_doc = 'task:'+row['simulation_task'], json.loads(base[0])
            _sync_task(conn, task_id, parent_key, parent_doc, label, row['cycle_id'])
    # Direct/manual queue simulations still count towards the research denominator.
    for row in conn.execute("SELECT task_id FROM tasks WHERE kind='brain_simulation'").fetchall():
        if not conn.execute('SELECT 1 FROM learning_trials WHERE task_id=?', (row[0],)).fetchone():
            _sync_task(conn, row[0], None, None, 'direct', None)
    _sync_manual(conn)
    return {'skipped_invalid_cycles': skipped, 'historical_backfill_available_at': 'import_time'}


def _sync_manual(conn):
    rows=conn.execute("""SELECT s.*,c.expression,c.config_json,c.family_id FROM simulations s
        JOIN candidates c ON c.candidate_id=s.candidate_id
        WHERE s.synthetic=0 AND NOT EXISTS(SELECT 1 FROM brain_runs b WHERE b.alpha_id=s.remote_id)""").fetchall()
    for row in rows:
        key='import:'+row['sim_id'];settings=json.loads(row['config_json'])
        doc={'execution_id':util.sha256_json({'expression':row['expression'],'settings':settings}),
             'scope_id':util.sha256_json(settings),'family_id':'import:'+row['family_id'],
             'expression':row['expression'],'settings':settings,'ast':None,'parent_id':None,'edit':[],
             'source':'manual_import','version':VERSION}
        record_trial(conn,key,doc)
        complete=row['status'] in ('passed','failed','quality_failed')
        append_outcome(conn,key,{'execution':'complete' if complete else 'unknown',
            'quality':'weak' if row['status'] in ('failed','quality_failed') else 'unassessed',
            'evidence_complete':False,'request_started':None,'simulation_status':row['status'],
            'original_observed_at':row['observed_at'],'imported_at':row['imported_at']})


def _sync_task(conn, task_id, parent_id, parent, label, cycle_id):
    from . import feedback
    task = conn.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
    if not task: return
    payload = json.loads(task['payload_json']); request = payload.get('request', {})
    if not isinstance(request.get('regular'), str) or not isinstance(request.get('settings'), dict): return
    settings = request['settings']; expression = request['regular']
    ast = None
    if parent and expression == parent.get('expression'): ast = parent.get('ast')
    elif parent and parent.get('ast') and expression == 'reverse('+parent.get('expression','')+')':
        ast = {'op':'neg','arg':parent['ast']}
    # Unrecognized expression rewrites remain unparsed, rather than inventing an AST.
    doc = {'execution_id': util.sha256_json({'expression': expression, 'settings': settings}),
           'structure_id': util.sha256_json(ast) if ast else None,
           'family_id': parent.get('family_id') if parent else None,
           'scope_id': parent['scope_id'] if parent else util.sha256_json(settings),
           'expression': expression, 'settings': settings, 'ast': ast,
           'roles': parent.get('roles', []) if parent else [], 'parent_id': parent_id,
           'edit': ast_diff({'ast': parent.get('ast'), 'settings': parent['settings']}, {'ast': ast, 'settings': settings}) if parent else [],
           'label': label, 'source': 'automatic' if parent else 'direct', 'version': VERSION}
    from . import research_campaign_v2
    frozen_pair=research_campaign_v2.pair_for_cycle(conn,cycle_id) if cycle_id is not None else None
    if frozen_pair:
        arm='control' if label=='campaign_control' else 'treatment'
        candidate=frozen_pair['candidates'][arm]
        policy=json.loads(conn.execute('SELECT policy_json FROM research_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()[0])
        actual=identities(candidate['ast'],policy['bindings'],settings)
        if actual['expression']!=expression:raise ValueError('Campaign actual AST does not match task request')
        original_family=conn.execute('SELECT family_hash FROM research_cycles WHERE cycle_id=?',(frozen_pair['parent_ref']['cycle_id'],)).fetchone()
        doc.update(ast=candidate['ast'],structure_id=actual['structure_id'],scope_id=actual['scope_id'],
                   roles=research_dsl.roles_used(candidate['ast']),source='campaign',pair_id=frozen_pair['pair_id'],
                   family_id=original_family[0] if original_family else doc['family_id'])
        doc['edit']=ast_diff({'ast':parent.get('ast'),'settings':parent['settings']},{'ast':doc['ast'],'settings':settings}) if parent else []
    key = 'task:'+task_id
    existing=conn.execute('SELECT document_json FROM learning_trials WHERE trial_id=?',(key,)).fetchone()
    if existing:
        saved=json.loads(existing[0])
        if saved['expression']!=expression or saved['settings']!=settings:raise ValueError('Task request changed after recording')
        if saved!=doc and cycle_id is not None:
            conn.execute('INSERT OR IGNORE INTO learning_reuses VALUES(?,?,?,?)',(key,cycle_id,parent_id,util.now_iso()))
    else: record_trial(conn, key, doc, cycle_id=cycle_id, task_id=task_id)
    run = conn.execute('SELECT * FROM brain_runs WHERE task_id=?', (task_id,)).fetchone()
    sim = conn.execute('SELECT * FROM simulations WHERE remote_id=? AND synthetic=0 ORDER BY imported_at DESC LIMIT 1', (run['alpha_id'],)).fetchone() if run and run['alpha_id'] else None
    obs = conn.execute('SELECT * FROM learning_observations WHERE alpha_id=? ORDER BY available_at DESC,rowid DESC LIMIT 1', (run['alpha_id'],)).fetchone() if run and run['alpha_id'] else None
    if run and run['alpha_id'] and not obs:
        old = conn.execute('SELECT report_json FROM research_feedback WHERE alpha_id=?', (run['alpha_id'],)).fetchone()
        if old:
            legacy=json.loads(old[0]);data_through=None;content_hash=None
            try:
                pnl=util.read_json(legacy['pnl_path']);feedback.daily_pnl(pnl)
                data_through=feedback.records(pnl)[-1]['date'];content_hash=util.sha256_json(pnl)
            except (KeyError,ValueError,OSError,TypeError):pass
            capture_feedback(conn, run['alpha_id'], legacy, origin='legacy_backfill',
                             data_through=data_through,content_hash=content_hash)
            obs = conn.execute('SELECT * FROM learning_observations WHERE alpha_id=? ORDER BY rowid DESC LIMIT 1', (run['alpha_id'],)).fetchone()
    observed = json.loads(obs['document_json']) if obs else {}
    report = observed.get('report', {})
    execution = 'unknown' if task['status']=='unknown' else ('complete' if sim and sim['status'] in SIMULATION_COMPLETED else 'failed' if task['status'] in ('failed','blocked','aborted') else 'pending')
    complete = report.get('collection_status')=='complete' and not report.get('validation_gaps')
    quality = 'unassessed'
    if execution=='complete':
        if report.get('platform_blockers'): quality='weak'
        elif complete and report.get('submission_candidate') is True: quality='usable'
        elif complete or sim['status'] in ('failed','quality_failed'): quality='weak'
    outcome = {'execution': execution, 'quality': quality, 'evidence_complete': complete,
               'simulation_status': sim['status'] if sim else None, 'observation_id': obs['observation_id'] if obs else None,
               'request_started': bool(run and run['state']!='not_sent'), 'task_status': task['status'], 'data_through': observed.get('data_through'),
               'content_hash': observed.get('content_hash'), 'origin': observed.get('origin'),
               'diagnostic_tags': sorted(k for k in ('platform_blockers','validation_gaps') if report.get(k)),
               'task_id': task_id, 'model_cost': None, 'human_seconds': None}
    append_outcome(conn, key, outcome)
    if label == 'base' and parent_id: append_outcome(conn, parent_id, outcome)


def derive_rules(conn, ttl_days=30):
    """Observed categorical child-vs-parent changes. No synthetic causal confidence."""
    if type(ttl_days) is not int or not 1 <= ttl_days <= 90: raise ValueError('Invalid rule lifetime')
    trials = as_of(conn); by_id = {t['trial_id']: t for t in trials}; groups = defaultdict(list)
    for trial in trials:
        doc = trial['document']; parent = by_id.get(doc.get('parent_id'))
        if doc.get('source')=='campaign' or (parent and parent['document'].get('source')=='campaign'):continue
        if not parent or not doc.get('edit') or not doc.get('ast') or not parent['document'].get('ast'): continue
        if trial['scope_id'] != parent['scope_id']: continue
        a, b = parent['outcome'], trial['outcome']
        if not a or not b or a['quality']=='unassessed' or b['quality']=='unassessed': continue
        motif = util.sha256_json(doc['edit'])
        groups[(trial['scope_id'], motif)].append((trial, parent))
    created=[]
    for (scope, motif), pairs in groups.items():
        support=[]; contradiction=[]; families=set()
        for trial, parent in pairs:
            families.add(parent['family_id'] or parent['execution_id'])
            item={'trial_id': trial['trial_id'], 'parent_id': parent['trial_id'],
                  'child_version': trial['outcome_version'], 'parent_version': parent['outcome_version']}
            (support if parent['outcome']['quality']=='weak' and trial['outcome']['quality']=='usable' else contradiction).append(item)
        rule_id=util.sha256_json({'scope':scope,'motif':motif})
        evidence={'scope_id':scope,'edit':pairs[0][0]['document']['edit'], 'support':support,
                  'contradictions':contradiction,'independent_families':len(families), 'mode':'shadow',
                  'action':'consider_edit' if len(support)>len(contradiction) else 'seek_counterexample',
                  'confidence_probability':None,'causal':False}
        version=util.sha256_json({'rule':rule_id,**evidence})
        if conn.execute('SELECT 1 FROM learning_rules WHERE version_id=?',(version,)).fetchone(): continue
        now=util.now_iso();evidence['expires_at']=(util.now()+dt.timedelta(days=ttl_days)).isoformat()
        conn.execute('INSERT INTO learning_rules VALUES(?,?,?,?)',(rule_id,version,json.dumps(evidence),now));created.append(rule_id)
    return created


def rules(conn, at=None):
    setup(conn); cutoff=timestamp(at); result=[]; seen=set()
    for row in conn.execute('SELECT * FROM learning_rules WHERE available_at<=? ORDER BY available_at DESC,rowid DESC',(cutoff,)):
        if row['rule_id'] in seen: continue
        seen.add(row['rule_id']); doc=json.loads(row['document_json'])
        result.append({'rule_id':row['rule_id'],**doc,'expired':util.parse_iso(doc['expires_at'])<=util.parse_iso(cutoff)})
    return result


def model_context(conn, bindings, settings, candidate=None, limit=12, operational=False):
    """Only abstract validated ASTs and categorical outcomes leave local storage."""
    from . import research_strategy, research_knowledge
    selected = research_strategy.retrieval(conn, bindings, settings, candidate, limit)
    facts = [{'trial_id': t['trial_id'], 'ast': t['document']['ast'],
              'quality': (t['outcome'] or {}).get('quality', 'unassessed'),
              'execution': (t['outcome'] or {}).get('execution', 'not_run'),
              'parent_id': t['document'].get('parent_id')} for t in selected]
    return {'version': VERSION, 'facts': facts, 'paired_findings':research_knowledge.context(conn,bindings,settings), 'rules_mode': 'evidence_bound_experimental',
            'search': research_strategy.measurement_opportunities(bindings) if operational else research_strategy.search_packet(conn, bindings, settings),
            'interpretation': 'Categorical historical evidence, not causal or out-of-sample proof.'}


def select_plans(conn, cycle_id, proposal, bindings, settings, use_rules=False, preflight=None, frozen_rules=None):
    """Choose one validated candidate. No additional model/Brain calls or retries."""
    setup(conn)
    previous=conn.execute('SELECT document_json FROM learning_selections WHERE cycle_id=?',(cycle_id,)).fetchone()
    if previous:
        frozen=json.loads(previous[0])
        if frozen.get('plans') is not None:
            if frozen['plans']!=proposal.get('plans'):raise ValueError('Plan collection already frozen')
            return frozen['plans'][frozen['selected_index']]['candidate'] if frozen['selected_index'] is not None else None
    plans=proposal.get('plans')
    if not isinstance(plans,list) or not 1<=len(plans)<=3 or 'candidate' in proposal:
        raise ValueError('Provide 1..3 plans OR one candidate')
    history=as_of(conn); exact={t['execution_id'] for t in history}
    def concept(role):
        spec=bindings.get(role,{})
        metadata=spec.get('measurement') or {}
        value=metadata.get('concept') if isinstance(metadata,dict) else None
        value=value or spec.get('cluster')
        return value if isinstance(value,str) and value.strip() else role
    usage=Counter();concept_usage=Counter();seen=set()
    for trial in history:
        if trial['execution_id'] in seen or trial['document'].get('settings')!=settings:continue
        seen.add(trial['execution_id']);past_roles=trial['document'].get('roles',[])
        usage.update(past_roles);concept_usage.update(set(concept(r) for r in past_roles))
    from . import research_strategy
    rows=[];valid=[];shadow=[];batch_exact=set();batch_mechanisms=set()
    ordinal=conn.execute("SELECT COUNT(*) FROM learning_selections WHERE document_json LIKE '%\"rules_requested\": true%'").fetchone()[0]+1
    for i,plan in enumerate(plans):
        if not isinstance(plan,dict) or set(plan)!={'candidate','measurement','prediction','falsifier'}:
            raise ValueError('Plan requires candidate/measurement/prediction/falsifier')
        if any(not isinstance(plan[k],str) or not 8<=len(plan[k])<=800 for k in ('measurement','prediction','falsifier')):
            raise ValueError('Plan reasoning must be bounded and substantive')
        try:
            candidate=plan['candidate'];research_dsl.validate_candidate(candidate,bindings)
            ids=identities(candidate['ast'],bindings,settings)
            reason='exact_duplicate' if ids['execution_id'] in exact else preflight(candidate) if preflight else None
            shape=util.sha256_json(research_strategy.mechanism(candidate['ast']))
            if reason is None:
                if ids['execution_id'] in batch_exact:reason='batch_exact_duplicate'
                elif shape in batch_mechanisms:reason='batch_same_mechanism_variant'
                else:batch_exact.add(ids['execution_id']);batch_mechanisms.add(shape)
            roles=research_dsl.roles_used(candidate['ast'])
            concepts=set(concept(r) for r in roles)
            role_score=sum(1/(1+usage[r]) for r in roles)/len(roles)
            concept_score=sum(1/(1+concept_usage[c]) for c in concepts)/len(concepts)
            advice=research_strategy.rule_advice(conn,candidate['ast'],bindings,settings,history,frozen_rules)
            # The choice ordinal belongs to learning decisions, not global alternating cycles.
            ordinal=conn.execute("SELECT COUNT(*) FROM learning_selections WHERE document_json LIKE '%\"rules_requested\": true%'").fetchone()[0]+1
            exploit=use_rules and (ordinal%4 if preflight else cycle_id%4)
            score=((advice['score'] if exploit else 0),concept_score,role_score)
            row={'index':i,'candidate_hash':util.sha256_json(candidate),'execution_id':ids['execution_id'],
                 'reason':reason,'coverage_score':[concept_score,role_score],'rule_advice':advice,
                 'measurement':measurement_checks(candidate['ast'],bindings,settings),
                 'measurement_contract':research_strategy.measurement_contract(candidate['ast'],bindings,settings)}
            if reason is None:
                valid.append((score,-i,candidate))
                shadow.append(((advice['score'],concept_score,role_score),-i))
        except (ValueError,TypeError,KeyError):
            row={'index':i,'reason':'invalid_candidate'}
        rows.append(row)
    chosen=max(valid,key=lambda x:(x[0],x[1])) if valid else None
    result={'version':VERSION,'candidates':rows,'selected_index':-chosen[1] if chosen else None,
            'strategy':'deterministic_concept_then_role_coverage','selection_probability':None,
            'budget':'one_existing_research_call_one_selected_candidate','rules_applied':bool(use_rules and (ordinal%4 if preflight else cycle_id%4)),
            'rules_requested':use_rules,'plans':plans,'eligible_count':len(valid),
            'choice_opportunity':len(valid)>1,'rule_match_count':sum(len(r.get('rule_advice',{}).get('matched_rules',[])) for r in rows),
            'nonzero_rule_scores':sum(r.get('rule_advice',{}).get('score',0)!=0 for r in rows),
            'shadow_selected_index':-max(shadow)[1] if shadow else None,
            'baseline_selected_index':-max(valid,key=lambda x:(x[0][1:],x[1]))[1] if valid else None,
            'selected_parent_id':rows[-chosen[1]].get('rule_advice',{}).get('parent_id') if chosen else None}
    result['selection_changed_by_rules']=result['selected_index']!=result['baseline_selected_index']
    old=conn.execute('SELECT document_json FROM learning_selections WHERE cycle_id=?',(cycle_id,)).fetchone()
    if old and json.loads(old[0])!=result:raise ValueError('Selection already frozen')
    conn.execute('INSERT OR IGNORE INTO learning_selections VALUES(?,?,?)',(cycle_id,json.dumps(result),util.now_iso()))
    return chosen[2] if chosen else None


def freeze_experiment(conn, experiment_id, baseline, max_cycles, max_requests_per_arm=None):
    setup(conn)
    if not isinstance(experiment_id,str) or not experiment_id.strip(): raise ValueError('Experiment ID required')
    if type(max_cycles) is not int or max_cycles<2:raise ValueError('Experiment needs a bounded cycle count')
    if not isinstance(baseline,dict) or not all(baseline.get(k) for k in ('source','policy','models','budget','usable_definition')):
        raise ValueError('Freeze source/policy/models/budget/usable_definition')
    request_cap=max_requests_per_arm if max_requests_per_arm is not None else max_cycles
    if type(request_cap) is not int or request_cap<1:raise ValueError('Positive per-arm request cap required')
    if active_experiment(conn):
        raise ValueError('An experiment is already frozen; no overlapping experiment')
    doc={'baseline':baseline,'baseline_hash':util.sha256_json(baseline),'max_cycles':max_cycles,
         'max_requests_per_arm':request_cap,'allocation':'stratified_ordinary_alternating_v2',
         'population':'ordinary_only_v2','root_experiment':experiment_id,
         'arms':['baseline','learning'],'version':VERSION}
    conn.execute('INSERT INTO learning_experiments VALUES(?,?,?)',(experiment_id,json.dumps(doc),util.now_iso()))
    return doc


def assign(conn, cycle_id, experiment_id, baseline):
    setup(conn)
    row=conn.execute('SELECT * FROM learning_experiments WHERE experiment_id=?',(experiment_id,)).fetchone()
    if not row:raise ValueError('Unknown experiment')
    if conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(experiment_id,)).fetchone():raise ValueError('Experiment stopped')
    doc=json.loads(row['document_json']);digest=util.sha256_json(baseline)
    if digest!=doc['baseline_hash']:raise ValueError('Frozen experiment baseline changed')
    from .research_lifecycle import lineage,stop_kind
    if any(stop_kind(conn,e)=='owner_stop' for e in lineage(conn,experiment_id)[1]):raise ValueError('Owner stopped this experiment lineage')
    from . import research_campaign
    if research_campaign.assignment(conn,cycle_id):
        conn.execute('INSERT OR IGNORE INTO learning_resource_cycles VALUES(?,?,?)',
                     (cycle_id,experiment_id,'campaign'))
        return None
    if any(research_campaign.assignment(conn,r[0]) for r in conn.execute(
            'SELECT cycle_id FROM learning_assignments WHERE experiment_id=?',(experiment_id,))):
        raise ValueError('Legacy mixed experiment requires a new reviewed epoch')
    cycle=conn.execute('SELECT research_task,state,created_at FROM research_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()
    if not cycle or cycle['state']!='researching' or cycle['created_at']<row['created_at'] or cycle[0] or conn.execute('SELECT 1 FROM learning_trials WHERE cycle_id=?',(cycle_id,)).fetchone():raise ValueError('Assign an existing cycle before dispatch or observation')
    if conn.execute('SELECT 1 FROM learning_assignments WHERE cycle_id=?',(cycle_id,)).fetchone():raise ValueError('Cycle already assigned')
    n=conn.execute('SELECT COUNT(*) FROM learning_assignments WHERE experiment_id=?',(experiment_id,)).fetchone()[0]
    if n>=doc['max_cycles']:raise ValueError('Experiment allocation budget exhausted')
    policy=json.loads(conn.execute('SELECT policy_json FROM research_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()[0])
    stratum=util.sha256_json({'settings':policy.get('settings'), 'bindings':policy.get('bindings')})
    counts=Counter(r['arm'] for r in conn.execute('''SELECT a.arm FROM learning_assignments a
        JOIN learning_allocation_scopes s ON s.cycle_id=a.cycle_id
        WHERE a.experiment_id=? AND s.stratum=?''',(experiment_id,stratum)))
    arm=min(doc['arms'],key=lambda a:(counts[a],doc['arms'].index(a))) if doc.get('population')=='ordinary_only_v2' else doc['arms'][n%2]
    conn.execute('INSERT INTO learning_assignments VALUES(?,?,?,?,?)',(cycle_id,experiment_id,arm,digest,util.now_iso()))
    conn.execute('INSERT INTO learning_allocation_scopes VALUES(?,?)',(cycle_id,stratum))
    conn.execute('INSERT INTO learning_resource_cycles VALUES(?,?,?)',(cycle_id,experiment_id,'ordinary'))
    return arm


def check_request_budget(conn, cycle_id, cfg=None):
    setup(conn)
    assignment=conn.execute('SELECT * FROM learning_assignments WHERE cycle_id=?',(cycle_id,)).fetchone()
    if not assignment:
        check_campaign_epoch(conn,cfg,cycle_id)
        return
    if conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(assignment['experiment_id'],)).fetchone():raise ValueError('Experiment stopped before dispatch')
    experiment=json.loads(conn.execute('SELECT document_json FROM learning_experiments WHERE experiment_id=?',(assignment['experiment_id'],)).fetchone()[0])
    from .research_lifecycle import lineage,stop_kind
    if any(stop_kind(conn,e)=='owner_stop' for e in lineage(conn,assignment['experiment_id'])[1]):raise ValueError('Owner stopped this experiment lineage')
    if cfg and current_baseline(cfg)!=experiment['baseline']:raise ValueError('Frozen experiment baseline changed before request')
    from .research_lifecycle import arm_cycles
    cycles=arm_cycles(conn,assignment['experiment_id'],assignment['arm'])
    reserved=sum(json.loads(r[0]).get('research_cycle_id') in cycles for r in conn.execute("SELECT payload_json FROM tasks WHERE kind='brain_simulation'"))
    if reserved>=experiment['max_requests_per_arm']:
        raise RequestBudgetExhausted('Frozen experiment request budget exhausted; queued/failed/unknown reservations count')


def stop_experiment(conn,experiment_id,reason,stop_type='owner_stop'):
    setup(conn)
    if not conn.execute('SELECT 1 FROM learning_experiments WHERE experiment_id=?',(experiment_id,)).fetchone():raise ValueError('Unknown experiment')
    if not isinstance(reason,str) or not 8<=len(reason)<=1000:raise ValueError('Concrete stop reason required')
    if stop_type not in ('owner_stop','authority_expired','budget_exhausted','baseline_superseded','technical_blocked'):
        raise ValueError('Unknown experiment stop type')
    conn.execute('INSERT OR IGNORE INTO learning_experiment_stops VALUES(?,?,?)',(experiment_id,reason,util.now_iso()))
    conn.execute('INSERT OR IGNORE INTO learning_stop_types VALUES(?,?,?,?)',(experiment_id,stop_type,reason,util.now_iso()))
    return {'experiment_id':experiment_id,'stopped':True,'stop_type':stop_type,'in_flight_requests':'Continue reconciliation only; no replay'}


def freeze_pool(conn,pool_id,trial_ids):
    setup(conn)
    if not isinstance(pool_id,str) or not pool_id.strip():raise ValueError('Pool ID required')
    selected=set(trial_ids or [])
    if not selected:raise ValueError('Explicit trial IDs required')
    eligible={t['trial_id']:t for t in as_of(conn) if t['task_id'] and (t['outcome'] or {}).get('quality')=='usable'}
    if not selected<=set(eligible):raise ValueError('Pool members must have complete usable evidence')
    members=[]
    for tid in sorted(selected):
        trial=eligible[tid];outcome=trial['outcome']
        if not outcome.get('data_through') or not outcome.get('content_hash'):raise ValueError('Data cutoff and content identity required')
        members.append({'trial_id':tid,'execution_id':trial['execution_id'],'outcome_version':trial['outcome_version'],
                        'data_through':outcome['data_through'],'content_hash':outcome['content_hash']})
    doc={'members':members,'method':'post_freeze_quality_retention_v1','portfolio_return':None}
    conn.execute('INSERT INTO learning_pools VALUES(?,?,?)',(pool_id,json.dumps(doc),util.now_iso()))
    return doc


def pool_report(conn,at=None):
    cutoff=timestamp(at);latest={t['trial_id']:t for t in as_of(conn,cutoff)};out=[]
    for row in conn.execute('SELECT * FROM learning_pools WHERE created_at<=?',(cutoff,)):
        pool=json.loads(row['document_json']);eligible=[];retained=[]
        for member in pool['members']:
            trial=latest.get(member['trial_id']);result=trial['outcome'] if trial else None
            if not result or result.get('origin')!='platform_collection' or not result.get('data_through'):continue
            if result['data_through']<=member['data_through'] or result.get('content_hash')==member['content_hash']:continue
            if result.get('execution')!='complete' or result.get('quality')=='unassessed':continue
            eligible.append(member['trial_id'])
            if result['quality']=='usable':retained.append(member['trial_id'])
        out.append({'pool_id':row['pool_id'],'frozen_members':len(pool['members']),'new_data_evaluable':len(eligible),
                    'quality_retained':len(retained),'quality_retention_rate':len(retained)/len(eligible) if eligible else None,
                    'note':'Refreshed full-history quality with newer market dates; not isolated forward returns or portfolio contribution.'})
    return out


def check_campaign_epoch(conn,cfg,cycle_id):
    """Campaigns keep resource ownership without entering the ordinary experiment."""
    from . import research_campaign, research_meta
    resource=conn.execute('SELECT * FROM learning_resource_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()
    dual=bool(store.get_flag(conn,'dual_cycle:'+str(cycle_id))) or bool(cfg and research_meta.enabled(cfg))
    if not dual:return
    if not cfg or not resource or resource['work_kind']!='campaign' or not research_campaign.assignment(conn,cycle_id):
        raise ValueError('Dual-loop request has no experiment assignment')
    if not research_meta.authority(cfg):raise ValueError('Dual-loop request authority expired or disabled')
    if research_meta.required_epoch(conn,cfg,cycle_id)!=resource['experiment_id']:
        raise ValueError('Campaign request belongs to another epoch')


def validate_dispatch(conn,cfg,task_id,payload):
    cycle=payload.get('research_cycle_id')
    if cycle is None:return
    setup(conn)
    assignment=conn.execute('SELECT * FROM learning_assignments WHERE cycle_id=?',(cycle,)).fetchone()
    if not assignment:
        check_campaign_epoch(conn,cfg,cycle)
        return
    if conn.execute('SELECT 1 FROM learning_experiment_stops WHERE experiment_id=?',(assignment['experiment_id'],)).fetchone():raise ValueError('Experiment stopped before dispatch')
    experiment=json.loads(conn.execute('SELECT document_json FROM learning_experiments WHERE experiment_id=?',(assignment['experiment_id'],)).fetchone()[0])
    from .research_lifecycle import lineage,stop_kind
    if any(stop_kind(conn,e)=='owner_stop' for e in lineage(conn,assignment['experiment_id'])[1]):raise ValueError('Owner stopped this experiment lineage')
    if current_baseline(cfg)!=experiment['baseline']:raise ValueError('Frozen experiment baseline changed before dispatch')
    from .research_lifecycle import arm_cycles
    cycles=arm_cycles(conn,assignment['experiment_id'],assignment['arm'])
    reserved=[r['task_id'] for r in conn.execute("SELECT task_id,payload_json FROM tasks WHERE kind='brain_simulation' ORDER BY rowid") if json.loads(r['payload_json']).get('research_cycle_id') in cycles]
    if task_id not in reserved or reserved.index(task_id)>=experiment['max_requests_per_arm']:
        raise ValueError('Frozen experiment dispatch reservation exceeded')


def model_cost(conn, cycle_ids, task_ids=None):
    from . import usage
    calls=set()
    for row in conn.execute("SELECT task_id,payload_json FROM tasks WHERE kind='agent_call'"):
        if json.loads(row['payload_json']).get('autopilot_cycle') not in cycle_ids: continue
        if task_ids is not None and row['task_id'] not in task_ids:continue
        for attempt in conn.execute('SELECT detail_json FROM attempts WHERE task_id=?',(row['task_id'],)):
            detail=json.loads(attempt[0] or '{}')
            if isinstance(detail,dict) and isinstance(detail.get('call_id'),str):calls.add(detail['call_id'])
    known=0.0;unknown=0;tokens=0;unknown_tokens=0
    for call_id in calls:
        row=conn.execute('SELECT * FROM agent_calls WHERE call_id=?',(call_id,)).fetchone()
        metering=usage.for_call(dict(row)) if row else None
        cost=metering.get('cost_usd') if metering else None
        count=metering.get('total_tokens') if metering else None
        if type(cost) in (int,float) and cost>=0 and cost<float('inf'): known+=cost
        else:unknown+=1
        if type(count) is int and count>=0:tokens+=count
        else:unknown_tokens+=1
    return {'linked_calls':len(calls),'known_usd':known,'unknown_cost_calls':unknown,
            'known_tokens':tokens,'unknown_token_calls':unknown_tokens,
            'completeness':'Only linked call receipts; missing linkage is not zero cost.'}


def report(conn, at=None, experiment_id=None):
    from . import research_metrics, research_strategy, research_maintenance
    trials=as_of(conn,at)
    if experiment_id:
        assignments={r['cycle_id']:r['arm'] for r in conn.execute('SELECT * FROM learning_assignments WHERE experiment_id=? AND created_at<=?',(experiment_id,timestamp(at)))}
        trials=[t for t in trials if t['cycle_id'] in assignments]
    actual=[t for t in trials if t['task_id'] or t['document'].get('source')=='manual_import']
    counts=Counter((t['outcome'] or {}).get('execution','not_run') for t in actual)
    usable={t['family_id'] or t['execution_id'] for t in actual if (t['outcome'] or {}).get('quality')=='usable'}
    started=sum(bool((t['outcome'] or {}).get('request_started')) for t in actual)
    effective=counts['complete']
    return {'version':VERSION,'as_of':at or util.now_iso(),'trials':len(trials),'execution_counts':dict(counts),
            'actual_requests':started,'usable_families':len(usable),
            'usable_per_100_effective':100*len(usable)/effective if effective else None,
            'usable_per_100_requests':100*len(usable)/started if started else None,
            'model_cost':model_cost(conn,{t['cycle_id'] for t in trials}) if at is None else None,
            'scope':'automatic cycles, queued Brain simulations and non-synthetic manual imports; manual request costs and quality completeness may be unknown',
            'coverage':research_metrics.coverage(conn),'sensitivity':research_strategy.sensitivity(conn) if at is None else None,
            'learning_state':research_maintenance.state(conn),
            'comparison':research_metrics.comparison(conn,experiment_id) if experiment_id and at is None else None,
            'rules':rules(conn,at),'arms':arm_report(conn,trials,experiment_id,at) if experiment_id else None,'cash':research_metrics.economics(conn,at),'pool_contribution':research_metrics.forward_report(conn,at),'forward_retention':pool_report(conn,at),
            'statistical_eligibility':{'DSR':{'status':'unavailable','missing':['return frequency','all search trials','skew and kurtosis','independent search count']},
                'PBO':{'status':'unavailable','missing':['aligned return matrix for all strategies','preregistered combinatorial split design']}},
            'warning':'Family grouping is conservative; missing outcomes/costs are not zero. No performance superiority established.'}


def command(args):
    from .config import Config
    from . import db
    cfg=Config.load(args.config,str(Path.cwd()));conn=db.connect(cfg.db_path)
    try:
        setup(conn)
        if args.action=='sync':
            result=sync(conn);result['new_shadow_rules']=derive_rules(conn);conn.commit()
        elif args.action=='maintain':
            from . import research_maintenance
            result=research_maintenance.tick(conn,cfg,force=True);conn.commit()
        elif args.action=='gate-audit':
            from .research_metrics import gate_audit
            result=gate_audit(conn,util.read_json(args.input))
        elif args.action=='measurement-policy':
            from . import autopilot
            from .research_measurement import policy_coverage
            result=policy_coverage(cfg,autopilot.policy(cfg))
        elif args.action=='measurement-audit':
            from .research_measurement import audit_panel
            result=audit_panel(util.read_json(args.input))
        elif args.action=='effort':
            from . import research_metrics
            research_metrics.effort(conn,args.entry_id,args.cycle,args.seconds,args.reason);conn.commit();result={'recorded':True}
        elif args.action=='evaluate':
            from . import research_metrics
            result=research_metrics.comparison(conn,args.experiment)
        elif args.action=='freeze-contribution':
            from . import research_metrics
            result=research_metrics.freeze_contract(conn,args.pool,args.trials,args.candidate_trial);conn.commit()
        elif args.action=='stop':
            result=stop_experiment(conn,args.experiment,args.reason);conn.commit()
        elif args.action=='freeze-pool':
            result=freeze_pool(conn,args.pool,args.trials);conn.commit()
        elif args.action=='refresh':
            from . import feedback
            task_id,created=feedback.enqueue_refresh(conn,cfg,args.alpha_id)
            result={'task_id':task_id,'created':created,'kind':'read_only_platform_feedback','new_simulations':0};conn.commit()
        elif args.action=='freeze':
            result=freeze_experiment(conn,args.experiment,util.read_json(args.baseline) if args.baseline else current_baseline(cfg),args.max_cycles,args.max_requests_per_arm)
            if args.research_provider:
                from . import routing
                catalog=routing.catalog(cfg);preset=catalog['presets'][routing.active_preset(conn,cfg,catalog)]
                if args.research_provider not in preset['routes']['research']:raise ValueError('Experimental provider must be in the current authorized research route')
                result['learning_research_provider']=args.research_provider
                conn.execute('UPDATE learning_experiments SET document_json=? WHERE experiment_id=?',(json.dumps(result),args.experiment))
            conn.commit()
        elif args.action=='assign':
            result={'arm':assign(conn,args.cycle,args.experiment,util.read_json(args.baseline) if args.baseline else current_baseline(cfg))};conn.commit()
        else: result=report(conn,args.at,args.experiment)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    finally:conn.close()


def add_parser(sub,lang='zh'):
    p=sub.add_parser('research-learning',help='Local research lineage, shadow rules and as-of evidence')
    p.add_argument('action',choices=['report','sync','replay','freeze','assign','refresh','freeze-pool','stop','maintain','effort','evaluate','freeze-contribution','measurement-audit','measurement-policy','gate-audit'],nargs='?',default='report')
    p.add_argument('--reason');p.add_argument('--pool');p.add_argument('--trials',nargs='+');p.add_argument('--alpha-id');p.add_argument('--at');p.add_argument('--experiment');p.add_argument('--baseline')
    p.add_argument('--max-requests-per-arm',type=int);p.add_argument('--max-cycles',type=int,default=20);p.add_argument('--cycle',type=int)
    p.add_argument('--research-provider');p.add_argument('--input');p.add_argument('--candidate-trial');p.add_argument('--entry-id');p.add_argument('--seconds',type=float)
    p.set_defaults(fn=command)
