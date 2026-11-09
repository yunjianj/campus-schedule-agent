"""Persistent memory scenarios using embedded latest nodes and RPC contract doubles."""
import copy
import json
import unittest
import uuid
from datetime import timedelta
from test_context_workflow import Chat, MemoryStore, NOW, ID, ID2, event, node, base, NODES
import context_runtime as runtime


class PersistentMemoryTests(unittest.TestCase):
    def seed_chat(self, **kwargs):
        chat=Chat(**kwargs)
        chat.schedules=[event('桌游社打桌游',day='2026-11-11',start='08:00',end='10:00',location='桌游社'),
                        event('和导师开会',day='2026-11-11',start='15:00',end='16:00',location='教三201',ident=ID2)]
        return chat

    def query(self,chat,text='我这周有什么安排？'):
        return chat.turn(text,{'intent':{'intent':'query','start_date':'2026-11-09','end_date':'2026-11-15'}})

    def test_delete_confirmation_ignores_model_reconstructed_filters(self):
        chat=self.seed_chat();self.query(chat)
        reply,_=chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        self.assertIn('桌游社打桌游',reply);self.assertIn('确认',reply)
        reply,_=chat.turn('确认',{'intent':{'intent':'delete','target':'桌游社打桌游','start_date':'2026-11-09','end_date':'2026-11-09','target_time':'15:00'}})
        self.assertIn('已删除 1',reply)
        self.assertEqual([ID2],[r['id'] for r in chat.schedules])
        query_url=next(url for name,url,_ in reversed(chat.calls) if name=='window')
        self.assertIn(ID,query_url);self.assertNotIn('event_date=',query_url)
        self.assertNotIn('title=',query_url);self.assertNotIn('start_time=',query_url)

    def test_confirmation_wording_and_model_intents_cannot_redirect_delete(self):
        cases=[('确认','other'),('确认删除','query'),('好的，确认删除！','update'),('确定删除','plan'),('可以','add'),('确认✅','profile')]
        for word,intent in cases:
            with self.subTest(word=word,intent=intent):
                chat=self.seed_chat();self.query(chat)
                chat.turn('删除第一条',{'intent':{'intent':'delete'}})
                reply,_=chat.turn(word,{'intent':{'intent':intent,'target':'和导师开会','start_date':'not-a-date','end_date':'2026-01-01','target_time':'99:99'}})
                self.assertIn('已删除 1',reply);self.assertEqual([ID2],[r['id'] for r in chat.schedules])
                reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
                self.assertNotIn('已删除',reply);self.assertEqual(1,len(chat.schedules))

    def test_cancel_and_plan_wording_never_confirm_a_delete(self):
        for word in ('取消删除','先不删了','不要删除','不确认','保存','就按这个排'):
            chat=self.seed_chat();self.query(chat)
            chat.turn('删除第一条',{'intent':{'intent':'delete'}})
            reply,_=chat.turn(word,{'intent':{'intent':'delete','target':'桌游社打桌游','start_date':'2026-11-11'}})
            self.assertNotIn('已删除',reply);self.assertEqual(2,len(chat.schedules))
            self.assertFalse(any(c[0]=='del_exec' for c in chat.calls))

    def test_confirmation_uses_snapshot_ids_not_corrupted_filter_or_extra_rows(self):
        first=event('桌游社打桌游',day='2026-11-11',start='08:00',end='10:00')
        second=event('导师会议',ident=ID2)
        result=node('locate',body=runtime.dumps([first,second]),query='确认',pending=ID,stage='confirm',pending_filter='in.('+ID2+')',candidates=runtime.dumps([first]))
        self.assertEqual('true',result['execute']);self.assertEqual('in.('+ID+')',result['id_filter'])
        req=node('request',intent='delete',start_date='bad',end_date='bad',target='错误标题',target_time='99:99',reference_id=ID2,
                 state_json=runtime.dumps({'pending_delete':ID,'delete_stage':'confirm','delete_candidates':runtime.dumps([first])}),base_json=base(),delete_confirmed='true')
        self.assertIn(ID,req['schedule_params']);self.assertNotIn(ID2,req['schedule_params'])
        self.assertNotIn('event_date=',req['schedule_params'])

    def test_confirmation_reports_missing_or_changed_records_and_does_not_write(self):
        chat=self.seed_chat();self.query(chat)
        chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        chat.schedules=[r for r in chat.schedules if r['id']!=ID]
        reply,_=chat.turn('确认',{'intent':{'intent':'delete','target':'桌游社打桌游','start_date':'2026-11-09'}})
        self.assertIn('已不存在',reply);self.assertNotIn('已删除 1',reply)
        self.assertFalse(any(c[0]=='del_exec' for c in chat.calls))
        chat=self.seed_chat();self.query(chat)
        chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        chat.schedules[0]['location']='新地点'
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('发生了变化',reply);self.assertEqual(2,len(chat.schedules))
        reply,_=chat.turn('确认',{'intent':{'intent':'delete','target':'不存在的标题','start_date':'2026-11-09'}})
        self.assertIn('已删除 1',reply);self.assertEqual([ID2],[r['id'] for r in chat.schedules])

    def test_duplicate_records_are_confirmed_and_deleted_as_the_original_group(self):
        chat=self.seed_chat();copy_of_first={**chat.schedules[0],'id':str(uuid.uuid4())}
        chat.schedules.append(copy_of_first)
        chat.turn('删掉桌游社打桌游',{'intent':{'intent':'delete','target':'桌游社打桌游'}})
        reply,_=chat.turn('确认删除',{'intent':{'intent':'delete','target':'错误标题','start_date':'2026-11-09'}})
        self.assertIn('已删除 2',reply);self.assertEqual([ID2],[r['id'] for r in chat.schedules])

    def test_polish_cannot_claim_no_pending_record_after_successful_delete(self):
        result=node('polish_result',text='已删除 1 条日程。',raw=runtime.dumps({'opening':'收到确认，不过目前没有待处理的记录哦','closing':'还有想调整的内容，也可以继续说～'}))
        self.assertNotIn('没有待处理',result['text']);self.assertIn('已删除 1',result['text'])
        self.assertIn('继续说',result['text'])

    def test_original_query_list_survives_update_and_delete(self):
        chat=self.seed_chat();self.query(chat)
        source=chat.memory.entries[0]['id']
        chat.turn('把第二条改到主楼会议室',{'intent':{'intent':'update','new_location':'主楼会议室'}})
        reply,_=chat.turn('再把第一条删除',{'intent':{'intent':'other'}})
        self.assertIn('桌游社打桌游',reply);self.assertIn('确认',reply)
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('已删除 1',reply)
        self.assertEqual([ID2],[r['id'] for r in chat.schedules]);self.assertEqual('主楼会议室',chat.schedules[0]['location'])
        scope=json.loads(chat.state['active_list'])
        self.assertEqual(source,scope['scope_id']);self.assertTrue(scope['items'][0]['deleted'])
        self.assertEqual(ID2,scope['items'][1]['id'])
        original=chat.memory.entries[0]['payload']['list_snapshot']
        self.assertEqual('教三201',original['items'][1]['location'])
        reply,_=chat.turn('再删除第一条',{'intent':{'intent':'delete'}})
        self.assertIn('已经删除',reply);self.assertEqual(1,len(chat.schedules))

    def test_more_than_six_turns_and_full_ordinal_list(self):
        chat=Chat();chat.schedules=[event('活动'+str(i),ident=str(uuid.uuid4())) for i in range(25)]
        self.query(chat)
        self.assertEqual(25,len(json.loads(chat.state['active_list'])['items']))
        for i in range(12):chat.turn('你好'+str(i),{'intent':{'intent':'other'}})
        self.assertEqual(6,len(json.loads(chat.state['recent_dialogue'])))
        self.assertEqual(13,len(chat.memory.entries))
        reply,_=chat.turn('删除第二十五条',{'intent':{'intent':'delete'}})
        self.assertIn('活动24',reply);self.assertIn('确认',reply)

    def test_new_chat_requires_explicit_source_but_can_restore_yesterdays_table(self):
        old=self.seed_chat();self.query(old)
        old.memory.advance(days=1)
        new=Chat(memory=old.memory);new.schedules=old.schedules
        reply,_=new.turn('删除第一条',{'intent':{'intent':'delete'}})
        self.assertNotIn('回“确认”',reply);self.assertFalse(any(c[0]=='window' for c in new.calls))
        reply,_=new.turn('删除昨天那张表的第一条',{'intent':{'intent':'delete'}})
        self.assertIn('桌游社打桌游',reply);self.assertIn('确认',reply)
        self.assertEqual(2,len(new.schedules))

    def test_historical_list_is_not_displaced_by_many_later_chat_turns(self):
        old=self.seed_chat();self.query(old)
        for i in range(12):old.turn('谢谢'+str(i),{'intent':{'intent':'other'}})
        old.memory.advance(days=1)
        new=Chat(memory=old.memory);new.schedules=old.schedules
        reply,_=new.turn('删除昨天那张表的第一条',{'intent':{'intent':'delete'}})
        self.assertIn('桌游社打桌游',reply);self.assertIn('确认',reply)

    def test_historical_conversation_date_is_not_event_date(self):
        old=Chat();old.schedules=[event('导师会议',day='2026-11-11',start='15:00',end='16:00')]
        self.query(old);old.memory.advance(days=1)
        new=Chat(memory=old.memory);new.schedules=old.schedules
        reply,_=new.turn('把昨天聊过的导师会议改到下午四点',{'intent':{'intent':'update','target':'导师会议','start_date':'2026-11-09','new_start':'16:00'}})
        self.assertIn('已修改',reply);self.assertEqual('2026-11-11',new.schedules[0]['event_date']);self.assertEqual('16:00',new.schedules[0]['start_time'])

    def test_multiple_historical_tables_have_resumable_selection(self):
        old=self.seed_chat();self.query(old);self.query(old)
        old.memory.advance(days=1)
        new=Chat(memory=old.memory);new.schedules=old.schedules
        reply,_=new.turn('删除昨天那张表的第一条',{'intent':{'intent':'delete'}})
        self.assertIn('多张历史列表',reply);self.assertTrue(new.state['memory_choices'])
        reply,_=new.turn('第二张',{'intent':{'intent':'other'}})
        self.assertIn('桌游社打桌游',reply);self.assertIn('确认',reply)
        self.assertFalse(new.state['memory_choices'])

    def test_ignore_context_keeps_raw_log_but_clears_ordinal_scope(self):
        chat=self.seed_chat();self.query(chat)
        reply,_=chat.turn('忽略之前的上下文',{'intent':{'intent':'other'}})
        self.assertIn('已忽略',reply);self.assertFalse(chat.state['active_list'])
        self.assertEqual(2,len(chat.memory.entries))
        reply,_=chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        self.assertNotIn('回“确认”',reply)

    def test_document_view_contains_original_dialogue_and_local_time(self):
        chat=self.seed_chat();self.query(chat)
        reply,_=chat.turn('查看上下文文档原文',{'intent':{'intent':'memory'}})
        self.assertIn('我这周有什么安排',reply);self.assertIn('桌游社打桌游',reply)
        self.assertIn('2026-11-09 15:30:00 +0800',reply)
        first=chat.memory.entries[0]
        self.assertEqual('我这周有什么安排？',first['user_text'])
        self.assertEqual('complete',first['status']);self.assertTrue(first['finished_at']);self.assertTrue(first['summary'])

    def test_logging_failure_is_retried_without_repeating_business_operation(self):
        chat=self.seed_chat();self.query(chat)
        chat.fail['memory_finish']=500
        reply,_=chat.turn('第二条位置改为主楼会议室',{'intent':{'intent':'update'}})
        self.assertIn('已修改',reply);self.assertIn('暂未同步',reply);self.assertTrue(chat.state['pending_memory_sync'])
        operations=sum(c[0]=='upd_exec' for c in chat.calls)
        chat.fail.clear()
        chat.turn('你好',{'intent':{'intent':'other'}})
        self.assertEqual(operations,sum(c[0]=='upd_exec' for c in chat.calls))
        self.assertFalse(chat.state['pending_memory_sync'])
        saved=chat.memory.entries[1]
        self.assertEqual('complete',saved['status']);self.assertIn('暂未同步',saved['assistant_text'])

    def test_memory_read_failure_and_duplicate_requests_never_write_twice(self):
        chat=self.seed_chat();chat.fail['memory_begin']=500
        reply,_=chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        self.assertIn('未执行日程操作',reply);self.assertFalse(any(c[0]=='del_exec' for c in chat.calls))
        chat.fail.clear();self.query(chat)
        turn='same-turn';chat.turn_key=turn
        chat.turn('第二条位置改为主楼会议室',{'intent':{'intent':'update'}})
        n=sum(c[0]=='upd_exec' for c in chat.calls)
        chat.turn_key=turn
        reply,path=chat.turn('第二条位置改为主楼会议室')
        self.assertIn('memory_replay',path);self.assertIn('主楼会议室',reply)
        self.assertEqual(n,sum(c[0]=='upd_exec' for c in chat.calls))

    def test_concurrent_turns_preserve_newer_state_and_idempotent_finish(self):
        store=MemoryStore(NOW)
        def begin(key):return store.begin({'p_user_id':'demo','p_conversation_id':'c','p_turn_key':key,'p_user_text':key})
        a,b=begin('a'),begin('b')
        def finish(entry,marker):return store.finish({'p_user_id':'demo','p_entry_id':entry['entry_id'],'p_assistant_text':marker,'p_summary':marker,'p_topics':[],'p_state':{'active_operation':marker},'p_payload':{}})
        self.assertTrue(finish(b,'new')['state_applied']);self.assertFalse(finish(a,'old')['state_applied'])
        self.assertTrue(finish(b,'duplicate')['duplicate'])
        self.assertEqual('new',store.sessions['c']['state']['active_operation'])
        self.assertEqual(2,len(store.entries))

    def test_retention_deletes_unused_entries_but_not_business_data(self):
        chat=self.seed_chat();self.query(chat)
        profile={'reply_style':'轻松'};chat.profile=profile
        chat.memory.advance(days=91)
        self.assertEqual(1,chat.memory.cleanup());self.assertEqual(2,len(chat.schedules));self.assertEqual(profile,chat.profile)
        self.assertEqual({},chat.memory.sessions[chat.conversation_id]['state'])
        reply,_=chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        self.assertNotIn('回“确认”',reply)

    def test_index_reads_do_not_renew_expiry_but_recall_does(self):
        chat=self.seed_chat();self.query(chat)
        source=chat.memory.entries[0];stamp=source['last_accessed_at']
        chat.memory.advance(days=60)
        p={'p_user_id':'demo','p_conversation_id':'index-only','p_turn_key':'x','p_user_text':'hi'}
        b=chat.memory.begin(p)
        self.assertEqual(stamp,source['last_accessed_at'])
        chat.memory.recall({'p_user_id':'demo','p_conversation_id':'index-only','p_entry_id':b['entry_id'],'p_entry_ids':[source['id']],
             'p_topics':[],'p_explicit_history':True,'p_scope_id':None,'p_date_start':None,'p_date_end':None})
        self.assertNotEqual(stamp,source['last_accessed_at'])
        chat.memory.advance(days=31);chat.memory.cleanup()
        self.assertIn(source['id'],[e['id'] for e in chat.memory.entries])

    def test_pending_operation_protects_scope_then_expires(self):
        chat=self.seed_chat();self.query(chat)
        source=chat.memory.entries[0]
        chat.memory.advance(days=89)
        chat.turn('删除第一条',{'intent':{'intent':'delete'}})
        chat.memory.advance(days=2)
        # Even if the original entry's use time was old, an unfinished recent confirmation pins it.
        source['last_accessed_at']=(chat.memory.now-timedelta(days=91)).isoformat()
        chat.memory.cleanup();self.assertIn(source['id'],[e['id'] for e in chat.memory.entries])
        chat.memory.advance(days=8);chat.memory.cleanup()
        self.assertNotIn(source['id'],[e['id'] for e in chat.memory.entries])

    def test_expired_sync_and_focus_cannot_resurrect_deleted_memory(self):
        chat=self.seed_chat();self.query(chat)
        chat.fail['memory_finish']=500
        chat.turn('第二条位置改为主楼会议室',{'intent':{'intent':'update'}})
        self.assertTrue(chat.state['pending_memory_sync'])
        chat.memory.advance(days=100);chat.memory.cleanup();chat.fail.clear()
        reply,_=chat.turn('你好',{'intent':{'intent':'other'}})
        self.assertFalse(chat.state['active_list']);self.assertFalse(chat.state['last_referenced_items'])
        self.assertFalse(chat.state['pending_memory_sync'])
        self.assertNotIn('未执行日程操作',reply)
        self.assertEqual('主楼会议室',chat.schedules[1]['location'])

    def test_invalid_selector_ids_are_not_sent_to_the_database(self):
        result=node('memory_recall_body',raw=runtime.dumps({'entry_ids':['forged-id'],'scope_id':{},'topics':['导师']}),
            query='查一下历史对话',base_json=base(),state_json='{}',entry_id=str(uuid.uuid4()),user_id='demo',conversation_id='c',index_json='[]')
        params=json.loads(result['body'])
        self.assertEqual([],params['p_entry_ids']);self.assertIsNone(params['p_scope_id'])
        self.assertEqual(['导师'],params['p_topics'])

    def test_sql_rpc_parameter_contracts_match_workflow(self):
        from pathlib import Path
        import re
        sql=(Path(__file__).resolve().parents[1]/'docs/context-memory.sql').read_text()
        chat=self.seed_chat();self.query(chat)
        for name,url,body in chat.calls:
            if name not in ('memory_begin','memory_recall','memory_finish'):continue
            function=url.rsplit('/',1)[-1]
            signature=re.search(r'function public\.'+function+r'\((.*?)\) returns table',sql,re.S)
            self.assertIsNotNone(signature,function)
            keys=set(re.findall(r'\b(p_\w+)\s+(?:text|uuid|jsonb|boolean|timestamptz)',signature[1]))
            self.assertEqual(keys,set(json.loads(body)),function)

    def test_custom_retention_and_disable_switch(self):
        chat=self.seed_chat();self.query(chat)
        chat.memory.retention_days=3;chat.memory.enabled=False;chat.memory.advance(days=4)
        self.assertEqual(0,chat.memory.cleanup())
        chat.memory.enabled=True;self.assertEqual(1,chat.memory.cleanup())


if __name__=='__main__':unittest.main(verbosity=2)
