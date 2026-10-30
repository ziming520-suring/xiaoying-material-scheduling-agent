"""Validate phase-two fixture structure only; never executes business tools or models."""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from schedule_agent.contracts import normalize_time  # existing strict time parser


class FixtureError(ValueError):
    pass


class Audit:
    def __init__(self, root):
        self.root = root.resolve()
        self.checks = 0
        self.loaded = {}

    def require(self, condition, message):
        self.checks += 1
        if not condition:
            raise FixtureError(message)

    def keys(self, value, names):
        self.require(type(value) is dict and set(value) == set(names.split()),
                     f"unexpected object fields: expected {names}")

    def identifier(self, value):
        self.require(type(value) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value),
                     f"invalid ID: {value!r}")

    def positive(self, value):
        self.require(type(value) is int and value > 0, "version must be positive integer")

    def unique(self, values):
        self.require(len(values) == len(set(values)), f"duplicate values: {values}")

    def strings(self, values):
        self.require(type(values) is list and all(type(v) is str and v for v in values),
                     "expected array of nonempty strings")

    def read(self, relative):
        self.require(type(relative) is str, "manifest path must be a string")
        p = (self.root / relative).resolve()
        self.require(p.is_relative_to(self.root) and p.suffix == ".json", "unsafe fixture path")
        def pairs(entries):
            obj = {}
            for key, value in entries:
                self.require(key not in obj, f"duplicate JSON key in {relative}: {key}")
                obj[key] = value
            return obj
        def constant(value):
            raise FixtureError(f"nonstandard JSON number: {value}")
        value = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
        self.loaded[relative] = hashlib.sha256(p.read_bytes()).hexdigest()
        return value

    def records(self, relative, id_field, fields):
        rows = self.read(relative)
        self.require(type(rows) is list, f"{relative}: expected array")
        for row in rows:
            self.keys(row, fields)
            self.identifier(row[id_field])
        self.unique([row[id_field] for row in rows])
        return {row[id_field]: row for row in rows}

    def fields(self, fields):
        for key, value in fields.items():
            if key in {"title", "caption"}:
                self.require(value is None or type(value) is str, f"{key}: string or null")
            elif key == "rights_status":
                self.require(value in {"authorized", "unknown", "denied"}, "invalid rights")
            elif key == "manual_review_required":
                self.require(type(value) is bool, "manual review must be bool")
            else:
                raise FixtureError(f"unknown material field: {key}")

    def run(self):
        m = self.read("manifest.json")
        self.keys(m, "schema_version dataset_id synthetic initial_time business_utc_offset files calendars scenarios")
        self.require(type(m['schema_version']) is int and m['schema_version'] == 1, "manifest version")
        self.identifier(m['dataset_id'])
        self.require(m['synthetic'] is True, "must label synthetic data")
        self.require(m['business_utc_offset'] == '+08:00', "business offset")
        self.require(normalize_time(m['initial_time'], 'initial_time') == '2026-10-28T09:00:00Z', "initial time")
        self.keys(m['files'], 'materials supplements rules slots')
        mats = self.records(m['files']['materials'], 'material_id',
                            'material_id version source_id title caption rights_status manual_review_required')
        source_ids = []
        content = {'title', 'caption', 'rights_status', 'manual_review_required'}
        for material in mats.values():
            self.positive(material['version']); self.identifier(material['source_id'])
            source_ids.append(material['source_id'])
            self.fields({k: material[k] for k in content})
        supplements = self.records(m['files']['supplements'], 'source_id', 'source_id material_id version fields')
        for sup in supplements.values():
            self.positive(sup['version']); source_ids.append(sup['source_id'])
            self.require(sup['material_id'] in mats, 'supplement material reference')
            self.require(type(sup['fields']) is dict and bool(sup['fields']), 'empty supplement')
            self.fields(sup['fields'])
        self.unique(source_ids)
        rules = self.records(m['files']['rules'], 'rule_id', 'rule_id version kind severity fields terms reason')
        expected_rules = {'R001': ('required_fields', 'missing'), 'R002': ('denied_rights', 'block'),
                          'R003': ('banned_terms', 'block'), 'R004': ('manual_review', 'confirm'),
                          'R005': ('source_conflict', 'block')}
        self.require(set(rules) == set(expected_rules), 'five synthetic rules required')
        for rid, rule in rules.items():
            self.positive(rule['version'])
            self.require((rule['kind'], rule['severity']) == expected_rules[rid], 'rule kind/severity')
            self.strings(rule['fields']); self.strings(rule['terms'])
            self.unique(rule['fields']); self.unique(rule['terms'])
            self.require(rule['fields'] == (['title','caption','rights_status'] if rid == 'R001' else []), 'rule fields')
            self.require(rule['terms'] == (['保证收益'] if rid == 'R003' else []), 'rule terms')
            self.require(type(rule['reason']) is str and bool(rule['reason']), 'rule reason')
        slots = self.records(m['files']['slots'], 'slot_id', 'slot_id starts_at')
        self.require(set(slots) == {'S18','S19','S20','S21'}, 'four fixed slots required')
        for sid, slot in slots.items():
            t = normalize_time(slot['starts_at'], 'starts_at')
            self.require(t == slot['starts_at'], 'slots must store canonical UTC')
            local = datetime.fromisoformat(t) + timedelta(hours=8)
            self.require(local.strftime('%Y-%m-%dT%H:%M:%S') == f'2026-10-28T{sid[1:]}:00:00', 'slot local time')
        self.unique([s['starts_at'] for s in slots.values()])
        calendars = {}
        self.strings(m['calendars']); self.unique(m['calendars'])
        for path in m['calendars']:
            cal = self.read(path); self.keys(cal, 'calendar_id revision existing')
            self.identifier(cal['calendar_id']); self.positive(cal['revision'])
            self.require(cal['calendar_id'] not in calendars, 'calendar id duplicate')
            self.require(type(cal['existing']) is list, 'existing must be list')
            for row in cal['existing']:
                self.keys(row, 'schedule_id slot_id'); self.identifier(row['schedule_id'])
                self.require(row['slot_id'] in slots, 'unknown occupied slot')
            self.unique([e['slot_id'] for e in cal['existing']]); self.unique([e['schedule_id'] for e in cal['existing']])
            calendars[cal['calendar_id']] = cal
        self.strings(m['scenarios']); self.unique(m['scenarios'])
        ids, coverage = [], set()
        actions = {'agent_run','user_confirm','calendar_replace','repeat_saved_request','repeat_save_new_key',
                   'inject_fault','clear_fault','read_state','clock_advance','revise_pending'}
        for path in m['scenarios']:
            s = self.read(path)
            self.keys(s, 'schema_version scenario_id title execution_status requirement_ids calendar_id task steps expected')
            self.require(type(s['schema_version']) is int and s['schema_version'] == 1, 'scenario version')
            self.identifier(s['scenario_id']); ids.append(s['scenario_id'])
            self.require(s['execution_status'] == 'not_run', 'expected scenarios cannot claim execution')
            self.require(type(s['title']) is str and bool(s['title']), 'scenario title')
            self.strings(s['requirement_ids']); self.unique(s['requirement_ids'])
            self.require(bool(s['requirement_ids']) and set(s['requirement_ids']) <= {f'A0{i}' for i in range(1,7)}, 'requirement IDs')
            coverage.update(s['requirement_ids'])
            self.require(s['calendar_id'] in calendars, 'unknown calendar')
            self.keys(s['task'], 'task_id items'); self.identifier(s['task']['task_id'])
            self.require(type(s['task']['items']) is list and bool(s['task']['items']), 'empty task')
            for ti in s['task']['items']:
                self.keys(ti, 'material_id requested_time')
                self.require(ti['material_id'] in mats, 'task references unknown material')
                self.require(normalize_time(ti['requested_time'], 'requested_time') > m['initial_time'], 'request not future')
            mids = [ti['material_id'] for ti in s['task']['items']]; self.unique(mids)
            self.require(type(s['steps']) is list and bool(s['steps']), 'empty scenario arrangement')
            for step in s['steps']:
                self.keys(step, 'action details')
                self.require(step['action'] in actions and type(step['details']) is dict, 'unknown scenario action')
            ex = s['expected']; self.keys(ex, 'task_status items assertions')
            self.strings(ex['assertions']); self.require(bool(ex['assertions']), 'missing assertions')
            self.require(type(ex['items']) is list and [i['material_id'] for i in ex['items']] == mids, 'result order')
            for it in ex['items']:
                self.keys(it, 'material_id review_status item_status save_status draft_status draft_count occupies_slot reason_codes allowed_suggested_slot_ids')
                self.require(it['review_status'] in {'not_checked','passed','needs_information','needs_confirmation','blocked'}, 'review enum')
                self.require(it['item_status'] in {'completed','pending_confirmation','needs_information','blocked','save_failed','unknown','incomplete'}, 'item enum')
                self.require(it['save_status'] in {'not_attempted','saved','failed','unknown'}, 'save enum')
                self.require(it['draft_status'] in {None,'pending_confirmation','confirmed'}, 'draft enum')
                self.require(type(it['draft_count']) is int and it['draft_count'] in {0,1}, 'draft uniqueness')
                self.require((it['draft_count'] == 0) == (it['draft_status'] is None), 'draft absence')
                self.require(type(it['occupies_slot']) is bool and it['occupies_slot'] == (it['draft_status']=='confirmed'), 'occupancy boundary')
                self.require((it['item_status']=='completed') == (it['draft_status']=='confirmed'), 'completion boundary')
                self.strings(it['reason_codes']); self.unique(it['reason_codes'])
                self.strings(it['allowed_suggested_slot_ids']); self.unique(it['allowed_suggested_slot_ids'])
                self.require(set(it['allowed_suggested_slot_ids']) <= set(slots), 'suggestion unknown slot')
                self.require(not it['draft_count'] or bool(it['allowed_suggested_slot_ids']), 'draft without suggestion')
                self.require(it['save_status'] != 'saved' or it['draft_count']==1, 'saved without draft')
            n = sum(i['item_status']=='completed' for i in ex['items'])
            self.require(ex['task_status'] == ('completed' if n==len(mids) else 'partial' if n else 'incomplete'), 'task completion')
        self.unique(ids)
        self.require(set(ids) == {f'S{i:02}' for i in range(1,19)}, '18 designed scenarios required')
        self.require(coverage == {f'A0{i}' for i in range(1,7)}, 'six acceptance categories required')
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob('*.json')}
        self.require(actual == set(self.loaded), 'manifest has unreferenced or missing JSON')
        return dict(validation_kind='static_fixture_consistency', status='passed', checks=self.checks,
                    counts=dict(materials=len(mats), supplements=len(supplements), rules=len(rules),
                                slots=len(slots), calendars=len(calendars), scenarios=len(ids)),
                    acceptance_categories=sorted(coverage), business_execution_status='not_run',
                    model_execution_status='not_run', file_sha256=self.loaded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=PROJECT / 'data')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = Audit(args.data).run()
    except Exception as exc:
        print(json.dumps(dict(status='failed', error=str(exc)), ensure_ascii=False))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='file_sha256'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
