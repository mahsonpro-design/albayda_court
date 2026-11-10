import os
import logging
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

import telebot
from telebot import types
from dotenv import load_dotenv

from case_records import (
    CaseRecordsError,
    available_case_types,
    available_judges,
    case_type_matches,
    display_judge_name,
    generate_attendance_ad_pdf,
    generate_case_pdf,
    generate_search_report_pdf,
    generate_sessions_pdf,
    load_case_records,
    normalize_judge_name,
    search_supreme_court_cases,
    search_case_records,
    search_sessions_by_date,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)

API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if API_TOKEN:
    API_TOKEN = API_TOKEN.strip()
if not API_TOKEN:
    raise RuntimeError(
        'TELEGRAM_BOT_TOKEN is not set. Create a .env file from .env.example and add your bot token.'
    )

bot = telebot.TeleBot(API_TOKEN)

ALERT_CHAT_FILE = os.path.join(BASE_DIR, '.alert_chat_id')


def _read_alert_chat_id():
    try:
        with open(ALERT_CHAT_FILE, 'r', encoding='utf-8') as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def _save_alert_chat_id(chat_id):
    if not chat_id:
        return
    if _read_alert_chat_id() == str(chat_id):
        return
    try:
        with open(ALERT_CHAT_FILE, 'w', encoding='utf-8') as handle:
            handle.write(str(chat_id))
    except OSError as exc:
        logging.warning('Could not store alert chat id (%s)', str(exc))


def _notify_owner(text):
    chat_id = _read_alert_chat_id()
    if not chat_id:
        logging.info('No alert chat id stored yet, startup notice skipped.')
        return
    try:
        bot.send_message(chat_id, text)
    except Exception as exc:
        logging.warning('Could not send startup notice (%s)', str(exc))

LOGO_STICKER_PATH = os.path.join(BASE_DIR, 'albayda-it-logo.webp')
INFO_STICKER_PATH = os.path.join(BASE_DIR, 'ME.webp')
CASE_NUMBER_MAX_LENGTH = 10
HIJRI_YEAR_LENGTH = 4
SEARCH_LIMIT = 3
SEARCH_WINDOW_SECONDS = 60
MAX_TRACKED_USERS = 10_000
CASE_CANCEL_BUTTON = '❌ إلغاء البحث'
CASE_FLOW_TTL_SECONDS = 15 * 60
CASE_FLOW_MAX_USERS = 10_000
SESSIONS_FLOW_TTL_SECONDS = 15 * 60
SESSIONS_FLOW_MAX_USERS = 10_000
COURT_TIMEZONE = timezone(timedelta(hours=3))


INFO_TEXT = """
⚙️ النظام الإلكتروني المطور للاستعلام
تقنية معلومات محكمة البيضاء الابتدائية
برمجة وتصميم/احمد حسين محسون
📲 للتواصل والدعم الفني: 734840710-771421919
Whatssapp: https://wa.me/qr/QIQRW7LA325GG1
Telegram: t.me/Ahmed_Mahson
"""

WELCOME_CAPTION = (
    'تقنية معلومات محكمة البيضاء الابتدائية للاستعلام الالكتروني عن القضايا '
    'والجلسات وانشاء الاعلانات القضائية\n'
    'اختر الخدمة المطلوبة:'
)
WELCOME_PROMPT = 'اختر الخدمة المطلوبة:'

DAILY_SESSIONS_BUTTON = 'الاستعلام عن الجلسات اليومية'
DAILY_SESSIONS_MENU_LABEL = f'📅 {DAILY_SESSIONS_BUTTON}'
CASE_SEARCH_BUTTON = '🔎 الاستعلام المباشر عن قضية'
RECORDS_SEARCH_BUTTON = '📁 البحث في سجل القضايا'
ATTENDANCE_AD_BUTTON = '📄 إنشاء إعلان حضور جلسة (PDF)'
ALL_CASES_BUTTON = '🔍 بحث شامل (كافة الأقسام)'
CASE_TYPES_HEADER = '📂 نــــوع القضـــية'
OTHER_SERVICES_HEADER = '🔷 خدمـــات أخـــرى'
SUPREME_COURT_BUTTON = '🏛️ الاستعلام عن القضايا بالمحكمة العليا'
CASE_TYPE_MENU_ORDER = (
    'شخصيـــة',
    'مدنيــــة',
    'جزائية (جسيمة)',
    'جزائية (غير جسيمة)',
    'مدنية (مستعجلة)',
    'قضايا التنفيذ (منازعات)',
    'جزائية (أحداث)',
)
CASE_TYPE_MENU_LABELS = {
    'شخصيـــة': '👥 شخصيـــة',
    'مدنيــــة': '🏛️ مدنيـــة',
    'جزائية (جسيمة)': '⚖️ جزائية (جسيمة)',
    'جزائية (غير جسيمة)': '⚖️ جزائية (غير جسيمة)',
    'مدنية (مستعجلة)': '🏛️ مدنية (مستعجلة)',
    'قضايا التنفيذ (منازعات)': '📑 تنفيذية (منازعات)',
    'جزائية (أحداث)': '🧑‍🧒‍🧒 جزائية (أحداث)',
}

