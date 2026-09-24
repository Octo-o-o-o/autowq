"""单并发模拟：先持久化派发意图，再POST一次；轮询GET可恢复。"""
import copy
import datetime as dt
import json
import math
from pathlib import Path
from . import util, store, importer, contracts
from .brain_client import BrainClient, safe_url, retry_delay
from .errors import AdapterError, WqExit, INVALID

DDL='''CREATE TABLE IF NOT EXISTS brain_runs(
 task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), state TEXT NOT NULL,
 location TEXT, alpha_id TEXT, evidence_path TEXT, started_at TEXT NOT NULL,
 updated_at TEXT NOT NULL)'''

def setup(conn):conn.execute(DDL)

def later(conn, tid, delay, reason):
    when=(util.now()+dt.timedelta(seconds=max(60,delay))).isoformat(timespec='microseconds')
    conn.execute("UPDATE tasks SET status='queued',not_before=?,last_error=? WHERE task_id=?",(when,reason,tid))
    store.add_attempt(conn,tid,'brain_wait','scheduled',{'not_before':when,'reason':reason})
    return 'retry_scheduled',{},reason


def get_with_reauth(client, cfg, path):
    """GET may be retried once after a successful read-only reauthentication."""
    try:
        return client.request('GET', path)
    except AdapterError as exc:
        if exc.kind != AdapterError.AUTH:
            raise
        # GET is idempotent; unlike POST, retrying it after Keychain login does
        # not duplicate a remote simulation.  A second AUTH is returned to the
        # caller and pauses the queue.
        client.preflight(cfg)
        return client.request('GET', path)

def validate_request(payload):
    if not isinstance(payload,dict) or payload.get('type')!='REGULAR':
        raise ValueError('只支持REGULAR模拟')
    if not isinstance(payload.get('regular'),str) or not payload['regular'].strip():
        raise ValueError('缺少明确表达式')
    if not isinstance(payload.get('settings'),dict):raise ValueError('缺少已核验settings')

def enqueue(conn,cfg,doc):
    setup(conn)
    if cfg.get('brain_api','enabled') is not True:raise ValueError('brain_api.enabled未启用，等待认证和真实接口核验')
    validate_request(doc.get('request'))
    errs=[];contracts.validate_sim_config(doc.get('config',{}),errs)
    if errs:raise ValueError('; '.join(errs))
    if doc.get('config',{}).get('catalog_verified') is not True or not doc['config'].get('fields'):
        raise ValueError('需明确列出已核验的字段')
    if doc.get('evidence',{}).get('settings_verified') is not True:raise ValueError('需核验过的字段和设置证据')
    if not doc['evidence'].get('source'):raise ValueError('缺证据来源')
    if doc.get('purpose') not in ('tutorial_validation','research_validation'):raise ValueError('需声明教学/研究验证用途')
    if doc['purpose']=='research_validation':
        from . import research_gate
        doc=copy.deepcopy(doc)
        doc['evidence']['research_review_sha256']=research_gate.validate(cfg,doc)
    settings=doc['request']['settings']
    for key in ('region','universe','delay','decay','truncation','neutralization'):
        if doc.get('config',{}).get(key)!=settings.get(key):raise ValueError('账本配置与请求不一致: '+key)
    key='brain:'+util.sha256_json({'account':cfg.get('account_alias'),'request':doc['request'],'config':doc['config']})
    return store.enqueue_task(conn,'brain_simulation',doc,key,max_attempts=360)

