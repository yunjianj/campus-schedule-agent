"""Pure, dependency-free runtime embedded into Dify code nodes by build_workflow.py.

Database calls and credentials stay in HTTP nodes; model-facing context is allowlisted.
"""
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

DEFAULT_PROFILE = {
    'timezone': 'Asia/Shanghai', 'semester_start': None, 'lessons': {},
    'allowed_windows': [['08:00', '22:00']], 'blocked_windows': [],
    'study_blocked_windows': [], 'max_blocks_per_day': 2, 'reply_style': '简洁、自然的中文',
}
PROFILE_KEYS = set(DEFAULT_PROFILE)
UPDATE_FIELDS = ('new_date','new_start','new_end','new_location','new_title')
STATE_KEYS = ['draft', 'pending_event', 'review_range', 'pending_delete',
              'conflict_note', 'review_stage', 'plan_stage', 'pending_plan',
              'delete_stage', 'delete_candidates', 'pending_delete_filter',
              'active_operation', 'last_referenced_items', 'review_feedback', 'recent_dialogue', 'pending_update', 'active_list', 'memory_context', 'memory_control', 'pending_memory_sync', 'turn_facts', 'memory_choices', 'focus_entry_id']


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def obj(value, default=None):
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def model_data(value):
    """Remove service identity/configuration fields from model-facing state."""
    if isinstance(value, str) and value[:1] in ('{','['):
        parsed = obj(value)
        if isinstance(parsed,(dict,list)):
            return model_data(parsed)
    if isinstance(value, dict):
        return {k:model_data(v) for k,v in value.items() if k.lower() not in
                ('user_id','conversation_id','apikey','supabase_key','authorization','access_token','refresh_token','pending_memory_sync')}
    if isinstance(value, list):
        return [model_data(v) for v in value]
    return value


def model_json(raw):
    text = re.sub(r'<think\b[^>]*>.*?</think>', '', str(raw or ''), flags=re.S)
    start, end = text.find('{'), text.rfind('}')
    value = obj(text[start:end + 1], {}) if start >= 0 else {}
    return value if isinstance(value, dict) else {}


def rows(body, status=200, allow_limit=False):
    value = obj(body)
    if not 200 <= int(status) < 300 or not isinstance(value, list):
        raise ValueError('读取失败，请稍后重试；未将失败视为空记录。')
    if len(value) >= 1000 and not allow_limit:
        raise ValueError('记录达到读取上限，请缩小日期范围后重试。')
    return value


def hm(value):
    m = re.fullmatch(r'(\d{2}):(\d{2})(?::00)?', str(value or ''))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError('时间格式须为 HH:MM，且在 00:00–23:59 内。')
    return int(m[1]) * 60 + int(m[2])


def clock(value):
    return '%02d:%02d' % divmod(value, 60)


def iso(value):
    return date.fromisoformat(str(value)).isoformat()


def windows(value):
    if not isinstance(value, list):
        raise ValueError('时段须为二维数组。')
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2 or hm(pair[0]) >= hm(pair[1]):
            raise ValueError('时段起止无效。')
        result.append([str(pair[0]), str(pair[1])])
    return result


def profile_checked(raw):
    if not isinstance(raw, dict) or set(raw) - PROFILE_KEYS:
        raise ValueError('画像字段不受支持。')
    if len(dumps(raw)) > 12000:
        raise ValueError('画像过长，请减少节次或限制条目。')
    p = dict(DEFAULT_PROFILE)
    p.update(raw)
    ZoneInfo(p['timezone'])
    if p['semester_start']:
        d = date.fromisoformat(p['semester_start'])
        if d.weekday() != 0:
            raise ValueError('学期第一周起始日必须为周一。')
    if not isinstance(p['lessons'], dict):
        raise ValueError('节次表须为对象。')
    for key, pair in p['lessons'].items():
        if not str(key).isdigit() or int(key) < 1:
            raise ValueError('节次编号无效。')
        windows([pair])
    p['allowed_windows'] = windows(p['allowed_windows'])
    p['blocked_windows'] = windows(p['blocked_windows'])
    p['study_blocked_windows'] = windows(p['study_blocked_windows'])
    if not p['allowed_windows']:
        raise ValueError('至少需要一个可安排时段。')
    if type(p['max_blocks_per_day']) is not int or not 1 <= p['max_blocks_per_day'] <= 20:
        raise ValueError('每天块数须在 1–20 之间。')
    if not isinstance(p['reply_style'], str) or len(p['reply_style']) > 200:
        raise ValueError('回复偏好须为不超过 200 字的文本。')
    return p


def read_guard(body, status):
    try:
        rows(body, status)
        return {'ok': 'true', 'error': ''}
    except (ValueError, TypeError) as exc:
        return {'ok': 'false', 'error': str(exc)}


def base_context(body, now_iso=''):
    data = rows(body)
    if len(data) > 1:
        raise ValueError('画像记录不唯一。')
    profile = profile_checked(data[0].get('profile') or {}) if data else dict(DEFAULT_PROFILE)
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(ZoneInfo(profile['timezone']))
    now = now.astimezone(ZoneInfo(profile['timezone']))
    context = {'now': now.isoformat(), 'today': now.date().isoformat(),
               'weekday': now.isoweekday(), 'profile': profile}
    return {'now': context['now'], 'today': context['today'],
            'weekday': '周' + '一二三四五六日'[now.weekday()],
            'base_context': dumps(context)}


def context_items(records):
    """The displayed result, not an LLM's prose, defines subsequent referents."""
    fields = ('id', 'title', 'event_date', 'start_time', 'end_time', 'location', 'category')
    return [{k: r.get(k) for k in fields} for r in records if r.get('id')]


def chinese_number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    if '十' in value:
        a, _, b = value.partition('十')
        return (digits.get(a, 1) if a else 1) * 10 + (digits.get(b, 0) if b else 0)
    return digits.get(value, -1)


def date_span(text, base):
    """Resolve common explicit selectors without borrowing dates from model memory."""
    today = date.fromisoformat(base['today'])
    m = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?', text)
    if m:
        day = date(int(m[1]), int(m[2]), int(m[3]))
        return day.isoformat(), day.isoformat()
    m = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})[日号]?', text)
    if m:
        day = date(today.year, int(m[1]), int(m[2]))
        return day.isoformat(), day.isoformat()
    m = re.search(r'(下下|下|这|本)?(?:周|星期|礼拜)([一二三四五六日天])', text)
    if m:
        prefix = m[1] or ''
        idx = '一二三四五六日'.find('日' if m[2] == '天' else m[2])
        day = today - timedelta(days=today.weekday()) + timedelta(days=idx, weeks={'下下':2, '下':1}.get(prefix,0))
        if not prefix and day < today:
            day += timedelta(days=7)
        return day.isoformat(), day.isoformat()
    for label, offset in [('大后天',3),('后天',2),('明天',1),('今天',0),('昨天',-1)]:
        if label in text:
            day = today + timedelta(days=offset)
            return day.isoformat(), day.isoformat()
    m = re.search(r'(上|下|这|本)周', text)
    if m:
        begin = today - timedelta(days=today.weekday()) + timedelta(weeks={'上':-1,'下':1}.get(m[1],0))
        return begin.isoformat(), (begin+timedelta(days=6)).isoformat()
    m = re.search(r'(上个|下个|这个|本)月', text)
    if m:
        month = today.year*12+today.month-1 + {'上个':-1,'下个':1}.get(m[1],0)
        year, index = divmod(month,12)
        begin = date(year,index+1,1)
        ny, nm = divmod(month+1,12)
        return begin.isoformat(), (date(ny,nm+1,1)-timedelta(days=1)).isoformat()
    return '', ''


TIME_PATTERN = r'(凌晨|上午|早上|中午|下午|晚上|傍晚)?\s*([零一二两三四五六七八九十\d]{1,3})(?:[:：](\d{2})|(?:点|时)(半|[零一二两三四五六七八九十\d]{1,3}分?)?)'
EDIT_PATTERN = r'改成|改到|改为|改在|修改|更改|改一下|调整到|调整为|挪到|移到|换成|换到|搬到|推迟|提前|延后|往后.{0,3}挪|往前.{0,3}挪'


def spoken_time(match, item=None):
    period, hour, minute, tail = match.groups()
    h = chinese_number(hour)
    minute = int(minute) if minute is not None else (30 if tail == '半' else chinese_number(tail.rstrip('分')) if tail else 0)
    if period in ('下午','晚上','傍晚') and h < 12:
        h += 12
    elif period == '中午' and h < 11:
        h += 12
    elif not period and ':' not in match[0] and '：' not in match[0] and 1 <= h <= 12:
        if not item or not item.get('start_time'):
            return ''
        original = hm(item['start_time'])
        if original >= 12*60 and h < 12:
            h += 12
        elif original < 12*60 and h == 12:
            return ''
    value = '%02d:%02d' % (h, minute)
    hm(value)
    return value


