"""Business reads and prepared writes inside the shared transaction boundary."""
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .contracts import RequestError, parse_json_object, serialize, success_response, error_response, normalize_time
from .storage import ReadOnlyTransaction, check_schema
from . import business_validation as v
from .business_schema import SCHEMA, INDEX
from .material_tools import read_material_data, check_material_data

DATA_DIR=Path(__file__).resolve().parents[2]/'data'


def fail(code,message):
    raise RequestError(code,message)


def version(tx):
    return tx.query('SELECT storage_schema_version FROM runtime_meta')[0][0]


def require_v2(tx):
    if version(tx)!=2: fail('STORAGE_ERROR','Upgrade the database explicitly to version 2.')


def obj(raw):
    try: return parse_json_object(raw)
    except RequestError: fail('STORAGE_ERROR','Stored business record is invalid.')


def task_data(tx,task_id):
    if not tx.query('SELECT task_id FROM tasks WHERE task_id=?',(task_id,)):
        fail('TASK_NOT_FOUND','Task does not exist.')
    return dict(task_id=task_id,items=[dict(material_id=r[0],requested_time=r[1]) for r in tx.query(
        'SELECT material_id, requested_time FROM task_items WHERE task_id=? ORDER BY position',(task_id,))])


def task_item(tx,task_id,mid):
    for item in task_data(tx,task_id)['items']:
        if item['material_id']==mid: return item
    fail('MATERIAL_NOT_FOUND','Material is not part of the task.')


def material_records(tx,mid):
    rows=tx.query('SELECT record_json FROM materials WHERE material_id=?',(mid,))
    if not rows: fail('MATERIAL_NOT_FOUND','Material does not exist.')
    primary=obj(rows[0][0]); supplements=[obj(r[0]) for r in tx.query('SELECT record_json FROM supplements WHERE material_id=? ORDER BY source_id',(mid,))]
    try:
        v.material(primary)
        for s in supplements: v.supplement(s)
        v.require(primary['material_id']==mid and all(s['material_id']==mid for s in supplements))
    except RequestError: fail('STORAGE_ERROR','Stored material is invalid.')
    return primary,supplements


def rules_data(tx):
    rules=[obj(r[0]) for r in tx.query('SELECT record_json FROM rules ORDER BY rule_id')]
    try: v.rules(rules)
    except RequestError: fail('STORAGE_ERROR','Stored rules are invalid.')
    return rules


def current_read(tx,mid,view):
    return read_material_data(*material_records(tx,mid),view)


def resolve_read(tx,mid,reference):
    for view in ('primary','complete'):
        read=current_read(tx,mid,view)
        if read['material_ref']==reference: return read
    fail('STALE_MATERIAL','Read current material before saving or checking.')


def occupant(tx,slot):
    external=tx.query('SELECT schedule_id FROM existing_schedules WHERE slot_id=?',(slot,))
    draft=tx.query("SELECT draft_id FROM drafts WHERE suggested_slot_id=? AND status='confirmed'",(slot,))
    if external and draft: fail('STORAGE_ERROR','Conflicting persisted occupancy detected.')
    if external: return dict(kind='existing_schedule',id=external[0][0])
    if draft: return dict(kind='confirmed_draft',id=draft[0][0])
    return None


def business_day(time):
    return (datetime.fromisoformat(time)+timedelta(hours=8)).date()


def query_slots(tx,task_id,mid):
    requested=task_item(tx,task_id,mid)['requested_time']; now=tx.clock()[0]
    state=tx.query('SELECT revision, occupancy_version FROM calendar_state WHERE singleton_id=1')
    if not state: fail('STORAGE_ERROR','Load a dataset before querying slots.')
    slots=tx.query('SELECT slot_id, starts_at FROM slots ORDER BY starts_at, slot_id')
    original=next((s for s in slots if s[1]==requested),None)
    occ=occupant(tx,original[0]) if original else None
    status='past' if requested<=now else 'not_offered' if not original else 'occupied' if occ else 'available'
    alternatives=[dict(slot_id=sid,starts_at=t) for sid,t in slots if t>now and t!=requested and business_day(t)==business_day(requested) and not occupant(tx,sid)]
    return dict(requested_time=requested,requested_slot_id=original[0] if original else None,
                requested_status=status,calendar_revision=state[0][0],occupancy_version=state[0][1],
                requested_occupant=occ,alternatives=alternatives)


