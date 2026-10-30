"""Model boundary. OfflineMock is explicitly NOT evidence of real model decisions."""
from typing import Protocol


class Model(Protocol):
    mode: str
    def next_action(self, context: dict) -> dict: ...


class OfflineMock:
    """Deterministic observation-based fixture for offline flow verification only."""
    mode='offline_mock'

    def next_action(self,context):
        mid=context['item']['material_id']; tid=context['task_id']; observations=context['observations']
        def action(name,args,reason): return dict(action=name,arguments=args,reason=reason)
        def finish(reason): return action('finish',{},reason)
        if not observations:
            return action('read_material',dict(material_id=mid,view='primary'),'先读取有来源的基础资料。')
        last=observations[-1]; result=last['result']
        if result.get('ok') is False or result.get('status') in ('failed','unknown'):
            if result.get('error',{}).get('code')=='SLOT_CONFLICT':
                return action('query_slots',dict(task_id=tid,material_id=mid),'保存发现冲突，重新查询实际占用。')
            return finish('工具没有成功，保留错误并停止本项。')
        data=result['data']
        if last['action']=='read_material':
            return action('check_rules',dict(material_id=mid,material_ref=data['material_ref']),'按读取到的资料引用检查规则。')
        if last['action']=='check_rules':
            read=next(o['result']['data'] for o in reversed(observations) if o['action']=='read_material' and o['result'].get('ok'))
            if data['review_status']=='needs_information' and read['view']=='primary' and read['supplement_available']:
                return action('read_material',dict(material_id=mid,view='complete'),'审核缺资料且存在补充来源，补查后重检。')
            if data['review_status'] in ('blocked','needs_information'): return finish('当前资料不足或存在阻断风险，不能保存排期。')
            return action('query_slots',dict(task_id=tid,material_id=mid),'审核允许提出排期，查询原时段及替代候选。')
        if last['action']=='query_slots':
            slot=data['requested_slot_id'] if data['requested_status']=='available' else (data['alternatives'][0]['slot_id'] if data['alternatives'] else None)
            if slot is None: return finish('工具没有返回可用时段，明确未完成。')
            checked=next(o['result']['data'] for o in reversed(observations) if o['action']=='check_rules' and o['result'].get('ok'))
            return action('save_draft',dict(task_id=tid,material_id=mid,suggested_slot_id=slot,material_ref=checked['material_ref'],check_ref=checked['check_ref']),
                          '原时段可用则保留原要求，否则仅保存替代建议等待用户确认。')
        return finish('根据实际保存结果结束；待确认稿不代表已发布。')
