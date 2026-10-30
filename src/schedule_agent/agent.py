"""Bounded model/tool loop. Tools, not model prose, determine the final result."""
import copy
import os
from pathlib import Path
from uuid import uuid4

from .contracts import RequestError, serialize, parse_json_object
from .business import read_tool, read_task, read_drafts
from .runtime import handle_request
from . import business_validation as v

TOOLS={'read_material','check_rules','query_slots','save_draft'}


class Journal:
    def __init__(self,path):
        try: self.file=Path(path).open('x',encoding='utf-8')
        except OSError: raise RequestError('TRACE_ERROR','Use a new trace path in an existing writable directory.') from None

    def record(self,event,**fields):
        self.file.write(serialize(dict(event=event,**fields))+'\n')
        self.file.flush(); os.fsync(self.file.fileno())

    def close(self):
        try: self.file.close()
        except OSError: pass  # diagnostic close must not invalidate acknowledged writes


def validate_action(action,task_id,mid):
    # JSON roundtrip isolates mutable/foreign model objects and rejects NaN.
    action=parse_json_object(serialize(action))
    v.fields(action,'action arguments reason')
    v.require(type(action['action']) is str and action['action'] in TOOLS|{'finish'})
    v.require(type(action['reason']) is str and len(action['reason'])<=600)
    args=action['arguments']
    if action['action']=='finish': v.fields(args,'')
    elif action['action']=='save_draft':
        v.fields(args,'task_id material_id suggested_slot_id material_ref check_ref')
        v.validate_payload('draft.save',dict(args,expected_draft_version=None))
    else: v.validate_tool(action['action'],args)
    if action['action']!='finish':
        v.require(args.get('material_id')==mid,'Tool must refer to the current task item.')
        if 'task_id' in args: v.require(args['task_id']==task_id,'Tool cannot access another task.')
    return action


def item_result(item,observations,draft,error_code=None):
    checks=[o['result']['data'] for o in observations if o['action']=='check_rules' and o['result'].get('ok')]
    reads=[o['result']['data'] for o in observations if o['action']=='read_material' and o['result'].get('ok')]
    queries=[o['result']['data'] for o in observations if o['action']=='query_slots' and o['result'].get('ok')]
    saves=[o for o in observations if o['action']=='save_draft']
    acknowledged=[o['result']['data']['draft'] for o in saves if o['result']['status']=='succeeded']
    newer_draft=bool(draft and acknowledged and draft['version']>acknowledged[-1]['version'])
    check=checks[-1] if checks and not newer_draft else None
    review=check['review_status'] if check else draft['review_status'] if draft else 'not_checked'
    if not check and not draft and reads and reads[-1]['missing_fields']:
        review='needs_information'  # Trusted read gaps, not fabricated rule findings.
    findings=check['findings'] if check else draft['findings'] if draft else []
    saved=saves[-1]['result'] if saves else None
    save_status={'succeeded':'saved','failed':'failed','unknown':'unknown'}[saved['status']] if saved else 'not_attempted'
    reasons=[]
    if error_code: reasons.append(error_code)
    for o in observations:
        if o['result'].get('error'): reasons.append(o['result']['error']['code'])
    suggested=dict(slot_id=draft['suggested_slot_id'],starts_at=draft['suggested_time']) if draft else None
    if saves and queries and not newer_draft:
        sid=saves[-1]['arguments']['suggested_slot_id']; query=queries[-1]
        candidates=query['alternatives']+([dict(slot_id=query['requested_slot_id'],starts_at=query['requested_time'])] if query['requested_slot_id'] else [])
        suggested=next((s for s in candidates if s['slot_id']==sid),suggested)
    pending=[]
    if review=='needs_information':
        for field in check['missing_fields'] if check else reads[-1]['missing_fields'] if reads else []:
            pending.append(dict(kind='missing_information',field=field,risk_id=None,message='请补充 '+field))
    if draft and draft['status']=='pending_confirmation':
        if draft['suggested_time']!=draft['requested_time']:
            pending.append(dict(kind='time_change',field='suggested_slot_id',risk_id=None,message='请明确接受该版本的替代时段。'))
        for risk in draft['required_risk_ids']:
            pending.append(dict(kind='risk_confirmation',field=None,risk_id=risk,message='请明确接受提示风险 '+risk))
    if save_status=='unknown': status='unknown'
    elif draft and draft['status']=='confirmed': status='completed'; pending=[]
    elif error_code=='STEP_LIMIT': status='incomplete'
    elif save_status=='failed': status='save_failed'
    elif review=='blocked': status='blocked'; reasons.append('REVIEW_BLOCKED')
    elif review=='needs_information': status='needs_information'; reasons.append('MATERIAL_INCOMPLETE')
    elif draft: status='pending_confirmation'
    else:
        status='incomplete'
        if queries and queries[-1]['requested_status']!='available' and not queries[-1]['alternatives']: reasons.append('NO_AVAILABLE_SLOT')
    return dict(material_id=item['material_id'],requested_time=item['requested_time'],review_status=review,findings=findings,
                suggested_slot=suggested,pending_items=pending,draft=draft,save_status=save_status,item_status=status,reason_codes=sorted(set(reasons)))