def step(conn,cfg,task,payload):
    setup(conn);tid=task['task_id']
    row=conn.execute('SELECT * FROM brain_runs WHERE task_id=?',(tid,)).fetchone()
    if cfg.get('brain_api','enabled') is not True:return 'blocked',{},'BRAIN API 尚未启用'
    if row and row['state']=='post_started':return 'unknown',{},'上次POST结果未知，禁止自动重发'
    if row and row['state']=='complete':return 'succeeded',{'evidence':row['evidence_path']},None
    cooldown=store.get_flag(conn,'brain_not_before')
    if cooldown and util.now()<util.parse_iso(cooldown):
        return later(conn,tid,(util.parse_iso(cooldown)-util.now()).total_seconds(),'遵守平台全局Retry-After')
    client=BrainClient(cfg.private_dir)
    root=Path(cfg.private_dir)/'brain-runs'/tid;root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not row:
        if payload.get('purpose')=='research_validation':
            from . import research_gate
            try:research_gate.validate(cfg,payload)
            except ValueError as exc:return 'blocked',{},str(exc)
        deadline=cfg.get('brain_api','authorized_until')
        if not deadline or util.now()>=util.parse_iso(deadline):return 'blocked',{},'BRAIN自动模拟授权未设置或已到期'
        cooldown=store.get_flag(conn,'brain_not_before')
        if cooldown and util.now()<util.parse_iso(cooldown):
            return later(conn,tid,(util.parse_iso(cooldown)-util.now()).total_seconds(),'遵守平台全局Retry-After')
        active=conn.execute("SELECT task_id FROM brain_runs WHERE state IN ('post_started','polling','fetching') LIMIT 1").fetchone()
        if active:return later(conn,tid,60,'已有在途模拟，保持单并发')
        starts=[util.parse_iso(r[0]) for r in conn.execute('SELECT started_at FROM brain_runs')]
        now=util.now()
        daily=cfg.get('brain_api','max_posts_per_24h',default=4)
        if int(daily)<=0:return 'blocked',{},'本地模拟日派发额度为0'
        recent=sorted(t for t in starts if now-t<dt.timedelta(hours=24))
        if recent and len(recent)>=int(daily):
            return later(conn,tid,(recent[-int(daily)]+dt.timedelta(hours=24)-now).total_seconds(),'滚动24小时模拟派发上限，包含拒绝和未知尝试')
        spacing=int(cfg.get('brain_api','min_post_interval_s',default=60))
        if starts and (now-max(starts)).total_seconds()<spacing:
            return later(conn,tid,spacing-(now-max(starts)).total_seconds(),'公共入口最短派发间隔')
        week=util.iso_week()
        used=sum(util.iso_week(util.parse_iso(r[0]))==week for r in conn.execute('SELECT started_at FROM brain_runs'))
        if used>=int(cfg.get('limits','sims_per_week',default=24)):return 'blocked',{},'本地模拟派发上限已到，含结果未知的尝试'
        validate_request(payload['request'])
        # 在唯一允许POST的临界点再次做只读预检；Keychain自动登录只恢复
        # 认证，不重放任何不确定的POST。
        try:
            code, _, _ = client.preflight(cfg)
        except AdapterError as exc:
            if exc.kind == AdapterError.AUTH:
                store.set_flag(conn,'paused','1')
                store.set_flag(conn,'pause_origin','auth')
                store.set_flag(conn,'pause_reason','auth: '+str(exc))
                return 'blocked', {}, str(exc) + '；未发送POST'
            if exc.kind in (AdapterError.NETWORK, AdapterError.RATE_LIMIT):
                # 只读预检遇到瞬时网络/限流：稍后重试，不作废本轮研究；尚未发送任何POST。
                if exc.kind == AdapterError.RATE_LIMIT:
                    store.set_flag(conn,'brain_not_before',(util.now()+dt.timedelta(seconds=max(60,exc.retry_after or 60))).isoformat())
                return later(conn, tid, exc.retry_after or 300, '预检'+str(exc)+'；未发送POST，稍后重试')
            return 'blocked', {}, str(exc) + '；未发送POST'
        if not 200 <= int(code) < 300:
            return 'blocked', {}, f'BRAIN预检返回HTTP {code}；未发送POST'
        conn.execute('INSERT INTO brain_runs(task_id,state,started_at,updated_at) VALUES(?,?,?,?)',(tid,'post_started',util.now_iso(),util.now_iso()))
        conn.commit()
        try:
            status,headers,data=client.request('POST','/simulations',payload['request'])
        except AdapterError as e:
            if e.kind in (AdapterError.AUTH,AdapterError.POLICY,AdapterError.RATE_LIMIT):
                conn.execute("UPDATE brain_runs SET state='rejected',updated_at=? WHERE task_id=?",(util.now_iso(),tid))
                if e.kind==AdapterError.AUTH:
                    store.set_flag(conn,'paused','1')
                    store.set_flag(conn,'pause_origin','auth')
                    store.set_flag(conn,'pause_reason','auth: '+str(e))
                elif e.kind==AdapterError.RATE_LIMIT:
                    store.set_flag(conn,'brain_not_before',(util.now()+dt.timedelta(seconds=max(60,e.retry_after or 60))).isoformat())
                return 'blocked',{},str(e)+'；POST不自动重发'
            raise
        loc=headers.get('location')
        if status not in (201,202) or not loc:return 'unknown',{},'派发回执缺少明确Location，需对账'
        try:
            loc=safe_url(loc)
        except ValueError:
            return 'unknown',{},'回执指向非官方地址，未跟随；需对账'
        if not loc.startswith('https://api.worldquantbrain.com/simulations/'):return 'unknown',{},'回执Location类型异常，未跟随'
        conn.execute("UPDATE brain_runs SET state='polling',location=?,updated_at=? WHERE task_id=?",(loc,util.now_iso(),tid))
        util.write_json(str(root/'receipt.json'),{'http_status':status,'location':loc,'body':data})
        return later(conn,tid,retry_delay(headers.get('retry-after')),'已接受模拟，等待结果；不会再次POST')
    if row['state']=='rejected':return 'blocked',{},'已被拒绝的请求不自动重发'
    if row['state'] in ('polling','fetching') and not list(client.jar):
        try:
            code, _, _ = client.preflight(cfg)
        except AdapterError as e:
            if e.kind == AdapterError.AUTH:
                store.set_flag(conn,'paused','1')
                store.set_flag(conn,'pause_origin','auth')
                store.set_flag(conn,'pause_reason','auth: '+str(e))
                return 'blocked',{},str(e)+'；未重放POST'
            if e.kind in (AdapterError.NETWORK, AdapterError.RATE_LIMIT):
                return later(conn, tid, e.retry_after or 300, '预检'+str(e)+'；已有回执只GET，稍后重试')
            return 'blocked',{},str(e)+'；未重放POST'
        if not 200 <= int(code) < 300:
            return 'blocked',{},f'BRAIN预检返回HTTP {code}；未重放POST'
    try:
        if row['state']=='polling':
            _,headers,data=get_with_reauth(client,cfg,row['location'])
            util.write_json(str(root/'poll.json'),data)
            if data.get('status') in ('ERROR','FAIL','FAILED','CANCELLED'):
                conn.execute("UPDATE brain_runs SET state='failed' WHERE task_id=?",(tid,))
                return 'failed',{'evidence':str(root/'poll.json')},'平台模拟失败'
            if not data.get('alpha'):
                elapsed = max(0, (util.now()-util.parse_iso(row['started_at'])).total_seconds())
                progress = data.get('progress')
                label = (f'平台报告进度 {progress:.0%}'
                         if type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 1
                         else '平台未提供有效进度')
                reason = f'模拟仍未完成：{label}，已等待 {elapsed/60:.0f} 分钟'
                if elapsed >= 86400:
                    return 'blocked', {'evidence': str(root/'poll.json')}, reason+'；超过24小时，保留回执待核对，不重发POST'
                delay = retry_delay(headers.get('retry-after'), default=300 if elapsed >= 3600 else 60)
                return later(conn,tid,delay,reason)
            aid=data['alpha']
            if not isinstance(aid,str) or not aid.isalnum():return 'unknown',{},'异常alpha标识，需核对'
            conn.execute("UPDATE brain_runs SET state='fetching',alpha_id=?,updated_at=? WHERE task_id=?",(aid,util.now_iso(),tid))
            return later(conn,tid,retry_delay(headers.get('retry-after')),'模拟生成Alpha，下一轮读取真实结果')
        if row['state']!='fetching':return 'blocked',{},'模拟状态需人工核对'
        _,headers,alpha=get_with_reauth(client,cfg,'/alphas/'+row['alpha_id'])
    except AdapterError as e:
        if e.kind==AdapterError.AUTH:
            store.set_flag(conn,'paused','1')
            store.set_flag(conn,'pause_origin','auth')
            store.set_flag(conn,'pause_reason','auth: '+str(e))
            return 'blocked',{},str(e)+'；未重放POST'
        if e.kind in (AdapterError.NETWORK,AdapterError.RATE_LIMIT):
            if e.kind==AdapterError.RATE_LIMIT:
                store.set_flag(conn,'brain_not_before',(util.now()+dt.timedelta(seconds=max(60,e.retry_after or 60))).isoformat())
            return later(conn,tid,e.retry_after or 60,str(e))
        raise
    regular=alpha.get('regular',{})
    if alpha.get('id')!=row['alpha_id'] or regular.get('code','').strip()!=payload['request']['regular'].strip():
        return 'unknown',{},'Alpha ID或表达式与任务不符，拒绝入账'
    for key,value in payload['request']['settings'].items():
        if alpha.get('settings',{}).get(key)!=value:return 'unknown',{},'返回的模拟设置不符: '+key
    if not isinstance(alpha.get('is'),dict) or 'sharpe' not in alpha['is']:return later(conn,tid,retry_delay(headers.get('retry-after')),'Alpha统计结果尚未就绪')
    evidence=root/'alpha.json';util.write_json(str(evidence),alpha)
    checks=alpha['is'].get('checks',[])
    labels=[x.get('result') for x in checks if isinstance(x,dict)]
    passed=False if 'FAIL' in labels else (True if labels and len(labels)==len(checks) and all(x=='PASS' for x in labels) else None)
    document={'schema':'wq.imported-result/v1','synthetic':False,'source':'api',
        'simulation':{'remote_id':row['alpha_id'],'expression':payload['request']['regular'],
            'config':payload['config'],'observed_at':util.now_iso(),'stats':{k:v for k,v in alpha['is'].items() if k!='checks'},
            'checks':{'passed':passed,'raw':checks}},
        'quality':{'status':'unknown','notes':'真实模拟结果不等于研究验证或收益'},
        'evidence':{'path':str(evidence),'purpose':payload['purpose'],'source':payload['evidence']}}
    util.write_json(str(root/'import.json'),document)
    conn.execute('BEGIN IMMEDIATE')
    try:
        result=importer.import_result_obj(conn,cfg,document,True)
        conn.execute("UPDATE brain_runs SET state='complete',evidence_path=?,updated_at=? WHERE task_id=?",(str(evidence),util.now_iso(),tid))
        if cfg.get('research_feedback','enabled') and payload['purpose']=='research_validation':
            from .feedback import enqueue
            enqueue(conn,cfg,row['alpha_id'])
        conn.commit()
    except Exception:
        conn.rollback();raise
    return 'succeeded',result,None
