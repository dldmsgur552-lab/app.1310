"""장보기 알림메모 | 실행: streamlit run app.py
공유: 배포된 앱 주소 + ?list=목록ID (QR 만들기에서 생성)
주의: SQLite는 실행 서버의 로컬 저장소입니다. Streamlit Cloud 재배포/재시작 시
데이터가 유실될 수 있으므로 더보기에서 JSON 백업을 내려받으세요.
링크를 가진 사람은 해당 목록을 읽고 수정할 수 있습니다. 로그인은 없습니다.
"""
import io
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import qrcode
import streamlit as st

st.set_page_config(page_title="장보기 알림메모", page_icon="🛒", layout="centered", initial_sidebar_state="collapsed")
DB_PATH = os.environ.get("SHOPPING_DB_PATH", "shopping.sqlite3")
CATEGORIES = ["🥬 야채", "🍎 과일", "🥩 육류", "🐟 수산", "🥚 유제품·달걀", "🍚 곡류·식품", "🧊 냉동·간편식", "🧴 생활용품", "🛍️ 기타"]
PRIORITIES = {1: "1 · 꼭 필요", 2: "2 · 가격 보고", 3: "3 · 저렴할 때만"}
PAGE_SIZE = 3

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, list_id TEXT NOT NULL,
            category TEXT NOT NULL, name TEXT NOT NULL, priority INTEGER NOT NULL,
            ceiling INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'todo',
            qty REAL, unit_price INTEGER, total INTEGER, bought_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_list ON items(list_id)")


def load_items(list_id):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM items WHERE list_id=? ORDER BY priority,id", (list_id,))]


def add_item(list_id, category, name, priority, ceiling):
    with db() as c:
        c.execute("INSERT INTO items(list_id,category,name,priority,ceiling) VALUES(?,?,?,?,?)", (list_id, category, name, priority, ceiling))