sessions_search_states = OrderedDict()
sessions_search_states_lock = threading.Lock()
records_search_states = OrderedDict()
records_search_states_lock = threading.Lock()


def parse_case_query(input_text):
    """Return normalized case number and Hijri year, or None for invalid input."""
    if not isinstance(input_text, str) or input_text.count('/') != 1:
        return None

    case_num, hijri_year = (part.strip() for part in input_text.split('/'))

    def normalize_digits(value):
        try:
            return ''.join(str(unicodedata.decimal(char)) for char in value)
        except ValueError:
            return None

    case_num = normalize_digits(case_num)
    hijri_year = normalize_digits(hijri_year)
    if case_num is None or hijri_year is None:
        return None
    if not 1 <= len(case_num) <= CASE_NUMBER_MAX_LENGTH:
        return None
    if len(hijri_year) != HIJRI_YEAR_LENGTH:
        return None
    if int(case_num) == 0 or int(hijri_year) == 0:
        return None

    return case_num, hijri_year


class UserRateLimiter:
    """Thread-safe sliding-window request limiter with bounded user tracking."""

    def __init__(self, limit, window_seconds, max_tracked_users=MAX_TRACKED_USERS, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_tracked_users = max_tracked_users
        self.clock = clock
        self._requests_by_user = OrderedDict()
        self._lock = threading.Lock()

    def is_limited(self, user_id):
        with self._lock:
            now = self.clock()
            requests = self._requests_by_user.get(user_id)
            if requests is not None:
                while requests and now - requests[0] >= self.window_seconds:
                    requests.popleft()
                if not requests:
                    del self._requests_by_user[user_id]
                    requests = None

            if requests is None:
                if len(self._requests_by_user) >= self.max_tracked_users:
                    self._requests_by_user.popitem(last=False)
                requests = deque()
                self._requests_by_user[user_id] = requests
            else:
                self._requests_by_user.move_to_end(user_id)

            if len(requests) >= self.limit:
                return True
            requests.append(now)
            return False


search_rate_limiter = UserRateLimiter(SEARCH_LIMIT, SEARCH_WINDOW_SECONDS)
sessions_rate_limiter = UserRateLimiter(SEARCH_LIMIT, SEARCH_WINDOW_SECONDS)


def utc_today():
    return datetime.now(COURT_TIMEZONE).date().isoformat()


def parse_sitting_date(input_text):
    if not isinstance(input_text, str):
        return None
    value = input_text.strip()
    if value == 'اليوم':
        return utc_today()

    normalized = []
    try:
        for char in value:
            normalized.append(str(unicodedata.decimal(char)) if char.isdecimal() else char)
    except ValueError:
        return None
    value = ''.join(normalized)

    for date_format in ('%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _case_state_key(message, user_id=None):
    if user_id is None:
        user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    chat_id = getattr(getattr(message, 'chat', None), 'id', None)
    if user_id is None or chat_id is None:
        return None
    return chat_id, user_id


def _save_sessions_state(message, state, user_id=None):
    key = _case_state_key(message, user_id)
    if key is None:
        return False
    now = time.monotonic()
    with sessions_search_states_lock:
        expired = [
            state_key for state_key, value in sessions_search_states.items()
            if now - value.get('updated_at', now) >= SESSIONS_FLOW_TTL_SECONDS
        ]
        for state_key in expired:
            sessions_search_states.pop(state_key, None)
        if key not in sessions_search_states and len(sessions_search_states) >= SESSIONS_FLOW_MAX_USERS:
            sessions_search_states.popitem(last=False)
        state['updated_at'] = now
        sessions_search_states[key] = state
        sessions_search_states.move_to_end(key)
    return True


def _get_sessions_state(message, user_id=None):
    key = _case_state_key(message, user_id)
    if key is None:
        return None
    now = time.monotonic()
    with sessions_search_states_lock:
        state = sessions_search_states.get(key)
        if state is None:
            return None
        if now - state.get('updated_at', now) >= SESSIONS_FLOW_TTL_SECONDS:
            del sessions_search_states[key]
            return None
        state['updated_at'] = now
        sessions_search_states.move_to_end(key)
        return state


def _clear_sessions_state(message, user_id=None):
    key = _case_state_key(message, user_id)
    if key is None:
        return
    with sessions_search_states_lock:
        sessions_search_states.pop(key, None)


def _save_records_state(chat_id, user_id, state):
    key = (chat_id, user_id)
    now = time.monotonic()
    with records_search_states_lock:
        expired = [
            state_key for state_key, value in records_search_states.items()
            if now - value.get('updated_at', now) >= CASE_FLOW_TTL_SECONDS
        ]
        for state_key in expired:
            records_search_states.pop(state_key, None)
        if key not in records_search_states and len(records_search_states) >= CASE_FLOW_MAX_USERS:
            records_search_states.popitem(last=False)
        state['updated_at'] = now
        records_search_states[key] = state
        records_search_states.move_to_end(key)


def _get_records_state(chat_id, user_id):
    key = (chat_id, user_id)
    now = time.monotonic()
    with records_search_states_lock:
        state = records_search_states.get(key)
        if state is None:
            return None
        if now - state.get('updated_at', now) >= CASE_FLOW_TTL_SECONDS:
            del records_search_states[key]
            return None
        state['updated_at'] = now
        records_search_states.move_to_end(key)
        return state


def _clear_records_state(chat_id, user_id):
    if chat_id is None or user_id is None:
        return
    with records_search_states_lock:
        records_search_states.pop((chat_id, user_id), None)


def _records_choice_keyboard(kind, values, include_all=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if include_all:
        markup.row(types.InlineKeyboardButton(
            '🔍 بحث شامل (كافة الأقسام)' if kind == 'type' else '❓ البحث في كافة القضاة',
            callback_data=f'records:{kind}:all',
        ))
    buttons = [
        types.InlineKeyboardButton(
            (
                display_judge_name(value)
                if kind in {'judge', 'ad_judge'}
                else str(value)
            )[:64],
            callback_data=f'records:{kind}:{index}',
        )
        for index, value in enumerate(values[:80])
    ]
    for index in range(0, len(buttons), 2):
        markup.row(*buttons[index:index + 2])
    markup.row(types.InlineKeyboardButton(
        '🏠 إلغاء والعودة للقائمة الرئيسية',
        callback_data='records:cancel',
    ))
    return markup


def _cancel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        '❌ إلغاء والعودة للقائمة الرئيسية',
        callback_data='flow:cancel',
    ))
    return markup


def _back_to_main_menu_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            '🔙 العودة للقائمة الرئيسية',
            callback_data='flow:cancel',
        )
    )
    return markup