def edit_patch(text, item, base):
    """Explicit clock edits and relative moves; no subject or ID is model-generated."""
    # Text attributes are literal values: a room such as “四点会议室” is not a clock edit.
    result, time_clauses = {}, []
    clauses = re.split(r'[，,;；](?=(?:并|再|同时)?(?:时间|开始时间|结束时间|日期|地点|位置|地址|标题|名称|名字))', text)
    for clause in clauses:
        field = re.search(r'(地点|位置|地址|标题|名称|名字)(?:\s*(?:修改|更改|改|调整|换)(?:成|为|到|在|一下)?\s*)[：:]*\s*(.*)', clause)
        if field:
            key = 'new_location' if field[1] in ('地点','位置','地址') else 'new_title'
            value = re.sub(r'(?:谢谢|就好|就行|即可|吧)$', '', field[2]).strip().strip('“”"\'')
            result[key] = value
        else:
            time_clauses.append(clause)
    if result and not time_clauses:
        return result
    text = '，'.join(time_clauses)
    change = re.search(EDIT_PATTERN, text)
    tail = text[change.end():] if change else text
    # For '推迟到四点' the '到' is harmless to the time/date parsers.
    start, end = date_span(tail,base)
    if start and start == end:
        result['new_date'] = start
    relative = re.search(r'(往后|往前|推迟|提前|延后|晚|早).*?(半|[一二两三四五六七八九十\d]+)(个?小时|分钟)',text)
    if relative and item:
        amount = 30 if relative[2] == '半' else chinese_number(relative[2]) * (60 if '小时' in relative[3] else 1)
        sign = -1 if relative[1] in ('往前','提前','早') else 1
        total = hm(item['start_time']) + sign*amount
        if not 0 <= total < 1440:
            raise ValueError('调整后会跨日，请明确新的日期和时间。')
        result['new_start'] = clock(total)
        return result
    times = list(re.finditer(TIME_PATTERN,tail))
    if times:
        value = spoken_time(times[0], item)
        # An unresolved bare clock must not fall back to a guessed model value.
        field = 'new_end' if '结束' in text and '开始' not in text and len(times)==1 else 'new_start'
        result[field] = value
        if len(times)>1:
            result['new_end'] = spoken_time(times[1],item)
    return result


def select_context(text, records, base, target=''):
    selected = list(records)
    ordinal = re.search(r'第([一二两三四五六七八九十\d]+)(?:条|个|场|项)',text)
    if ordinal:
        index = chinese_number(ordinal[1])-1
        return selected[index:index+1] if 0 <= index < len(selected) else []
    named = [r for r in selected if r.get('title') and r['title'] in text]
    if named:
        selected = named
    elif target and target in text:
        selected = [r for r in selected if target in r.get('title','')]
    else:
        categories = [word for word in ('讲座','课程','会议','考试','社团') if word in text]
        if categories:
            selected = [r for r in selected if any(word in r.get('title','') or word == r.get('category') for word in categories)]
    start,end = date_span(text,base)
    if start:
        selected = [r for r in selected if start <= r.get('event_date','') <= end]
    tm = re.search(TIME_PATTERN,text)
    if tm:
        selected = [r for r in selected if str(r.get('start_time',''))[:5] == spoken_time(tm,r)]
    return selected


GENERIC_CONFIRMATIONS = {'可以','确认','确定','保存','就按这个排','没问题','好的','好','是的','对'}
DELETE_CONFIRMATIONS = {'删','删除','删掉','确认删除','确定删除','确认删掉','确定删掉','删除吧','删吧','好的确认删除','确认吧','确认删除吧'}
CANCELLATIONS = {'取消','算了','不记了','不删了','不要了','取消规划','取消复盘','取消删除','不要删除','不删除','暂不删除','先不删','先不删了','不确认'}


def confirmation_kind(text):
    normalized=re.sub(r'[\s，,。.!！?？～~✅👍]','',str(text or ''))
    if normalized in CANCELLATIONS:return 'cancel'
    if normalized in DELETE_CONFIRMATIONS:return 'delete'
    if normalized in GENERIC_CONFIRMATIONS:return 'generic'
    return ''


def is_delete_confirmation(text):
    kind=confirmation_kind(text)
    normalized=re.sub(r'[\s，,。.!！?？～~✅👍]','',str(text or ''))
    return kind=='delete' or (kind=='generic' and normalized not in ('保存','就按这个排'))


def pending_delete_ids(state):
    """Only the snapshot shown to the user may authorize a confirmation lookup."""
    import uuid
    candidates=obj(state.get('delete_candidates'),[])
    if not isinstance(candidates,list) or not candidates or state.get('delete_stage')!='confirm':return []
    ids=[]
    try:
        for row in candidates:
            ident=str(uuid.UUID(str(row['id'])))
            if ident not in ids:ids.append(ident)
        if str(uuid.UUID(str(state.get('pending_delete') or ''))) not in ids:return []
    except (ValueError,TypeError,KeyError,AttributeError):return []
    return ids


def route(raw, query, files, state_json, base_json, user_id):
    data = model_json(raw)
    base = obj(base_json, {})
    state = {k: str(obj(state_json, {}).get(k) or '') for k in STATE_KEYS}
    intent = str(data.get('intent') or 'other')
    valid = {'add','query','review','delete','plan','update','review_feedback','plan_confirm','task','other','profile','cancel','confirm','memory'}
    intent = intent if intent in valid else 'other'
    text = str(query or '').strip().rstrip('。！!')
    acknowledgement=confirmation_kind(text)
    confirmed_delete=bool(not files and state['active_operation']=='delete' and state['delete_stage']=='confirm' and is_delete_confirmation(text) and pending_delete_ids(state))
    refs = obj(state['last_referenced_items'], [])
    ordinal_ref = bool(re.search(r'第[一二两三四五六七八九十\d]+[条个场项]',str(query)))
    scope_items = obj(state.get('active_list'),{}).get('items',[])
    memory_control = obj(state.get('memory_control'),{})
    pending = obj(state['pending_update'], {})
    pending = pending if isinstance(pending,dict) else {}
    edit = re.search(EDIT_PATTERN,text)
    requested_text_field = re.search(r'地点|位置|地址|标题|名称|名字',pending.get('instruction',''))
    attribute_reply = bool(requested_text_field and len(pending.get('candidates',[]))==1 and not edit and len(text)<=500 and not re.search(r'第[一二两三四五六七八九十\d]+[条个场项]|取消|算了|查|有什么|复盘|规划|新增|添加',text))
    referred = bool(re.search(r'第[一二两三四五六七八九十\d]+[条个场项]|刚才|那个|那条|上一个|这条|这一|那一|这场|那场|这门|那门|这次|那次|它|这个安排|该日程|该记录',text))
    new_request = bool(re.search(r'查|有什么|有哪些|什么安排|复盘|规划|记个|新增|添加|以后|记住',text))
    negated = bool(re.search(r'不要|不用|别改|别删|不想改|不想删',text))
    explicit_saved = bool(re.search(r'已保存|已有日程|日历上|日程里|原来的日程',text))
    persistent = bool(re.search(r'以后|今后|长期|默认|记住',text))
    if edit and not negated and not persistent and state['active_operation']=='plan' and state['pending_plan'] and not explicit_saved:
        intent='plan'
    elif edit and not negated and not persistent and state['active_operation']=='add' and state['draft'] and not explicit_saved:
        intent='add'
    elif edit and not negated and not persistent:
        intent = 'update'
    elif re.search(r'删掉|删除|删了',text) and not negated:
        intent = 'delete'
    elif attribute_reply and not negated:
        intent='update'
    elif pending and not new_request and not edit and not negated and (
            re.search(r'第.+[条个场项]|这条|那条|就它',text) or re.search(TIME_PATTERN,text)
            or any(r.get('title') and r['title'] in text for r in pending.get('candidates',[]))
            or date_span(text,base)[0]):
        intent = 'update'
    elif state['active_operation']=='query' and intent=='other' and date_span(text,base)[0] and re.search(r'^(?:那|那么|再查|看看)|呢[?？]?$',text):
        intent='query'
    elif state['active_operation']=='review' and state['review_stage']=='collect' and (
            re.search(r'生成报告|完成复盘|就这些|说完了',text) or (not new_request and intent=='other')):
        intent = 'review_feedback'
    elif state['active_operation']=='add' and state['draft'] and not new_request and intent=='other' and (
            re.search(TIME_PATTERN,text) or date_span(text,base)[0] or re.search(r'教.{0,6}室|教三|线上|地点|待定|楼|教室',text)):
        intent = 'add'
    elif state['active_operation']=='plan' and state['pending_plan'] and not new_request and intent=='other' and (
            edit or re.search(TIME_PATTERN,text) or date_span(text,base)[0] or re.search(r'别排|不要排|每天|最多|休息|只排',text)):
        intent = 'plan'
    if acknowledgement=='cancel':
        intent='cancel'
    elif files:
        intent='add'
    elif acknowledgement in ('generic','delete'):
        active=state['active_operation']
        intent=('delete' if confirmed_delete else
                'plan_confirm' if acknowledgement=='generic' and active=='plan' and state['plan_stage']=='awaiting' else
                'add' if acknowledgement=='generic' and active=='add' and state['draft'] else 'other')
    if negated and intent in ('update','delete'):
        intent='other'
    if intent=='plan_confirm' and not (state['active_operation']=='plan' and state['pending_plan']):intent='other'
    if intent=='review_feedback' and not (state['active_operation']=='review' and state['review_stage']=='collect'):intent='other'
    if intent=='confirm':intent='add' if state['active_operation']=='add' and state['draft'] else 'other'
    operation={'plan_confirm':'plan','review_feedback':'review'}.get(intent,intent)
    if operation!=state['active_operation'] or intent in ('cancel','review'):
        for key in STATE_KEYS:
            if key not in ('last_referenced_items','recent_dialogue','active_list','memory_context','memory_control','pending_memory_sync','memory_choices','focus_entry_id'):state[key]=''
    if intent=='cancel':state['memory_choices']=''
    state['active_operation']='' if intent in ('cancel','other','profile') else operation
    out={k:str(data.get(k) or '') for k in ['start_date','end_date','target','target_time','new_date','new_start','new_end','new_location','new_title']}
    reference_id=''
    message='请说明具体的活动和日期；如果有多条候选，请先选定一条。'
    if acknowledgement in ('generic','delete') and intent=='other':
        message='当前没有可执行的待确认操作。请先选定日程，再确认删除；不会根据聊天历史直接执行。'
    if confirmed_delete:
        reference_id=','.join(pending_delete_ids(state))
        out.update(target='',start_date='',end_date='',target_time='')
    if intent in ('query','review'):
        start,end=date_span(text,base)
        # Do not collapse explicitly requested ranges such as 11月1日到11月7日.
        if start and not re.search(r'(?:至|到).*(?:日|号|周|月)',text):out.update(start_date=start,end_date=end)
    if not confirmed_delete and state['delete_stage'] and intent=='delete' and not out['target']:
        candidates=obj(state['delete_candidates'],[])
        if state['pending_delete']:
            saved=state['pending_delete_filter']
            reference_id=saved[4:-1] if saved.startswith('in.(') and saved.endswith(')') else state['pending_delete']
        elif candidates:reference_id=','.join(r['id'] for r in candidates if r.get('id'))
    if intent in ('update','delete'):
        pool=pending.get('candidates',[]) if pending and intent=='update' else (obj(state['delete_candidates'],[]) if intent=='delete' and state['delete_stage']=='clarify' else scope_items if ordinal_ref and scope_items else refs)
        generic=out['target'] in ('记录','日程','安排','活动','这个','那个','这条','那条','上一个','位置','地点','地址','标题','名称','名字') or bool(re.fullmatch(r'(?:这|那|刚才|上面|上一|该|第).*(?:条|个|场|门|记录|日程|安排|活动|讲座|课程|会议)|它',out['target']))
        named=bool(out['target'] and out['target'] in text and not generic)
        if re.search(r'第[一二两三四五六七八九十\d]+[条个场项]',text):named=False
        prefix=text[:edit.start()] if edit else text
        use_context=not confirmed_delete and bool(pool) and not (named and not any(out['target'] in r.get('title','') for r in pool)) and (referred or memory_control.get('historical') or (intent=='update' and pending and not new_request) or
                     (intent=='update' and edit and not named and not date_span(prefix,base)[0]))
        if use_context:
            selection=prefix if edit else text
            if memory_control.get('historical'):
                selection=re.sub(r'(?:今天|昨天|前天|上周|本周|这周|上个月|\d{1,2}月\d{1,2}[日号]?).{0,8}?(?:对话|聊过的?|提到的?|那张表|列表|那张清单|清单)','',selection)
            # An unqualified time response after asking for the new time is a patch, not an old-time selector.
            if pending and len(pool)==1 and not edit and (attribute_reply or re.search(TIME_PATTERN,text)):selection=''
            chosen=select_context(selection,pool,base,out['target'] if named else '')
            if chosen and any(r.get('deleted') for r in chosen):
                chosen=[]
                intent='other'
                message='原列表中的这条记录已经删除，编号没有移动；请指定其他条目。'
            if chosen:
                reference_id=','.join(r['id'] for r in chosen)
                out.update(target='',start_date='',end_date='',target_time='')
            else:
                intent='other'
                if '已经删除' not in message:message='当前候选中没有匹配项，请按列表中的编号、标题或原开始时间选定一条。'
        else:
            chosen=[]
            if generic:out['target']=''
            if referred and not pool:
                intent='other';message='当前会话没有可定位的日程，请先查询或提供标题和日期。'
        if intent=='update':
            previous_patch=pending.get('patch',{}) if pending and not new_request else {}
            for k,v in previous_patch.items():
                if not out.get(k):out[k]=v
            instruction=text if edit or not pending or (len(chosen)==1 and re.search(TIME_PATTERN,text) and not re.search(r'那条|这条|原来|原本|那场',text)) else pending.get('instruction','')
            if attribute_reply:instruction=requested_text_field[0]+'改为'+text
            item=chosen[0] if len(chosen)==1 else None
            derived=edit_patch(instruction,item,base)
            if any(k in derived for k in ('new_location','new_title')) and not any(k in derived for k in ('new_date','new_start','new_end')):
                for key in ('new_date','new_start','new_end'):out[key]=previous_patch.get(key,'')
            for key in ('new_location','new_title'):
                if out[key] and out[key] not in text and not previous_patch.get(key):out[key]=''
            if pending and len(chosen)==1 and not edit and not attribute_reply and re.search(TIME_PATTERN,text):
                derived.update(edit_patch(text,item,base))
            edit_tail=re.split(EDIT_PATTERN,instruction,maxsplit=1)[-1]
            if derived and not re.search(r'明天|后天|今天|周|星期|月|日|号|20\d{2}[-/]',edit_tail):
                out['new_date']=previous_patch.get('new_date','')
            if 'new_date' in derived and not any(k in derived for k in ('new_start','new_end')):
                out['new_start']=previous_patch.get('new_start','')
                out['new_end']=previous_patch.get('new_end','')
            if 'new_start' in derived and 'new_end' not in derived:
                out['new_end']=''  # Preserve duration; do not accept a model's copied old end.
            out.update(derived)
            state['pending_update']=dumps({'candidates':context_items(chosen or pool),
                'patch':{k:out[k] for k in UPDATE_FIELDS},'instruction':instruction})
            if not reference_id and not out['target'] and not out['start_date']:
                intent='other';message='请先选定要修改的日程，或提供标题、日期；不会搜索全部记录作为修改候选。'
        elif intent=='delete' and not reference_id and not out['target'] and not out['start_date']:
            intent='other';message='请先选定要删除的日程，或提供标题、日期。'
    if intent=='review_feedback':
        state['review_feedback']=dumps((obj(state['review_feedback'],[])+[text])[-10:])
    profile_row=''
    if intent=='profile':
        patch=data.get('profile_patch') or {}
        if not re.search(r'以后|今后|长期|默认|记住|偏好|学期|节次|时区',text):
            intent='other';message='这条要求仅适用于本次安排；请和规划目标一起告诉我。'
        else:
            try:
                if not patch:raise ValueError('没有识别出可保存的偏好，请说明具体时段或配置。')
                profile=profile_checked({**base['profile'],**patch})
                profile_row=dumps({'user_id':user_id,'profile':profile})
                message='已保存长期配置：'+dumps(patch)
            except (ValueError,TypeError,KeyError) as exc:intent='other';message=str(exc)
    if intent=='other' and not state['pending_update']:state['active_operation']=''
    if memory_control.get('view'):
        intent='memory'
    elif memory_control.get('ignore_only'):
        intent='other';message='已忽略之前的上下文。历史记录仍保留，之后可按时间明确查看。'
    elif ordinal_ref and memory_control.get('ambiguous_scopes'):
        intent='other';message='找到多张历史列表，请先指定来源：\n'+str(memory_control['ambiguous_scopes'])
    state['turn_facts']=dumps({'intent':intent,'reference_id':reference_id,'changes':obj(state.get('pending_update'),{}).get('patch',{})})
    # Extra editable fields travel in pending_update, not additional top-level node outputs.
    out.pop('new_location',None)
    out.pop('new_title',None)
    out.update(delete_confirmed='true' if confirmed_delete and intent=='delete' else 'false',intent=intent,reference_id=reference_id,profile_row=profile_row,message=message,
               state_json=dumps(state),text_history=dumps(obj(state['recent_dialogue'],[])[-3:]),
               model_plan=dumps(model_data(obj(state['pending_plan'],{}))))
    out.update(state)
    return out


def request_context(intent, start_date, end_date, target, reference_id, state_json, base_json, target_time='', delete_confirmed='false'):
    base = obj(base_json, {})
    state = obj(state_json, {})
    today = date.fromisoformat(base['today'])
    start, end = start_date, end_date
    if intent=='delete' and delete_confirmed=='true':
        reference_id=','.join(pending_delete_ids(state))
        start=end=target=target_time=''
        if not reference_id:
            return {'schedule_params':'id=is.null&limit=0','task_params':'limit=0','start_date':'','end_date':'','period':'day','range_json':'{}'}
    if intent == 'review_feedback':
        stored = obj(state.get('review_range'), {})
        start, end = stored.get('start'), stored.get('end')
    if intent == 'plan_confirm':
        plan = obj(state.get('pending_plan'), {})
        days = [b['event_date'] for b in plan.get('blocks', [])]
        start, end = (min(days), max(days)) if days else (base['today'], base['today'])
    if not start:
        start = base['today'] if intent in ('query', 'review', 'plan', 'plan_confirm') else ''
    if not end:
        end = (today + timedelta(days=7)).isoformat() if intent == 'plan' else start
    if start:
        start, end = iso(start), iso(end or start)
        if start > end:
            start, end = end, start
    filters = []
    if reference_id:
        import uuid
        ids = [str(uuid.UUID(x)) for x in reference_id.split(',')]
        filters.append('id=in.(' + ','.join(ids) + ')')
    if start:
        filters += ['event_date=gte.' + start, 'event_date=lte.' + end]
    if target and intent in ('update', 'delete'):
        # Encode a PostgREST quoted literal, escaping its grammar and LIKE wildcards.
        literal = target.replace('\\', '\\\\').replace('"', '\\"').replace('%', '\\%').replace('_', '\\_').replace('*', '\\*')
        filters.append('title=like.' + quote('"*' + literal + '*"', safe=''))
    if target_time and intent in ('update','delete'):
        hm(target_time)
        filters.append('start_time=eq.' + target_time)
    if intent in ('update','delete') and not filters:
        filters.append('id=is.null')
    schedule_params = '&'.join(filters + ['order=event_date.asc,start_time.asc', 'limit=1000'])
    task_params = ('task_date=gte.' + start + '&task_date=lte.' + end + '&' if start else '') + 'order=task_date.asc&limit=1000'
    if intent not in ('query', 'review', 'review_feedback'):
        task_params = 'limit=0'
    period = 'day'
    if start:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        period = 'day' if days <= 3 else 'week' if days <= 10 else 'month'
    return {'schedule_params': schedule_params, 'task_params': task_params,
            'start_date': start, 'end_date': end, 'period': period,
            'range_json': dumps({'start': start, 'end': end, 'period': period})}


def operation_context(schedule_body, task_body, start_date, end_date, base_json):
    schedules, tasks = rows(schedule_body), rows(task_body)
    allowed = ('id', 'title', 'event_date', 'start_time', 'end_time', 'location', 'category', 'status', 'manual_override')
    clean = [{k: r.get(k) for k in allowed} for r in schedules]
    task_clean = [{k: r.get(k) for k in ('id', 'title', 'task_date', 'done')} for r in tasks]
    summary = {'range': [start_date, end_date], 'schedule_count': len(clean), 'task_count': len(tasks)}
    daily = {}
    for r in clean:
        entry = daily.setdefault(r['event_date'], {'count': 0, 'minutes': 0})
        entry['count'] += 1
        entry['minutes'] += max(0, hm(r['end_time']) - hm(r['start_time']))
    summary['days'] = dict(sorted(daily.items())[:60])
    summary['days_truncated'] = len(daily) > 60
    lines = ['📆 **%s 至 %s**' % (start_date or '不限', end_date or '不限')]
    if clean:
        lines.append('### 📅 已安排的日程（%d 项）' % len(clean))
        for i,r in enumerate(clean):
            lines.append('%d. **%s %s–%s %s**\n\n   📍 %s' % (
                i+1,r['event_date'],str(r['start_time'])[:5],str(r['end_time'])[:5],r['title'],r.get('location') or '地点待定'))
    else:
        lines.append('📅 这段时间暂无日程记录。')
    if task_clean:
        lines.append('### 📝 待办任务（%d 项）' % len(task_clean))
        lines += ['- **%s** · %s · %s' % (r['title'],r['task_date'],'✅ 已完成' if r['done'] else '待完成') for r in task_clean]
    else:
        lines.append('📝 这段时间任务栏里暂无待办记录。')
    if not clean and not tasks:
        lines.append('该范围没有日程或任务记录。')
    if len('\n'.join(lines)) > 40000:
        raise ValueError('完整清单过长，请缩小日期范围。')
    refs = context_items(clean)
    return {'body': dumps(clean), 'tasks': dumps(task_clean), 'summary': dumps(summary),
            'listing': '\n\n'.join(lines), 'references': dumps(refs),
            'operation_context': dumps({'summary': summary, 'profile': obj(base_json)['profile']})}


def prepare_add(raw, draft, base_json):
    base = obj(base_json)
    data = model_json(raw)
    items = data.get('items') or []
    if not isinstance(items, list):
        items = []
    old = obj(draft, {})
    old_items = old.get('items', []) if isinstance(old, dict) else []
    if not old_items and isinstance(old, dict) and old.get('title'):
        old_items = [old]
    # Multi-item drafts use stable draft_id; an unqualified edit to several drafts is ambiguous.
    if old_items:
        if len(old_items) == 1 and len(items) <= 1:
            delta = items[0] if items else {}
            items = [{**old_items[0], **{k: v for k, v in delta.items() if v is not None}}]
        else:
            by_id = {str(x.get('draft_id') if x.get('draft_id') is not None else i): dict(x, draft_id=str(x.get('draft_id') if x.get('draft_id') is not None else i)) for i, x in enumerate(old_items)}
            for item in items:
                key = str(item.get('draft_id', ''))
                if key not in by_id:
                    return {'ok': 'false', 'error': '有多条草稿，请按 draft_id 指定要补充哪一条。', 'draft': dumps({'items': old_items}), 'prepared': '{}', 'schedule_params': 'limit=0'}
                by_id[key].update({k: v for k, v in item.items() if v is not None})
            items = list(by_id.values())
    errors, normalized, days = [], [], []
    for index, item in enumerate(items):
        r = dict(item, draft_id=str(item.get('draft_id') if item.get('draft_id') is not None else index))
        if r.get('week') is not None:
            semester = base['profile'].get('semester_start')
            if not semester:
                errors.append('请先设置学期第一周周一的日期。')
            else:
                week, weekday = int(r['week']), int(r.get('weekday') or 0)
                if not 1 <= week <= 60 or not 1 <= weekday <= 7:
                    errors.append('请明确学期周数和星期几。')
                else:
                    r['date'] = (date.fromisoformat(semester) + timedelta(weeks=week-1, days=weekday-1)).isoformat()
        if r.get('lesson_start') is not None:
            lessons = base['profile'].get('lessons', {})
            first = lessons.get(str(r['lesson_start']))
            last = lessons.get(str(r.get('lesson_end') or r['lesson_start']))
            if not first or not last:
                errors.append('请先设置所涉及的课程节次起止时间。')
            else:
                r['start_time'], r['end_time'] = first[0], last[1]
        if not str(r.get('title') or '').strip():
            errors.append('请补充第 %d 条的标题。' % (index + 1))
        if r.get('ambiguous'):
            errors.append(str(r['ambiguous']))
        if r.get('kind') == 'event' and not r.get('start_time'):
            errors.append('请补充「%s」的开始时间。' % r.get('title', '活动'))
        if r.get('start_time') and not r.get('date'):
            errors.append('请补充「%s」的日期。' % r.get('title', '活动'))
        try:
            if r.get('date'):
                r['date'] = iso(r['date'])
                days.append(r['date'])
            if r.get('start_time'):
                start = hm(r['start_time'])
                if not r.get('end_time'):
                    if start + 60 >= 1440:
                        raise ValueError('默认一小时会跨日，请明确结束时间。')
                    r['end_time'] = clock(start + 60)
                    r['estimated_end'] = True
                if hm(r['end_time']) <= start:
                    raise ValueError('结束时间必须晚于开始时间。')
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
        normalized.append(r)
    if not items:
        errors.append('没识别到安排，请补充标题、日期或图片内容。')
    return {'ok': 'false' if errors else 'true', 'error': '\n'.join(dict.fromkeys(errors)),
            'prepared': dumps({'items': normalized}), 'draft': dumps({'items': normalized}),
            'schedule_params': ('event_date=in.(' + ','.join(sorted(set(days))) + ')&' if days else '') + 'order=event_date.asc,start_time.asc&limit=1000'}


def validate_add(prepared, body, user_id, base_json):
    existing = rows(body)
    events, tasks, summary = [], [], []
    for r in obj(prepared)['items']:
        if r.get('start_time'):
            event = {'user_id': user_id, 'title': r['title'], 'event_date': r['date'],
                     'start_time': r['start_time'], 'end_time': r['end_time'],
                     'location': r.get('location') or '', 'category': r.get('category') or '其他',
                     'note': '结束时间按默认 60 分钟估算' if r.get('estimated_end') else '', 'source': 'agent', 'status': 'planned'}
            hits = [x['title'] for x in existing + events if x['event_date'] == event['event_date'] and hm(event['start_time']) < hm(x['end_time']) and hm(x['start_time']) < hm(event['end_time'])]
            events.append(event)
            summary.append('%s %s %s–%s%s%s' % (r['title'], r['date'], r['start_time'], r['end_time'], '（结束时间按 60 分钟估算）' if r.get('estimated_end') else '', '；与' + '、'.join(hits) + '冲突' if hits else ''))
        else:
            tasks.append({'user_id': user_id, 'title': r['title'], 'task_date': r.get('date') or obj(base_json)['today'], 'done': False})
            summary.append(r['title'] + '｜已放任务栏')
    return {'events_json': dumps(events), 'tasks_json': dumps(tasks), 'summary': '\n'.join(summary)}


def subtract_slots(allowed, busy):
    merged = []
    for a, b in sorted(busy):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # Union allowed windows too, so overlapping preferences never duplicate availability.
    union = []
    for a, b in sorted(allowed):
        if union and a <= union[-1][1]:
            union[-1][1] = max(union[-1][1], b)
        else:
            union.append([a, b])
    out = []
    for a, b in union:
        cursor = a
        for s, e in merged:
            if e <= cursor or s >= b:
                continue
            if s > cursor:
                out.append((cursor, min(s, b)))
            cursor = max(cursor, e)
        if cursor < b:
            out.append((cursor, b))
    return out


def plan_slots(raw, body, base_json, start_date, end_date, user_id, pending_plan=''):
    base, payload = obj(base_json), model_json(raw)
    profile = base['profile']
    previous = obj(pending_plan, {})
    constraints = {**previous.get('constraints', {}), **(payload.get('constraints') or {})} if isinstance(payload.get('constraints') or {}, dict) else payload['constraints']
    if not isinstance(constraints, dict):
        raise ValueError('规划限制须为结构化对象。')
    if payload.get('unsupported_constraints'):
        raise ValueError('暂不支持这些限制，请具体化为日期或时段：' + str(payload['unsupported_constraints']))
    unknown = set(constraints) - {'start_date','end_date','allowed_windows','blocked_windows','blocked_dates','max_blocks_per_day'}
    if unknown:
        raise ValueError('不支持的限制字段：' + ','.join(sorted(unknown)))
    blocked_dates = constraints.get('blocked_dates', {})
    if not isinstance(blocked_dates, dict):
        raise ValueError('按日期禁用时段格式无效。')
    for day, pairs in blocked_dates.items():
        iso(day)
        windows(pairs)
    # Temporary allowed windows narrow, never widen, persistent availability.
    allowed = [(hm(a), hm(b)) for a, b in profile['allowed_windows']]
    if constraints.get('allowed_windows'):
        temporary = [(hm(a), hm(b)) for a, b in windows(constraints['allowed_windows'])]
        allowed = [(max(a, c), min(b, d)) for a, b in allowed for c, d in temporary if max(a, c) < min(b, d)]
    blocked = [(hm(a), hm(b)) for a, b in profile['blocked_windows'] + windows(constraints.get('blocked_windows', []))]
    maximum = min(profile['max_blocks_per_day'], int(constraints.get('max_blocks_per_day') or profile['max_blocks_per_day']))
    if maximum < 1:
        raise ValueError('每天至少允许一个时间块。')
    now = datetime.fromisoformat(base['now'])
    lo = max(date.fromisoformat(start_date), now.date())
    hi = date.fromisoformat(end_date)
    if constraints.get('start_date'):
        lo = max(lo, date.fromisoformat(constraints['start_date']))
    if constraints.get('end_date'):
        hi = min(hi, date.fromisoformat(constraints['end_date']))
    busy, counts = {}, {}
    for row in rows(body):
        busy.setdefault(row['event_date'], []).append((hm(row['start_time']), hm(row['end_time'])))
    previous = obj(pending_plan, {})
    goals = payload.get('goals') or previous.get('goals') or []
    if len(goals) > 5:
        raise ValueError('一次最多规划 5 个目标。')
    def gaps(day, study=False):
        exclusions = busy.get(day.isoformat(), []) + blocked + [(hm(a), hm(b)) for a, b in blocked_dates.get(day.isoformat(), [])]
        if study:
            exclusions = exclusions + [(hm(a), hm(b)) for a, b in profile['study_blocked_windows']]
        if day == now.date():
            exclusions = exclusions + [(0, now.hour * 60 + now.minute + (1 if now.second or now.microsecond else 0))]
        return subtract_slots(allowed, exclusions)
    free_lines, day = [], lo
    while day <= hi:
        slots = [(a, b) for a, b in gaps(day) if b - a >= 60]
        if slots:
            free_lines.append(day.isoformat() + ' ' + '、'.join(clock(a) + '–' + clock(b) for a, b in slots))
        day += timedelta(days=1)
    blocks, tasks, unallocated, assumptions = [], [], [], []
    def order(g):
        return (g.get('deadline') or '9999-12-31', {'高': 0, '中': 1, '低': 2}.get(g.get('priority'), 1))
    for goal in sorted(goals, key=order):
        title = str(goal.get('title') or '').strip()
        if not title:
            continue
        duration = goal.get('duration_min')
        if not duration:
            duration = 60
            assumptions.append(title + '：时长按 60 分钟估算')
        remaining = int(duration)
        if remaining <= 0 or remaining > 10080:
            raise ValueError('目标时长须在 1–10080 分钟内。')
        last = min(hi, date.fromisoformat(goal['deadline'])) if goal.get('deadline') else hi
        day, first = lo, None
        while day <= last and remaining >= 60:
            key = day.isoformat()
            while counts.get(key, 0) < maximum and remaining >= 60:
                slots = [(a, b) for a, b in gaps(day, goal.get('category') == '学习') if b-a >= 60]
                if not slots:
                    break
                a, b = slots[0]
                take = min(120, b-a, remaining)
                block = {'user_id': user_id, 'title': title, 'event_date': key, 'start_time': clock(a), 'end_time': clock(a+take), 'location': '', 'category': '个人事项', 'note': '来自智能体规划', 'source': 'agent', 'status': 'planned'}
                blocks.append(block)
                busy.setdefault(key, []).append((a, a+take))
                counts[key] = counts.get(key, 0) + 1
                remaining -= take
                first = first or key
            day += timedelta(days=1)
        tasks.append({'user_id': user_id, 'title': title, 'done': False, 'task_date': goal.get('deadline') or first or base['today']})
        if remaining:
            unallocated.append({'title': title, 'remaining_min': remaining})
    lines = ['%s %s–%s %s' % (b['event_date'], b['start_time'], b['end_time'], b['title']) for b in blocks]
    plan = {'blocks': blocks, 'tasks': tasks, 'goals': goals, 'constraints': constraints, 'created_at': base['now']}
    return {'text': '\n'.join(lines) or '没有排出时间块。', 'free_text': '\n'.join(free_lines) or '没有符合限制的整块空档。',
            'plan_json': dumps(plan), 'model_plan': dumps(model_data(plan)), 'unallocated': dumps(unallocated), 'assumptions': dumps(assumptions),
            'applied_constraints': dumps({'profile': profile, 'temporary': constraints}),
            'count': len(blocks), 'stage': 'awaiting' if blocks or tasks else ''}


