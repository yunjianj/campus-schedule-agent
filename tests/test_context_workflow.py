"""Regression tests execute the actual code embedded in the distributable DSL.

The graph runner mocks only external HTTP and LLM responses. It checks selectors,
branch routing, declared outputs and state changes across complete chat turns.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
import uuid
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import context_runtime as runtime
from memory_store import MemoryStore

NOW = '2026-11-09T15:30:00+08:00'
ID = 'a9d65d68-a865-4ca3-bbe5-85f0c248ac99'
ID2 = 'f95b2611-a3fb-4f93-90e4-6539e253d671'
PACKAGE = ROOT / 'dist/校园日程智能体-v37.ifpkg'
with zipfile.ZipFile(PACKAGE) as package:
    APP = yaml.safe_load(package.read('app.yaml'))
NODES = {n['id']: n['data'] for n in APP['workflow']['graph']['nodes']}
EDGES = APP['workflow']['graph']['edges']


def node(name, **kwargs):
    scope = {}
    exec(NODES[name]['code'], scope)
    result = scope['main'](**kwargs)
    assert set(result) == set(NODES[name]['outputs']), (name, set(result), set(NODES[name]['outputs']))
    for key, value in result.items():
        kind = NODES[name]['outputs'][key]['type']
        assert isinstance(value, str if kind == 'string' else (int, float)), (name, key, value)
    return result


def base(profile=None):
    # Execute embedded function with a fixed clock, not a separately imported clone.
    scope = {}
    exec(NODES['now']['code'], scope)
    return scope['base_context'](runtime.dumps([{'profile': profile}] if profile else []), NOW)['base_context']


def event(title='开会', day='2026-11-09', start='16:00', end='17:00', ident=ID, **extra):
    return {'id': ident, 'title': title, 'event_date': day, 'start_time': start,
            'end_time': end, 'user_id': 'demo', 'location': '教三', 'category': '会议',
            'status': 'planned', 'manual_override': False, **extra}


class Chat:
    """Minimal serial graph executor. Unexecuted selectors deliberately raise."""
    def __init__(self, memory=None, conversation_id=None):
        self.memory=memory or MemoryStore(NOW)
        self.conversation_id=conversation_id or str(uuid.uuid4())
        self.turn_key=None
        self.state = {v['name']: v['value'] for v in APP['workflow']['conversation_variables']}
        self.profile = None
        self.schedules = []
        self.tasks = []
        self.reviews = []
        self.fail = {}
        self.calls = []

    def turn(self, query, models=None, files=None):
        models = models or {}
        self.turn_key=self.turn_key or str(uuid.uuid4())
        pool = {'conversation': self.state, 'sys': {'query': query, 'files': files or [],'conversation_id':self.conversation_id,'workflow_run_id':self.turn_key},
                'env': {'USER_ID': 'demo', 'BASE_URL': 'https://example.supabase.co', 'SUPABASE_KEY': 'test-key'}}
        def value(selector):
            val = pool
            for key in selector: val = val[key]
            return val
        def render(text):
            return re.sub(r'\{\{#([^#]+)#\}\}', lambda m: str(value(m[1].split('.'))), text)
        def filter_rows(records, url, date_key):
            from urllib.parse import unquote
            url = unquote(url)
            selected = list(records)
            for key, op, val in re.findall(r'(?:[?&])(event_date|task_date|id|start_time)=(gte|lte|eq|in)\.([^&]+)', url):
                if op == 'gte':selected = [r for r in selected if r[key] >= val]
                elif op == 'lte':selected = [r for r in selected if r[key] <= val]
                elif op == 'eq':selected = [r for r in selected if r[key] == val]
                else:selected = [r for r in selected if r[key] in val.strip('()').split(',')]
            match = re.search(r'title=like\."\*(.*?)\*"',url)
            if match:selected = [r for r in selected if match[1] in r['title']]
            if 'limit=0' in url:return []
            return selected[:1000]
        current, traversed = 'start', []
        for _ in range(200):
            traversed.append(current)
            data = NODES[current]
            kind, handle = data['type'], 'source'
            if kind == 'start':pool[current] = {}
            elif kind == 'code':
                args = {v['variable']: value(v['value_selector']) for v in data['variables']}
                if current == 'now':
                    scope={};exec(data['code'],scope)
                    pool[current]=scope['base_context'](args['body'],self.memory.stamp())
                else:pool[current] = node(current, **args)
            elif kind == 'llm':
                # Validate every prompt reference on the chosen path.
                for prompt in data['prompt_template']:render(prompt['text'])
                response=models.get(current, {'opening':'','closing':''} if current=='reply_polish' else {} if current=='memory_select' else None)
                if response is None:raise AssertionError('Missing mock for '+current)
                pool[current]={'text': runtime.dumps(response) if isinstance(response,dict) else response}
            elif kind == 'assigner':
                for item in data['items']:
                    self.state[item['variable_selector'][1]] = '' if item['operation']=='clear' else value(item['value'])
                pool[current]={}
            elif kind == 'if-else':
                handle='false'
                for case in data['cases']:
                    if all(value(c['variable_selector']) == c['value'] for c in case['conditions']):handle=case['case_id'];break
                pool[current]={}
            elif kind == 'http-request':
                url=render(data['url']);method=data['method']
                body=render(data['body']['data'][0]['value']) if data['body']['data'] else ''
                self.calls.append((current,url,body))
                if current in self.fail:
                    pool[current]={'status_code':self.fail[current], 'body':'{"message":"failure"}'}
                else:
                    if current in ('memory_begin','memory_recall','memory_finish'):
                        params=json.loads(body)
                        fn={'memory_begin':self.memory.begin,'memory_recall':self.memory.recall,'memory_finish':self.memory.finish}[current]
                        records=[{'bundle':fn(params)}]
                    elif current=='get_profile':records=[{'profile':self.profile}] if self.profile else []
                    elif current=='save_profile':self.profile=json.loads(body)['profile'];records=[{'profile':self.profile}]
                    elif current=='get_reviews':records=self.reviews[-3:]
                    elif current in ('post','plan_post'):
                        batch=json.loads(body);records=[]
                        for r in batch['schedule_rows']:
                            r={**r,'id':str(uuid.uuid4())};self.schedules.append(r);records.append(r)
                        for r in batch['task_rows']:self.tasks.append({**r,'id':str(uuid.uuid4())})
                    elif current=='task_post':
                        r={**json.loads(body),'id':str(uuid.uuid4())};self.tasks.append(r);records=[r]
                    elif current=='rev_save':
                        r=json.loads(body);self.reviews.append(r);records=[r]
                    elif method=='delete':
                        records=filter_rows(self.schedules,url,'event_date')
                        self.schedules=[r for r in self.schedules if r not in records]
                    elif method=='patch':
                        records=filter_rows(self.schedules,url,'event_date')
                        for r in records:r.update(json.loads(body))
                    else:records=filter_rows(self.tasks if current=='get_tasks' else self.schedules,url,'task_date' if current=='get_tasks' else 'event_date')
                    pool[current]={'status_code':200,'body':runtime.dumps(records)}
            elif kind == 'template-transform':
                args = {v['variable']: value(v['value_selector']) for v in data['variables']}
                pool[current] = {'output': re.sub(r'\{\{ (v\d+) \}\}', lambda m: str(args[m[1]]), data['template'])}
            elif kind == 'variable-aggregator':
                available = [value(selector) for selector in data['variables'] if selector[0] in pool]
                assert len(available) == 1, available
                pool[current] = {'output': available[0]}
            elif kind == 'answer':
                self.turn_key=None
                return render(data['answer']),traversed
            else:raise AssertionError(kind)
            outgoing=[e for e in EDGES if e['source']==current and e['sourceHandle']==handle]
            self.assert_one(outgoing,current,handle)
            current=outgoing[0]['target']
        raise AssertionError('Graph did not terminate')

    @staticmethod
    def assert_one(edges,current,handle):
        assert len(edges)==1,(current,handle,edges)


class PackageTests(unittest.TestCase):
    def test_manifest_and_source(self):
        with zipfile.ZipFile(PACKAGE) as z:
            payload=z.read('app.yaml');manifest=yaml.safe_load(z.read('manifest.yaml'))['apps'][0]
        self.assertEqual(payload,(ROOT/'workflow/app.yaml').read_bytes())
        self.assertEqual(hashlib.sha256(payload).hexdigest(),manifest['sha256'])
        self.assertEqual(len(payload),manifest['size'])

    def test_graph_integrity(self):
        self.assertEqual(8,sum(d['type']=='llm' for d in NODES.values()))
        reached=set()
        def visit(ident,ancestors):
            self.assertNotIn(ident,ancestors)
            reached.add(ident)
            if NODES[ident]['type']=='code':compile(NODES[ident]['code'],ident,'exec')
            for edge in [e for e in EDGES if e['source']==ident]:
                self.assertIn(edge['target'],NODES)
                visit(edge['target'],ancestors|{ident})
        visit('start',set())
        self.assertEqual(set(NODES),reached)
        for ident,d in NODES.items():
            if d['type']=='http-request':
                self.assertIn('{{#env.BASE_URL#}}/rest/v1/',d['url'])
                self.assertNotIn('sb_publishable_',str(d))
        for variable in APP['workflow']['environment_variables']:
            self.assertEqual(variable['value'],'demo' if variable['name']=='USER_ID' else '')

    def test_selectors_have_declared_outputs(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from build_workflow import selectors
        for ident,data in NODES.items():
            for selector in selectors(data):
                source,field=selector[:2]
                if source in ('sys','env','conversation'):continue
                self.assertIn(source,NODES,(ident,selector))
                producer=NODES[source]
                expected={'llm':{'text'},'http-request':{'status_code','body'},'template-transform':{'output'},'variable-aggregator':{'output'}}.get(producer['type'],set(producer.get('outputs',{})))
                self.assertIn(field,expected,(ident,selector))

    def test_memory(self):
        for ident,size in [('task_extract',3),('plan_extract',6)]:
            self.assertEqual(size,NODES[ident]['memory']['window']['size'])
        for ident in ['rev_report','plan_reply2','extract','intent']:self.assertNotIn('memory',NODES[ident])
        self.assertIn('{{#intent_parse.text_history#}}',NODES['extract']['prompt_template'][0]['text'])


class RuntimeTests(unittest.TestCase):
    def test_polish_falls_back_without_changing_facts_or_confirmation(self):
        canonical='删除 2026-11-11 15:30 反诈讲座？回“确认”删除，回“取消”退出。'
        for raw in ('', 'bad json', runtime.dumps({'opening':42}), runtime.dumps({'opening':'已删除所有记录','closing':''}), runtime.dumps({'opening':'明天下午四点有空','closing':''}), runtime.dumps({'opening':'A'*81,'closing':''})):
            with self.subTest(raw=raw):
                self.assertEqual(canonical,node('polish_result',text=canonical,raw=raw)['text'])
        data=NODES['reply_polish']
        self.assertEqual('default-value',data['error_strategy'])
        self.assertEqual('',data['default_value'][0]['value'])
        self.assertNotIn('memory',data)

    def test_polish_keeps_safe_opening_when_closing_is_invalid(self):
        original='📅 日程清单'
        result=node('polish_result',text=original,raw=runtime.dumps({'opening':'这周的安排帮你整理好啦 📚','closing':'明天下午四点有空，放心玩吧'}))
        self.assertIn('整理好啦',result['text']);self.assertIn(original,result['text']);self.assertNotIn('有空',result['text'])

    def test_polish_input_is_bounded_and_contains_no_service_identity(self):
        profile=runtime.dumps([{'user_id':'demo','apikey':'private','profile':{'reply_style':'轻松一点'}}])
        result=node('polish_context',text='日程清单'*3000,profile_body=profile)
        data=json.loads(result['context'])
        self.assertEqual('轻松一点',data['reply_style'])
        self.assertEqual(6000,len(data['reply_excerpt']));self.assertTrue(data['excerpt_truncated'])
        self.assertNotIn('private',result['context']);self.assertNotIn('user_id',result['context'])
        self.assertIn('reply_polish',NODES)

    def test_school_calendar_and_missing_config(self):
        raw=runtime.dumps({'items':[{'title':'高数','kind':'event','week':3,'weekday':2,'lesson_start':1,'lesson_end':2}]})
        missing=node('prepare_add',raw=raw,draft='',base_json=base())
        self.assertEqual('false',missing['ok'])
        p={'semester_start':'2026-09-07','lessons':{'1':['08:00','08:45'],'2':['08:55','09:40']}}
        prepared=node('prepare_add',raw=raw,draft='',base_json=base(p))
        self.assertEqual('true',prepared['ok'])
        item=json.loads(prepared['prepared'])['items'][0]
        self.assertEqual(('2026-09-22','08:00','09:40'),(item['date'],item['start_time'],item['end_time']))

    def test_multi_draft_merge_and_default_end(self):
        draft=runtime.dumps({'items':[{'draft_id':'0','title':'开会','kind':'event','date':'2026-11-10'}, {'draft_id':'1','title':'课','kind':'event','date':'2026-11-11'}]})
        missing=node('prepare_add',raw=runtime.dumps({'items':[{'start_time':'15:00'}]}),draft=draft,base_json=base())
        self.assertEqual('false',missing['ok'])
        prepared=node('prepare_add',raw=runtime.dumps({'items':[{'draft_id':'0','start_time':'15:00'},{'draft_id':'1','start_time':'09:00'}]}),draft=draft,base_json=base())
        self.assertEqual('true',prepared['ok'])
        result=node('validate',prepared=prepared['prepared'],body='[]',user_id='demo',base_json=base())
        self.assertIn('估算',result['summary'])

    def test_null_draft_ids_are_unique_and_partial_batches_survive(self):
        raw=runtime.dumps({'items':[{'draft_id':None,'title':'课A','kind':'event'}, {'draft_id':None,'title':'课B','kind':'event'}]})
        prepared=node('prepare_add',raw=raw,draft='',base_json=base())
        items=json.loads(prepared['draft'])['items']
        self.assertEqual(['0','1'],[x['draft_id'] for x in items])
        self.assertEqual('false',prepared['ok'])

    def test_far_add_conflict_and_invalid_time(self):
        payload=runtime.dumps({'items':[{'title':'考试','date':'2027-05-01','start_time':'09:00','kind':'event'}]})
        prep=node('prepare_add',raw=payload,draft='',base_json=base())
        self.assertIn('2027-05-01',prep['schedule_params'])
        out=node('validate',prepared=prep['prepared'],body=runtime.dumps([event(day='2027-05-01',start='09:00',end='10:00')]),user_id='demo',base_json=base())
        self.assertIn('冲突',out['summary'])
        bad=node('prepare_add',raw=payload.replace('09:00','25:00'),draft='',base_json=base())
        self.assertEqual('false',bad['ok'])

    def test_planning_enforces_constraints_and_internal_busy(self):
        raw=runtime.dumps({'goals':[{'title':'复习A','duration_min':120,'category':'学习'},{'title':'复习B','duration_min':120,'category':'学习'}], 'constraints':{'allowed_windows':[['16:00','22:00']],'blocked_windows':[['17:00','18:00']],'max_blocks_per_day':2}})
        out=node('plan_slots',raw=raw,body='[]',base_json=base({'study_blocked_windows':[['21:00','23:59']]}),start_date='2026-11-09',end_date='2026-11-10',user_id='demo',pending_plan='')
        blocks=json.loads(out['plan_json'])['blocks']
        self.assertTrue(blocks)
        for i,b in enumerate(blocks):
            self.assertGreaterEqual(b['start_time'],'16:00');self.assertLessEqual(b['end_time'],'21:00')
            self.assertFalse(b['start_time']<'18:00' and b['end_time']>'17:00')
            for other in blocks[:i]:
                if other['event_date']==b['event_date']:
                    self.assertFalse(b['start_time']<other['end_time'] and other['start_time']<b['end_time'])
        self.assertLessEqual(sum(b['event_date']=='2026-11-09' for b in blocks),2)

    def test_tonight_only_and_unallocated(self):
        out=node('plan_slots',raw=runtime.dumps({'goals':[{'title':'论文','duration_min':600}], 'constraints':{'blocked_dates':{'2026-11-09':[['15:00','23:59']]}}}),body='[]',base_json=base(),start_date='2026-11-09',end_date='2026-11-10',user_id='demo',pending_plan='')
        blocks=json.loads(out['plan_json'])['blocks']
        self.assertTrue(all(b['event_date']=='2026-11-10' for b in blocks))
        self.assertTrue(json.loads(out['unallocated']))
        self.assertFalse(json.loads(out['assumptions']))

    def test_confirm_detects_new_conflict_and_expiry(self):
        b=event(start='16:00',end='17:00')
        plan=runtime.dumps({'blocks':[b],'tasks':[]})
        self.assertEqual('true',node('check_plan',plan_json=plan,body='[]',base_json=base(),user_id='demo')['ok'])
        self.assertEqual('false',node('check_plan',plan_json=plan,body=runtime.dumps([b]),base_json=base(),user_id='demo')['ok'])
        b['start_time']='09:00';b['end_time']='10:00'
        self.assertEqual('false',node('check_plan',plan_json=runtime.dumps({'blocks':[b]}),body='[]',base_json=base(),user_id='demo')['ok'])

    def test_query_preserves_full_list_and_bounds_model_input(self):
        records=[event(title='活动'+str(i),ident=str(uuid.uuid4())) for i in range(80)]
        out=node('operation',schedule_body=runtime.dumps(records),task_body='[]',start_date='2026-11-09',end_date='2026-11-09',base_json=base())
        self.assertIn('活动79',out['listing'])
        self.assertNotIn('活动79',out['operation_context'])

    def test_review_evidence_and_history(self):
        records=[event(status='done',manual_override=True), event('自动',ident=ID2,status='done'),event('未完成',ident=str(uuid.uuid4()))]
        tasks=[{'id':ID,'title':'写作业','task_date':'2026-11-09','done':True}]
        history=[{'range_start':'2026-11-01','stats':{'done_manual':1},'advice_summary':'下午留一小时复习'}]
        out=node('rev_stats',schedule_body=runtime.dumps(records),task_body=runtime.dumps(tasks),reviews_body=runtime.dumps(history),start_date='2026-11-09',end_date='2026-11-09',period='day',feedback='["太累"]')
        stats=json.loads(out['stats_json'])
        self.assertEqual((1,1,1,1),(stats['done_manual'],stats['done_auto'],stats['undone'],stats['task_done']))
        self.assertIn('下午留一小时复习',out['history']);self.assertIn('未人工核实',out['items'])

    def test_model_context_excludes_service_identity(self):
        state={'pending_plan':runtime.dumps({'blocks':[event()], 'tasks':[{'user_id':'demo','title':'复习'}]})}
        captured=node('state_input',**{k:state.get(k,'') for k in runtime.STATE_KEYS})
        self.assertNotIn('user_id',captured['model_state'])
        self.assertIn('user_id',captured['state_json'])
        out=node('plan_slots',raw=runtime.dumps({'goals':[{'title':'复习','duration_min':60}]}),body='[]',base_json=base(),start_date='2026-11-09',end_date='2026-11-09',user_id='demo',pending_plan='')
        self.assertNotIn('user_id',out['model_plan'])
        self.assertIn('user_id',out['plan_json'])

    def test_pending_plan_modification_keeps_goals_and_limits(self):
        pending=runtime.dumps({'goals':[{'title':'复习','duration_min':60}], 'constraints':{'blocked_windows':[['08:00','10:00']], 'start_date':'2026-12-01','end_date':'2026-12-02'}})
        raw=runtime.dumps({'goals':[],'constraints':{'max_blocks_per_day':1}})
        request=node('plan_request',raw=raw,base_json=base(),pending_plan=pending,requested_start='',requested_end='')
        self.assertEqual('2026-12-01',request['start_date'])
        out=node('plan_slots',raw=raw,body='[]',base_json=base(),start_date=request['start_date'],end_date=request['end_date'],user_id='demo',pending_plan=pending)
        plan=json.loads(out['plan_json'])
        self.assertEqual('复习',plan['blocks'][0]['title'])
        self.assertGreaterEqual(plan['blocks'][0]['start_time'],'10:00')

    def test_read_and_write_failures(self):
        self.assertEqual('false',node('window_guard',body='{"message":"oops"}',status=200)['ok'])
        self.assertEqual('false',node('window_guard',body='[]',status=500)['ok'])
        self.assertEqual('false',node('window_guard',body=runtime.dumps([{}]*1000),status=200)['ok'])
        self.assertEqual('false',node('delete_write',body='[]',status=200)['ok'])
        self.assertEqual('false',node('profile_write',body='{}',status=401)['ok'])


class ConversationTests(unittest.TestCase):
    def test_reported_second_event_location_update(self):
        chat=Chat();chat.schedules=[event('桌游社打桌游',day='2026-11-11',start='08:00',end='10:00',location='桌游社'),event('和导师开会',day='2026-11-11',start='15:00',end='16:00',location='教三201',ident=ID2)]
        chat.turn('我这周都有什么任务？',{'intent':{'intent':'query','start_date':'2026-11-09','end_date':'2026-11-15'}})
        reply,_=chat.turn('把第二条的位置改为主楼会议室',{'intent':{'intent':'update','target':'位置'}})
        self.assertIn('已修改',reply);self.assertIn('主楼会议室',reply)
        self.assertEqual('主楼会议室',chat.schedules[1]['location'])
        self.assertEqual(('2026-11-11','15:00','16:00'),tuple(chat.schedules[1][k] for k in ('event_date','start_time','end_time')))
        self.assertEqual('桌游社',chat.schedules[0]['location'])

    def test_location_change_survives_classifier_errors_and_literal_clock_room(self):
        for intent in ('other','query','update'):
            chat=Chat();chat.schedules=[event('桌游',ident=ID),event('导师会',ident=ID2)]
            chat.turn('这周有什么任务',{'intent':{'intent':'query'}})
            reply,_=chat.turn('把第二条的位置改为四点会议室',{'intent':{'intent':intent,'target':'位置','new_start':'04:00','new_date':'2026-11-15'}})
            self.assertIn('四点会议室',reply);self.assertIn('已修改',reply)
            self.assertEqual('四点会议室',chat.schedules[1]['location'])
            self.assertEqual(('2026-11-09','16:00','17:00'),tuple(chat.schedules[1][k] for k in ('event_date','start_time','end_time')))

    def test_missing_location_followup_and_title_changes(self):
        chat=Chat();chat.schedules=[event('旧标题')]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        reply,_=chat.turn('这条地点改一下',{'intent':{'intent':'other'}})
        self.assertIn('地点改到哪里',reply)
        reply,_=chat.turn('主楼会议室',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply);self.assertEqual('主楼会议室',chat.schedules[0]['location'])
        reply,_=chat.turn('把这条标题改为十一月读书会',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply);self.assertEqual('十一月读书会',chat.schedules[0]['title'])
        self.assertEqual('2026-11-09',chat.schedules[0]['event_date'])

    def test_location_and_time_change_in_one_instruction(self):
        chat=Chat();chat.schedules=[event()]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        reply,_=chat.turn('把这条地点改为主楼会议室，开始时间改成下午五点',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply)
        self.assertEqual(('主楼会议室','17:00','18:00'),tuple(chat.schedules[0][k] for k in ('location','start_time','end_time')))

    def test_multi_candidate_location_patch_is_retained_until_selection(self):
        chat=Chat();chat.schedules=[event('会议A'),event('会议B',ident=ID2)]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        reply,_=chat.turn('把这条地点改为主楼会议室',{'intent':{'intent':'other'}})
        self.assertNotIn('已修改',reply)
        reply,_=chat.turn('第二条',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply)
        self.assertEqual('主楼会议室',chat.schedules[1]['location']);self.assertEqual('教三',chat.schedules[0]['location'])

    def test_lively_framing_and_numbered_lists_survive_normal_validation(self):
        chat=Chat();chat.schedules=[event('桌游社打桌游'),event('和导师开会',ident=ID2)]
        reply,_=chat.turn('我这周都有什么任务？',{'intent':{'intent':'query'},'reply_polish':{'opening':'这周的安排帮你整理好啦 📚','closing':'想修改时间或地点，直接告诉我第几条就好～'}})
        self.assertIn('这周的安排帮你整理好啦',reply);self.assertIn('想修改时间或地点',reply)
        self.assertIn('1. **',reply);self.assertIn('2. **',reply);self.assertIn('📍',reply)
        self.assertIn('任务栏里暂无待办',reply)
        reply,_=chat.turn('把第二条位置改为主楼会议室',{'intent':{'intent':'other'},'reply_polish':{'opening':'好哒，地点已经帮你更新啦 ✨','closing':'想再调整也可以接着说～'}})
        self.assertIn('地点已经帮你更新啦',reply);self.assertIn('主楼会议室',reply)

    def test_ai_polish_keeps_canonical_list_and_reference_chain(self):
        chat=Chat();chat.schedules=[event('反诈讲座',day='2026-11-11',start='15:30',end='16:30')]
        reply,_=chat.turn('这周三有什么安排',{'intent':{'intent':'query'},'reply_polish':{'opening':'帮你整理在下面啦 📅','closing':'想怎么调整，接着告诉我就好～'}})
        self.assertIn('帮你整理',reply);self.assertIn('15:30–16:30 反诈讲座',reply)
        self.assertEqual(ID,json.loads(chat.state['last_referenced_items'])[0]['id'])
        self.assertIn('帮你整理',json.loads(chat.state['recent_dialogue'])[-1]['assistant'])
        reply,_=chat.turn('这条改成四点开始',{'intent':{'intent':'other'},'reply_polish':{'opening':'好啦 ✨','closing':'还有其他想法，也可以接着说～'}})
        self.assertIn('好啦',reply);self.assertIn('日程已修改',reply)
        self.assertEqual('16:00',chat.schedules[0]['start_time'])

    def test_reported_lecture_followup_survives_classifier_failure(self):
        lecture='反诈护航，守护青春｜新生反诈教育讲座'
        for intent in ('other','query','add','update'):
            with self.subTest(intent=intent):
                chat=Chat()
                chat.schedules=[event('课程'+str(i),day='2026-11-02',ident=str(uuid.uuid4())) for i in range(23)]
                chat.schedules.append(event(lecture,day='2026-11-11',start='15:30',end='16:30'))
                chat.turn('这周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'},'query_reply':'周三只有一场讲座。'})
                reply,path=chat.turn('帮我把这条记录改成四点开始',{'intent':{'intent':intent,'target':'这条记录','start_date':'2026-11-09','target_time':'04:00','new_start':None,'new_end':'16:30','new_date':'2026-11-09'}})
                self.assertIn('已修改',reply)
                self.assertEqual(('16:00','17:00'),(chat.schedules[-1]['start_time'],chat.schedules[-1]['end_time']))
                self.assertEqual('2026-11-11',chat.schedules[-1]['event_date'])
                self.assertFalse(any(c[0]=='extract' for c in chat.calls))

    def test_bare_modification_and_second_followup_keep_the_same_id(self):
        chat=Chat();chat.schedules=[event(day='2026-11-11',start='15:30',end='16:30')]
        chat.turn('周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'},'query_reply':'有一场安排。'})
        reply,_=chat.turn('改成下午四点开始',{'intent':{'intent':'update','new_start':'16:00'}})
        self.assertIn('已修改',reply)
        reply,_=chat.turn('再往后挪半小时',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply);self.assertEqual('16:30',chat.schedules[0]['start_time'])

    def test_candidate_selection_retains_original_change(self):
        chat=Chat();chat.schedules=[event('讲座A',day='2026-11-11',start='15:30',end='16:30'),event('讲座B',day='2026-11-11',start='18:00',end='19:00',ident=ID2)]
        chat.turn('周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'},'query_reply':'有两场讲座。'})
        reply,_=chat.turn('把这条改成四点开始',{'intent':{'intent':'update'}})
        self.assertNotIn('已修改',reply)
        reply,_=chat.turn('第一条',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply);self.assertEqual('16:00',chat.schedules[0]['start_time']);self.assertEqual('18:00',chat.schedules[1]['start_time'])

    def test_common_reference_words_and_malformed_intent(self):
        for phrase in ('把这场讲座改到下午四点','把该记录改成16:00开始','改成四点半开始'):
            with self.subTest(phrase=phrase):
                chat=Chat();chat.schedules=[event('反诈讲座',day='2026-11-11',start='15:30',end='16:30')]
                chat.turn('这周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-02','end_date':'2026-11-16'}})
                reply,_=chat.turn(phrase,{'intent':'not valid JSON'})
                self.assertIn('已修改',reply)
                self.assertEqual('16:30' if '半' in phrase else '16:00',chat.schedules[0]['start_time'])

    def test_unscoped_edit_does_not_query_all_rows(self):
        chat=Chat();chat.schedules=[event()]
        reply,_=chat.turn('改成下午四点开始',{'intent':{'intent':'update','target':'这条记录','new_start':'16:00'}})
        self.assertNotIn('已修改',reply)
        self.assertFalse(any(c[0]=='window' for c in chat.calls))
        result=node('request',intent='update',start_date='',end_date='',target='',reference_id='',state_json='{}',base_json=base(),target_time='',delete_confirmed='false')
        self.assertIn('id=is.null',result['schedule_params'])

    def test_context_id_is_enforced_again_after_fetch(self):
        pending=runtime.dumps({'instruction':'改到下午四点','patch':{'new_start':'16:00'}})
        result=node('upd_find',body=runtime.dumps([event(),event('别的会',ident=ID2)]),target='',start_date='',end_date='',reference_id=ID,pending_json=pending,base_json=base())
        self.assertEqual(ID,result['id']);self.assertEqual('true',result['found'])

    def test_new_named_target_overrides_previous_focus(self):
        chat=Chat();chat.schedules=[event('反诈讲座',day='2026-11-11',start='15:30',end='16:30'),event('高数课',day='2026-11-12',start='10:00',end='11:00',ident=ID2)]
        chat.turn('周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'}})
        reply,_=chat.turn('把高数课改成上午九点',{'intent':{'intent':'update','target':'高数课','new_start':'09:00'}})
        self.assertIn('已修改',reply);self.assertEqual('15:30',chat.schedules[0]['start_time']);self.assertEqual('09:00',chat.schedules[1]['start_time'])

    def test_date_only_change_does_not_accept_copied_model_times(self):
        chat=Chat();chat.schedules=[event('讲座',day='2026-11-11',start='15:30',end='16:30')]
        chat.turn('周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'}})
        reply,_=chat.turn('把这条改到明天',{'intent':{'intent':'other','new_start':'04:00','new_end':'05:00'}})
        self.assertIn('已修改',reply)
        self.assertEqual(('2026-11-10','15:30','16:30'),tuple(chat.schedules[0][k] for k in ('event_date','start_time','end_time')))

    def test_missing_new_time_is_asked_and_preserves_focus(self):
        chat=Chat();chat.schedules=[event('讲座',day='2026-11-11',start='15:30',end='16:30')]
        chat.turn('周三有什么安排',{'intent':{'intent':'query','start_date':'2026-11-11','end_date':'2026-11-11'}})
        reply,_=chat.turn('把这条改一下',{'intent':{'intent':'other'}})
        self.assertIn('补充新的日期',reply);self.assertTrue(chat.state['pending_update'])
        reply,_=chat.turn('下午五点',{'intent':{'intent':'other'}})
        self.assertIn('已修改',reply);self.assertEqual('17:00',chat.schedules[0]['start_time'])

    def test_invalid_candidate_and_cancel_never_apply_pending_patch(self):
        chat=Chat();chat.schedules=[event('讲座A'),event('讲座B',ident=ID2)]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        chat.turn('把这条改成四点',{'intent':{'intent':'other'}})
        reply,_=chat.turn('第三条',{'intent':{'intent':'other'}})
        self.assertNotIn('已修改',reply)
        chat.turn('取消',{'intent':{'intent':'cancel'}})
        self.assertFalse(chat.state['pending_update'])
        reply,_=chat.turn('第一条',{'intent':{'intent':'other'}})
        self.assertNotIn('已修改',reply)
        self.assertFalse(any(c[0]=='upd_exec' for c in chat.calls))

    def test_explicit_long_term_request_never_edits_previous_event(self):
        chat=Chat();chat.schedules=[event()]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        reply,_=chat.turn('以后学习都改到下午四点以后',{'intent':{'intent':'profile','profile_patch':{'allowed_windows':[['16:00','22:00']]}}})
        self.assertIn('已保存长期配置',reply);self.assertFalse(any(c[0]=='upd_exec' for c in chat.calls))

    def test_query_date_followup_uses_explicit_history_and_new_range(self):
        chat=Chat();chat.schedules=[event('周三讲座',day='2026-11-11'),event('周四课程',day='2026-11-12',ident=ID2)]
        chat.turn('这周三有什么安排',{'intent':{'intent':'query'}})
        reply,_=chat.turn('那周四呢',{'intent':{'intent':'other'}})
        self.assertIn('周四课程',reply);self.assertNotIn('周三讲座',reply)
        refs=json.loads(chat.state['last_referenced_items'])
        self.assertEqual([ID2],[r['id'] for r in refs])
        self.assertNotIn('memory',NODES['intent'])
        self.assertIn('{{#conversation.recent_dialogue#}}',NODES['intent']['prompt_template'][0]['text'])

    def test_editing_pending_plan_never_modifies_saved_focus(self):
        chat=Chat();chat.schedules=[event('已有会议',start='18:00',end='19:00')]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        chat.turn('帮我规划复习',{'intent':{'intent':'plan'},'plan_extract':{'goals':[{'title':'复习','duration_min':60}],'constraints':{}},'plan_reply2':'规划待确认。'})
        reply,_=chat.turn('把这条改成下午四点开始',{'intent':{'intent':'update','new_start':'16:00'},'plan_extract':{'goals':[],'constraints':{'allowed_windows':[['16:00','18:00']]}},'plan_reply2':'已调整草案，确认后保存。'})
        self.assertNotIn('日程已修改',reply)
        self.assertEqual('18:00',chat.schedules[0]['start_time'])
        self.assertEqual('16:00',json.loads(chat.state['pending_plan'])['blocks'][0]['start_time'])
        self.assertFalse(any(c[0]=='upd_exec' for c in chat.calls))

    def test_editing_draft_uses_draft_even_if_classifier_says_update(self):
        chat=Chat();chat.schedules=[event('已有会议',start='18:00',end='19:00')]
        chat.turn('今天有什么安排',{'intent':{'intent':'query'}})
        chat.turn('明天开会',{'intent':{'intent':'add'},'extract':{'items':[{'title':'新会议','date':'2026-11-10','kind':'event'}]}})
        reply,_=chat.turn('改成下午四点，线上',{'intent':{'intent':'update','new_start':'16:00'},'extract':{'items':[{'start_time':'16:00','location':'线上'}]}})
        self.assertIn('已保存',reply)
        self.assertEqual('18:00',chat.schedules[0]['start_time'])
        self.assertEqual('16:00',chat.schedules[1]['start_time'])
        self.assertFalse(any(c[0]=='upd_exec' for c in chat.calls))

    def test_add_followup_reference_update(self):
        chat=Chat()
        reply,_=chat.turn('明天开会',{'intent':{'intent':'add'},'extract':{'items':[{'title':'开会','date':'2026-11-10','kind':'event'}]}})
        self.assertIn('开始时间',reply);self.assertTrue(chat.state['draft'])
        reply,_=chat.turn('下午三点，教三201',{'intent':{'intent':'add'},'extract':{'items':[{'start_time':'15:00','location':'教三201'}]}})
        self.assertIn('估算',reply);self.assertFalse(chat.state['draft']);self.assertEqual(1,len(chat.schedules))
        reply,_=chat.turn('把刚才那个改到下午四点',{'intent':{'intent':'update','new_start':'16:00'}})
        self.assertIn('已修改',reply);self.assertEqual('16:00',chat.schedules[0]['start_time'])

    def test_far_query_and_keyword_update(self):
        chat=Chat();chat.schedules=[event(day='2026-12-15')]
        reply,_=chat.turn('下个月有哪些安排',{'intent':{'intent':'query','start_date':'2026-12-01','end_date':'2026-12-31'},'query_reply':'下个月有一条安排。'})
        self.assertIn('2026-12-15',reply)
        reply,_=chat.turn('把十二月十五日开会改到下午五点',{'intent':{'intent':'update','target':'开会','start_date':'2026-12-15','end_date':'2026-12-15','new_start':'17:00'}})
        self.assertIn('已修改',reply);self.assertEqual('17:00',chat.schedules[0]['start_time'])

    def test_delete_plan_confirmation_and_cancel(self):
        chat=Chat();chat.schedules=[event()]
        reply,_=chat.turn('删掉今天开会',{'intent':{'intent':'delete','target':'开会','start_date':'2026-11-09','end_date':'2026-11-09'}})
        self.assertIn('回“确认”',reply)
        reply,_=chat.turn('帮我规划复习',{'intent':{'intent':'plan'},'plan_extract':{'goals':[{'title':'复习','duration_min':60}],'constraints':{}},'plan_reply2':'可以这样安排，确认吗？'})
        self.assertEqual('',chat.state['pending_delete'])
        reply,_=chat.turn('可以',{'intent':{'intent':'plan_confirm'}})
        self.assertIn('规划已保存',reply);self.assertEqual(2,len(chat.schedules))
        reply,_=chat.turn('可以',{'intent':{'intent':'plan_confirm'}})
        self.assertEqual(2,len(chat.schedules));self.assertNotIn('已保存',reply)
        chat.turn('删掉今天开会',{'intent':{'intent':'delete','target':'开会','start_date':'2026-11-09','end_date':'2026-11-09'}})
        chat.turn('取消',{'intent':{'intent':'cancel'}})
        chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertEqual(2,len(chat.schedules))

    def test_ambiguous_historical_reference_asks(self):
        chat=Chat();chat.memory.sessions[chat.conversation_id]={'state':{'last_referenced_items':runtime.dumps([event(),event(ident=ID2)])},'last_activity_at':chat.memory.stamp()}
        reply,_=chat.turn('把刚才那个删掉',{'intent':{'intent':'delete'}})
        self.assertIn('选定一条',reply);self.assertFalse(any(c[0]=='del_exec' for c in chat.calls))

    def test_profile_persists_across_new_chat(self):
        chat=Chat()
        reply,_=chat.turn('以后晚上九点后别排学习',{'intent':{'intent':'profile','profile_patch':{'study_blocked_windows':[['21:00','23:59']]}}})
        self.assertIn('已保存',reply)
        new=Chat();new.profile=copy.deepcopy(chat.profile)
        self.assertEqual([['21:00','23:59']],json.loads(base(new.profile))['profile']['study_blocked_windows'])
        before=copy.deepcopy(new.profile)
        reply,_=new.turn('今晚别排',{'intent':{'intent':'profile','profile_patch':{'blocked_windows':[['18:00','23:59']]}}})
        self.assertEqual(before,new.profile);self.assertNotIn('已保存',reply)

    def test_image_routes_to_extraction(self):
        chat=Chat()
        reply,path=chat.turn('',{'intent':{'intent':'other'},'extract':{'items':[{'title':'高数','date':'2026-11-10','start_time':'08:00','end_time':'09:00','kind':'event'}]}},files=[{'type':'image','url':'https://example.com/a.png'}])
        self.assertIn('extract',path);self.assertIn('已保存',reply)

    def test_delete_time_clarification_duplicates_and_changed_record(self):
        chat=Chat()
        chat.schedules=[event(start='16:00',end='17:00'),event(ident=ID2,start='18:00',end='19:00')]
        chat.turn('删除今天开会',{'intent':{'intent':'delete','target':'开会','start_date':'2026-11-09','end_date':'2026-11-09'}})
        reply,_=chat.turn('下午六点那条',{'intent':{'intent':'delete','target_time':'18:00'}})
        self.assertIn('18:00',reply)
        chat.schedules[1]['title']='已修改的会议'
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('已修改的会议',reply);self.assertEqual(2,len(chat.schedules))
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('已删除 1',reply);self.assertEqual(1,len(chat.schedules))
        chat.schedules.append(event(ident=ID2))
        reply,_=chat.turn('删今天开会',{'intent':{'intent':'delete','target':'开会','start_date':'2026-11-09','end_date':'2026-11-09'}})
        self.assertIn('共 2 条',reply)
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('已删除 2',reply);self.assertFalse(chat.schedules)

    def test_task_and_actual_delete_and_failed_write(self):
        chat=Chat()
        reply,_=chat.turn('记个待办写论文',{'intent':{'intent':'task'},'task_extract':{'title':'写论文','date':None}})
        self.assertIn('已保存',reply);self.assertEqual(1,len(chat.tasks))
        chat.schedules=[event()]
        chat.turn('删除今天开会',{'intent':{'intent':'delete','target':'开会','start_date':'2026-11-09','end_date':'2026-11-09'}})
        reply,_=chat.turn('确认',{'intent':{'intent':'delete'}})
        self.assertIn('已删除 1',reply);self.assertFalse(chat.schedules)
        chat.fail['post']=500
        reply,_=chat.turn('明天下午三点开会',{'intent':{'intent':'add'},'extract':{'items':[{'title':'开会','date':'2026-11-10','start_time':'15:00','kind':'event'}]}})
        self.assertNotIn('已保存',reply);self.assertFalse(chat.schedules)

    def test_text_history_never_stores_images(self):
        chat=Chat()
        chat.turn('看看这张图',{'intent':{'intent':'other'},'extract':{'items':[{'title':'高数','date':'2026-11-10','start_time':'08:00','kind':'event'}]}},files=[{'url':'https://example.com/secret-history-image.png'}])
        history=json.loads(chat.state['recent_dialogue'])
        self.assertEqual('看看这张图',history[0]['user'])
        self.assertNotIn('secret-history-image',json.dumps(history))
        for i in range(8):chat.turn('你好'+str(i),{'intent':{'intent':'other'}})
        self.assertEqual(6,len(json.loads(chat.state['recent_dialogue'])))

    def test_http_failure_never_means_empty(self):
        chat=Chat();chat.fail['window']=500
        reply,_=chat.turn('明天有哪些安排',{'intent':{'intent':'query','start_date':'2026-11-10','end_date':'2026-11-10'}})
        self.assertIn('读取失败',reply);self.assertNotIn('没有安排',reply)

    def test_review_accumulates_feedback_and_save_failure_keeps_it(self):
        chat=Chat();chat.schedules=[event(status='done')]
        chat.tasks=[{'id':ID2,'title':'作业','task_date':'2026-11-09','done':False}]
        reply,_=chat.turn('复盘今天',{'intent':{'intent':'review','start_date':'2026-11-09','end_date':'2026-11-09'}})
        self.assertIn('作业',reply)
        chat.turn('作业没写完因为太累',{'intent':{'intent':'review_feedback'}})
        self.assertIn('太累',chat.state['review_feedback'])
        chat.fail['rev_save']=500
        reply,_=chat.turn('生成报告',{'intent':{'intent':'review_feedback'},'rev_report':{'report':'今天安排按时间默认完成。','advice_summary':'明天提前写作业'}})
        self.assertIn('存档失败',reply);self.assertTrue(chat.state['review_feedback'])
        chat.fail.clear()
        reply,_=chat.turn('生成报告',{'intent':{'intent':'review_feedback'},'rev_report':{'report':'今天安排按时间默认完成。','advice_summary':'明天提前写作业'}})
        self.assertIn('已存档',reply);self.assertFalse(chat.state['review_feedback'])

    def test_plan_rechecks_before_write(self):
        chat=Chat()
        chat.turn('规划复习',{'intent':{'intent':'plan'},'plan_extract':{'goals':[{'title':'复习','duration_min':60}],'constraints':{}},'plan_reply2':'确认吗？'})
        pending=json.loads(chat.state['pending_plan'])['blocks'][0]
        chat.schedules.append({**pending,'id':ID})
        reply,_=chat.turn('可以',{'intent':{'intent':'plan_confirm'}})
        self.assertIn('冲突',reply);self.assertEqual(1,len(chat.schedules));self.assertFalse(chat.state['pending_plan'])


if __name__ == '__main__':unittest.main(verbosity=2)
