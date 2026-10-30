"""Prepare atomic draft/confirmation writes; no model-controlled authorization."""
import copy
from uuid import uuid4
from .contracts import RequestError, serialize
from . import business_validation as v
from .business import (fail, obj, task_item, current_read, resolve_read, material_records,
                       rules_data, check_material_data, occupant, business_day)


def load_draft(tx, *, draft_id=None, task_id=None, material_id=None):
    where,params=('draft_id=?',(draft_id,)) if draft_id else ('task_id=? AND material_id=?',(task_id,material_id))
    rows=tx.query('SELECT draft_id, task_id, material_id, version, status, suggested_slot_id, record_json FROM drafts WHERE '+where,params)
    if not rows: return None
    row=rows[0]; d=obj(row[6])
    try:
        v.validate_draft(d)
        v.require(tuple(d[k] for k in ('draft_id','task_id','material_id','version','status','suggested_slot_id'))==row[:6])
        v.require(task_item(tx,d['task_id'],d['material_id'])['requested_time']==d['requested_time'])
        slot=tx.query('SELECT starts_at FROM slots WHERE slot_id=?',(d['suggested_slot_id'],))
        v.require(bool(slot) and slot[0][0]==d['suggested_time'])
        confirmations=tx.query('SELECT confirmation_id,record_json FROM confirmations WHERE draft_id=?',(d['draft_id'],))
        if d['authorization_basis']=='user_confirmation':
            v.require(len(confirmations)==1,'User confirmation record is missing.')
            c=obj(confirmations[0][1]); v.require(c['confirmation_id']==confirmations[0][0])
            payload=dict(draft_id=d['draft_id'],expected_draft_version=d['version']-1,
                         accepted_slot_id=d['suggested_slot_id'],accepted_risk_ids=d['required_risk_ids'])
            v.validate_result('draft.confirm',dict(draft=d,confirmation=c),payload,c['request_id'])
        else: v.require(not confirmations,'Unexpected confirmation record.')
        if d['status']=='confirmed':
            occupied=occupant(tx,d['suggested_slot_id'])
            v.require(occupied==dict(kind='confirmed_draft',id=d['draft_id']),'Confirmed slot is inconsistent.')
    except Exception:
        fail('STORAGE_ERROR','Stored draft or review basis is inconsistent.')
    return d


def verified_basis(tx,p):
    read=resolve_read(tx,p['material_id'],p['material_ref']); rules=rules_data(tx)
    check=check_material_data(read,rules)
    if check['check_ref']!=p['check_ref']: fail('STALE_RULE_CHECK','Read and check current rules before saving.')
    if check['review_status']=='blocked': fail('REVIEW_BLOCKED','Blocking risks cannot be waived.')
    if check['review_status']=='needs_information': fail('MATERIAL_INCOMPLETE','Material information is incomplete.')
    primary,supplements=material_records(tx,p['material_id'])
    return dict(primary=primary,supplements=supplements,rules=rules,read_result=read,check_result=check,checked_at=tx.clock()[0])


def available_slot(tx,slot_id,requested):
    rows=tx.query('SELECT starts_at FROM slots WHERE slot_id=?',(slot_id,))
    if not rows: fail('SLOT_NOT_FOUND','Suggested slot does not exist.')
    time=rows[0][0]
    if time<=tx.clock()[0]: fail('INVALID_PUBLISH_TIME','Suggested slot must be in the future.')
    if business_day(time)!=business_day(requested): fail('INVALID_REQUEST','Alternative must be on the requested business date.')
    if occupant(tx,slot_id): fail('SLOT_CONFLICT','Suggested slot is already occupied.')
    return time