def _sessions_judge_keyboard(judges):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton(
        'جميع القضاة',
        callback_data='sessions:judge:all',
    ))
    buttons = [
        types.InlineKeyboardButton(
            judge,
            callback_data=f'sessions:judge:{index}',
        )
        for index, judge in enumerate(judges)
    ]
    for index in range(0, len(buttons), 2):
        markup.row(*buttons[index:index + 2])
    markup.add(types.InlineKeyboardButton(
        '❌ إلغاء والعودة للقائمة الرئيسية',
        callback_data='flow:cancel',
    ))
    return markup


def _send_system_info(chat_id):
    with open(INFO_STICKER_PATH, 'rb') as sticker:
        bot.send_sticker(chat_id, sticker)
    bot.send_message(
        chat_id,
        INFO_TEXT.strip(),
        reply_markup=_cancel_keyboard(),
    )


def _main_menu_keyboard(case_types):
    markup = types.InlineKeyboardMarkup(row_width=2)
    ordered_case_types = _ordered_menu_case_types(case_types)
    markup.row(types.InlineKeyboardButton(
        CASE_TYPES_HEADER,
        callback_data='main:noop',
        style='primary',
    ))
    buttons = [
        types.InlineKeyboardButton(
            CASE_TYPE_MENU_LABELS.get(case_type, str(case_type))[:64],
            callback_data=f'main:type:{index}',
            style='success',
        )
        for index, case_type in enumerate(ordered_case_types[:80])
    ]
    for index in range(0, len(buttons), 2):
        markup.row(*buttons[index:index + 2])
    markup.row(
        types.InlineKeyboardButton(CASE_SEARCH_BUTTON, callback_data='main:case'),
        types.InlineKeyboardButton(ALL_CASES_BUTTON, callback_data='main:all'),
    )
    markup.row(types.InlineKeyboardButton(
        OTHER_SERVICES_HEADER,
        callback_data='main:noop',
        style='primary',
    ))
    markup.row(
        types.InlineKeyboardButton(ATTENDANCE_AD_BUTTON, callback_data='main:attendance'),
        types.InlineKeyboardButton(
            DAILY_SESSIONS_MENU_LABEL,
            callback_data='main:sessions',
        ),
    )
    markup.row(types.InlineKeyboardButton(
        SUPREME_COURT_BUTTON,
        callback_data='main:supreme',
        style='primary',
    ))
    markup.row(types.InlineKeyboardButton(
        'ℹ️ معلومات النظام والملكية',
        callback_data='main:info',
    ))
    return markup


def _ordered_menu_case_types(case_types):
    order = {
        case_type: index
        for index, case_type in enumerate(CASE_TYPE_MENU_ORDER)
    }
    return sorted(
        case_types,
        key=lambda case_type: (order.get(case_type, len(order)), case_type),
    )


def _menu_case_type_by_label(case_types):
    return {
        CASE_TYPE_MENU_LABELS.get(case_type, str(case_type))[:64]: case_type
        for case_type in _ordered_menu_case_types(case_types)[:80]
    }


