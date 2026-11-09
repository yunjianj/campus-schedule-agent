"""In-memory contract double for the three context RPCs and the cleanup job.

Used for Chatflow path tests, not a substitute for tests/test_memory_database.sql.
"""
import copy
from datetime import datetime, timedelta
import uuid


def parsed(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value


class MemoryStore:
    def __init__(self, now):
        self.now=parsed(now)
        self.entries=[]
        self.sessions={}
        self.sequence=0
        self.retention_days=90
        self.pending_grace_days=7
        self.enabled=True

    def stamp(self):return self.now.isoformat()

    def advance(self, **kwargs):self.now+=timedelta(**kwargs)

    def finish(self, p):
        e=next(e for e in self.entries if e['id']==p['p_entry_id'] and e['user_id']==p['p_user_id'])
        if e['status']=='complete':return {'entry_id':e['id'],'saved':True,'state_applied':False,'duplicate':True}
        e.update(status='complete',assistant_text=p['p_assistant_text'],summary=p['p_summary'][:1200],topics=copy.deepcopy(p['p_topics']),
                 payload=copy.deepcopy(p['p_payload']),finished_at=self.stamp(),last_accessed_at=self.stamp())
        session=self.sessions[e['conversation_id']]
        applied=session.get('last_started_seq')==e['seq']
        if applied:session.update(state=copy.deepcopy(p['p_state']),last_completed_seq=e['seq'],last_activity_at=max(session.get('last_activity_at',e['created_at']),e['created_at']))
        return {'entry_id':e['id'],'saved':True,'state_applied':applied,'duplicate':False}

    def cleanup(self):
        protected=set()
        for session in self.sessions.values():
            state=session.get('state',{})
            if state.get('active_operation') in ('add','update','delete','plan','review') and parsed(session['last_activity_at'])>self.now-timedelta(days=min(self.pending_grace_days,self.retention_days)):
                scope=state.get('active_list') or {}
                if isinstance(scope,str):
                    import json
                    scope=json.loads(scope)
                if scope.get('scope_id'):protected.add(scope['scope_id'])
        old=[e for e in self.entries if self.enabled and parsed(e['last_accessed_at'])<self.now-timedelta(days=self.retention_days) and e['id'] not in protected]
        removed={e['id'] for e in sorted(old,key=lambda x:x['last_accessed_at'])[:5000]}
        self.entries=[e for e in self.entries if e['id'] not in removed]
        for session in self.sessions.values():
            state=session.get('state',{})
            if self.enabled and parsed(session['last_activity_at'])<self.now-timedelta(days=self.retention_days):session['state']={}
            else:
                import json
                scope=state.get('active_list') or {}
                scope=json.loads(scope) if isinstance(scope,str) else scope
                if scope.get('scope_id') in removed:state.pop('active_list',None)
                if state.get('focus_entry_id') in removed:
                    state.pop('focus_entry_id',None);state.pop('last_referenced_items',None)
                if parsed(session['last_activity_at'])<self.now-timedelta(days=self.pending_grace_days):
                    for key in ('active_operation','pending_update','pending_delete','pending_delete_filter','delete_candidates','delete_stage','pending_plan','plan_stage','review_stage','review_range','review_feedback','draft','pending_event'):state.pop(key,None)
        return len(removed)

    def begin(self,p):
        if p.get('p_pending_sync') and any(e['id']==p['p_pending_sync'].get('p_entry_id') for e in self.entries):self.finish(p['p_pending_sync'])
        deleted=self.cleanup()
        cid=p['p_conversation_id']
        session=self.sessions.setdefault(cid,{'state':{},'last_activity_at':self.stamp()})
        e=next((e for e in self.entries if e['conversation_id']==cid and e['turn_key']==p['p_turn_key']),None)
        if e is None:
            self.sequence+=1
            e={'id':str(uuid.uuid4()),'seq':self.sequence,'user_id':p['p_user_id'],'conversation_id':cid,'turn_key':p['p_turn_key'],
               'status':'pending','user_text':p['p_user_text'],'assistant_text':'','summary':'','topics':[],
               'payload':{},'created_at':self.stamp(),'last_accessed_at':self.stamp(),'finished_at':None}
            self.entries.append(e)
        if p.get('p_ignore') and e['status']!='complete' and e['seq']>=session.get('last_started_seq',0):session.update(state={},ignore_before=e['created_at'],ignore_before_seq=e['seq'])
        session.update(last_started_seq=max(session.get('last_started_seq',0),e['seq']),last_activity_at=self.stamp())
        cutoff=session.get('ignore_before')
        index=[{**{k:e.get(k) for k in ('id','conversation_id','created_at','summary','topics')},'has_list':'list_snapshot' in e['payload']} for e in sorted(self.entries,key=lambda x:-x['seq']) if e['status']=='complete' and parsed(e['created_at'])>=self.now-timedelta(days=30) and (not cutoff or e['created_at']>=cutoff) and e['seq']>=session.get('ignore_before_seq',0)][:100]
        state=copy.deepcopy(session['state'])
        import json
        pointer=state.get('active_list') or {}
        pointer=json.loads(pointer) if isinstance(pointer,str) else pointer
        original=next((e['payload']['list_snapshot'] for e in self.entries if e['id']==pointer.get('scope_id') and 'list_snapshot' in e['payload']),{})
        return {'entry_id':e['id'],'created_at':e['created_at'],'state':state,'index':index,'active_scope':copy.deepcopy(original),
                'ignore_before':cutoff,'retention_days':self.retention_days,'deleted_count':deleted,
                'duplicate_complete':e['status']=='complete','saved_reply':e['assistant_text'] if e['status']=='complete' else ''}

    def recall(self,p):
        cutoff=None if p['p_explicit_history'] else self.sessions[p['p_conversation_id']].get('ignore_before')
        selected=[]
        for e in sorted(self.entries,key=lambda x:(-int(p['p_explicit_history'] and not p.get('p_view_raw') and 'list_snapshot' in x['payload']),-x['seq'])):
            if e['status']!='complete' or e['user_id']!=p['p_user_id']:continue
            if cutoff and e['created_at']<cutoff:continue
            if not p['p_explicit_history'] and e['seq']<self.sessions[p['p_conversation_id']].get('ignore_before_seq',0):continue
            if p.get('p_date_start') and parsed(e['created_at'])<parsed(p['p_date_start']):continue
            if p.get('p_date_end') and parsed(e['created_at'])>=parsed(p['p_date_end']):continue
            ids=p.get('p_entry_ids',[]);topics=p.get('p_topics',[])
            if e['id'] in ids or (topics and any(word.lower() in (e['summary']+' '+e['user_text']+' '+e['assistant_text']).lower() for word in topics)) or (not ids and not topics and (p['p_explicit_history'] or e['conversation_id']==p['p_conversation_id'])):
                selected.append(e)
            if len(selected)==8:break
        scope={}
        if p.get('p_scope_id'):
            source=next((e for e in self.entries if e['id']==p['p_scope_id'] and e['user_id']==p['p_user_id'] and (e['conversation_id']==p['p_conversation_id'] or p['p_explicit_history'])),None)
            if source:scope=source['payload'].get('list_snapshot',{})
        scopes=[{'scope_id':e['id'],'conversation_id':e['conversation_id'],'created_at':e['created_at'],'label':e['payload']['list_snapshot']['label']} for e in selected if 'list_snapshot' in e['payload']]
        if not p.get('p_scope_id') and p['p_explicit_history'] and len(scopes)==1:
            scope=next(e['payload']['list_snapshot'] for e in selected if e['id']==scopes[0]['scope_id'])
        for e in self.entries:
            if e in selected or e['id']==scope.get('scope_id'):e['last_accessed_at']=self.stamp()
        records=[{k:e.get(k) for k in ('id','conversation_id','created_at','summary','topics','user_text','assistant_text')} for e in selected]
        return {'entries':copy.deepcopy(records),'scope':copy.deepcopy(scope),'scopes':scopes}
