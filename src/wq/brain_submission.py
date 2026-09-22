"""逐个 Alpha 的正式提交队列：新鲜检查、一次 POST、持久化 GET 对账。"""
import datetime as dt
import json
from pathlib import Path

from . import store, util
from .brain_client import BrainClient, retry_delay
from .brain_jobs import get_with_reauth, later
from .errors import AdapterError

REQUIRED = {'LOW_SHARPE', 'LOW_FITNESS', 'LOW_TURNOVER', 'HIGH_TURNOVER',
            'CONCENTRATED_WEIGHT', 'LOW_SUB_UNIVERSE_SHARPE', 'SELF_CORRELATION'}
DDL = '''CREATE TABLE IF NOT EXISTS brain_submissions (
 task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), alpha_id TEXT NOT NULL UNIQUE,
 sim_id TEXT NOT NULL, state TEXT NOT NULL, submission_id TEXT,
 started_at TEXT NOT NULL, updated_at TEXT NOT NULL)'''


def setup(conn):
    conn.execute(DDL)


def source(conn, alpha_id):
    if not isinstance(alpha_id, str) or not alpha_id.isascii() or not alpha_id.isalnum():
        raise ValueError('Alpha ID格式无效')
    row = conn.execute('SELECT * FROM simulations WHERE remote_id=? AND synthetic=0', (alpha_id,)).fetchone()
    if not row or row['source'] != 'api':
        raise ValueError('仅允许有真实API证据的研究Alpha；教学/手工快照不能直接提交')
    evidence = json.loads(row['evidence_json'])
    if evidence.get('purpose') != 'research_validation':
        raise ValueError('教学Alpha不得进入正式提交队列')
    candidate = conn.execute('SELECT * FROM candidates WHERE candidate_id=?', (row['candidate_id'],)).fetchone()
    if not candidate or candidate['synthetic']:
        raise ValueError('缺真实候选')
    alpha = util.read_json(evidence['path'])
    if alpha.get('id') != alpha_id or alpha.get('regular', {}).get('code') != candidate['expression']:
        raise ValueError('本地Alpha证据与账本不一致')
    return dict(row), alpha


def identity(alpha):
    return util.sha256_json({k: alpha.get(k) for k in ('id', 'regular', 'settings')})


def problems(checks):
    if not isinstance(checks, list) or not checks:
        return ['缺少平台检查']
    if any(not isinstance(x, dict) or not isinstance(x.get('name'), str) for x in checks):
        return ['检查结构无效']
    names = [x['name'] for x in checks]
    errors = ['缺少检查: '+','.join(sorted(REQUIRED-set(names)))] if REQUIRED-set(names) else []
    if len(names) != len(set(names)):
        errors.append('检查名称重复')
    errors.extend(x['name']+':'+str(x.get('result')) for x in checks if x.get('result') != 'PASS')
    return errors


def readiness(conn, alpha_id):
    sim, alpha = source(conn, alpha_id)
    errors = problems(alpha.get('is', {}).get('checks'))
    return {'alpha_id': alpha_id, 'request_hash': identity(alpha), 'ready': not errors,
            'blockers': errors, 'note': '本地快照仅预筛；入队后重新读取官方检查，PASS不代表盈利'}


def validate_review(review, alpha):
    if not isinstance(review, dict) or review.get('schema') != 'wq.submission-review/v1':
        raise ValueError('缺提交研究验收文档')
    if review.get('alpha_id') != alpha['id'] or review.get('request_hash') != identity(alpha):
        raise ValueError('提交验收未绑定Alpha及其完整设置')
    if review.get('decision') != 'approved_for_submission':
        raise ValueError('尚未通过提交验收')
    for key in ('originality', 'robustness', 'data_timing'):
        item = review.get(key, {})
        if not isinstance(item, dict) or item.get('accepted') is not True or not isinstance(item.get('evidence'), str) or len(item['evidence'].strip()) < 16:
            raise ValueError('缺实质研究验收依据: '+key)
    if not isinstance(review.get('reviewer'), str) or len(review['reviewer'].strip()) < 3:
        raise ValueError('缺验收人/渠道')
    timestamp = util.parse_iso(review['reviewed_at'])
    if timestamp.tzinfo is None or not dt.timedelta(0) <= util.now()-timestamp <= dt.timedelta(days=7):
        raise ValueError('提交验收过期或来自未来')