def read_tool(store,name,args):
    conn=None
    try:
        v.validate_tool(name,args)
        conn=store.connect(readonly=True); conn.execute('BEGIN')
        if not check_schema(conn): fail('NOT_INITIALIZED','Initialize the database explicitly.')
        tx=ReadOnlyTransaction(conn); require_v2(tx)
        if name=='read_material': data=current_read(tx,args['material_id'],args['view'])
        elif name=='check_rules': data=check_material_data(resolve_read(tx,args['material_id'],args['material_ref']),rules_data(tx))
        else: data=query_slots(tx,args['task_id'],args['material_id'])
        return dict(ok=True,data=data,error=None)
    except Exception as exc:
        error=exc if isinstance(exc,RequestError) else RequestError('TOOL_UNAVAILABLE','Read tool unavailable.',retryable=True)
        return dict(ok=False,data=None,error=error_response(error)['error'])
    finally:
        if conn is not None: conn.close()


def fixture(payload):
    manifest_raw=(DATA_DIR/'manifest.json').read_bytes()
    v.require(hashlib.sha256(manifest_raw).hexdigest()==payload['manifest_sha256'],'Manifest digest does not match.')
    manifest=parse_json_object(manifest_raw.decode('utf-8'))
    v.require(manifest['dataset_id']==payload['dataset_id'] and manifest['synthetic'] is True)
    def read(path):
        p=(DATA_DIR/path).resolve()
        v.require(p.is_relative_to(DATA_DIR.resolve()) and p.suffix=='.json','Invalid data path.')
        return parse_json_object('{"value":'+p.read_text(encoding='utf-8')+'}')['value']
    mats=read(manifest['files']['materials']); supplements=read(manifest['files']['supplements'])
    rules=read(manifest['files']['rules']); slots=read(manifest['files']['slots'])
    calendars=[read(path) for path in manifest['calendars']]
    for rows in (mats,supplements,slots,calendars): v.require(type(rows) is list)
    for m in mats: v.material(m)
    for s in supplements: v.supplement(s)
    v.rules(rules)
    mids=[m['material_id'] for m in mats]; v.string_list(mids)
    v.string_list([m['source_id'] for m in mats]+[s['source_id'] for s in supplements])
    v.require(all(s['material_id'] in mids for s in supplements))
    for s in slots:
        v.fields(s,'slot_id starts_at'); v.identifier(s['slot_id']); s['starts_at']=normalize_time(s['starts_at'],'starts_at')
    slot_ids=[s['slot_id'] for s in slots]; v.string_list(slot_ids); v.string_list([s['starts_at'] for s in slots])
    for cal in calendars:
        v.fields(cal,'calendar_id revision existing'); v.identifier(cal['calendar_id']); v.positive(cal['revision']); v.existing(cal['existing'])
        v.require(all(e['slot_id'] in slot_ids for e in cal['existing']))
    v.string_list([c['calendar_id'] for c in calendars])
    selected=next((c for c in calendars if c['calendar_id']==payload['calendar_id']),None)
    v.require(selected is not None,'Unknown fixture calendar.')
    return manifest,mats,supplements,rules,slots,selected


