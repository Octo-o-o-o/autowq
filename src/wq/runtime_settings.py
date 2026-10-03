"""Owner-editable operating limits and durable application at a drained boundary.

Scientific policy, evidence bindings and root identities are not generic settings.
A saved request is distinct from an applied configuration; old work drains first.
"""
import copy
import json
import os

from . import store, util
from .config import Config

# key: (group, Chinese label, English label, default, minimum, maximum, nullable)
SPECS = {
    'limits.model_spend_cap_usd': ('providers', '累计已知模型花费上限（美元）', 'Cumulative known model spend (USD)', None, 0, 100000, True),
    'research_dual_loop.max_model_starts': ('research', '累计模型启动上限', 'Cumulative model starts', 64, 1, 100000, False),
    'research_dual_loop.max_evidence_reads': ('research', '累计取证读取上限', 'Cumulative evidence reads', 24, 1, 100000, False),
    'research_dual_loop.max_reads_per_source': ('research', '每个来源取证上限', 'Reads per evidence source', 3, 1, 5, False),
    'research_dual_loop.max_empty_discoveries': ('research', '同一输入无信息发现上限', 'No-information discoveries per input', 2, 1, 100, False),
    'research_dual_loop.max_evidence_tasks_per_day': ('research', '每日取证与核验任务上限', 'Daily evidence and verification tasks', 4, 1, 1000, False),
    'research_framework.evidence_tasks_per_day': ('research', '每日资料采集任务上限', 'Daily collection tasks', 4, 0, 10, False),
    'research_learning.refresh_per_day': ('research', '每日历史反馈刷新上限', 'Daily historical feedback refreshes', 2, 0, 5, False),
    'limits.configs_per_family_max': ('research', '每假设族配置数上限', 'Configurations per hypothesis family', 4, 1, 10000, False),
    'research_learning.experiment_cycles': ('research', '新实验默认轮数（不改现有实验）', 'Default cycles for new experiments only', 40, 2, 100000, False),
    'research_learning.requests_per_arm': ('research', '新实验默认每组请求数', 'Default per-arm requests for new experiments', 40, 1, 100000, False),
    'history_research.every_cycles': ('research', '历史复盘间隔轮数', 'History review cycle cadence', 5, 1, 10000, False),
    'history_research.min_interval_hours': ('research', '历史复盘最小间隔（小时）', 'History review minimum hours', 24, 1, 8760, False),
    'autopilot.interval_s': ('scheduling', '轮次间隔（秒）', 'Cycle interval (seconds)', 3600, 60, 86400, False),
    'autopilot.concurrent_lanes': ('scheduling', '并行泳道上限', 'Parallel lanes', 1, 1, 8, False),
    'autopilot.max_cycles_per_day': ('scheduling', 'UTC 每日轮数上限', 'Cycles per UTC day', 4, 1, 500, False),
    'autopilot.max_cycles_total': ('scheduling', '累计轮数上限', 'Total cycle limit', None, 1, 100000, True),
    'autopilot.error_cooldown_s': ('scheduling', '失败后冷却（秒）', 'Failure cooldown (seconds)', 3600, 0, 86400, False),
    'autopilot.max_simulations_per_week': ('simulation', '自动研究每周模拟上限', 'Autopilot simulations per week', 3, 0, 100000, False),
    'limits.sims_per_week': ('simulation', '所有模拟每周派发上限', 'All simulation dispatches per week', 24, 0, 100000, False),
    'brain_api.max_posts_per_24h': ('simulation', '模拟滚动 24 小时上限', 'Simulation POSTs per rolling 24 hours', 4, 0, 10000, False),
    'brain_api.min_post_interval_s': ('simulation', '模拟最小间隔（秒）', 'Minimum simulation spacing (seconds)', 60, 0, 86400, False),
    'brain_submission.max_posts_per_24h': ('simulation', '正式提交滚动 24 小时上限', 'Submission POSTs per rolling 24 hours', 1, 0, 10000, False),
    'limits.max_agent_parallel': ('providers', '模型调用并行上限（空值跟随泳道）', 'Parallel model calls (none follows lanes)', None, 1, 8, True),
    'limits.supervisor_max_s': ('providers', '调度批次时限（秒）', 'Supervisor batch duration (seconds)', 3000, 300, 14400, False),
    'limits.rate_limit_max_wait_s': ('providers', '通用限流等待上限（秒）', 'Generic rate-limit wait cap (seconds)', 900, 0, 86400, False),
    'routing.quota_probe_s': ('providers', '渠道额度复查间隔（秒）', 'Provider quota probe interval (seconds)', 3600, 60, 86400, False),
    'routing.retry_delay_s': ('providers', '失败重试间隔（秒；空值使用预设）', 'Retry delay (seconds; none uses preset)', None, 0, 3600, True),
    'research_learning.maintenance_interval_s': ('research', '研究维护间隔（秒）', 'Research maintenance interval (seconds)', 21600, 60, 604800, False),
    'routing.max_retries': ('providers', '每渠道失败重试上限', 'Failure retries per provider', 3, 0, 3, False),
    'limits.max_repair_attempts_per_incident': ('providers', '每事件修复调用上限', 'Repair calls per incident', 2, 0, 100, False),
}
DEADLINES = {
    'research_dual_loop.valid_until': ('双环研究授权截止', 'Dual-loop authorization expiry'),
    'routing.authorized_until': ('模型路由授权截止', 'Model routing authorization expiry'),
    'brain_api.authorized_until': ('BRAIN 模拟授权截止', 'BRAIN simulation authorization expiry'),
    'brain_submission.authorized_until': ('正式提交授权截止', 'Submission authorization expiry'),
}
GROUPS = {'research': ('研究与取证额度', 'Research and evidence limits'),
          'scheduling': ('调度', 'Scheduling'), 'simulation': ('模拟与提交', 'Simulation and submission'),
          'providers': ('渠道与执行', 'Providers and execution'), 'authorization': ('授权期限', 'Authorization expiry')}