def enqueue(conn, cfg, alpha_id, review):
    setup(conn)
    sim, alpha = source(conn, alpha_id)
    validate_review(review, alpha)
    if cfg.get('research_feedback','enabled'):
        from .feedback import require_submission_evidence
        require_submission_evidence(conn, alpha)
    # FAIL不能入队；SELF_CORRELATION PENDING允许只读检查阶段尝试补齐。
    checks = alpha.get('is', {}).get('checks', [])
    if any(x.get('result') == 'FAIL' for x in checks):
        raise ValueError('Alpha存在明确FAIL，禁止提交；不会为验收流程强行提交')
    if cfg.get('brain_submission', 'enabled') is not True:
        raise ValueError('brain_submission.enabled未启用')
    prior = conn.execute('SELECT task_id,state FROM brain_submissions WHERE alpha_id=?', (alpha_id,)).fetchone()
    payload = {'alpha_id': alpha_id, 'review': review, 'expected': alpha,
               'title': '正式提交验收：'+alpha_id}
    if prior:
        task = conn.execute('SELECT status FROM tasks WHERE task_id=?', (prior['task_id'],)).fetchone()
        if prior['state'] == 'checking' and task['status'] in ('blocked', 'failed'):
            conn.execute("UPDATE tasks SET payload_json=?,status='queued',not_before=?,last_error=NULL,max_attempts=attempts+180 WHERE task_id=?",
                         (json.dumps(payload,ensure_ascii=False),util.now_iso(),prior['task_id']))
            store.add_attempt(conn, prior['task_id'], 'renew_submission_preflight', 'queued', {'note': 'explicit command; no previous POST'})
        return prior['task_id'], False
    conn.execute('SAVEPOINT enqueue_submission')
    try:
        tid, created = store.enqueue_task(conn, 'brain_submission', payload, 'brain-submit:'+alpha_id, max_attempts=180)
        if created:
            conn.execute('INSERT INTO brain_submissions VALUES(?,?,?,?,?,?,?)',
                         (tid, alpha_id, sim['sim_id'], 'checking', None, util.now_iso(), util.now_iso()))
        conn.execute('RELEASE enqueue_submission')
        return tid, created
    except Exception:
        conn.execute('ROLLBACK TO enqueue_submission'); conn.execute('RELEASE enqueue_submission')
        raise



def set_state(conn, tid, state):
    conn.execute('UPDATE brain_submissions SET state=?,updated_at=? WHERE task_id=?', (state, util.now_iso(), tid))


def accepted(alpha):
    return (alpha.get('status') in ('ACTIVE', 'INACTIVE') and alpha.get('stage') == 'OS'
            and isinstance(alpha.get('dateSubmitted'), str) and bool(alpha['dateSubmitted']))


def _save_acceptance(conn, row, alpha, path):
    sid = row['submission_id']
    if not sid:
        sid = store.add_submission(conn, row['sim_id'], row['alpha_id'], 'accepted',
                                   {'evidence': str(path), 'dateSubmitted': alpha['dateSubmitted'],
                                    'platform_status': alpha['status'], 'note': '平台接收，不代表收入或最终有效'})
        conn.execute('UPDATE brain_submissions SET submission_id=? WHERE task_id=?', (sid, row['task_id']))
    conn.execute('UPDATE submissions SET receipt_json=?,updated_at=? WHERE submission_id=?',
                 (json.dumps({'evidence': str(path), 'dateSubmitted': alpha['dateSubmitted'],
                              'platform_status': alpha['status'], 'stage': alpha['stage']}, ensure_ascii=False), util.now_iso(), sid))
    set_state(conn, row['task_id'], 'accepted')
    conn.execute("UPDATE candidates SET status='submitted' WHERE candidate_id=(SELECT candidate_id FROM simulations WHERE sim_id=?)", (row['sim_id'],))
    return 'succeeded', {'alpha_id': row['alpha_id'], 'submission_id': sid, 'platform_status': alpha['status']}, None


def save_acceptance(conn, row, alpha, path):
    conn.execute('SAVEPOINT accept_submission')
    try:
        result = _save_acceptance(conn, row, alpha, path)
        conn.execute('RELEASE accept_submission')
        return result
    except Exception:
        conn.execute('ROLLBACK TO accept_submission'); conn.execute('RELEASE accept_submission')
        raise


