"""Deterministic, pure material merging and synthetic rule checks."""
import copy
import hashlib
from .contracts import serialize

CONTENT=('title','caption','rights_status','manual_review_required')


def missing(field,value):
    return value is None or (field in ('title','caption') and not value.strip()) or (field=='rights_status' and value=='unknown')


def ref(prefix,value):
    return prefix+'.'+hashlib.sha256(serialize(value).encode('utf-8')).hexdigest()


def read_material_data(primary,supplements,view):
    supplements=sorted(supplements,key=lambda x:x['source_id'])
    effective={k:primary[k] for k in CONTENT}; provenance={k:[] for k in CONTENT}; conflicts=[]
    for field in CONTENT:
        grouped={}
        sources=[(primary['source_id'],primary[field])]+[(s['source_id'],s['fields'][field]) for s in supplements if field in s['fields']]
        for sid,value in sources:
            if not missing(field,value):
                grouped.setdefault(serialize(value),{'value':value,'source_ids':[]})['source_ids'].append(sid)
        values=[grouped[k] for k in sorted(grouped)]
        for v in values: v['source_ids'].sort()
        if len(values)>1: conflicts.append({'field':field,'values':values})
        if not missing(field,primary[field]):
            provenance[field]=sorted(sid for sid,value in sources if value==primary[field]) if view=='complete' else [primary['source_id']]
        elif view=='complete' and len(values)==1:
            effective[field]=values[0]['value']; provenance[field]=values[0]['source_ids']
    return copy.deepcopy(dict(material_id=primary['material_id'],view=view,
        material_ref=ref('mat',dict(material_id=primary['material_id'],primary=primary,supplements=supplements,view=view)),
        effective=effective,sources=sorted([dict(source_id=primary['source_id'],version=primary['version'])]+[dict(source_id=s['source_id'],version=s['version']) for s in supplements],key=lambda x:x['source_id']),
        provenance=provenance,missing_fields=sorted(k for k in CONTENT if missing(k,effective[k])),
        conflicts=sorted(conflicts,key=lambda x:x['field']),supplement_available=bool(supplements)))


def check_material_data(read,rules):
    rules=sorted(rules,key=lambda x:x['rule_id']); effective=read['effective']; findings=[]
    for rule in rules:
        hits=[]
        if rule['kind']=='required_fields':
            hits=[(f,[],[]) for f in read['missing_fields']]
        elif rule['kind']=='denied_rights' and effective['rights_status']=='denied':
            hits=[('rights_status',read['provenance']['rights_status'],['denied'])]
        elif rule['kind']=='banned_terms':
            found=sorted(t for t in rule['terms'] if t in (effective['caption'] or ''))
            if found: hits=[('caption',read['provenance']['caption'],found)]
        elif rule['kind']=='manual_review' and effective['manual_review_required']:
            hits=[('manual_review_required',read['provenance']['manual_review_required'],['true'])]
        elif rule['kind']=='source_conflict':
            hits=[(c['field'],[sid for v in c['values'] for sid in v['source_ids']],
                   [serialize(v['value']) for v in c['values']]) for c in read['conflicts']]
        for field,sources,evidence in hits:
            findings.append(dict(rule_id=rule['rule_id'],severity=rule['severity'],field=field,reason=rule['reason'],source_ids=sorted(set(sources)),evidence=sorted(set(evidence))))
    findings.sort(key=lambda x:(x['rule_id'],x['field'],x['reason']))
    levels={f['severity'] for f in findings}
    status='blocked' if 'block' in levels else 'needs_information' if 'missing' in levels else 'needs_confirmation' if 'confirm' in levels else 'passed'
    payload=dict(material_ref=read['material_ref'],rules=rules,review_status=status,findings=findings,missing_fields=read['missing_fields'],required_risk_ids=sorted({f['rule_id'] for f in findings if f['severity']=='confirm'}))
    return dict(material_id=read['material_id'],material_ref=read['material_ref'],check_ref=ref('check',payload),
                rule_versions=[dict(rule_id=r['rule_id'],version=r['version']) for r in rules],
                **{k:payload[k] for k in ('review_status','findings','missing_fields','required_risk_ids')})
