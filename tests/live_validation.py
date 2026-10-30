"""Validate recorded outcomes against authorization, not a fixed model plan."""


def validate_save_failure_evidence(evidence):
    checks=[]
    def check(name,value): checks.append({'check':name,'passed':bool(value)})
    try:
        initial=evidence['agent_runs'][0]
        item=initial['items'][0]
        check('Failure is explicit and initial item has no saved draft',
              initial['task_status']=='incomplete' and item['save_status']=='failed' and item['draft'] is None)
        saves=[op for op in evidence['operations'] if op['request']['operation']=='draft.save']
        a,b=saves[-2:]
        check('Harness repeats exact original request and receives identical success',
              a['request']==b['request'] and a['response']==b['response'] and a['response']['status']=='succeeded')
        drafts=evidence['final_drafts']['data']['drafts']
        check('Exactly one draft equals acknowledged recovery result',len(drafts)==1 and drafts[0]==a['response']['data']['draft'])
        draft=drafts[0]
        check('Original requirement is preserved',draft['requested_time']==item['requested_time'])
        pending=draft['suggested_time']!=draft['requested_time'] or bool(draft['required_risk_ids'])
        check('Recovered proposal occupies only if original time and no unaccepted risk',
              draft['status']==('pending_confirmation' if pending else 'confirmed') and
              draft['occupies_slot']==(not pending) and
              draft['suggested_slot_id']==a['request']['payload']['suggested_slot_id'])
    except (KeyError,IndexError,TypeError,ValueError):
        check('Complete evidence structure',False)
    return {'passed':all(c['passed'] for c in checks),'checks':checks,
            'notice':'Offline follow-up validation of preserved actual records, not a new model run or rewritten original verdict.'}