PENDING = 'runtime_settings_pending'
LAST = 'runtime_settings_last'


def integer(cfg, key):
    spec=SPECS[key];value=cfg.get(*key.split('.'),default=spec[3])
    if value is None and spec[6]:return None
    if type(value) is not int or not spec[4]<=value<=spec[5]:
        raise ValueError(f'{key} must be an integer in {spec[4]}..{spec[5]}')
    return value


def definitions(cfg):
    specs=dict(SPECS)
    from . import routing
    for name, provider in routing.catalog(cfg).get('providers',{}).items():
        specs[f'routing.provider_timeouts.{name}']=('providers', f'{name} 调用超时（秒）', f'{name} timeout (seconds)', provider.get('timeout_s',900), 1, 3600, False)
        if cfg.budget(name).get('unit')=='calls':
            specs[f'budgets.{name}.remaining']=('providers',f'{name} 调用预算基数（不重置已用）',f'{name} call budget basis (usage preserved)',None,0,1000000,True)
        weekly='devin_tickets_per_week' if name=='devin' else name+'_calls_per_week'
        specs['limits.'+weekly]=('providers',f'{name} 每周调用上限',f'{name} calls per week', 1 if name=='devin' else 3 if name=='grok' else 0, 0, 100000, False)
    return specs


def pending(conn):
    raw=store.get_flag(conn,PENDING)
    return json.loads(raw) if raw else None


def parse(cfg,key,raw):
    if key in DEADLINES:
        value=util.parse_iso(raw)
        if not isinstance(raw,str) or not raw.strip() or value<=util.now():raise ValueError('A future ISO8601 time with timezone is required')
        import datetime as dt
        if dt.datetime.fromisoformat(raw.replace('Z','+00:00')).tzinfo is None:raise ValueError('Timezone required, for example +08:00')
        return value.isoformat()
    spec=SPECS.get(key) or definitions(cfg).get(key)
    if not spec:raise ValueError('Unknown operating setting: '+key)
    if raw in ('none','',None) and spec[6]:return None
    try:value=int(raw)
    except (ValueError,TypeError):raise ValueError('Integer required: '+key)
    if str(value)!=str(raw).strip() or not spec[4]<=value<=spec[5]:raise ValueError(f'{key}: {spec[4]}..{spec[5]}')
    return value