def begin_records_search(message, case_type='all', user_id=None):
    bot.send_chat_action(message.chat.id, 'typing')
    wait_msg = bot.reply_to(message, '⏳ جاري تحميل سجل القضايا...')
    try:
        records = load_case_records()
    except CaseRecordsError as exc:
        bot.edit_message_text(
            str(exc),
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
        )
        return

    case_types = available_case_types(records)
    if not records or not case_types:
        bot.edit_message_text(
            '⚠️ لا توجد قضايا أو أقسام متاحة في سجل القضايا حالياً.',
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
        )
        return

    if case_type != 'all' and case_type not in case_types:
        bot.edit_message_text(
            '⚠️ نوع القضية المحدد غير متوفر.',
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
        )
        return
    filtered_records = records if case_type == 'all' else [
        record for record in records
        if case_type_matches(record.get('نوع القضية'), case_type)
    ]
    judges = available_judges(filtered_records)
    _save_records_state(
        message.chat.id,
        user_id if user_id is not None else message.from_user.id,
        {
            'step': 'judge',
            'records': records,
            'case_type': case_type,
            'judges': judges,
        },
    )
    bot.edit_message_text(
        (
            'اختر القاضي:'
            if case_type == 'all'
            else f'اختر القاضي في قسم {case_type}:'
        ),
        chat_id=message.chat.id,
        message_id=wait_msg.message_id,
        reply_markup=_records_choice_keyboard('judge', judges, include_all=True),
    )


def handle_records_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    state = _get_records_state(chat_id, user_id)
    if call.data == 'records:cancel':
        _clear_records_state(chat_id, user_id)
        bot.answer_callback_query(call.id)
        send_welcome(call.message, user_id=user_id)
        return
    if call.data == 'records:back:type':
        if state is None:
            bot.answer_callback_query(call.id, 'انتهت صلاحية البحث. ابدأ من جديد.', show_alert=True)
            return
        case_types = available_case_types(state.get('records', []))
        state.update({'step': 'type'})
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            'اختر نوع القضية:',
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=_records_choice_keyboard('type', case_types, include_all=True),
        )
        return
    if state is None:
        bot.answer_callback_query(call.id, 'انتهت صلاحية البحث. ابدأ من جديد.', show_alert=True)
        return

    _, kind, selected = call.data.split(':', 2)
    records = state.get('records', [])
    if kind == 'ad_type' and state['step'] == 'ad_type':
        case_types = available_case_types(records)
        if not selected.isdigit() or int(selected) >= len(case_types):
            bot.answer_callback_query(
                call.id,
                'نوع القضية المحدد غير موجود في سجل القضايا.',
                show_alert=True,
            )
            return
        case_type = case_types[int(selected)]
        filtered_records = [
            record for record in records
            if case_type_matches(record.get('نوع القضية'), case_type)
        ]
        if not filtered_records:
            bot.answer_callback_query(
                call.id,
                'لا توجد قضايا في هذا النوع.',
                show_alert=True,
            )
            return
        judges = available_judges(filtered_records)
        if not judges:
            bot.answer_callback_query(
                call.id,
                'لا توجد أسماء قضاة مسجلة لهذا النوع من القضايا.',
                show_alert=True,
            )
            return
        state.update({
            'step': 'ad_judge',
            'case_type': case_type,
            'records': filtered_records,
            'judges': judges,
        })
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            f'اختر القاضي المختص في قسم {case_type}:',
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=_records_choice_keyboard('ad_judge', judges),
        )
        return

    if kind == 'ad_judge' and state['step'] == 'ad_judge':
        judges = state.get('judges', [])
        if not selected.isdigit() or int(selected) >= len(judges):
            bot.answer_callback_query(
                call.id,
                'اختيار القاضي غير صالح.',
                show_alert=True,
            )
            return
        judge = judges[int(selected)]
        filtered_records = [
            record for record in records
            if normalize_judge_name(record.get('برئاسة القاضي')) == judge
        ]
        state.update({
            'step': 'ad_case_num',
            'judge': judge,
            'records': filtered_records,
        })
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            'أرسل رقم القضية لإنشاء إعلان حضور الجلسة:',
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=_cancel_keyboard(),
        )
        return

    if kind == 'type' and state['step'] == 'type':
        case_types = available_case_types(records)
        if selected == 'all':
            case_type = 'all'
            filtered_records = records
        elif selected.isdigit() and int(selected) < len(case_types):
            case_type = case_types[int(selected)]
            filtered_records = [
                record for record in records
                if case_type_matches(record.get('نوع القضية'), case_type)
            ]
        else:
            bot.answer_callback_query(call.id, 'اختيار القسم غير صالح.', show_alert=True)
            return

        judges = available_judges(filtered_records)
        state.update({
            'step': 'judge',
            'case_type': case_type,
            'judges': judges,
        })
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            'اختر القاضي:',
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=_records_choice_keyboard('judge', judges, include_all=True),
        )
        return

    if kind == 'judge' and state['step'] == 'judge':
        judges = state.get('judges', [])
        if selected == 'all':
            judge = 'all'
        elif selected.isdigit() and int(selected) < len(judges):
            judge = judges[int(selected)]
        else:
            bot.answer_callback_query(call.id, 'اختيار القاضي غير صالح.', show_alert=True)
            return

        state.update({'step': 'method', 'judge': judge})
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                '🔢 الاستعلام برقم القضية',
                callback_data='records:method:number',
            ),
            types.InlineKeyboardButton(
                '👤 الاستعلام باسم المدعي أو المدعى عليه',
                callback_data='records:method:name',
            ),
            types.InlineKeyboardButton(
                '🏠 إلغاء والعودة للقائمة الرئيسية',
                callback_data='records:cancel',
            ),
            types.InlineKeyboardButton(
                '⬅️ العودة لتغيير نوع القضية',
                callback_data='records:back:type',
            ),
        )
        bot.edit_message_text(
            'حدد طريقة البحث:',
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=markup,
        )
        return

    if kind == 'method' and state['step'] == 'method' and selected in {'number', 'name'}:
        state.update({'step': 'query', 'method': selected})
        _save_records_state(chat_id, user_id, state)
        bot.answer_callback_query(call.id)
        prompt = (
            'أرسل رقم القضية للبحث:'
            if selected == 'number'
            else 'أرسل اسم المدعي أو المدعى عليه للبحث:'
        )
        bot.send_message(chat_id, prompt)
        return

    bot.answer_callback_query(call.id, 'هذا الاختيار غير متاح الآن.', show_alert=True)