def prepare(tx,request):
    op,p=request.operation,request.payload; sql=[]
    if op=='schema.upgrade':
        previous=version(tx)
        if previous==1:
            sql=[(s,()) for s in SCHEMA]+[(INDEX,()),('UPDATE runtime_meta SET storage_schema_version=2 WHERE singleton_id=1',())]
        return dict(previous_storage_version=previous,storage_schema_version=2),sql
    require_v2(tx)
    if op in ('draft.save','draft.confirm'):
        from .draft_tools import prepare_save, prepare_confirmation
        return prepare_save(tx,p) if op=='draft.save' else prepare_confirmation(tx,p,request.request_id)
    if op=='fixture.load':
        for table in ('materials','supplements','rules','slots','calendar_state','tasks','task_items','drafts','confirmations','existing_schedules'):
            if tx.query('SELECT 1 FROM '+table+' LIMIT 1'): fail('FIXTURE_ALREADY_LOADED','Business data already exists.')
        manifest,mats,supplements,rules,slots,cal=fixture(p)
        v.require(tx.clock()[0]==manifest['initial_time'],'Logical time must match fixture initial time.')
        sql += [('INSERT INTO materials (material_id, record_json) VALUES (?,?)',(m['material_id'],serialize(m))) for m in mats]
        sql += [('INSERT INTO supplements (source_id, material_id, record_json) VALUES (?,?,?)',(s['source_id'],s['material_id'],serialize(s))) for s in supplements]
        sql += [('INSERT INTO rules (rule_id, record_json) VALUES (?,?)',(r['rule_id'],serialize(r))) for r in rules]
        sql += [('INSERT INTO slots (slot_id, starts_at) VALUES (?,?)',(s['slot_id'],s['starts_at'])) for s in slots]
        sql += [('INSERT INTO calendar_state (singleton_id,dataset_id,calendar_id,revision,occupancy_version) VALUES (1,?,?,?,1)',(p['dataset_id'],cal['calendar_id'],cal['revision']))]
        sql += [('INSERT INTO existing_schedules (schedule_id,slot_id) VALUES (?,?)',(e['schedule_id'],e['slot_id'])) for e in cal['existing']]
        return dict(dataset_id=p['dataset_id'],calendar_id=cal['calendar_id'],calendar_revision=cal['revision'],occupancy_version=1),sql
    if op=='task.create':
        if tx.query('SELECT task_id FROM tasks WHERE task_id=?',(p['task_id'],)):
            current=task_data(tx,p['task_id'])
            if current!=p: fail('TASK_CONFLICT','Task ID already has different immutable requirements.')
            return dict(task=current),[]
        for item in p['items']:
            material_records(tx,item['material_id'])
            if item['requested_time']<=tx.clock()[0]: fail('INVALID_PUBLISH_TIME','Publish time must be in the future.')
        sql=[('INSERT INTO tasks (task_id) VALUES (?)',(p['task_id'],))]
        sql += [('INSERT INTO task_items (task_id,material_id,position,requested_time) VALUES (?,?,?,?)',(p['task_id'],it['material_id'],n,it['requested_time'])) for n,it in enumerate(p['items'])]
        return dict(task=p),sql
    if op=='calendar.replace':
        state=tx.query('SELECT revision,occupancy_version FROM calendar_state WHERE singleton_id=1')
        if not state: fail('STORAGE_ERROR','No calendar loaded.')
        rev,occ=state[0]
        if p['expected_revision']!=rev: fail('CALENDAR_VERSION_CONFLICT','Calendar revision changed.')
        for e in p['existing']:
            if not tx.query('SELECT slot_id FROM slots WHERE slot_id=?',(e['slot_id'],)): fail('SLOT_NOT_FOUND','Slot does not exist.')
            if tx.query("SELECT draft_id FROM drafts WHERE suggested_slot_id=? AND status='confirmed'",(e['slot_id'],)): fail('SLOT_CONFLICT','Confirmed draft already occupies the slot.')
        sql=[('DELETE FROM existing_schedules',())]+[('INSERT INTO existing_schedules (schedule_id,slot_id) VALUES (?,?)',(e['schedule_id'],e['slot_id'])) for e in p['existing']]
        sql += [('UPDATE calendar_state SET revision=revision+1,occupancy_version=occupancy_version+1 WHERE singleton_id=1',())]
        return dict(calendar_revision=rev+1,occupancy_version=occ+1),sql
    fail('UNKNOWN_OPERATION','Business operation is not implemented.')


def apply(tx,prepared):
    data,statements=prepared
    for sql,params in statements: tx.execute(sql,params)
    return data


def read_task_or_drafts(store,task_id, *, drafts):
    conn=None; op='drafts.read' if drafts else 'task.read'
    try:
        v.identifier(task_id)
        conn=store.connect(readonly=True); conn.execute('BEGIN')
        if not check_schema(conn): fail('NOT_INITIALIZED','Initialize the database explicitly.')
        tx=ReadOnlyTransaction(conn); require_v2(tx); task=task_data(tx,task_id)
        if drafts:
            from .draft_tools import load_draft
            data=dict(task_id=task_id,drafts=[d for item in task['items'] if (d:=load_draft(tx,task_id=task_id,material_id=item['material_id'])) is not None])
        else: data=dict(task=task)
        return success_response(None,op,data)
    except Exception as exc:
        from .runtime import _error_for
        return error_response(_error_for(exc),operation=op)
    finally:
        if conn is not None: conn.close()


def read_drafts(store,task_id):
    return read_task_or_drafts(store,task_id,drafts=True)


def read_task(store,task_id):
    return read_task_or_drafts(store,task_id,drafts=False)