def step(conn, cfg, task, payload):
    setup(conn)
    row = conn.execute('SELECT * FROM brain_submissions WHERE task_id=?', (task['task_id'],)).fetchone()
    if not row:
        return 'blocked', {}, '缺提交状态记录'
    tid, aid = row['task_id'], row['alpha_id']
    if row['state'] == 'post_started':
        return 'unknown', {}, 'POST结果未知；只允许reconcile-submit查询，禁止重发'
    if row['state'] == 'accepted':
        return 'succeeded', {'alpha_id': aid}, None
    if row['state'] not in ('checking', 'polling', 'verifying'):
        return 'blocked', {}, '已停止的提交任务需检查，禁止重复POST'
    if row['state'] != 'checking' and util.now()-util.parse_iso(row['started_at']) > dt.timedelta(hours=24):
        return ('unknown' if row['state'] != 'checking' else 'blocked'), {}, '提交检查/对账超过24小时，保留证据等待核实'
    cooldown = store.get_flag(conn, 'brain_not_before')
    if cooldown and util.now() < util.parse_iso(cooldown):
        return later(conn, tid, (util.parse_iso(cooldown)-util.now()).total_seconds(), '遵守BRAIN全局冷却')
    client = BrainClient(cfg.private_dir)
    root = Path(cfg.private_dir)/'brain-submissions'/aid
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not list(client.jar):
        # A missing local cookie may be repaired from macOS Keychain. This is
        # still read-only: no submission POST is attempted by preflight.
        code, _, _ = client.preflight(cfg)
        if not 200 <= int(code) < 300:
            return 'blocked', {}, f'BRAIN预检返回HTTP {code}；未发送提交POST'
    try:
        if row['state'] == 'polling':
            status, headers, data = get_with_reauth(client, cfg, '/alphas/'+aid+'/submit')
            util.write_json(str(root/'poll.json'), {'status': status, 'headers': headers, 'data': data})
            # 不从空200或204推断成功；随后读取Alpha的真实提交状态。
            if status in (200, 204) and not headers.get('retry-after'):
                set_state(conn, tid, 'verifying')
            return later(conn, tid, retry_delay(headers.get('retry-after')), '等待提交处理/真实状态核验')
        status, headers, alpha = get_with_reauth(client, cfg, '/alphas/'+aid)
        if status != 200 or not isinstance(alpha, dict) or identity(alpha) != identity(payload['expected']):
            return 'unknown', {}, '官方Alpha身份/表达式/设置改变，停止提交并对账'
        util.write_json(str(root/'latest-alpha.json'), alpha)
        if accepted(alpha):
            return save_acceptance(conn, row, alpha, root/'latest-alpha.json')
        if headers.get('retry-after'):
            return later(conn, tid, retry_delay(headers['retry-after']), '官方要求等待后核验Alpha')
        if row['state'] == 'verifying':
            if alpha.get('status') != 'UNSUBMITTED':
                return 'unknown', {}, '未识别的平台提交状态，不能当作接收成功'
            if any(x.get('result') == 'FAIL' for x in alpha.get('is', {}).get('checks', [])):
                set_state(conn, tid, 'rejected')
                return 'blocked', {}, '平台检查未通过；不重新提交'
            return later(conn, tid, 300, '尚未观察到平台接收，保留原提交不重发')
        deadline = cfg.get('brain_submission', 'authorized_until')
        if (cfg.get('brain_submission', 'enabled') is not True or not deadline
                or util.now() >= util.parse_iso(deadline)):
            return 'blocked', {}, '正式提交未启用或授权到期'
        validate_review(payload['review'], alpha)
        if cfg.get('research_feedback','enabled'):
            from .feedback import require_submission_evidence
            require_submission_evidence(conn, alpha)
        if alpha.get('status') != 'UNSUBMITTED':
            return 'blocked', {}, 'Alpha不是未提交状态'
        basic = alpha.get('is', {}).get('checks', [])
        if any(x.get('result') == 'FAIL' for x in basic):
            return 'blocked', {}, '最新回测已有FAIL，禁止提交'
        status, check_headers, check_data = get_with_reauth(client, cfg, '/alphas/'+aid+'/check')
        util.write_json(str(root/'checks.json'), {'status': status, 'data': check_data})
        if check_headers.get('retry-after'):
            return later(conn, tid, retry_delay(check_headers['retry-after']), '完整提交检查仍在计算')
        checks = check_data.get('is', {}).get('checks') if status == 200 and isinstance(check_data, dict) and isinstance(check_data.get('is'), dict) else None
        errors = problems(checks)
        if errors and all(x.endswith(':PENDING') for x in errors):
            return later(conn, tid, 300, '提交检查仍有PENDING；不发送POST')
        if errors:
            return 'blocked', {'checks': errors}, '提交检查未全部PASS：'+'; '.join(errors)
        if conn.execute("SELECT 1 FROM tasks WHERE status='unknown' AND task_id!=?", (tid,)).fetchone():
            return 'blocked', {}, '存在UNKNOWN，禁止新增提交'
        pending = conn.execute("SELECT 1 FROM brain_submissions WHERE state IN ('post_started','polling','verifying') AND task_id!=?", (tid,)).fetchone()
        if pending:
            return later(conn, tid, 300, '已有在途提交，串行等待')
        # 单账号滚动24小时最多一次POST；未知和拒绝尝试也计入。
        last = store.get_flag(conn, 'brain_last_submit_at')
        if last and util.now()-util.parse_iso(last) < dt.timedelta(hours=24):
            return later(conn, tid, (util.parse_iso(last)+dt.timedelta(hours=24)-util.now()).total_seconds(), '本地24小时提交上限')
        set_state(conn, tid, 'post_started')
        conn.execute('UPDATE brain_submissions SET started_at=? WHERE task_id=?', (util.now_iso(), tid))
        store.set_flag(conn, 'brain_last_submit_at', util.now_iso())
        conn.commit()  # 必须在网络写请求前落盘；进程崩溃不丢提交意图。
        try:
            status, headers, data = client.request('POST', '/alphas/'+aid+'/submit')
        except AdapterError as exc:
            if exc.kind in (AdapterError.AUTH, AdapterError.POLICY, AdapterError.RATE_LIMIT):
                set_state(conn, tid, 'rejected')
                if exc.kind == AdapterError.RATE_LIMIT:
                    store.set_flag(conn, 'brain_not_before', (util.now()+dt.timedelta(seconds=exc.retry_after or 300)).isoformat())
                if exc.kind == AdapterError.AUTH:
                    raise
                return 'blocked', {}, str(exc)+'；不会自动重发POST'
            raise
        util.write_json(str(root/'receipt.json'), {'status': status, 'headers': headers, 'data': data})
        if status not in (200, 201, 202, 204):
            return 'unknown', {}, '未识别提交回执；不重发'
        set_state(conn, tid, 'polling')
        return later(conn, tid, retry_delay(headers.get('retry-after')), '已发出一次提交，等待官方接收证据')
    except AdapterError as exc:
        state = conn.execute('SELECT state FROM brain_submissions WHERE task_id=?', (tid,)).fetchone()[0]
        if state != 'post_started' and exc.kind in (AdapterError.NETWORK, AdapterError.RATE_LIMIT):
            if exc.kind == AdapterError.RATE_LIMIT:
                store.set_flag(conn, 'brain_not_before', (util.now()+dt.timedelta(seconds=exc.retry_after or 300)).isoformat())
            return later(conn, tid, exc.retry_after or 300, str(exc))
        raise