def _record_caption(record):
    fields = (
        ('رقم القضية', 'رقم القضية'),
        ('نوع القضية', 'نوع القضية'),
        ('موضوعها', 'موضوعها'),
        ('المدعي', 'المدعي'),
        ('المدعى عليه', 'المدعى عليه'),
        ('برئاسة القاضي', 'برئاسة القاضي'),
        ('حالة القضية', 'حالة القضية'),
        ('الجلسة القادمة', 'الجلسة القادمة'),
    )
    return '\n'.join(
        f'{label}: {record.get(key) or "-"}'
        for label, key in fields
    )[:1000]


def process_supreme_court_search(message, state, decision_number=''):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    if user_id is None or search_rate_limiter.is_limited(user_id):
        bot.reply_to(
            message,
            '⏱️ تجاوزت الحد المسموح للاستعلامات (3 طلبات خلال دقيقة). '
            'يرجى الانتظار قبل المحاولة مرة أخرى.',
        )
        return
    try:
        rows = search_supreme_court_cases(
            state.get('name', ''),
            state.get('case_type', '0'),
            state.get('authority', '0'),
            decision_number,
        )
        pdf = generate_search_report_pdf('نتائج المحكمة العليا', rows)
        pdf.name = 'نتائج_المحكمة_العليا.pdf'
        bot.send_document(
            message.chat.id,
            pdf,
            caption='⚖️ نتائج البحث في المحكمة العليا',
        )
        _clear_records_state(message.chat.id, user_id)
        bot.send_message(
            message.chat.id,
            '✅ انتهى البحث.',
            reply_markup=_back_to_main_menu_keyboard(),
        )
    except CaseRecordsError as exc:
        logging.warning('Could not search Supreme Court cases (%s)', str(exc))
        bot.reply_to(message, str(exc))


def _supreme_cancel_button():
    return types.InlineKeyboardButton(
        '❌ إلغاء والعودة للقائمة الرئيسية',
        callback_data='flow:cancel',
    )