def run_agent(store,task_id,model,trace_path,*,max_steps=24):
    v.identifier(task_id); v.require(type(max_steps) is int and 1<=max_steps<=1000,'max_steps must be 1..1000.')
    task_response=read_task(store,task_id)
    if task_response['status']!='succeeded':
        e=task_response['error']; raise RequestError(e['code'],e['message'],e['field'],e['retryable'])
    task=task_response['data']['task']; results=[]; item_runs=[]; count=0; stop=None; journal=Journal(trace_path)
    try:
        journal.record('run_start',task=task,model_mode=getattr(model,'mode','unverified_adapter'),max_steps=max_steps,
                       notice='offline_mock only verifies flow; bailian_live records actual provider-selected actions')
        for item in task['items']:
            mid=item['material_id']; observations=[]; error_code='TRACE_ERROR' if stop=='trace_error' else None; current=None; retry_request=None
            latest=read_drafts(store,task_id)
            if latest['status']=='succeeded': current=next((d for d in latest['data']['drafts'] if d['material_id']==mid),None)
            else: error_code=latest['error']['code']
            while not error_code and not (current and current['status']=='confirmed'):
                if count>=max_steps:
                    error_code='STEP_LIMIT'; stop='step_limit'; break
                count+=1
                context=dict(task_id=task_id,item=item,current_draft=current,observations=observations,remaining_steps=max_steps-count)
                try: action=validate_action(model.next_action(copy.deepcopy(context)),task_id,mid)
                except RequestError as exc:
                    error_code=exc.code if exc.code.startswith('MODEL_') else 'MODEL_PROTOCOL_ERROR'
                    try: journal.record('model_error',step=count,code=error_code,diagnostic=getattr(model,'last_diagnostic',None))
                    except OSError: stop='trace_error'
                    break
                except Exception:
                    error_code='MODEL_PROTOCOL_ERROR'; break
                try:
                    journal.record('model_action',step=count,material_id=mid,**action)
                    if action['action']=='finish': break
                    name,args=action['action'],action['arguments']
                    journal.record('tool_call',step=count,action=name,arguments=args)
                except OSError:
                    error_code='TRACE_ERROR'; stop='trace_error'; break
                if name=='save_draft':
                    latest=read_drafts(store,task_id)
                    if latest['status']!='succeeded': error_code=latest['error']['code']; break
                    current=next((d for d in latest['data']['drafts'] if d['material_id']==mid),None)
                    if retry_request and all(retry_request['payload'][k]==value for k,value in args.items()):
                        request=retry_request
                    else:
                        payload=dict(args,expected_draft_version=current['version'] if current else None)
                        request=dict(schema_version=1,request_id='save.'+uuid4().hex,operation='draft.save',payload=payload)
                    # Durable diagnostic before execution provides the exact key for recovery.
                    try: journal.record('write_request',step=count,request=request)
                    except OSError:
                        error_code='TRACE_ERROR'; stop='trace_error'; break
                    result=handle_request(serialize(request),store,channel='model')
                else: result=read_tool(store,name,args)
                observations.append(dict(action=name,arguments=args,reason=action['reason'],result=result))
                try: journal.record('tool_result',step=count,action=name,result=result)
                except OSError:
                    # A diagnostic failure after commit must not be called a failed save.
                    if name=='save_draft' and result['status']=='succeeded': current=result['data']['draft']
                    error_code='TRACE_ERROR'; stop='trace_error'; break
                if name=='save_draft':
                    if result['status']=='unknown': stop='outcome_unknown'; break
                    if result['status']=='succeeded': current=result['data']['draft']
                    retry_request=request if result['status']=='failed' and result['error']['retryable'] else None
            item_runs.append((item,observations,error_code))
        # One consistent final read refreshes every item, including earlier items
        # that a user may have confirmed while the model handled later ones.
        latest=read_drafts(store,task_id)
        if latest['status']=='succeeded':
            current_by_id={d['material_id']:d for d in latest['data']['drafts']}
            for item,observations,error_code in item_runs:
                results.append(item_result(item,observations,current_by_id.get(item['material_id']),error_code))
        else:
            stop='state_read_failed'
            for item,observations,_ in item_runs:
                result=item_result(item,observations,None,latest['error']['code'])
                if result['item_status']!='unknown': result['item_status']='incomplete'
                results.append(result)
        complete=sum(r['item_status']=='completed' for r in results)
        output=dict(task_id=task_id,task_status='completed' if complete==len(results) else 'partial' if complete else 'incomplete',items=results,stop_reason=stop)
        try: journal.record('run_result',result=output,model_calls=count)
        except OSError: output['stop_reason']='trace_error'
        return output
    except OSError:
        raise RequestError('TRACE_ERROR','Trace could not be written; inspect original write requests before retrying.') from None
    finally: journal.close()
