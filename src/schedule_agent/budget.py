"""Persistent CNY budget: conservative known usage + unresolved reservations."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3

from .contracts import RequestError

DEFAULT_BUDGET=Path(__file__).resolve().parents[2]/'var'/'bailian-budget.sqlite3'
# Integer micro-yuan. Rates are 1/3 CNY per million tokens, above the verified
# Beijing qwen-plus non-thinking tariff (0.8/2). A reservation exceeds the
# allowed 65000*1 + 2048*3 = 71144 micro-yuan per request.
POLICY=(2,10_000_000,100_000,1,3)


class BudgetLedger:
    def __init__(self,path=DEFAULT_BUDGET):
        self.path=Path(path)
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, prompt_tokens INTEGER, completion_tokens INTEGER, response_id TEXT)')
                exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_policy'").fetchone()
                if not exists:
                    totals=self._totals(conn)
                    conn.execute('CREATE TABLE budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER, limit_micro INTEGER, reserve_micro INTEGER, input_rate INTEGER, output_rate INTEGER, legacy_calls INTEGER, migrated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                    conn.execute('INSERT INTO budget_policy (id,version,limit_micro,reserve_micro,input_rate,output_rate,legacy_calls) VALUES (1,?,?,?,?,?,?)',(*POLICY,totals['calls']))
                self._check_policy(conn)
                self._totals(conn)
                conn.commit()
        except (OSError,sqlite3.Error):
            raise RequestError('MODEL_BUDGET_ERROR','Budget ledger unavailable or incompatible; no model call allowed.') from None

    def connect(self):
        return closing(sqlite3.connect(self.path,timeout=5,isolation_level=None))

    def _check_policy(self,conn):
        if conn.execute('SELECT version,limit_micro,reserve_micro,input_rate,output_rate FROM budget_policy').fetchall()!=[POLICY]:
            raise RequestError('MODEL_BUDGET_ERROR','Budget policy changed or unsupported; no automatic reset.')

    def _totals(self,conn):
        rows=conn.execute('SELECT prompt_tokens,completion_tokens FROM calls').fetchall()
        known=prompt=completion=0
        for p,c in rows:
            if p is None and c is None: continue
            if type(p) is not int or type(c) is not int or not 0<=p<=65000 or not 0<=c<=2048:
                raise RequestError('MODEL_BUDGET_ERROR','Invalid stored usage; preserve ledger and investigate.')
            known+=1; prompt+=p; completion+=c
        held=(len(rows)-known)*POLICY[2]
        settled=prompt*POLICY[3]+completion*POLICY[4]
        return dict(calls=len(rows),known=known,prompt=prompt,completion=completion,held=held,settled=settled)

    def reserve(self):
        try:
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE'); self._check_policy(conn)
                s=self._totals(conn)
                if s['held']+s['settled']+POLICY[2]>POLICY[1]:
                    raise RequestError('MODEL_BUDGET_EXCEEDED','Project accounted cost plus next reservation exceeds 10 CNY.')
                call_id=conn.execute('INSERT INTO calls DEFAULT VALUES').lastrowid
                conn.commit(); return call_id
        except sqlite3.Error:
            raise RequestError('MODEL_BUDGET_ERROR','Cannot reserve budget; model was not called.') from None

    def record_usage(self,call_id,result):
        usage=result.get('usage',{}) if isinstance(result,dict) else {}
        if not isinstance(usage,dict): usage={}
        p,c=usage.get('prompt_tokens'),usage.get('completion_tokens')
        if type(p) is not int or type(c) is not int or not 0<=p<=65000 or not 0<=c<=2048:
            raise RequestError('MODEL_USAGE_ERROR','Missing or out-of-bounds usage; full reservation retained.')
        response_id=result.get('id')
        if type(response_id) is not str or not response_id or len(response_id)>200:
            raise RequestError('MODEL_USAGE_ERROR','Missing provider response ID; full reservation retained.')
        try:
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE'); self._check_policy(conn)
                old=conn.execute('SELECT prompt_tokens,completion_tokens,response_id FROM calls WHERE id=?',(call_id,)).fetchone()
                if old is None or (old!=(None,None,None) and old!=(p,c,response_id)):
                    raise RequestError('MODEL_BUDGET_ERROR','Unknown reservation or conflicting usage; records not overwritten.')
                conn.execute('UPDATE calls SET prompt_tokens=?,completion_tokens=?,response_id=? WHERE id=?',(p,c,response_id,call_id))
                conn.commit()
        except sqlite3.Error:
            raise RequestError('MODEL_BUDGET_ERROR','Cannot record usage; reservation retained.') from None

    def summary(self):
        try:
            with self.connect() as conn:
                conn.execute('BEGIN'); self._check_policy(conn); s=self._totals(conn)
            def yuan(n): return f'{Decimal(n)/1000000:.6f}'
            return dict(policy_version=2,calls=s['calls'],usage_known_calls=s['known'],
                        reserved_yuan=yuan(s['held']),settled_conservative_yuan=yuan(s['settled']),
                        accounted_yuan=yuan(s['held']+s['settled']),limit_yuan='10.00',
                        remaining_yuan=yuan(max(0,POLICY[1]-s['held']-s['settled'])),
                        prompt_tokens=s['prompt'],completion_tokens=s['completion'],
                        estimated_yuan=str((Decimal(s['prompt'])*Decimal('0.8')+Decimal(s['completion'])*2)/1000000),
                        notice='Provider usage settled conservatively at 1/3 CNY per million tokens; unresolved calls keep 0.10 CNY each. Not the provider bill.')
        except sqlite3.Error:
            raise RequestError('MODEL_BUDGET_ERROR','Cannot read budget ledger.') from None