def amount(qty, unit_price):
    return int((Decimal(str(qty)) * Decimal(str(unit_price))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def purchase(list_id, item_id, qty, unit_price):
    if qty <= 0 or unit_price < 0:
        raise ValueError("수량과 단가를 확인하세요.")
    with db() as c:
        c.execute("UPDATE items SET status='cart',qty=?,unit_price=?,total=?,bought_at=? WHERE list_id=? AND id=? AND status='todo'", (qty, unit_price, amount(qty, unit_price), datetime.now(timezone.utc).isoformat(), list_id, item_id))


def set_status(list_id, item_id, status):
    if status not in ('todo', 'skip'):
        raise ValueError("잘못된 상태")
    with db() as c:
        c.execute("UPDATE items SET status=?,qty=NULL,unit_price=NULL,total=NULL,bought_at=NULL WHERE list_id=? AND id=?", (status, list_id, item_id))


def delete_item(list_id, item_id):
    with db() as c:
        c.execute("DELETE FROM items WHERE list_id=? AND id=?", (list_id, item_id))


def share_link(base, list_id):
    parts = urlsplit(base.strip())
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        raise ValueError("https://로 시작하는 실제 배포 주소를 입력하세요.")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['list'] = list_id
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def restore(list_id, payload):
    if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('items'), list):
        raise ValueError('지원하지 않는 백업입니다.')
    if len(payload['items']) > 2000:
        raise ValueError('최대 2,000개까지 복원할 수 있습니다.')
    valid = []
    for r in payload['items']:
        name = str(r.get('name', '')).strip()
        category = r.get('category')
        priority = int(r.get('priority', 0))
        ceiling = int(r.get('ceiling', 0))
        status = r.get('status')
        if not name or len(name) > 60 or category not in CATEGORIES or priority not in PRIORITIES or not 0 <= ceiling <= 100000000 or status not in ('todo', 'cart', 'skip'):
            raise ValueError('백업 항목의 형식이 올바르지 않습니다.')
        qty, price, total = None, None, None
        if status == 'cart':
            qty, price = float(r['qty']), int(r['unit_price'])
            if not 0.01 <= qty <= 10000 or not 0 <= price <= 100000000:
                raise ValueError('백업의 가격 또는 수량이 잘못되었습니다.')
            total = amount(qty, price)
        valid.append((list_id, category, name, priority, ceiling, status, qty, price, total, r.get('bought_at')))
    with db() as c:
        c.execute('DELETE FROM items WHERE list_id=?', (list_id,))
        c.executemany('INSERT INTO items(list_id,category,name,priority,ceiling,status,qty,unit_price,total,bought_at) VALUES(?,?,?,?,?,?,?,?,?,?)', valid)


initialize()
list_id = st.query_params.get('list', '')
if not isinstance(list_id, str) or not (16 <= len(list_id) <= 80 and all(ch.isalnum() or ch in '-_' for ch in list_id)):
    list_id = secrets.token_urlsafe(18)
    st.query_params['list'] = list_id

st.markdown('''<style>
.block-container {max-width:580px;padding:0.65rem 0.8rem 1rem;}
header[data-testid="stHeader"] {height:0;background:transparent;}
h1 {font-size:1.4rem!important;padding:0!important;margin:0!important;}
[data-testid="stVerticalBlock"] {gap:0.45rem;}
[data-testid="stMetricValue"] {font-size:1.6rem;}
[data-testid="stMetricLabel"] {font-size:0.8rem;}
[data-testid="stCaptionContainer"] p {font-size:0.76rem;}
.stButton button {min-height:2.3rem;padding:0.2rem 0.45rem;}
[data-testid="stHorizontalBlock"] {gap:0.4rem;}
@media(max-width:640px){
[data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0!important;}
}
</style>''', unsafe_allow_html=True)


@st.dialog('➕ 장볼 물건', width='small')
def add_dialog():
    with st.form('add_form', clear_on_submit=True):
        category = st.selectbox('카테고리', CATEGORIES)
        name = st.text_input('상품명', max_chars=60, placeholder='예: 당근 2개 / 우유 1L')
        priority = st.select_slider('중요도', options=[1, 2, 3], value=2, format_func=lambda x: PRIORITIES[x])
        ceiling = st.number_input('단가 기준가격 (원, 0=미설정)', min_value=0, max_value=100000000, step=100, help='같은 규격의 상품끼리 비교하세요. 기준 초과여도 구매를 강제하지 않습니다.')
        if st.form_submit_button('메모 추가', type='primary', use_container_width=True):
            if not name.strip():
                st.error('상품명을 입력해주세요.')
            else:
                add_item(list_id, category, name.strip(), priority, ceiling)
                st.rerun()


@st.dialog('🛒 카트에 담기', width='small')
def buy_dialog(item):
    st.write(f"**{item['name']}** · {PRIORITIES[item['priority']]}")
    st.caption('가격은 1개/1팩/1kg 등 입력 수량 1단위의 가격입니다.')
    qty = st.number_input('수량 (개·팩·kg 등)', min_value=0.01, max_value=10000.0, value=1.0, step=1.0, format='%.2f', key=f"qty_{item['id']}")
    price = st.number_input('단가 (원)', min_value=0, max_value=100000000, value=int(item['ceiling']), step=100, key=f"price_{item['id']}")
    st.metric('이번 상품 합계', f'{amount(qty, price):,}원')
    if item['ceiling'] and price > item['ceiling']:
        st.warning(f"기준 {item['ceiling']:,}원보다 비싸요. 오늘은 패스해도 괜찮아요.")
    elif item['priority'] > 1:
        st.caption('가격이 마음에 들지 않으면 이번엔 패스하세요.')
    a, b = st.columns(2)
    if a.button('구매 확정', type='primary', use_container_width=True):
        purchase(list_id, item['id'], qty, price)
        st.rerun()
    if b.button('이번엔 패스', use_container_width=True):
        set_status(list_id, item['id'], 'skip')
        st.rerun()


@st.dialog('물건 관리', width='small')
def manage_dialog(item):
    st.write(f"**{item['name']}**")
    if item['status'] == 'cart':
        st.write(f"{item['unit_price']:,}원 × {item['qty']:g} = **{item['total']:,}원**")
        st.caption('수량·가격 수정은 메모로 되돌린 뒤 다시 담아주세요.')
    if st.button('↩ 메모로 되돌리기', use_container_width=True):
        set_status(list_id, item['id'], 'todo')
        st.rerun()
    if item['status'] == 'todo' and st.button('⏭ 이번엔 패스', use_container_width=True):
        set_status(list_id, item['id'], 'skip')
        st.rerun()
    confirmed = st.checkbox('이 항목을 영구 삭제합니다')
    if st.button('삭제', disabled=not confirmed, use_container_width=True):
        delete_item(list_id, item['id'])
        st.rerun()


@st.dialog('QR 공유 · 백업', width='small')
def tools_dialog():
    st.caption('같은 QR로 접속하면 같은 목록을 봅니다. 링크 소지자는 수정할 수 있으니 신뢰하는 사람에게만 공유하세요.')
    base = st.text_input('배포된 앱 주소', value=st.session_state.get('base_url', ''), placeholder='https://your-app.streamlit.app')
    if base:
        try:
            url = share_link(base, list_id)
            st.session_state['base_url'] = base
            buffer = io.BytesIO()
            qrcode.make(url).save(buffer, format='PNG')
            st.image(buffer.getvalue(), width=170)
            st.code(url, language=None)
            st.download_button('QR 이미지 저장', buffer.getvalue(), 'shopping-qr.png', 'image/png')
        except ValueError as e:
            st.error(str(e))
    st.divider()
    snapshot = json.dumps({'version': 1, 'items': load_items(list_id)}, ensure_ascii=False, indent=2)
    st.download_button('목록·카트 JSON 백업', snapshot, 'shopping-backup.json', 'application/json', use_container_width=True)
    st.caption('서버 임시 저장입니다. 재배포 시 사라질 수 있으니 백업하세요. 휴대폰 알림 발송 기능은 없습니다.')
    uploaded = st.file_uploader('백업 복원', type=['json'])
    confirm = st.checkbox('복원 시 현재 목록 전체를 교체하는 데 동의합니다')
    if st.button('백업으로 복원', disabled=uploaded is None or not confirm, use_container_width=True):
        try:
            if uploaded.size > 2_000_000:
                raise ValueError('백업 파일은 2MB 이하여야 합니다.')
            restore(list_id, json.loads(uploaded.getvalue()))
            st.rerun()
        except (ValueError, TypeError, KeyError, OverflowError) as e:
            st.error(f'복원 실패: {e}')


items = load_items(list_id)
todo = [r for r in items if r['status'] == 'todo']
cart = [r for r in items if r['status'] == 'cart']
skipped = [r for r in items if r['status'] == 'skip']
total = sum(r['total'] or 0 for r in cart)

head, tools = st.columns([4, 1])
head.title('🛒 장보기 알림메모')
if tools.button('⚙️', help='QR 공유 · 백업', use_container_width=True):
    tools_dialog()
a, b = st.columns([1.25, 1])
a.metric('현재 카트 총액', f'{total:,}원')
b.metric('남은 메모 / 담은 품목', f'{len(todo)} / {len(cart)}')

left, right = st.columns([3, 1])
view = left.selectbox('보기', ['📝 메모', '🛒 카트', '⏭ 패스'], label_visibility='collapsed')
if right.button('＋ 추가', type='primary', use_container_width=True):
    add_dialog()

rows = todo if view == '📝 메모' else cart if view == '🛒 카트' else skipped
st.caption('1 꼭 필요 · 2 가격 보고 · 3 저렴할 때만' if view == '📝 메모' else '카트는 입력한 상품금액의 합계입니다. 할인·봉투값 등은 포함되지 않습니다.' if view == '🛒 카트' else '이번에 건너뛴 물건 · 필요하면 메모로 되돌리세요.')
page_key = 'page_' + view
pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
st.session_state[page_key] = min(st.session_state.get(page_key, 0), pages - 1)
page = st.session_state[page_key]
if not rows:
    st.info('메모를 추가해 장보기를 시작하세요.' if view == '📝 메모' else '아직 담긴 물건이 없어요.' if view == '🛒 카트' else '패스한 물건이 없어요.')
for item in rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
    with st.container(border=True):
        namecol, actioncol, menucol = st.columns([5, 1.5, 1])
        namecol.markdown(f"**{item['name'].replace('*', '').replace(chr(10), ' ')}**")
        if item['status'] == 'cart':
            detail = f"{item['qty']:g} × {item['unit_price']:,}원 = {item['total']:,}원"
        else:
            detail = f"{item['category']} · 중요도 {item['priority']}"
            if item['ceiling']:
                detail += f" · 기준 {item['ceiling']:,}원"
        namecol.caption(detail)
        if item['status'] == 'todo':
            if actioncol.button('담기', key=f"buy_{item['id']}", use_container_width=True):
                buy_dialog(item)
        else:
            actioncol.caption('구매' if item['status'] == 'cart' else '보류')
        if menucol.button('⋯', key=f"more_{item['id']}", use_container_width=True):
            manage_dialog(item)

prev, label, nxt = st.columns([1, 2, 1])
if prev.button('‹ 이전', disabled=page == 0, use_container_width=True):
    st.session_state[page_key] = page - 1
    st.rerun()
label.caption(f'{page + 1} / {pages} 페이지 · 총 {len(rows)}개')
if nxt.button('다음 ›', disabled=page >= pages - 1, use_container_width=True):
    st.session_state[page_key] = page + 1
    st.rerun()
st.caption('🔄 다른 휴대폰의 변경 내용은 새로고침하면 반영됩니다.')