def write_draft(d, *, create):
    if create:
        return ('INSERT INTO drafts (draft_id,task_id,material_id,version,status,suggested_slot_id,record_json) VALUES (?,?,?,?,?,?,?)',
                (d['draft_id'],d['task_id'],d['material_id'],d['version'],d['status'],d['suggested_slot_id'],serialize(d)))
    return ('UPDATE drafts SET version=?,status=?,suggested_slot_id=?,record_json=? WHERE draft_id=?',
            (d['version'],d['status'],d['suggested_slot_id'],serialize(d),d['draft_id']))


def prepare_save(tx,p):
    requested=task_item(tx,p['task_id'],p['material_id'])['requested_time']
    old=load_draft(tx,task_id=p['task_id'],material_id=p['material_id'])
    same=old is not None and all(old[k]==p[k] for k in ('task_id','material_id','suggested_slot_id','material_ref','check_ref'))
    if old and old['status']=='confirmed':
        if same: return dict(draft=old,save_status='saved'),[]
        fail('DRAFT_FINALIZED','Confirmed drafts cannot be changed or cancelled.')
    if not same and p['expected_draft_version']!=(old['version'] if old else None):
        fail('DRAFT_VERSION_CONFLICT','Draft version changed; read the current draft.')
    basis=verified_basis(tx,p)
    time=available_slot(tx,p['suggested_slot_id'],requested)
    if same: return dict(draft=old,save_status='saved'),[]
    check=basis['check_result']; needs=time!=requested or bool(check['required_risk_ids'])
    draft=dict(draft_id=old['draft_id'] if old else 'draft.'+uuid4().hex,task_id=p['task_id'],material_id=p['material_id'],
        version=old['version']+1 if old else 1,requested_time=requested,suggested_slot_id=p['suggested_slot_id'],suggested_time=time,
        material_ref=p['material_ref'],check_ref=p['check_ref'],review_basis=basis,review_status=check['review_status'],
        findings=check['findings'],confirmation_required=needs,required_risk_ids=check['required_risk_ids'],
        status='pending_confirmation' if needs else 'confirmed',authorization_basis=None if needs else 'original_request',occupies_slot=not needs)
    statements=[write_draft(draft,create=old is None)]
    if not needs: statements.append(('UPDATE calendar_state SET occupancy_version=occupancy_version+1 WHERE singleton_id=1',()))
    return dict(draft=draft,save_status='saved'),statements


def prepare_confirmation(tx,p,request_id):
    d=load_draft(tx,draft_id=p['draft_id'])
    if d is None: fail('DRAFT_NOT_FOUND','Draft does not exist.')
    if d['status']=='confirmed': fail('DRAFT_FINALIZED','Draft is already confirmed.')
    if p['expected_draft_version']!=d['version']: fail('DRAFT_VERSION_CONFLICT','Confirm the current draft version explicitly.')
    if p['accepted_slot_id']!=d['suggested_slot_id'] or p['accepted_risk_ids']!=d['required_risk_ids']:
        fail('CONFIRMATION_MISMATCH','Accept the exact proposed slot and all listed warning risks.')
    verified_basis(tx,d)
    available_slot(tx,d['suggested_slot_id'],d['requested_time'])
    confirmation=dict(confirmation_id='confirmation.'+uuid4().hex,draft_id=d['draft_id'],accepted_version=d['version'],
                      accepted_slot_id=d['suggested_slot_id'],accepted_risk_ids=d['required_risk_ids'],request_id=request_id,confirmed_at=tx.clock()[0])
    confirmed=copy.deepcopy(d); confirmed.update(version=d['version']+1,status='confirmed',authorization_basis='user_confirmation',occupies_slot=True)
    statements=[write_draft(confirmed,create=False),
                ('INSERT INTO confirmations (confirmation_id,draft_id,record_json) VALUES (?,?,?)',(confirmation['confirmation_id'],d['draft_id'],serialize(confirmation))),
                ('UPDATE calendar_state SET occupancy_version=occupancy_version+1 WHERE singleton_id=1',())]
    return dict(draft=confirmed,confirmation=confirmation),statements
