"""Pure business input validation; no database or filesystem writes."""
import copy
import re
from .contracts import ID_PATTERN, RequestError, normalize_time


def require(condition, message='Invalid business request.', field=None):
    if not condition:
        raise RequestError('INVALID_REQUEST',message,field)


def fields(value, names):
    require(type(value) is dict and set(value)==set(names.split()),'Missing or unknown fields.')


def identifier(value):
    require(type(value) is str and ID_PATTERN.fullmatch(value) is not None,'Invalid identifier.')
    return value


def positive(value):
    require(type(value) is int and value>=1,'Version must be a positive integer.')


def string_list(value, *, ids=False):
    require(type(value) is list and all(type(v) is str and bool(v) for v in value),'Expected string array.')
    require(len(value)==len(set(value)),'Duplicate values are not allowed.')
    if ids:
        for v in value: identifier(v)


def digest_ref(value, prefix):
    require(type(value) is str and re.fullmatch(re.escape(prefix)+r'\.[0-9a-f]{64}',value) is not None,'Invalid reference.')


def material_values(values):
    for field,value in values.items():
        if field in {'title','caption'}:
            require(value is None or type(value) is str)
        elif field=='rights_status':
            require(type(value) is str and value in {'authorized','unknown','denied'})
        elif field=='manual_review_required':
            require(type(value) is bool)
        else: require(False,'Unknown material field.')


def material(value):
    fields(value,'material_id version source_id title caption rights_status manual_review_required')
    identifier(value['material_id']); identifier(value['source_id']); positive(value['version'])
    material_values({k:value[k] for k in ('title','caption','rights_status','manual_review_required')})


def supplement(value):
    fields(value,'source_id material_id version fields')
    identifier(value['source_id']); identifier(value['material_id']); positive(value['version'])
    require(type(value['fields']) is dict and bool(value['fields']))
    material_values(value['fields'])


def rules(values):
    require(type(values) is list and len(values)==5,'Five synthetic rules are required.')
    expected={'R001':('required_fields','missing'),'R002':('denied_rights','block'),
              'R003':('banned_terms','block'),'R004':('manual_review','confirm'),'R005':('source_conflict','block')}
    seen=[]
    for r in values:
        fields(r,'rule_id version kind severity fields terms reason')
        identifier(r['rule_id']); positive(r['version']); seen.append(r['rule_id'])
        require(r['rule_id'] in expected and (r['kind'],r['severity'])==expected[r['rule_id']])
        string_list(r['fields']); string_list(r['terms'])
        require(r['fields']==(['title','caption','rights_status'] if r['rule_id']=='R001' else []))
        require(bool(r['terms']) if r['rule_id']=='R003' else r['terms']==[])
        require(type(r['reason']) is str and bool(r['reason']))
    require(set(seen)==set(expected))


def existing(values):
    require(type(values) is list)
    ids=[]; slots=[]
    for row in values:
        fields(row,'schedule_id slot_id'); identifier(row['schedule_id']); identifier(row['slot_id'])
        ids.append(row['schedule_id']); slots.append(row['slot_id'])
    string_list(ids); string_list(slots)


