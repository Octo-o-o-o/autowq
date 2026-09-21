"""SQLite 持久层。WAL + 外键；claim 用 BEGIN IMMEDIATE 保证双触发只领一个。"""
from __future__ import annotations

import os
import sqlite3

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS families (
  family_id TEXT PRIMARY KEY,
  family_key TEXT NOT NULL UNIQUE,   -- 参数无关指纹：同族变体归并
  hypothesis_id TEXT,
  origin TEXT NOT NULL DEFAULT 'unknown',  -- human|grok|imported-adhoc
  synthetic INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',     -- open|paused|closed
  card_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL REFERENCES families(family_id),
  expression TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE,        -- 精确去重：账号+表达式+全配置+数据版本
  status TEXT NOT NULL DEFAULT 'screening',-- screening|rejected|selected|submitted
  synthetic INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- 通用任务队列。远端类任务超时后进入 UNKNOWN，对账前禁止重发（dedup_key 兜底）。
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                  -- simulation|submission|agent_call|reconcile|import
  payload_json TEXT NOT NULL,
  dedup_key TEXT UNIQUE,               -- 业务去重键（如 simulation 用 config_hash）
  status TEXT NOT NULL DEFAULT 'queued',-- queued|claimed|running|succeeded|failed|blocked|unknown|aborted
  not_before TEXT NOT NULL,
  claim_owner TEXT, claim_token TEXT, lease_until TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- 每一次尝试都留痕，不只存最好结果。
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  event TEXT NOT NULL,                 -- claim|dispatch|reclaim|finish|rate_limit|...
  outcome TEXT,
  detail_json TEXT,
  created_at TEXT NOT NULL
);

-- 模拟结果：平台 checks 与研究质量分开记。
CREATE TABLE IF NOT EXISTS simulations (
  sim_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
  remote_id TEXT,
  source TEXT NOT NULL,                -- manual|api|fixture
  synthetic INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,                -- passed|failed|quality_failed|unknown|awaiting_import
  stats_json TEXT, checks_json TEXT, pnl_json TEXT, quality_json TEXT, evidence_json TEXT,
  observed_at TEXT, imported_at TEXT NOT NULL
);

-- 提交：accepted / final_valid / final_invalid 与报酬资格分开。
CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  sim_id TEXT NOT NULL REFERENCES simulations(sim_id),
  remote_id TEXT,
  status TEXT NOT NULL,                -- pending|accepted|rejected|final_valid|final_invalid|unknown
  receipt_json TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- 现金账本：只有这里算收入。PnL、积分、平台管理资金都不是现金。
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  submission_id TEXT REFERENCES submissions(submission_id),
  kind TEXT NOT NULL,                  -- confirmed_payable|received
  amount REAL NOT NULL,
  currency TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  evidence TEXT NOT NULL               -- 必填证据说明，禁止无据记收入
);

CREATE TABLE IF NOT EXISTS expenses (
  expense_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                  -- cash|quota_points|credit|other
  amount REAL NOT NULL,
  unit TEXT NOT NULL,                  -- USD|pct_week_pool|call|...
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL
);

-- 模型调用账本：预算闸门、活调用去重、故障上限都查这张表。
CREATE TABLE IF NOT EXISTS agent_calls (
  call_id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,                 -- grok|devin
  purpose TEXT NOT NULL,
  incident_id TEXT,
  status TEXT NOT NULL,                -- running|succeeded|failed|timeout|crashed|aborted|artifact_invalid|blocked_*
  pid INTEGER,
  exit_code INTEGER,
  prompt_file TEXT,
  log_path TEXT,
  artifacts_json TEXT,
  detail TEXT,
  week TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, -- 跨会话累计修复尝试
  status TEXT NOT NULL DEFAULT 'open', -- open|paused|closed
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_flags (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_routes (
  task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
  snapshot_json TEXT NOT NULL,
  provider_index INTEGER NOT NULL DEFAULT 0,
  retry_index INTEGER NOT NULL DEFAULT 0,
  phase TEXT NOT NULL DEFAULT 'ready',
  attempt_dir TEXT,
  last_call_id TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL,                 -- REGISTERED|RESEARCHING|GOLD|...|PAID
  evidence TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.isolation_level = None   # autocommit；事务只走显式 BEGIN IMMEDIATE
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(DDL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn
