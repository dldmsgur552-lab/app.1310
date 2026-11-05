"""가족 집안일 보드 — streamlit run app.py
GitHub에 app.py와 requirements.txt 업로드 후 Streamlit Community Cloud에서 배포.
설정에서 실제 https 배포 주소를 입력해 가족 공유 QR을 생성하세요.
SQLite 로컬 저장: Cloud 재배포/초기화 시 유실 가능. JSON 백업 권장.
공유 링크 소지자는 수정 가능하며 별도 인증·푸시 알림은 없습니다.
"""
import io
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import streamlit as st
import qrcode

st.set_page_config(page_title='우리집 함께하기', page_icon='🏠', layout='centered')
TZ = ZoneInfo('Asia/Seoul')
DB = 'family_board.sqlite3'
REPEATS = ['한 번', '매일', '매주']

@contextmanager
def database():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with database() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS members(family TEXT, name TEXT, PRIMARY KEY(family,name));
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,family TEXT NOT NULL,
          title TEXT NOT NULL, assignee TEXT NOT NULL, due TEXT NOT NULL, repeat TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,family TEXT NOT NULL,
          task_id INTEGER NOT NULL,title TEXT NOT NULL,person TEXT NOT NULL,due TEXT NOT NULL,
          completed TEXT NOT NULL, UNIQUE(family,task_id,due));
        CREATE TABLE IF NOT EXISTS thanks(family TEXT,history_id INTEGER,person TEXT,
          PRIMARY KEY(family,history_id,person));
        ''')


def rows(sql, args=()):
    with database() as c:
        return [dict(x) for x in c.execute(sql, args)]


def execute(sql, args=()):
    with database() as c:
        c.execute(sql, args)


def finish(family, task_id, person):
    with database() as c:
        c.execute('BEGIN IMMEDIATE')
        t = c.execute('SELECT * FROM tasks WHERE family=? AND id=? AND active=1', (family, task_id)).fetchone()
        if not t:
            return False
        today = datetime.now(TZ).date()
        if date.fromisoformat(t['due']) > today:
            return False
        c.execute('INSERT INTO history(family,task_id,title,person,due,completed) VALUES(?,?,?,?,?,?)',
                  (family, task_id, t['title'], person, t['due'], datetime.now(TZ).isoformat()))
        if t['repeat'] == '한 번':
            c.execute('UPDATE tasks SET active=0 WHERE id=? AND family=?', (task_id, family))
        else:
            next_due = date.fromisoformat(t['due'])
            step = timedelta(days=1 if t['repeat'] == '매일' else 7)
            while next_due <= today:
                next_due += step
            c.execute('UPDATE tasks SET due=? WHERE id=? AND family=?', (next_due.isoformat(), task_id, family))
        return True


def export_data(family):
    return {'version': 1, 'members': rows('SELECT name FROM members WHERE family=?', (family,)),
            'tasks': rows('SELECT * FROM tasks WHERE family=?', (family,)),
            'history': rows('SELECT * FROM history WHERE family=?', (family,)),
            'thanks': rows('SELECT * FROM thanks WHERE family=?', (family,))}


def restore(family, data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('지원하지 않는 백업입니다.')
    for k in ('members', 'tasks', 'history', 'thanks'):
        if not isinstance(data.get(k), list) or len(data[k]) > 20000:
            raise ValueError('백업 형식 또는 크기가 올바르지 않습니다.')
    def text(value, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('문자열 값이 올바르지 않습니다.')
        return value.strip()
    with database() as c:
        c.execute('BEGIN IMMEDIATE')
        for table in ('thanks', 'history', 'tasks', 'members'):
            c.execute(f'DELETE FROM {table} WHERE family=?', (family,))
        for m in data['members']:
            c.execute('INSERT INTO members VALUES(?,?)', (family, text(m['name'], 20)))
        taskmap, histmap = {}, {}
        for t in data['tasks']:
            if t['repeat'] not in REPEATS or t['active'] not in (0, 1):
                raise ValueError('반복 설정이 잘못되었습니다.')
            due = date.fromisoformat(t['due']).isoformat()
            if t['id'] in taskmap:
                raise ValueError('중복 항목입니다.')
            cur = c.execute('INSERT INTO tasks(family,title,assignee,due,repeat,active) VALUES(?,?,?,?,?,?)',
                            (family, text(t['title'], 60), text(t['assignee'], 20), due, t['repeat'], t['active']))
            taskmap[t['id']] = cur.lastrowid
        for h in data['history']:
            if h['id'] in histmap:
                raise ValueError('중복 기록입니다.')
            datetime.fromisoformat(h['completed'])
            cur = c.execute('INSERT INTO history(family,task_id,title,person,due,completed) VALUES(?,?,?,?,?,?)',
                            (family, taskmap[h['task_id']], text(h['title'], 60), text(h['person'], 20), date.fromisoformat(h['due']).isoformat(), h['completed']))
            histmap[h['id']] = cur.lastrowid
        for t in data['thanks']:
            c.execute('INSERT INTO thanks VALUES(?,?,?)', (family, histmap[t['history_id']], text(t['person'], 20)))


init()
family = st.query_params.get('family', '')
if not isinstance(family, str) or not 20 <= len(family) <= 80 or not all(c.isalnum() or c in '-_' for c in family):
    family = secrets.token_urlsafe(24)
    st.query_params['family'] = family
members = [m['name'] for m in rows('SELECT name FROM members WHERE family=? ORDER BY name', (family,))]
today = datetime.now(TZ).date()

st.markdown('''<style>
.block-container{max-width:580px;padding:0.8rem 0.8rem 1rem}
h1{font-size:1.55rem!important;padding:0!important}
[data-testid="stVerticalBlock"]{gap:.45rem}
[data-testid="stMetricValue"]{font-size:1.45rem}
.stButton button{min-height:2.5rem}
@media(max-width:640px){[data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:.4rem}
[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{min-width:0!important}}
</style>''', unsafe_allow_html=True)

@st.dialog('➕ 함께 할 일', width='small')
def add_task():
    with st.form('new_task'):
        title = st.text_input('할 일', max_chars=60, placeholder='예: 저녁 설거지')
        assignee = st.selectbox('담당', ['누구나'] + members)
        due = st.date_input('시작 날짜', value=today)
        repeat = st.selectbox('반복', REPEATS)
        st.caption('반복은 시작 날짜 기준입니다. 밀린 일은 한 번 완료하면 다음 예정일로 넘어갑니다.')
        if st.form_submit_button('등록', type='primary', use_container_width=True):
            if not title.strip():
                st.error('할 일을 입력해주세요.')
            else:
                execute('INSERT INTO tasks(family,title,assignee,due,repeat) VALUES(?,?,?,?,?)',
                        (family, title.strip(), assignee, due.isoformat(), repeat))
                st.rerun()

@st.dialog('완료 기록', width='small')
def complete_dialog(task):
    st.write(task['title'])
    who = st.selectbox('실제로 한 사람', members, index=members.index(me) if me in members else 0)
    if st.button('완료하기 ✅', type='primary', use_container_width=True):
        if finish(family, task['id'], who):
            st.rerun()
        else:
            st.info('이미 완료되었거나 아직 예정일이 아닙니다. 창을 닫고 새로고침하세요.')

@st.dialog('할 일 관리', width='small')
def edit_task(task):
    with st.form('edit_task'):
        title = st.text_input('할 일', task['title'], max_chars=60)
        options = ['누구나'] + members
        assignee = st.selectbox('담당', options, index=options.index(task['assignee']) if task['assignee'] in options else 0)
        due = st.date_input('다음 예정일', date.fromisoformat(task['due']))
        repeat = st.selectbox('반복', REPEATS, index=REPEATS.index(task['repeat']))
        if st.form_submit_button('변경 저장', type='primary'):
            if title.strip():
                execute('UPDATE tasks SET title=?,assignee=?,due=?,repeat=? WHERE family=? AND id=?',
                        (title.strip(), assignee, due.isoformat(), repeat, family, task['id']))
                st.rerun()
            else:
                st.error('할 일을 입력해주세요.')
    confirmed = st.checkbox('이 할 일을 보관하고 앞으로 표시하지 않습니다')
    if st.button('할 일 보관', disabled=not confirmed):
        execute('UPDATE tasks SET active=0 WHERE family=? AND id=?', (family, task['id']))
        st.rerun()

@st.dialog('가족 · QR · 백업', width='small')
def settings():
    st.caption('같은 링크로 접속해야 같은 가족 보드가 열립니다. 링크 소지자는 누구나 수정할 수 있으니 가족에게만 공유하세요.')
    with st.form('member_add'):
        name = st.text_input('가족 이름 / 별명', max_chars=20)
        if st.form_submit_button('가족 추가'):
            if name.strip() and name.strip() != '누구나':
                execute('INSERT OR IGNORE INTO members VALUES(?,?)', (family, name.strip()))
                st.rerun()
            else:
                st.error('이름을 입력하세요. ‘누구나’는 사용할 수 없습니다.')
    st.caption('등록된 가족: ' + (', '.join(members) or '아직 없음'))
    base = st.text_input('배포된 앱 주소', value=st.session_state.get('base_url', ''), placeholder='https://your-app.streamlit.app')
    if base:
        p = urlsplit(base.strip())
        if p.scheme != 'https' or not p.netloc or p.username or p.password:
            st.error('https://로 시작하는 배포 주소를 입력하세요.')
        else:
            st.session_state['base_url'] = base
            query = dict(parse_qsl(p.query, keep_blank_values=True))
            query['family'] = family
            link = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ''))
            buf = io.BytesIO()
            qrcode.make(link).save(buf, format='PNG')
            st.image(buf.getvalue(), width=160)
            st.code(link, language=None)
            st.download_button('QR 다운로드', buf.getvalue(), 'family-qr.png', 'image/png')
    st.divider()
    st.warning('서버 로컬 저장입니다. Cloud 재배포·초기화 때 유실될 수 있으니 백업하세요. 실사용 영구 보관에는 외부 DB 연결이 필요합니다.')
    st.download_button('전체 JSON 백업', json.dumps(export_data(family), ensure_ascii=False, indent=2), 'family-backup.json', 'application/json')
    upload = st.file_uploader('JSON 백업 복원', type='json')
    confirm = st.checkbox('현재 가족 보드 전체를 백업으로 교체합니다')
    if st.button('복원', disabled=not confirm or upload is None):
        try:
            if upload.size > 5_000_000:
                raise ValueError('파일은 5MB 이하여야 합니다.')
            restore(family, json.loads(upload.getvalue()))
            st.rerun()
        except (ValueError, TypeError, KeyError, sqlite3.Error) as e:
            st.error(f'복원하지 못했습니다: {e}')

h, gear = st.columns([5, 1])
h.title('🏠 우리집 함께하기')
if gear.button('⚙️', use_container_width=True):
    settings()
if not members:
    st.info('처음 오셨나요? ⚙️에서 가족 이름을 추가하고 QR을 공유하세요.')
    st.stop()

me = st.selectbox('나는', members, key='current_member')
active = rows('SELECT * FROM tasks WHERE family=? AND active=1 ORDER BY due,id', (family,))
due_tasks = [t for t in active if t['due'] <= today.isoformat()]
history = rows('SELECT h.*, (SELECT COUNT(*) FROM thanks t WHERE t.family=h.family AND t.history_id=h.id) AS gratitude FROM history h WHERE family=? ORDER BY completed DESC,id DESC', (family,))
completed_today = [r for r in history if r['completed'][:10] == today.isoformat()]
a, b = st.columns(2)
a.metric('오늘 남은 일', len(due_tasks))
b.metric('오늘 함께한 일', len(completed_today))
left, right = st.columns([3, 1])
view = left.selectbox('보기', ['오늘 할 일', '내가 맡은 일', '완료 · 고마워요', '앞으로 할 일'], label_visibility='collapsed')
if right.button('＋ 등록', type='primary', use_container_width=True):
    add_task()
if view == '오늘 할 일':
    selected = due_tasks
elif view == '내가 맡은 일':
    selected = [t for t in due_tasks if t['assignee'] == me]
elif view == '앞으로 할 일':
    selected = [t for t in active if t['due'] > today.isoformat()]
else:
    selected = history
key = 'page_' + view + '_' + me
pages = max(1, (len(selected) + 2) // 3)
page = min(st.session_state.get(key, 0), pages - 1)
st.session_state[key] = page
if not selected:
    st.success('지금은 비어 있어요. 함께할 일을 등록해보세요!')
for item in selected[page * 3:page * 3 + 3]:
    with st.container(border=True):
        if view == '완료 · 고마워요':
            st.text('✅ ' + item['title'])
            st.caption(f"{item['person']} · {item['completed'][:16].replace('T', ' ')} · ❤️ {item['gratitude']}")
            thanked = bool(rows('SELECT 1 FROM thanks WHERE family=? AND history_id=? AND person=?', (family, item['id'], me)))
            if st.button('❤️ 고마워요' if not thanked else '❤️ 마음 전달 완료', key=f"thanks_{item['id']}", disabled=thanked or item['person'] == me, use_container_width=True):
                execute('INSERT OR IGNORE INTO thanks VALUES(?,?,?)', (family, item['id'], me))
                st.rerun()
        else:
            text, done, menu = st.columns([4, 1.5, 1])
            text.text(item['title'])
            overdue = ' · 밀린 일' if item['due'] < today.isoformat() else ''
            text.caption(f"{item['assignee']} · {item['repeat']} · {item['due'][5:]}{overdue}")
            if done.button('완료', key=f"done_{item['id']}", disabled=item['due'] > today.isoformat(), use_container_width=True):
                complete_dialog(item)
            if menu.button('⋯', key=f"edit_{item['id']}", use_container_width=True):
                edit_task(item)
a, b, c = st.columns([1, 2, 1])
if a.button('‹ 이전', disabled=page == 0, use_container_width=True):
    st.session_state[key] = page - 1
    st.rerun()
b.caption(f'{page + 1} / {pages} · 총 {len(selected)}개')
if c.button('다음 ›', disabled=page == pages - 1, use_container_width=True):
    st.session_state[key] = page + 1
    st.rerun()
if st.button('↻ 가족 변경사항 새로고침', use_container_width=True):
    st.rerun()
st.caption('서로의 수고를 알아주는 공간 ❤️ · 한국 시간 기준 · 푸시 알림 없음')