def validate_payload(op,payload):
    try: p=copy.deepcopy(payload)
    except (RecursionError, TypeError):
        raise RequestError('INVALID_REQUEST','Payload is too deeply nested or invalid.') from None
    if op=='schema.upgrade':
        fields(p,'target_storage_version'); require(type(p['target_storage_version']) is int and p['target_storage_version']==2)
    elif op=='fixture.load':
        fields(p,'dataset_id manifest_sha256 calendar_id'); identifier(p['dataset_id']); identifier(p['calendar_id'])
        require(type(p['manifest_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',p['manifest_sha256']) is not None)
    elif op=='task.create':
        fields(p,'task_id items'); identifier(p['task_id']); require(type(p['items']) is list and bool(p['items']))
        mids=[]
        for item in p['items']:
            fields(item,'material_id requested_time'); mids.append(identifier(item['material_id']))
            item['requested_time']=normalize_time(item['requested_time'],'payload.items.requested_time')
        string_list(mids)
    elif op=='draft.save':
        fields(p,'task_id material_id suggested_slot_id material_ref check_ref expected_draft_version')
        for k in ('task_id','material_id','suggested_slot_id'): identifier(p[k])
        digest_ref(p['material_ref'],'mat'); digest_ref(p['check_ref'],'check')
        if p['expected_draft_version'] is not None: positive(p['expected_draft_version'])
    elif op=='draft.confirm':
        fields(p,'draft_id expected_draft_version accepted_slot_id accepted_risk_ids')
        identifier(p['draft_id']); identifier(p['accepted_slot_id']); positive(p['expected_draft_version'])
        string_list(p['accepted_risk_ids'],ids=True); p['accepted_risk_ids'].sort()
    elif op=='calendar.replace':
        fields(p,'expected_revision existing'); positive(p['expected_revision']); existing(p['existing'])
        p['existing'].sort(key=lambda v:v['schedule_id'])
    else: require(False,'Unknown business operation.')
    return p


def validate_tool(name,args):
    if name=='read_material':
        fields(args,'material_id view'); identifier(args['material_id']); require(args['view'] in ('primary','complete'))
    elif name=='check_rules':
        fields(args,'material_id material_ref'); identifier(args['material_id']); digest_ref(args['material_ref'],'mat')
    elif name=='query_slots':
        fields(args,'task_id material_id'); identifier(args['task_id']); identifier(args['material_id'])
    else: raise RequestError('UNKNOWN_TOOL','Tool is not registered.')


def validate_draft(d):
    fields(d,'draft_id task_id material_id version requested_time suggested_slot_id suggested_time material_ref check_ref review_basis review_status findings confirmation_required required_risk_ids status authorization_basis occupies_slot')
    for k in ('draft_id','task_id','material_id','suggested_slot_id'): identifier(d[k])
    positive(d['version']); digest_ref(d['material_ref'],'mat'); digest_ref(d['check_ref'],'check')
    for k in ('requested_time','suggested_time'): require(normalize_time(d[k],k)==d[k])
    require(type(d['confirmation_required']) is bool and type(d['occupies_slot']) is bool)
    require(d['status'] in ('pending_confirmation','confirmed'))
    require(d['occupies_slot']==(d['status']=='confirmed'))
    require(d['review_status'] in ('passed','needs_confirmation'))
    string_list(d['required_risk_ids'],ids=True)
    basis=d['review_basis']
    fields(basis,'primary supplements rules read_result check_result checked_at')
    material(basis['primary']); require(type(basis['supplements']) is list)
    for s in basis['supplements']: supplement(s)
    rules(basis['rules']); require(normalize_time(basis['checked_at'],'checked_at')==basis['checked_at'])
    # Recompute the frozen result so altered snapshots cannot be reported as valid.
    from .material_tools import read_material_data, check_material_data
    view=basis['read_result']['view']
    require(view in ('primary','complete'))
    read=read_material_data(basis['primary'],basis['supplements'],view)
    check=check_material_data(read,basis['rules'])
    require(read==basis['read_result'] and check==basis['check_result'],'Invalid stored review snapshot.')
    require(d['material_id']==read['material_id'] and d['material_ref']==read['material_ref'] and d['check_ref']==check['check_ref'])
    require(d['review_status']==check['review_status'] and d['findings']==check['findings'] and d['required_risk_ids']==check['required_risk_ids'])
    needs=bool(d['required_risk_ids']) or d['requested_time']!=d['suggested_time']
    require(d['confirmation_required']==needs)
    if d['status']=='pending_confirmation': require(needs and d['authorization_basis'] is None)
    else: require(d['authorization_basis']==('user_confirmation' if needs else 'original_request'))


def validate_result(op,data,payload,request_id=None):
    if op=='schema.upgrade':
        fields(data,'previous_storage_version storage_schema_version')
        require(type(data['previous_storage_version']) is int and data['previous_storage_version'] in (1,2))
        require(type(data['storage_schema_version']) is int and data['storage_schema_version']==2)
    elif op=='fixture.load':
        fields(data,'dataset_id calendar_id calendar_revision occupancy_version')
        require(data['dataset_id']==payload['dataset_id'] and data['calendar_id']==payload['calendar_id'])
        for k in ('calendar_revision','occupancy_version'): positive(data[k])
    elif op=='task.create':
        fields(data,'task'); require(validate_payload(op,data['task'])==payload)
    elif op=='calendar.replace':
        fields(data,'calendar_revision occupancy_version')
        for k in data: positive(data[k])
        require(data['calendar_revision']==payload['expected_revision']+1)
    elif op=='draft.save':
        fields(data,'draft save_status'); require(data['save_status']=='saved'); validate_draft(data['draft'])
        for k in ('task_id','material_id','suggested_slot_id','material_ref','check_ref'): require(data['draft'][k]==payload[k])
    elif op=='draft.confirm':
        fields(data,'draft confirmation'); validate_draft(data['draft'])
        c=data['confirmation']; fields(c,'confirmation_id draft_id accepted_version accepted_slot_id accepted_risk_ids request_id confirmed_at')
        identifier(c['confirmation_id']); identifier(c['request_id']); positive(c['accepted_version'])
        if request_id is not None: require(c['request_id']==request_id,'Confirmation request identity mismatch.')
        require(normalize_time(c['confirmed_at'],'confirmed_at')==c['confirmed_at'])
        require(data['draft']['status']=='confirmed' and c['draft_id']==data['draft']['draft_id']==payload['draft_id'])
        require(c['accepted_version']==payload['expected_draft_version']==data['draft']['version']-1)
        require(c['accepted_slot_id']==payload['accepted_slot_id']==data['draft']['suggested_slot_id'])
        require(c['accepted_risk_ids']==payload['accepted_risk_ids']==data['draft']['required_risk_ids'])