def reconcile(conn, cfg, alpha_id):
    """一次只读查询；不从未提交状态推断旧POST丢失，永不重发。"""
    setup(conn)
    row = conn.execute('SELECT * FROM brain_submissions WHERE alpha_id=?', (alpha_id,)).fetchone()
    if not row:
        raise ValueError('没有该Alpha的提交任务')
    task = conn.execute('SELECT payload_json FROM tasks WHERE task_id=?', (row['task_id'],)).fetchone()
    payload = json.loads(task[0])
    cooldown = store.get_flag(conn, 'brain_not_before')
    if cooldown and util.now() < util.parse_iso(cooldown):
        raise ValueError('BRAIN全局冷却未结束：'+cooldown)
    client = BrainClient(cfg.private_dir)
    _, _, alpha = get_with_reauth(client, cfg, '/alphas/'+alpha_id)
    if identity(alpha) != identity(payload['expected']):
        raise ValueError('Alpha与原提交不一致')
    path = Path(cfg.private_dir)/'brain-submissions'/alpha_id/'reconcile.json'
    util.write_json(str(path), alpha)
    if accepted(alpha):
        _, result, _ = save_acceptance(conn, row, alpha, path)
        store.finish_task(conn, row['task_id'], 'succeeded', result)
        return result
    return {'alpha_id': alpha_id, 'platform_status': alpha.get('status'),
            'accepted': False, 'note': '未证明已接收；保留原状态，不重发POST'}