def handle_records_query(message, state):
    if state['step'] == 'supreme_name':
        name = (message.text or '').strip()
        if not name:
            bot.reply_to(message, 'أدخل اسم الطاعن/الملتمس للبحث.')
            return
        state.update({
            'name': name,
            'case_type': '0',
            'authority': '0',
        })
        _save_records_state(message.chat.id, message.from_user.id, state)
        process_supreme_court_search(message, state)
        return

    if state['step'] == 'direct_query':
        query = (message.text or '').strip()
        if not query:
            bot.reply_to(message, 'أرسل رقم القضية أو اسم أحد أطرافها للبحث.')
            return
        user_id = getattr(getattr(message, 'from_user', None), 'id', None)
        if user_id is None or search_rate_limiter.is_limited(user_id):
            bot.reply_to(
                message,
                '⏱️ تجاوزت الحد المسموح للاستعلامات (3 طلبات خلال دقيقة). '
                'يرجى الانتظار قبل المحاولة مرة أخرى.',
            )
            return
        method = 'number' if '/' in query else 'name'
        matches = search_case_records(
            state.get('records', []),
            query,
            method,
        )
        _clear_records_state(message.chat.id, message.from_user.id)
        if not matches:
            bot.reply_to(message, '❌ لم يتم العثور على القضية.')
            return
        _send_case_records(message, matches)
        return

    if state['step'] == 'ad_case_num':
        case_number = (message.text or '').strip()
        matches = search_case_records(
            state.get('records', []),
            case_number,
            'number',
            limit=1,
        )
        matching_record = matches[0] if matches else None
        if matching_record is None:
            bot.reply_to(
                message,
                '❌ لم أجد القضية بهذا الرقم في السجل. أرسل رقم قضية صحيحاً أو ألغِ العملية.',
                reply_markup=_choice_keyboard([]),
            )
            return
        case_number = matching_record.get('رقم القضية', case_number)
        state.update({'step': 'ad_date', 'case_number': case_number, 'record': matching_record})
        _save_records_state(message.chat.id, message.from_user.id, state)
        bot.reply_to(
            message,
            'أرسل تاريخ الجلسة بصيغة YYYY-MM-DD أو YYYY/MM/DD:',
            reply_markup=_cancel_keyboard(),
        )
        return

    if state['step'] == 'ad_date':
        date_text = (message.text or '').strip()
        session_date = parse_sitting_date(date_text)
        if session_date is None:
            bot.reply_to(message, '⚠️ التاريخ غير صحيح. أرسل YYYY-MM-DD أو YYYY/MM/DD.')
            return

        record = state['record']
        _clear_records_state(message.chat.id, message.from_user.id)
        try:
            pdf = generate_attendance_ad_pdf(
                state['case_number'],
                record.get('المدعي') or 'غير متوفر',
                record.get('المدعى عليه') or 'غير متوفر',
                session_date,
                record.get('برئاسة القاضي') or 'غير متوفر',
            )
            pdf.name = f"اعلان_{state['case_number'].replace('/', '_')}.pdf"
            bot.send_document(
                message.chat.id,
                pdf,
                caption=f"📢 إعلان حضور جلسة القضية رقم {state['case_number']}",
            )
            bot.send_message(
                message.chat.id,
                '✅ تم إنشاء الإعلان بنجاح.',
                reply_markup=_back_to_main_menu_keyboard(),
            )
        except CaseRecordsError as exc:
            logging.warning('Could not create attendance notice (%s)', str(exc))
            bot.reply_to(message, str(exc))
        return

    if state['step'] != 'query':
        bot.reply_to(message, 'استخدم أزرار البحث للمتابعة أو ابدأ من جديد.')
        return

    query = (message.text or '').strip()
    if not query:
        bot.reply_to(message, 'أرسل كلمة أو رقم القضية للبحث.')
        return
    matches = search_case_records(
        state.get('records', []),
        query,
        state['method'],
        state.get('case_type', 'all'),
        state.get('judge', 'all'),
    )
    _clear_records_state(message.chat.id, message.from_user.id)
    if not matches:
        bot.reply_to(message, '❌ لم يتم العثور على قضايا تطابق البحث والخيارات المحددة.')
        return

    _send_case_records(message, matches)


def _send_case_records(message, matches):
    for record in matches:
        try:
            pdf = generate_case_pdf(record)
            number = (record.get('رقم القضية') or 'قضية').replace('/', '_')
            pdf.name = f'القضية_{number}.pdf'
            bot.send_document(
                message.chat.id,
                pdf,
                caption=_record_caption(record),
            )
        except CaseRecordsError as exc:
            logging.warning('Could not create case record PDF (%s)', str(exc))
            bot.reply_to(message, str(exc))
            return
    bot.send_message(
        message.chat.id,
        '✅ انتهى عرض نتائج البحث.',
        reply_markup=_back_to_main_menu_keyboard(),
    )


def begin_attendance_ad(message, user_id=None):
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        records = load_case_records()
    except CaseRecordsError as exc:
        bot.reply_to(message, str(exc))
        return
    if not records:
        bot.reply_to(message, '⚠️ سجل القضايا فارغ حالياً.')
        return
    case_types = available_case_types(records)
    if not case_types:
        bot.reply_to(message, '⚠️ لا توجد أنواع قضايا متاحة لإنشاء الإعلان.')
        return
    _save_records_state(
        message.chat.id,
        user_id if user_id is not None else message.from_user.id,
        {'step': 'ad_type', 'records': records},
    )
    bot.reply_to(
        message,
        'اختر نوع القضية لإنشاء الإعلان:',
        reply_markup=_records_choice_keyboard('ad_type', case_types),
    )