def _set(data,path,value):
    keys=path.split('.');node=data
    for key in keys[:-1]:node=node.setdefault(key,{})
    node[keys[-1]]=value


def _hash(path):return util.sha256_json(util.read_json(str(path)))


def _has_experiment(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='learning_experiments'").fetchone() and conn.execute('SELECT 1 FROM learning_experiments').fetchone())


def request(cfg,updates):
    """Validated owner action. Never reset used counters or promise pending values are live."""
    from . import db
    from .wrappers.agent import _acquire_lock
    if not cfg.path:raise ValueError('Configuration file required')
    lock=_acquire_lock(cfg.run_dir,'settings')
    if lock is None:raise ValueError('Another settings save is active; retry')
    conn=db.connect(cfg.db_path)
    try:
        doc=pending(conn)
        base=util.read_json(cfg.path)
        if doc and _hash(cfg.path)!=doc['base_hash']:raise ValueError('Pending configuration changed; cancel the pending request before saving')
        combined={**(doc or {}).get('updates',{}),**updates}
        root=cfg.get('research_dual_loop','root_id')
        for key,flag in [('research_dual_loop.max_model_starts','dual_model_reservations:'),('research_dual_loop.max_evidence_reads','evidence_reads:')]:
            if key in combined and combined[key]<int(store.get_flag(conn,flag+str(root),'0')):
                raise ValueError('Limit cannot be lower than already used: '+key)
        target=copy.deepcopy(base)
        for key,value in combined.items():_set(target,key,value)
        if target==base:return {'state':'unchanged'}
        from . import research_learning
        baseline=research_learning.current_baseline(cfg) if _has_experiment(conn) else None
        doc={'updates':combined,'base':base,'base_hash':util.sha256_json(base),'target':target,
             'target_hash':util.sha256_json(target),'source':baseline['source'] if baseline else None,
             'requested_at':util.now_iso(),'state':'pending'}
        store.set_flag(conn,PENDING,json.dumps(doc));conn.commit()
        runner_lock=_acquire_lock(cfg.run_dir,'runner')
        if runner_lock is None:return {'state':'pending'}
        try:return apply_pending(conn,cfg,settings_locked=True)
        finally:os.close(runner_lock)
    finally:conn.close();os.close(lock)


def cancel(cfg):
    from . import db
    from .wrappers.agent import _acquire_lock
    lock=_acquire_lock(cfg.run_dir,'settings')
    if lock is None:raise ValueError('Settings are being applied; retry after completion')
    conn=db.connect(cfg.db_path)
    try:
        doc=pending(conn)
        if doc and doc.get('state')=='applying':raise ValueError('An interrupted apply must be recovered by the runner first')
        conn.execute('DELETE FROM state_flags WHERE key=?',(PENDING,));conn.commit()
        return {'state':'cancelled'}
    finally:conn.close();os.close(lock)


def apply_pending(conn,cfg,settings_locked=False):
    from .wrappers.agent import _acquire_lock
    lock=None if settings_locked else _acquire_lock(cfg.run_dir,'settings')
    if not settings_locked and lock is None:return {'state':'pending'}
    try:return _apply_pending(conn,cfg)
    finally:
        if lock is not None:os.close(lock)


def _apply_pending(conn,cfg):
    """Caller owns runner lock. The durable receipt recovers file/DB crash windows."""
    doc=pending(conn)
    if not doc:return {'state':'none'}
    if conn.execute("SELECT 1 FROM tasks WHERE status IN ('queued','claimed','running','unknown')").fetchone():return {'state':'pending'}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='research_cycles'").fetchone() and conn.execute("SELECT 1 FROM research_cycles WHERE state!='closed'").fetchone():return {'state':'pending'}
    try:
        actual=_hash(cfg.path)
        if actual not in (doc['base_hash'],doc['target_hash']):raise ValueError('Configuration changed outside this request')
        from . import research_learning, research_lifecycle
        if doc['source'] and research_learning.current_baseline(cfg)['source']!=doc['source']:
            raise ValueError('Engine changed; resave settings against the installed version')
        store.set_flag(conn,PENDING,json.dumps({**doc,'state':'applying'}));conn.commit()
        util.write_json(cfg.path,doc['target'])
        new=Config.load(cfg.path,cfg.root)
        conn.execute('BEGIN IMMEDIATE')
        if _has_experiment(conn):
            latest=conn.execute('SELECT experiment_id FROM learning_experiments ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()[0]
            if research_learning.current_baseline(new)!=research_lifecycle.document(conn,latest)['baseline']:
                transition=research_lifecycle.prepare_transition(conn,new,'tray-owner','Explicit operating settings update')
                research_lifecycle.apply_transition(conn,new,transition)
        result={'state':'applied','updates':doc['updates'],'applied_at':util.now_iso()}
        store.set_flag(conn,LAST,json.dumps(result));conn.execute('DELETE FROM state_flags WHERE key=?',(PENDING,));conn.commit()
        cfg.data=new.data
        return result
    except Exception as exc:
        conn.rollback()
        if _hash(cfg.path)==doc['target_hash']:util.write_json(cfg.path,doc['base'])
        result={'state':'failed','updates':doc['updates'],'error':str(exc),'at':util.now_iso()}
        store.set_flag(conn,LAST,json.dumps(result));conn.execute('DELETE FROM state_flags WHERE key=?',(PENDING,));conn.commit()
        cfg.data=Config.load(cfg.path,cfg.root).data
        return result


def snapshot(conn,cfg,lang='zh'):
    from .i18n import text
    queued=pending(conn);rows=[]
    for key,spec in definitions(cfg).items():
        value=cfg.get(*key.split('.'),default=spec[3]);used=None;note='';active=True
        if type(value) is float and value.is_integer():value=int(value)
        root=cfg.get('research_dual_loop','root_id')
        if key.startswith('research_dual_loop.'):
            active=cfg.get('research_dual_loop','enabled',default=False) is True
            if not active:note=text(lang,'双环模式未启用','Dual-loop mode is disabled')
        if key.startswith('history_research.') and (not cfg.get('history_research','enabled',default=False) or cfg.get('research_dual_loop','enabled',default=False)):
            active=False;note=text(lang,'双环模式由确定性控制器接管，或历史复盘未启用','Dual-loop deterministic controller replaces history calls, or history research is disabled')
        if key=='limits.model_spend_cap_usd':
            from . import usage
            used=usage.known_spend(conn,cfg.get('limits','model_spend_as_of',default='') or '')['known_usd']
            note=text(lang,'仅已知费用计入；未知费用不算零。原计数起点保持不变','Only known costs count; unknown costs are not zero. Counting origin is preserved')
        if key.startswith('budgets.'):
            name=key.split('.')[1];budget=cfg.budget(name)
            used=conn.execute('SELECT COUNT(*) FROM agent_calls WHERE agent=? AND pid IS NOT NULL AND started_at>=?',(name,budget.get('as_of',''))).fetchone()[0]
            note=text(lang,'原计数起点保持不变；实际余额为基数减已用。none 表示余额未知，会阻断非豁免调用','Counting origin is preserved; remaining is basis minus usage. none means unknown and blocks non-waived calls')
            if cfg.debug_window(name)[0]=='ok':active=False;note+=text(lang,'；当前授权窗口豁免','; currently waived by authorization')
        if key in ('research_learning.experiment_cycles','research_learning.requests_per_arm'):
            note=text(lang,'只用于下一次新建实验；现有实验请修改“实验累计轮数／每组请求上限”','Used only for the next new experiment; edit the active experiment limits separately')
        if key=='research_learning.maintenance_interval_s' and not cfg.get('research_learning','maintenance_enabled',default=False):active=False;note=text(lang,'研究维护未启用','Research maintenance is disabled')
        if key=='research_learning.refresh_per_day' and not cfg.get('research_learning','refresh_enabled',default=False):active=False;note=text(lang,'历史反馈刷新未启用','Historical feedback refresh is disabled')
        if key=='research_dual_loop.max_reads_per_source':note=text(lang,'全局上限；还受各来源登记的最大尝试次数约束','Global cap; each registered source also has its own attempt cap')
        if key=='research_dual_loop.max_model_starts':used=int(store.get_flag(conn,'dual_model_reservations:'+str(root),'0'))
        if key=='research_dual_loop.max_evidence_reads':used=int(store.get_flag(conn,'evidence_reads:'+str(root),'0'))
        if key.endswith(('_calls_per_week','_tickets_per_week')):
            name=key.removeprefix('limits.').removesuffix('_calls_per_week').removesuffix('_tickets_per_week')
            note=text(lang,'0 表示不设周上限；独立累计上限仍有效','0 means no weekly cap; cumulative limits still apply')
            if cfg.debug_window(name)[0]=='ok':active=False;note=text(lang,'当前授权窗口豁免此周上限；累计模型上限仍有效','Waived by current authorization; cumulative model cap still applies')
        effective=value
        if key=='limits.max_agent_parallel' and value is None:effective=cfg.get('autopilot','concurrent_lanes',default=1)
        rows.append({'key':key,'group':text(lang,*GROUPS[spec[0]]),'label':text(lang,spec[1],spec[2]),'value':value,'effective':effective,'used':used,'active':active,'note':note,'min':spec[4],'max':spec[5],'nullable':spec[6],'type':'decimal' if key=='limits.model_spend_cap_usd' else 'integer','pending':(queued or {}).get('updates',{}).get(key),'has_pending':key in (queued or {}).get('updates',{})})
    if _has_experiment(conn):
        from . import research_lifecycle as lifecycle
        budget=lifecycle.experiment_cycle_budget(conn);doc=lifecycle.document(conn,budget['root'])
        left=lifecycle.remaining(conn,budget['experiment_id'])
        for key,label,value,used,low in [('experiment_cycles',('实验累计轮数','Experiment cycles'),budget['limit'],budget['used'],2),('experiment_requests_per_arm',('实验每组请求上限','Experiment requests per arm'),doc['max_requests_per_arm'],max(left['used_requests_by_arm'].values()),1)]:
            rows.append({'key':key,'group':text(lang,*GROUPS['research']),'label':text(lang,*label),'value':value,'used':used,'type':'integer','min':low,'max':100000,'nullable':False,'note':text(lang,'实验轮数增加时会相应提高请求下限；每组请求也可单独设置','Increasing cycle limits also raises the request floor; per-arm requests can be set separately')})
    for key,label in DEADLINES.items():
        rows.append({'key':key,'group':text(lang,*GROUPS['authorization']),'label':text(lang,*label),'value':cfg.get(*key.split('.')),'type':'deadline','note':text(lang,'独立授权；不会自动延长其它期限','Independent authorization; does not extend other deadlines'),'pending':(queued or {}).get('updates',{}).get(key),'has_pending':key in (queued or {}).get('updates',{})})
    return {'entries':rows,'pending':bool(queued),'last':json.loads(store.get_flag(conn,LAST,'null')),
            'fixed':[text(lang,'同渠道最多 1 个调用；平台模拟与提交保持串行','One call per channel; platform simulation and submission remain serial'),text(lang,'研究策略及证据合同期限在研究进展中显示；普通额度修改不续期','Policy and evidence expiry are shown in Research progress; limits do not renew them')]}