def check_plan(plan_json, body, base_json, user_id):
    plan, existing = obj(plan_json, {}), rows(body)
    now = datetime.fromisoformat(obj(base_json)['now'])
    blocks, tasks = plan.get('blocks', []), plan.get('tasks', [])
    reasons = []
    for b in blocks:
        if b.get('user_id') != user_id:
            reasons.append('用户不一致')
        when = datetime.fromisoformat(b['event_date'] + 'T' + b['start_time']).replace(tzinfo=now.tzinfo)
        if when < now:
            reasons.append('部分时间块已过期')
        if any(x['event_date'] == b['event_date'] and hm(b['start_time']) < hm(x['end_time']) and hm(x['start_time']) < hm(b['end_time']) for x in existing):
            reasons.append('出现新的时间冲突')
        existing.append(b)
    if not blocks and not tasks:
        reasons.append('没有待确认规划')
    if any(t.get('user_id') != user_id for t in tasks):
        reasons.append('用户不一致')
    return {'ok': 'false' if reasons else 'true', 'error': '；'.join(dict.fromkeys(reasons)) + ('，请重新规划并确认。' if reasons else ''),
            'sched_body': dumps(blocks), 'task_body': dumps(tasks), 'count': len(blocks)}


def review_stats(schedule_body, task_body, reviews_body, start_date, end_date, period, feedback):
    schedules, tasks = rows(schedule_body), rows(task_body)
    history = rows(reviews_body, allow_limit=True)[:3]
    manual = [r for r in schedules if r.get('status') == 'done' and r.get('manual_override') is True]
    auto = [r for r in schedules if r.get('status') == 'done' and r.get('manual_override') is not True]
    unfinished = [r for r in schedules if r.get('status') != 'done']
    task_done = [t for t in tasks if t.get('done') is True]
    categories = {}
    for r in schedules:
        cat = r.get('category') or '其他'
        categories[cat] = categories.get(cat, 0) + hm(r['end_time']) - hm(r['start_time'])
    stats = {'schedule_total': len(schedules), 'done_manual': len(manual), 'done_auto': len(auto),
             'undone': len(unfinished), 'task_total': len(tasks), 'task_done': len(task_done), 'categories': categories}
    lines = []
    for label, group in [('手动确认完成', manual), ('按时间默认完成（未人工核实）', auto), ('未完成/待确认', unfinished)]:
        lines.append(label + '：' + ('；'.join(r['event_date'] + ' ' + r['title'] for r in group) or '无'))
    lines += ['任务 %s %s（%s）' % (t['task_date'], t['title'], '已完成' if t['done'] else '未完成') for t in tasks]
    history_context = [{'range_start': h.get('range_start'), 'range_end': h.get('range_end'),
                        'stats': h.get('stats') or {}, 'advice_summary': h.get('advice_summary') or '旧报告无结构化建议摘要'} for h in history]
    # Bound model details without losing totals or the complete code-rendered list.
    if len('\n'.join(lines)) > 60000:
        raise ValueError('复盘清单过长，请缩小日期范围。')
    return {'summary': dumps(stats), 'items': '\n'.join(lines)[:12000], 'listing': '\n'.join(lines),
            'history': dumps(history_context), 'feedback': feedback or '[]', 'stats_json': dumps(stats),
            'ask_text': '%s 至 %s\n%s\n\n未完成的原因是什么？有什么经验或下一步想法？可以分几次补充，最后说“生成报告”。' % (start_date, end_date, '\n'.join(lines))}


def report_row(raw, stats_json, start_date, end_date, period, user_id):
    data = model_json(raw)
    report = data.get('report')
    advice = data.get('advice_summary')
    if not isinstance(report, str) or not report.strip() or not isinstance(advice, str) or len(advice) > 1200:
        raise ValueError('复盘报告格式错误，未保存。')
    return {'report': report, 'row_json': dumps({'user_id': user_id, 'period': period, 'range_start': start_date,
            'range_end': end_date, 'report': report, 'advice_summary': advice, 'stats': obj(stats_json)})}


def locate_event(body, target, start_date, end_date, reference_id, pending_json='', base_json=''):
    records = rows(body)
    ids = set(reference_id.split(',')) if reference_id else set()
    matches = [r for r in records if (not ids or r.get('id') in ids)
               and (not target or target in r['title'])
               and (not start_date or start_date <= r['event_date'] <= (end_date or start_date))]
    pending = obj(pending_json, {})
    pending['candidates'] = context_items(matches)
    patch = dict(pending.get('patch', {}))
    if len(matches)==1 and base_json:
        patch.update(edit_patch(pending.get('instruction',''),matches[0],obj(base_json)))
    pending['patch']=patch
    has_patch = any(patch.get(k) for k in UPDATE_FIELDS)
    unique = len(matches)==1 and has_patch
    lines = ['%d. %s %s %s–%s' % (i+1,r['event_date'],r['title'],str(r['start_time'])[:5],str(r['end_time'])[:5]) for i,r in enumerate(matches[:20])]
    if len(matches)==1 and not has_patch:
        instruction=pending.get('instruction','')
        question='想把地点改到哪里？' if re.search(r'地点|位置|地址',instruction) else '想把标题改成什么？' if re.search(r'标题|名称|名字',instruction) else '请补充新的日期、开始时间（上午还是下午）、地点或标题。'
        text = '已选中日程，'+question+'\n\n'+'\n'.join(lines)
    else:
        text = ('找到：\n' if unique else '请先选定一条日程，原修改要求已保留，可回复“第一条”或标题：\n')+('\n'.join(lines) or '没有匹配记录，请提供标题和日期。')
    return {'found':'true' if unique else 'false', 'id':matches[0]['id'] if unique else '',
            'item_json':dumps(matches[0]) if unique else '{}', 'references':dumps(context_items(matches)),
            'pending_update':dumps(pending), 'text':text,
            **{k:str(patch.get(k) or '') for k in UPDATE_FIELDS}}


def deletion(body, query, pending, stage, pending_filter='', candidates='[]'):
    records = rows(body)
    state={'pending_delete':pending,'delete_stage':stage,'delete_candidates':candidates}
    expected_ids=pending_delete_ids(state)
    confirmed=is_delete_confirmation(query) and bool(expected_ids)
    def fingerprint(r):
        return (r.get('title'),r.get('event_date'),str(r.get('start_time'))[:5],str(r.get('end_time'))[:5],r.get('location'),r.get('category'))
    changed=''
    if confirmed:
        previous=obj(candidates,[])
        records=[r for r in records if r.get('id') in expected_ids]
        if not records:
            return {'execute':'false','id':'','id_filter':'','stage':'','candidates':'[]',
                    'text':'待删除的记录已不存在，本轮没有再次删除任何记录。原列表编号保持不变。'}
        expected={r['id']:fingerprint(r) for r in previous}
        actual={r['id']:fingerprint(r) for r in records}
        if expected==actual:
            # Build from validated IDs, never an old or model-supplied SQL filter.
            return {'execute':'true','id':pending,'id_filter':'in.('+','.join(expected_ids)+')',
                    'stage':'','candidates':'[]','text':''}
        changed='待删除记录发生了变化，请按最新信息重新确认。\n\n'
    groups={fingerprint(r) for r in records}
    if records and len(groups)==1:
        row=records[0];ids=[x['id'] for x in records]
        return {'execute':'false','id':row['id'],'id_filter':'in.('+','.join(ids)+')',
                'stage':'confirm','candidates':dumps(records),
                'text':changed+'删除 %s %s–%s %s？\n📍 %s\n\n%s回“确认”删除，回“取消”退出。' % (
                    row['event_date'],str(row['start_time'])[:5],str(row['end_time'])[:5],row['title'],row.get('location') or '地点待定',
                    ('共 %d 条完全相同的记录，将一起删除。' % len(ids)) if len(ids)>1 else '')}
    return {'execute':'false','id':'','id_filter':'','stage':'clarify' if records else '',
            'candidates':dumps(records[:20]),'text':changed+'请先选定一条要删除的日程，明确标题、日期和时间（显示前20条）：\n'+
            ('\n'.join('%s %s %s' % (r['event_date'],str(r['start_time'])[:5],r['title']) for r in records[:20]) or '没有匹配记录。')}


def patch_event(item_json, new_date, new_start, new_end, new_location='', new_title=''):
    item = obj(item_json)
    patch = {}
    if new_date:
        patch['event_date'] = iso(new_date)
    start = hm(new_start) if new_start else hm(item['start_time'])
    end = hm(new_end) if new_end else start + hm(item['end_time']) - hm(item['start_time'])
    if not 0 <= start < end < 1440:
        raise ValueError('修改后的起止时间无效，不能跨日。')
    if new_start or new_end:
        patch.update(start_time=clock(start), end_time=clock(end))
    for key,value in (('location',new_location),('title',new_title)):
        if value:
            if not isinstance(value,str) or not value.strip() or len(value)>500:
                raise ValueError('地点或标题须为 1–500 字符。')
            patch[key]=value.strip()
    if not patch:
        raise ValueError('请说明修改后的日期、时间、地点或标题。')
    updated={**item,**patch}
    changes=[]
    for key,label in (('location','📍 地点'),('title','📝 标题')):
        if key in patch:changes.append('%s：%s → **%s**' % (label,item.get(key) or '未填写',patch[key]))
    receipt='日程已修改 ✨\n\n**%s**\n\n📅 %s · %s–%s' % (updated['title'],updated['event_date'],str(updated['start_time'])[:5],str(updated['end_time'])[:5])
    receipt+='\n\n'+('\n\n'.join(changes) if changes else '📍 '+(updated.get('location') or '地点待定'))
    return {'patch': dumps(patch),'summary':receipt}


