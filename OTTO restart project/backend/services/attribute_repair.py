"""Repair only explicitly category-incompatible fields, never invalid values."""
import json
from datetime import datetime, timezone
from sqlalchemy import text

NOT_ALLOWED = {'ATTRIBUTE_NOT_ALLOWED','ATTRIBUTE_NOT_PERMITTED','UNKNOWN_ATTRIBUTE'}


def rejected_attribute_names(results):
    names=set()
    for row in results if isinstance(results,list) else []:
        if not isinstance(row,dict) or row.get('result')!='FAILED':
            continue
        for error in row.get('errors',[]):
            if isinstance(error,dict) and error.get('code') in NOT_ALLOWED:
                name=error.get('attribute')
                if isinstance(name,str) and 0<len(name.strip())<=120:
                    names.add(name.strip())
    return names


def queue_attribute_repair(conn, *, account, item_id, results):
    names=rejected_attribute_names(results)
    if not names:
        return False
    row=conn.execute(text("SELECT i.*,o.current_revision_number FROM restart_items i JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account WHERE i.id=:id AND i.account=:account AND i.status='NEEDS_REVIEW' AND o.submission_state='NEVER_SUBMITTED' FOR UPDATE OF i,o"),{'id':item_id,'account':account}).mappings().first()
    if row is None or row['auto_repair_count']>=5:
        return False
    old=row['excluded_attribute_names_json'] or []
    excluded=sorted(set(old)|names)
    current=datetime.now(timezone.utc)
    item=conn.execute(text("UPDATE restart_items SET status='PREPARING',preparation_generation=preparation_generation+1,auto_repair_count=auto_repair_count+1,excluded_attribute_names_json=CAST(:excluded AS jsonb),scheduled_at=:now,version=version+1 WHERE id=:id AND account=:account RETURNING *"),{'id':item_id,'account':account,'excluded':json.dumps(excluded),'now':current}).mappings().one()
    from services.drip_preparation import enqueue_preparation
    from services.drip_service import _event
    enqueue_preparation(conn,item=item,account=account,now=current)
    _event(conn,account=account,actor='system:attribute-repair',event_type='OTTO_CATEGORY_ATTRIBUTE_REPAIR',entity_type='RESTART_ITEM',entity_id=str(item_id),summary='Category-incompatible attributes excluded; new revision and validation queued',metadata={'excludedNames':sorted(names),'repairNumber':item['auto_repair_count']})
    return True