def _choice_keyboard(choices, include_cancel=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if include_cancel:
        markup.add(types.InlineKeyboardButton(
            '❌ إلغاء والعودة للقائمة الرئيسية',
            callback_data='flow:cancel',
        ))
    return markup


def begin_case_search(message, user_id=None):
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        records = load_case_records()
    except CaseRecordsError as exc:
        bot.reply_to(message, str(exc))
        return

    _save_records_state(
        message.chat.id,
        user_id if user_id is not None else message.from_user.id,
        {'step': 'direct_query', 'records': records},
    )
    bot.reply_to(
        message,
        'أرسل رقم القضية أو اسم المدعي أو المدعى عليه للبحث:',
        reply_markup=_choice_keyboard([]),
    )


def begin_supreme_court_search(message, user_id=None):
    if user_id is None:
        user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    if user_id is None:
        bot.reply_to(message, 'تعذر تحديد المستخدم. ابدأ البحث من جديد.')
        return
    _save_records_state(
        message.chat.id,
        user_id,
        {'step': 'supreme_name'},
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(_supreme_cancel_button())
    bot.reply_to(
        message,
        'أدخل اسم الطاعن/الملتمس:',
        reply_markup=markup,
    )


def begin_daily_sessions_search(message, user_id=None):
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        records = load_case_records()
    except CaseRecordsError as exc:
        bot.reply_to(message, str(exc))
        return

    judges = available_judges(records)
    if not records or not _save_sessions_state(
        message,
        {'step': 'judge', 'records': records, 'judges': judges},
        user_id=user_id,
    ):
        bot.reply_to(message, '⚠️ لا تتوفر سجلات صالحة حالياً.')
        return

    bot.reply_to(
        message,
        'اختر القاضي لعرض جلساته، أو اختر جميع القضاة:',
        reply_markup=_sessions_judge_keyboard(judges),
    )


def handle_sessions_judge_callback(call):
    state = _get_sessions_state(call.message, user_id=call.from_user.id)
    if state is None or state.get('step') != 'judge':
        bot.answer_callback_query(
            call.id,
            'انتهت صلاحية الاختيار. ابدأ الاستعلام من جديد.',
            show_alert=True,
        )
        return

    selected = call.data.split(':', 2)[2]
    if selected == 'all':
        judge = None
    elif selected.isdigit() and int(selected) < len(state.get('judges', [])):
        judge = state['judges'][int(selected)]
    else:
        bot.answer_callback_query(call.id, 'اختيار القاضي غير صالح.', show_alert=True)
        return

    state.update({'step': 'date', 'judge': judge})
    if not _save_sessions_state(call.message, state, user_id=call.from_user.id):
        bot.answer_callback_query(call.id, 'تعذر حفظ الاختيار. ابدأ من جديد.', show_alert=True)
        return

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        'أدخل تاريخ الجلسة بصيغة YYYY-MM-DD، أو أرسل «اليوم» '
        f'للبحث بتاريخ {utc_today()}:',
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=_cancel_keyboard(),
    )


@bot.callback_query_handler(
    func=lambda call: (
        bool(getattr(call, 'data', None))
        and call.data.startswith('sessions:judge:')
    )
)
def sessions_callback_handler(call):
    handle_sessions_judge_callback(call)


def handle_sessions_search_step(message, state):
    text = getattr(message, 'text', '') or ''
    sitting_date = parse_sitting_date(text)
    if sitting_date is None:
        bot.reply_to(
            message,
            '⚠️ التاريخ غير صحيح. استخدم YYYY-MM-DD، مثل 2026-10-21، أو أرسل «اليوم».',
        )
        return
    _clear_sessions_state(message)
    process_daily_sessions_search(
        message,
        state['records'],
        sitting_date,
        judge=state.get('judge'),
    )


def process_daily_sessions_search(message, records, sitting_date, judge=None):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    if user_id is None or sessions_rate_limiter.is_limited(user_id):
        bot.reply_to(
            message,
            '⏱️ تجاوزت الحد المسموح للاستعلامات (3 طلبات خلال دقيقة). '
            'يرجى الانتظار قبل المحاولة مرة أخرى.',
        )
        return

    bot.send_chat_action(message.chat.id, 'typing')
    sessions = search_sessions_by_date(records, sitting_date)
    if judge is not None:
        sessions = [
            record for record in sessions
            if normalize_judge_name(record.get('برئاسة القاضي')) == judge
        ]
    if not sessions:
        scope = f' للقاضي {judge}' if judge is not None else ''
        bot.reply_to(
            message,
            f'❌ لا توجد جلسات مسجلة بتاريخ {sitting_date}{scope}.',
        )
        return

    try:
        pdf = generate_sessions_pdf(
            sessions,
            sitting_date,
        )
        pdf.name = f'قائمة_الجلسات_{sitting_date}.pdf'
        caption = f'📅 قائمة جلسات يوم {sitting_date}'
        if judge is not None:
            caption += f'\n⚖️ القاضي: {judge}'
        bot.send_document(
            message.chat.id,
            pdf,
            caption=caption,
        )
        bot.send_message(
            message.chat.id,
            '✅ تم عرض قائمة الجلسات.',
            reply_markup=_back_to_main_menu_keyboard(),
        )
    except CaseRecordsError as exc:
        logging.warning('Could not create daily sessions PDF (%s)', str(exc))
        bot.reply_to(message, str(exc))


@bot.callback_query_handler(
    func=lambda call: bool(getattr(call, 'data', None))
    and call.data.startswith('records:')
)
def records_callback_handler(call):
    handle_records_callback(call)


@bot.callback_query_handler(
    func=lambda call: (
        getattr(call, 'data', None) == 'flow:cancel'
        or (
            bool(getattr(call, 'data', None))
            and call.data.startswith('main:')
        )
    )
)
def main_callback_handler(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    callback_data = call.data

    if callback_data == 'main:noop':
        bot.answer_callback_query(call.id)
        return

    if callback_data == 'flow:cancel':
        _clear_records_state(chat_id, user_id)
        _clear_sessions_state(call.message, user_id)
        bot.answer_callback_query(call.id)
        send_welcome(call.message, user_id=user_id)
        return

    _clear_records_state(chat_id, user_id)
    _clear_sessions_state(call.message, user_id)
    bot.answer_callback_query(call.id)
    if callback_data == 'main:case':
        begin_case_search(call.message, user_id=user_id)
    elif callback_data == 'main:sessions':
        begin_daily_sessions_search(call.message, user_id=user_id)
    elif callback_data == 'main:attendance':
        begin_attendance_ad(call.message, user_id=user_id)
    elif callback_data == 'main:supreme':
        begin_supreme_court_search(call.message, user_id=user_id)
    elif callback_data == 'main:all':
        begin_records_search(call.message, user_id=user_id)
    elif callback_data == 'main:info':
        _send_system_info(chat_id)
    elif callback_data.startswith('main:type:'):
        selected = callback_data.split(':', 2)[2]
        try:
            case_types = _ordered_menu_case_types(
                available_case_types(load_case_records())
            )
        except CaseRecordsError as exc:
            bot.send_message(chat_id, str(exc))
            return
        if not selected.isdigit() or int(selected) >= min(len(case_types), 80):
            bot.send_message(chat_id, '⚠️ نوع القضية المحدد غير متوفر.')
            return
        begin_records_search(
            call.message,
            case_type=case_types[int(selected)],
            user_id=user_id,
        )


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message, user_id=None, show_branding=True):
    _save_alert_chat_id(getattr(getattr(message, 'chat', None), 'id', None))
    if user_id is None:
        user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    _clear_records_state(
        message.chat.id,
        user_id,
    )
    _clear_sessions_state(message, user_id)
    try:
        case_types = available_case_types(load_case_records())
    except CaseRecordsError as exc:
        logging.warning('Could not load main-menu case types (%s)', str(exc))
        bot.send_message(message.chat.id, str(exc))
        case_types = []

    markup = _main_menu_keyboard(case_types)

    if show_branding:
        with open(LOGO_STICKER_PATH, 'rb') as sticker:
            bot.send_sticker(message.chat.id, sticker)
    bot.send_message(
        message.chat.id,
        WELCOME_CAPTION if show_branding else WELCOME_PROMPT,
        reply_markup=markup,
    )


@bot.message_handler(func=lambda message: True)
def handle_menu(message):
    text = getattr(message, 'text', None)
    sessions_state = _get_sessions_state(message)
    records_state = _get_records_state(message.chat.id, message.from_user.id)
    try:
        case_types = available_case_types(load_case_records())
    except CaseRecordsError:
        case_types = []

    if text == CASE_CANCEL_BUTTON:
        _clear_sessions_state(message)
        _clear_records_state(message.chat.id, message.from_user.id)
        bot.reply_to(
            message,
            'تم إلغاء الاستعلام. استخدم أزرار القائمة لبدء خدمة أخرى.',
            reply_markup=_main_menu_keyboard(case_types),
        )
        return

    menu_buttons = {
        CASE_SEARCH_BUTTON,
        DAILY_SESSIONS_BUTTON,
        DAILY_SESSIONS_MENU_LABEL,
        RECORDS_SEARCH_BUTTON,
        ALL_CASES_BUTTON,
        ATTENDANCE_AD_BUTTON,
        SUPREME_COURT_BUTTON,
        CASE_TYPES_HEADER,
        OTHER_SERVICES_HEADER,
        'ℹ️ معلومات النظام والملكية',
    } | set(case_types) | set(_menu_case_type_by_label(case_types))
    if records_state is not None and text not in menu_buttons:
        handle_records_query(message, records_state)
        return
    if sessions_state is not None and text not in menu_buttons:
        handle_sessions_search_step(message, sessions_state)
        return
    if sessions_state is not None:
        _clear_sessions_state(message)
    if records_state is not None:
        _clear_records_state(message.chat.id, message.from_user.id)

    if text == 'ℹ️ معلومات النظام والملكية':
        _send_system_info(message.chat.id)
    elif text in {DAILY_SESSIONS_BUTTON, DAILY_SESSIONS_MENU_LABEL}:
        begin_daily_sessions_search(message)
    elif text == CASE_SEARCH_BUTTON:
        begin_case_search(message)
    elif text == SUPREME_COURT_BUTTON:
        begin_supreme_court_search(message)
    elif text in {RECORDS_SEARCH_BUTTON, ALL_CASES_BUTTON}:
        begin_records_search(message, case_type='all')
    elif text in _menu_case_type_by_label(case_types):
        begin_records_search(
            message,
            case_type=_menu_case_type_by_label(case_types)[text],
        )
    elif text in case_types:
        begin_records_search(message, case_type=text)
    elif text == ATTENDANCE_AD_BUTTON:
        begin_attendance_ad(message)
    else:
        bot.reply_to(message, 'الرجاء استخدام الأزرار المضمنة داخل المحادثة.')


if __name__ == '__main__':
    logging.info('🚀 بوت محكمة البيضاء المرتبط بسجل القضايا CSV يعمل الآن...')
    _notify_owner('✅ تم تشغيل بوت محكمة البيضاء وهو يعمل الآن.')
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