def task_row(raw, user_id, base_json):
    data = model_json(raw)
    if not str(data.get('title') or '').strip():
        raise ValueError('任务标题为空，未写入。')
    return {'row_json': dumps({'user_id': user_id, 'title': data['title'], 'task_date': iso(data['date']) if data.get('date') else obj(base_json)['today'], 'done': False})}


def write_result(body, status, kind='write'):
    try:
        result = rows(body, status, allow_limit=True)
        if kind != 'batch' and not result:
            raise ValueError('没有操作到任何记录，请刷新后重试。')
        refs = context_items([r for r in result if r.get('event_date')])
        return {'ok': 'true', 'error': '', 'count': len(result), 'references': dumps(refs[:20])}
    except (ValueError, TypeError) as exc:
        return {'ok': 'false', 'error': str(exc), 'count': 0, 'references': '[]'}


def capture_state(**state):
    return {'state_json': dumps(state), 'model_state': dumps(model_data({k:(scope_model(v) if k=='active_list' else v) for k,v in state.items() if k not in ('recent_dialogue','memory_context','pending_memory_sync')}))}


def plan_request(raw, base_json, pending_plan='', requested_start='', requested_end=''):
    data = model_json(raw)
    previous = obj(pending_plan, {})
    limits = {**previous.get('constraints', {}), **(data.get('constraints') or {})} if isinstance(data.get('constraints') or {}, dict) else data['constraints']
    if not isinstance(limits, dict):
        raise ValueError('限制格式无效。')
    today = date.fromisoformat(obj(base_json)['today'])
    start = iso(limits.get('start_date') or requested_start or today.isoformat())
    end = iso(limits.get('end_date') or requested_end or (date.fromisoformat(start) + timedelta(days=7)).isoformat())
    if end < start or (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
        raise ValueError('规划范围须为正序，且不超过一年。')
    return {'start_date': start, 'end_date': end,
            'schedule_params': 'event_date=gte.' + start + '&event_date=lte.' + end + '&order=event_date.asc,start_time.asc&limit=1000'}


def batch_body(events_json, tasks_json, reject_conflicts=False):
    return {'body': dumps({'schedule_rows': obj(events_json, []), 'task_rows': obj(tasks_json, []), 'reject_conflicts': reject_conflicts})}


def review_decision(query, intent):
    return {'generate': 'true' if intent == 'review_feedback' and re.search(r'生成报告|完成复盘|就这些|说完了', query) else 'false'}


def finish_turn(text, query, history):
    previous = obj(history, [])
    previous = previous if isinstance(previous, list) else []
    turn = {'user': str(query)[:1000], 'assistant': str(text)[:1500]}
    return {'text': text, 'history': dumps((previous + [turn])[-6:])}


def prepare_polish(text, profile_body):
    """Bound style input; canonical reply remains outside the model's control."""
    profiles = obj(profile_body, [])
    style = DEFAULT_PROFILE['reply_style']
    if isinstance(profiles, list) and profiles:
        profile = profiles[0].get('profile') or {}
        if isinstance(profile, dict) and isinstance(profile.get('reply_style'), str):
            style = profile['reply_style'][:200]
    return {'context': dumps({'reply_style': style, 'reply_excerpt': text[:6000],
                              'excerpt_truncated': len(text)>6000})}


def compose_polish(text, raw):
    """Keep canonical facts; validate each framing part without banning ordinary words."""
    data = model_json(raw)
    opening, closing = data.get('opening', ''), data.get('closing', '')
    if not isinstance(opening,str) or not isinstance(closing,str):
        return {'text':text}
    numbers=set(re.findall(r'\d+(?::\d+)?',text))
    first_line=text.split('\n',1)[0]
    def allowed(part):
        if len(part)>80:
            return False
        if re.search(r'没有待处理|没有待确认|无待处理|无待确认|没有匹配|没找到|找不到',part) and not re.search(r'没有待处理|没有待确认|无待处理|无待确认|没有匹配|没找到|找不到',text):
            return False
        if re.search(r'空闲|有空|空着|自由|充裕|冲突|没安排|没有安排|全部完成',part):
            return False
        for number in re.findall(r'\d+(?::\d+)?',part):
            if number not in numbers:return False
        for number,unit in re.findall(r'([零一二两三四五六七八九十]+)(条|个|场|项|小时|分钟|点|时|分|月|日|号|年)',part):
            if unit in ('点','时','分','月','日','号','年') or str(chinese_number(number)) not in numbers:
                return False
        if re.search(r'周[一二三四五六日天]|星期[一二三四五六日天]',part):
            return False
        for claim in re.finditer(r'(?:已(?:经)?(?:帮你)?|操作)(保存|删除|修改|完成|取消|写入|更新|安排)',part):
            action=claim[1]
            if action=='更新':action='修改'
            if not re.search(r'已[^\n]{0,5}'+action,first_line):return False
        if re.search(r'保存成功|删除成功|修改成功|安排好了|搞定了',part) and not re.search(r'已保存|已删除|已修改|规划已保存',first_line):
            return False
        return True
    # One rejected closing no longer discards an otherwise useful opening.
    opening=opening.strip() if allowed(opening) else ''
    closing=closing.strip() if allowed(closing) else ''
    return {'text':'\n\n'.join(part for part in (opening,text,closing) if part)}


def scope_model(value):
    scope=obj(value,{})
    return {'scope_id':scope.get('scope_id'), 'label':scope.get('label'), 'count':len(scope.get('items',[])), 'items':scope.get('items',[])[:20]}


def rpc_bundle(body, status=200):
    result=rows(body,status,allow_limit=True)
    if len(result)!=1 or not isinstance(result[0].get('bundle'),dict):
        raise ValueError('上下文文档响应格式错误。')
    return result[0]['bundle']


def memory_begin_body(user_id, conversation_id, turn_key, query, pending_sync=''):
    return {'body':dumps({'p_user_id':user_id,'p_conversation_id':conversation_id,
        'p_turn_key':turn_key,'p_user_text':query,'p_ignore':bool(re.search(r'忽略之前|忽略以前|不用之前的上下文',query)),
        'p_pending_sync':obj(pending_sync)})}


def memory_load(body):
    bundle=rpc_bundle(body)
    state={k:'' for k in STATE_KEYS}
    saved=bundle.get('state') or {}
    for k in state:
        value=saved.get(k,'')
        state[k]=value if isinstance(value,str) else dumps(value)
    original=bundle.get('active_scope') or {}
    pointer=obj(state['active_list'],{})
    if original and original.get('scope_id')==pointer.get('scope_id'):
        original=dict(original)
        updates=pointer.get('updates',{})
        original['items']=[{**r,**updates.get(r['id'],{})} for r in original.get('items',[])]
        original['updates']=updates
        state['active_list']=dumps(original)
    elif pointer.get('scope_id'):
        state['active_list']=''
    state['pending_memory_sync']=''
    state['memory_context']=''
    state['memory_control']=''
    state['turn_facts']='{}'
    index=[{'id':r['id'],'created_at':r.get('created_at'),'summary':str(r.get('summary') or '')[:160],
            'topics':[str(t)[:32] for t in (r.get('topics') or [])[:5]],'has_list':bool(r.get('has_list'))}
           for r in bundle.get('index',[])[:100]]
    return {**state,'entry_id':str(bundle['entry_id']),'created_at':bundle['created_at'],
        'index':dumps(index),'state_json':dumps(state),
        'duplicate':'true' if bundle.get('duplicate_complete') else 'false','saved_reply':bundle.get('saved_reply') or ''}


def recall_request(raw, query, base_json, state_json, entry_id, user_id, conversation_id, index_json):
    import uuid
    data=model_json(raw)
    base=obj(base_json)
    state=obj(state_json,{})
    index=obj(index_json,[])
    allowed={r['id'] for r in index}
    selected=[x for x in data.get('entry_ids',[]) if isinstance(x,str) and x in allowed][:8] if isinstance(data.get('entry_ids',[]),list) else []
    topics=[str(t)[:80] for t in data.get('topics',[]) if isinstance(t,str) and t.strip()][:10] if isinstance(data.get('topics',[]),list) else []
    historical=bool(re.search(r'昨天.*(?:对话|聊|列表|表)|上次|以前|之前.*(?:列表|表|对话)|历史|上下文|原文|记录文档|刚才那张表',query))
    if re.search(r'忽略之前|忽略以前|不用之前的上下文',query):historical=False
    view=bool(re.search(r'(?:查看|看看|显示|读).*(?:上下文|对话|原文|记忆|文档)',query))
    explicit_date=historical and bool(re.search(r'今天|昨天|前天|上周|本周|这周|上个月|下个月|\d{1,2}月|20\d{2}-',query))
    choices=obj(state.get('memory_choices'),{})
    scope_choice=re.search(r'第([一二两三四五六七八九十\d]+)张',query)
    selected_scope=None
    if scope_choice and choices.get('scopes'):
        number=chinese_number(scope_choice[1])-1
        if 0<=number<len(choices['scopes']):
            selected_scope=choices['scopes'][number]['scope_id']
            historical=True;explicit_date=False;selected=[selected_scope]
    start=end=None
    if explicit_date:
        a,b=date_span(query,base)
        if not a and '前天' in query:a=b=(date.fromisoformat(base['today'])-timedelta(days=2)).isoformat()
        if a:
            tz=ZoneInfo(base['profile']['timezone'])
            start=datetime.combine(date.fromisoformat(a),datetime.min.time(),tzinfo=tz).isoformat()
            end=datetime.combine(date.fromisoformat(b)+timedelta(days=1),datetime.min.time(),tzinfo=tz).isoformat()
            selected=[]
    scope=obj(state.get('active_list'),{})
    scope_id=None
    if not historical and re.search(r'第.+[条个场项]|这条|那条|它|刚才',query):
        scope_id=scope.get('scope_id')
    proposed=data.get('scope_id')
    if historical and isinstance(proposed,str) and proposed in allowed and not explicit_date:scope_id=proposed
    if scope_id:
        try:scope_id=str(uuid.UUID(scope_id))
        except ValueError:scope_id=None
    control={'historical':historical,'view':view,'view_raw':'原文' in query,
        'ignore_only':bool(re.fullmatch(r'(?:请)?(?:忽略之前|忽略以前|不用之前的上下文)[的上下文。！!\s]*',query))}
    if selected_scope:
        scope_id=str(uuid.UUID(selected_scope))
        if re.fullmatch(r'第[一二两三四五六七八九十\d]+张[。！!\s]*',query):control['replay_query']=choices.get('instruction',query)
        control['scope_adopted']=True
    if scope_id in allowed and scope_id not in selected:selected=(selected+[scope_id])[:8]
    return {'body':dumps({'p_user_id':user_id,'p_conversation_id':conversation_id,'p_entry_id':entry_id,
         'p_entry_ids':selected,'p_date_start':start,'p_date_end':end,'p_topics':topics,
         'p_explicit_history':historical,'p_scope_id':scope_id,'p_view_raw':control['view_raw']}), 'control':dumps(control)}


def memory_recall_context(body, control_json, state_json, query='', base_json=''):
    bundle=rpc_bundle(body)
    control=obj(control_json,{})
    state=obj(state_json,{})
    existing=obj(state.get('active_list'),{})
    choices=''
    def local_time(value):
        if not base_json:return value
        tz=ZoneInfo(obj(base_json)['profile']['timezone'])
        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S %z')
    chosen=bundle.get('scope') or {}
    focus=state.get('last_referenced_items') or ''
    focus_source=state.get('focus_entry_id') or ''
    if control.get('historical') and not control.get('view'):
        scopes=bundle.get('scopes') or []
        if len(scopes)>1 and not chosen:
            choices=dumps({'scopes':scopes,'instruction':query})
            control['ambiguous_scopes']='\n'.join('第%d张：%s · %s' % (i+1,local_time(r['created_at']),r.get('label') or '日程列表') for i,r in enumerate(scopes))
        elif chosen:
            state['active_list']=dumps(chosen)
            focus=dumps(chosen.get('items',[]))
            focus_source=chosen.get('scope_id','')
    elif chosen and chosen.get('scope_id')!=existing.get('scope_id'):
        # Never adopt another chat's ordinal scope merely because a model recalled it.
        chosen={}
    records=bundle.get('entries') or []
    chunks=[]
    view=['### 📖 上下文文档']
    budget=12000
    snippet=min(2000,max(100,(12000//max(1,len(records[:8]))-900)//2))
    for record in records[:8]:
        data={k:record.get(k) for k in ('id','created_at','summary','topics')}
        data['summary']=str(data.get('summary') or '')[:200]
        data['topics']=(data.get('topics') or [])[:3]
        data['user_text']=str(record.get('user_text') or '')[:snippet]
        data['assistant_text']=str(record.get('assistant_text') or '')[:snippet]
        encoded=dumps(data)
        if len(encoded)<=budget:
            chunks.append(data);budget-=len(encoded)
        view.append('**%s**\n\n%s' % (local_time(record['created_at']),record.get('summary') or '（本轮暂无摘要）'))
        if control.get('view_raw'):
            raw_text='用户：'+str(record.get('user_text') or '')+'\n\n助手：'+str(record.get('assistant_text') or '')
            remaining=max(0,40000-sum(len(x) for x in view))
            view.append(raw_text[:remaining]+('\n\n（原文较长，本次展示部分内容；完整原文仍保存在上下文文档中。）' if len(raw_text)>remaining else ''))
    if not chunks:view.append('当前范围没有可用的历史记录；尚未开始记录或旧记忆已按保留策略清理。')
    scope=state.get('active_list') or ''
    return {'active_list':scope,'focus':focus,'focus_source':focus_source,'choices':choices,'effective_query':control.get('replay_query') or query,'context':dumps({'records':chunks,'historical':control.get('historical',False)}),
            'control':dumps(control),'document_text':'\n\n'.join(view)}


def create_scope(body, entry_id, conversation_id, created_at, start_date, end_date):
    items=context_items(rows(body))
    for r in items:r['entity_type']='schedule'
    scope={'scope_id':entry_id,'origin_entry_id':entry_id,'conversation_id':conversation_id,
           'created_at':created_at,'label':start_date+' 至 '+end_date,'items':items,'updates':{}}
    if len(dumps(scope))>20000:
        raise ValueError('日程列表过长，请缩小范围后再查询；不会展示无法保留编号的列表。')
    return {'scope':dumps(scope)}


def apply_scope_result(scope_json, body, deleted=False):
    scope=obj(scope_json,{})
    records=rows(body)
    updates=scope.get('updates',{})
    for row in records:
        change={k:row.get(k) for k in ('title','event_date','start_time','end_time','location','category')}
        if deleted:change['deleted']=True
        updates[row['id']]={**updates.get(row['id'],{}),**change}
    scope['items']=[{**r,**updates.get(r['id'],{})} for r in scope.get('items',[])]
    scope['updates']=updates
    return {'scope':dumps(scope) if scope.get('scope_id') else '',
            'references':'[]' if deleted else dumps(context_items(records))}


def journal_body(entry_id, user_id, text, canonical, state_json, polish_raw):
    state=obj(state_json,{})
    state={k:v for k,v in state.items() if k not in ('memory_context','memory_control','pending_memory_sync')}
    scope=obj(state.get('active_list'),{})
    facts=obj(state.get('turn_facts'),{})
    payload={'facts':facts,'result_excerpt':canonical[:4000]}
    if scope.get('scope_id')==entry_id:payload['list_snapshot']=scope
    if scope:
        state['active_list']={k:v for k,v in scope.items() if k!='items'}
    suggestion=model_json(polish_raw)
    summary=suggestion.get('memory_summary')
    if not isinstance(summary,str) or not summary.strip():
        summary=str(facts.get('intent') or '对话')+'：'+re.sub(r'\s+',' ',canonical)[:600]
    topics=suggestion.get('topics') or []
    topics=[x[:80] for x in topics if isinstance(x,str) and x.strip()][:10] if isinstance(topics,list) else []
    return {'body':dumps({'p_user_id':user_id,'p_entry_id':entry_id,'p_assistant_text':text,
                         'p_summary':summary[:1200],'p_topics':topics,'p_state':state,'p_payload':payload})}


def journal_result(body, status, text, payload):
    try:
        rpc_bundle(body,status)
        return {'text':text,'pending_sync':''}
    except (ValueError,TypeError):
        warning='⚠️ 本轮结果已返回，但上下文文档暂未同步；下轮只重试记忆保存，不重复执行日程操作。'
        data=obj(payload,{})
        data['p_assistant_text']=text+'\n\n'+warning
        return {'text':data['p_assistant_text'],'pending_sync':dumps(data)}


def memory_guard(body,status):
    try:
        rpc_bundle(body,status)
        return {'ok':'true','error':''}
    except (ValueError,TypeError) as exc:
        return {'ok':'false','error':str(exc)}
