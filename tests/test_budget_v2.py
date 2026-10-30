import sqlite3
import tempfile
from pathlib import Path
import unittest
from contextlib import closing

from schedule_agent.bailian import BudgetLedger
from schedule_agent.contracts import RequestError


class BudgetV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'budget.sqlite3'

    def usage(self,p=100,c=40): return {'id':'provider-id','usage':{'prompt_tokens':p,'completion_tokens':c}}

    def test_known_usage_settles_and_allows_more_than_100_small_calls(self):
        ledger=BudgetLedger(self.path)
        for _ in range(101): ledger.record_usage(ledger.reserve(),self.usage())
        s=ledger.summary()
        self.assertEqual(s['calls'],101)
        self.assertEqual(s['accounted_yuan'],'0.022220')
        self.assertEqual(s['reserved_yuan'],'0.000000')

    def test_legacy_upgrade_preserves_all_calls_and_unknown_reservation(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('CREATE TABLE calls (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, prompt_tokens INTEGER, completion_tokens INTEGER, response_id TEXT)')
            conn.execute("INSERT INTO calls (prompt_tokens,completion_tokens,response_id) VALUES (100,40,'original')")
            conn.execute('INSERT INTO calls DEFAULT VALUES')
            conn.commit()
            before=conn.execute('SELECT * FROM calls').fetchall()
        ledger=BudgetLedger(self.path)
        with closing(sqlite3.connect(self.path)) as conn: self.assertEqual(before,conn.execute('SELECT * FROM calls').fetchall())
        self.assertEqual(ledger.summary()['policy_version'],2)
        self.assertEqual(ledger.summary()['accounted_yuan'],'0.100220')
        self.assertEqual(BudgetLedger(self.path).summary(),ledger.summary())

    def test_invalid_usage_keeps_reservation_and_conflicting_settlement_rejected(self):
        ledger=BudgetLedger(self.path); call=ledger.reserve()
        for p,c in ((True,4),(-1,4),(65001,4),(1,2049)):
            with self.assertRaises(RequestError): ledger.record_usage(call,self.usage(p,c))
            self.assertEqual(ledger.summary()['reserved_yuan'],'0.100000')
        ledger.record_usage(call,self.usage())
        ledger.record_usage(call,self.usage())
        with self.assertRaises(RequestError): ledger.record_usage(call,self.usage(1,1))
        self.assertEqual(ledger.summary()['accounted_yuan'],'0.000220')

    def test_corrupted_or_future_policy_does_not_reset(self):
        ledger=BudgetLedger(self.path); ledger.reserve()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('UPDATE budget_policy SET version=999'); conn.commit()
        with self.assertRaises(RequestError): BudgetLedger(self.path)
        with self.assertRaises(RequestError): ledger.reserve()
        with closing(sqlite3.connect(self.path)) as conn: self.assertEqual(conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)
