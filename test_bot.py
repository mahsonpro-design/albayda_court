import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['TELEGRAM_BOT_TOKEN'] = '123456789:unit-test-token'
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bot as court_bot


class ParseCaseQueryTests(unittest.TestCase):
    def test_parses_ascii_digits_and_surrounding_whitespace(self):
        self.assertEqual(court_bot.parse_case_query(' 150 / 1445 '), ('150', '1445'))

    def test_normalizes_arabic_indic_digits(self):
        self.assertEqual(court_bot.parse_case_query('١٠١/١٤٤٦'), ('101', '1446'))

    def test_rejects_malformed_values(self):
        for value in (
            'abc/1446',
            '101/14a6',
            '١a١/١٤٤٦',
            '1011446',
            '101/1446/1',
            '/1446',
            '101/',
            '12345678901/1446',
            '101/144',
            '101/14455',
            '0/1446',
            '101/0000',
        ):
            with self.subTest(value=value):
                self.assertIsNone(court_bot.parse_case_query(value))


class LocalCsvFlowTests(unittest.TestCase):
    def setUp(self):
        court_bot.records_search_states.clear()
        court_bot.sessions_search_states.clear()

    def test_parse_sitting_date_accepts_today_and_arabic_digits(self):
        with patch.object(court_bot, 'utc_today', return_value='2026-10-21'):
            self.assertEqual(court_bot.parse_sitting_date('اليوم'), '2026-10-21')
        self.assertEqual(
            court_bot.parse_sitting_date('٢١/١٠/٢٠٢٦'),
            '2026-10-21',
        )
        self.assertEqual(
            court_bot.parse_sitting_date('2026-10-21'),
            '2026-10-21',
        )
        self.assertIsNone(court_bot.parse_sitting_date('2026-13-40'))

    @patch.object(court_bot.bot, 'reply_to')
    def test_case_search_loads_local_csv(self, reply_to):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        records = [{'رقم القضية': '1446/6'}]

        with (
            patch.object(court_bot, 'load_case_records', return_value=records),
            patch.object(court_bot.bot, 'send_chat_action'),
        ):
            court_bot.begin_case_search(message)

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'direct_query')
        self.assertIs(state['records'], records)
        self.assertIn('رقم القضية', reply_to.call_args.args[1])
        self.assertNotIn('CSV', reply_to.call_args.args[1])
        self.assertIsInstance(
            reply_to.call_args.kwargs['reply_markup'],
            court_bot.types.InlineKeyboardMarkup,
        )

    @patch.object(court_bot.bot, 'send_chat_action')
    @patch.object(court_bot.bot, 'reply_to')
    def test_attendance_ad_asks_for_case_type_before_case_number(
        self,
        reply_to,
        send_chat_action,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'مدنية', 'رقم القضية': '1446/1'},
            {'نوع القضية': 'شخصية', 'رقم القضية': '1446/2'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.begin_attendance_ad(message)

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'ad_type')
        self.assertEqual(state['records'], records)
        self.assertEqual(
            reply_to.call_args.args[1],
            'اختر نوع القضية لإنشاء الإعلان:',
        )
        button_texts = [
            button.text
            for row in reply_to.call_args.kwargs['reply_markup'].keyboard
            for button in row
        ]
        self.assertEqual(
            button_texts,
            ['مدنيــــة', 'شخصيـــة', '🏠 إلغاء والعودة للقائمة الرئيسية'],
        )

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot.bot, 'edit_message_text')
    def test_attendance_ad_case_type_opens_filtered_judge_selection(
        self,
        edit_message_text,
        answer_callback_query,
    ):
        records = [
            {
                'نوع القضية': 'مدنية',
                'رقم القضية': '1446/1',
                'برئاسة القاضي': 'القاضي الأول',
            },
            {
                'نوع القضية': 'مدنية',
                'رقم القضية': '1446/3',
                'برئاسة القاضي': 'القاضي الثاني',
            },
            {
                'نوع القضية': 'شخصية',
                'رقم القضية': '1446/2',
                'برئاسة القاضي': 'قاضي الأحوال',
            },
        ]
        court_bot._save_records_state(
            123,
            42,
            {'step': 'ad_type', 'records': records},
        )
        call = SimpleNamespace(
            id='callback-id',
            data='records:ad_type:0',
            message=SimpleNamespace(
                chat=SimpleNamespace(id=123),
                message_id=456,
            ),
            from_user=SimpleNamespace(id=42),
        )

        court_bot.handle_records_callback(call)

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'ad_judge')
        self.assertEqual(state['case_type'], 'مدنيــــة')
        self.assertEqual(
            state['records'],
            records[:2],
        )
        self.assertEqual(state['judges'], ['القاضي الأول', 'القاضي الثاني'])
        self.assertIn('اختر القاضي المختص', edit_message_text.call_args.args[0])
        markup = edit_message_text.call_args.kwargs['reply_markup']
        button_texts = [
            button.text
            for row in markup.keyboard
            for button in row
        ]
        self.assertIn('⚖️القاضي الأول', button_texts)
        self.assertIn('⚖️القاضي الثاني', button_texts)
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot.bot, 'edit_message_text')
    def test_attendance_ad_judge_filters_cases_before_case_number(
        self,
        edit_message_text,
        answer_callback_query,
    ):
        records = [
            {'رقم القضية': '1446/1', 'برئاسة القاضي': 'القاضي الأول'},
            {'رقم القضية': '1446/3', 'برئاسة القاضي': 'القاضي الثاني'},
        ]
        court_bot._save_records_state(
            123,
            42,
            {
                'step': 'ad_judge',
                'records': records,
                'judges': ['القاضي الأول', 'القاضي الثاني'],
            },
        )
        call = SimpleNamespace(
            id='judge-callback',
            data='records:ad_judge:1',
            message=SimpleNamespace(
                chat=SimpleNamespace(id=123),
                message_id=456,
            ),
            from_user=SimpleNamespace(id=42),
        )

        court_bot.handle_records_callback(call)

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'ad_case_num')
        self.assertEqual(state['judge'], 'القاضي الثاني')
        self.assertEqual(state['records'], [records[1]])
        self.assertIn('رقم القضية', edit_message_text.call_args.args[0])
        answer_callback_query.assert_called_once_with('judge-callback')

    @patch.object(court_bot.bot, 'reply_to')
    def test_daily_sessions_search_loads_local_csv_and_requests_judge(self, reply_to):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'رقم القضية': '1446/6', 'برئاسة القاضي': 'القاضي الأول'},
            {'رقم القضية': '1446/7', 'برئاسة القاضي': 'القاضي الثاني'},
        ]

        with (
            patch.object(court_bot, 'load_case_records', return_value=records),
            patch.object(court_bot.bot, 'send_chat_action'),
            patch.object(court_bot, 'utc_today', return_value='2026-10-21'),
        ):
            court_bot.begin_daily_sessions_search(message)

        state = court_bot._get_sessions_state(message)
        self.assertEqual(state['step'], 'judge')
        self.assertIs(state['records'], records)
        self.assertEqual(state['judges'], ['القاضي الأول', 'القاضي الثاني'])
        keyboard = reply_to.call_args.kwargs['reply_markup']
        self.assertEqual(
            [button.text for row in keyboard.keyboard for button in row],
            [
                'جميع القضاة',
                'القاضي الأول',
                'القاضي الثاني',
                '❌ إلغاء والعودة للقائمة الرئيسية',
            ],
        )
        self.assertEqual(keyboard.keyboard[0][0].callback_data, 'sessions:judge:all')

    @patch.object(court_bot.bot, 'edit_message_text')
    @patch.object(court_bot.bot, 'answer_callback_query')
    def test_daily_sessions_judge_selection_advances_to_date(
        self,
        answer_callback_query,
        edit_message_text,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=99),
            message_id=456,
        )
        call = SimpleNamespace(
            id='judge-callback',
            data='sessions:judge:1',
            message=message,
            from_user=SimpleNamespace(id=42),
        )
        court_bot._save_sessions_state(
            message,
            {
                'step': 'judge',
                'records': [{'برئاسة القاضي': 'القاضي الثاني'}],
                'judges': ['القاضي الأول', 'القاضي الثاني'],
            },
            user_id=42,
        )

        with patch.object(court_bot, 'utc_today', return_value='2026-10-21'):
            court_bot.handle_sessions_judge_callback(call)

        state = court_bot._get_sessions_state(message, user_id=42)
        self.assertEqual(state['step'], 'date')
        self.assertEqual(state['judge'], 'القاضي الثاني')
        self.assertEqual(
            edit_message_text.call_args.args[0],
            'أدخل تاريخ الجلسة بصيغة YYYY-MM-DD، أو أرسل «اليوم» '
            'للبحث بتاريخ 2026-10-21:',
        )
        answer_callback_query.assert_called_once_with('judge-callback')

    def test_daily_sessions_search_sends_the_official_pdf_template(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        sessions = [
            {
                'رقم القضية': f'1446/{case_number}',
                'الجلسة القادمة': '2026-10-21',
            }
            for case_number in range(1, 102)
        ]
        pdf = SimpleNamespace(name=None)

        with (
            patch.object(court_bot, 'generate_sessions_pdf', return_value=pdf) as create_pdf,
            patch.object(court_bot.bot, 'send_document') as send_document,
            patch.object(court_bot.bot, 'send_chat_action'),
            patch.object(court_bot.bot, 'reply_to'),
            patch.object(court_bot.sessions_rate_limiter, 'is_limited', return_value=False),
            patch.object(court_bot, 'send_welcome') as send_welcome,
        ):
            court_bot.process_daily_sessions_search(
                message,
                sessions,
                '2026-10-21',
            )

        create_pdf.assert_called_once_with(sessions, '2026-10-21')
        self.assertEqual(pdf.name, 'قائمة_الجلسات_2026-10-21.pdf')
        send_document.assert_called_once()
        self.assertEqual(
            send_document.call_args.kwargs['caption'],
            '📅 قائمة جلسات يوم 2026-10-21',
        )
        send_welcome.assert_called_once_with(message, show_branding=False)

    def test_daily_sessions_report_filters_by_selected_judge(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        sessions = [
            {
                'رقم القضية': '1446/6',
                'برئاسة القاضي': 'القاضي الأول',
                'الجلسة القادمة': '2026-10-21',
            },
            {
                'رقم القضية': '1446/7',
                'برئاسة القاضي': 'القاضي الثاني',
                'الجلسة القادمة': '2026-10-21',
            },
        ]

        with (
            patch.object(court_bot, 'generate_sessions_pdf', return_value=SimpleNamespace(name=None)) as create_pdf,
            patch.object(court_bot.bot, 'send_document') as send_document,
            patch.object(court_bot.bot, 'send_chat_action'),
            patch.object(court_bot.sessions_rate_limiter, 'is_limited', return_value=False),
            patch.object(court_bot, 'send_welcome'),
        ):
            court_bot.process_daily_sessions_search(
                message,
                sessions,
                '2026-10-21',
                judge='القاضي الثاني',
            )

        create_pdf.assert_called_once_with([sessions[1]], '2026-10-21')
        self.assertIn('القاضي الثاني', send_document.call_args.kwargs['caption'])

    def test_daily_sessions_judge_selection_includes_alias_records(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        sessions = [
            {
                'رقم القضية': '1446/6',
                'برئاسة القاضي': 'عبدالعزيز ناصر أحمد الهصيصي',
                'الجلسة القادمة': '2026-10-21',
            },
            {
                'رقم القضية': '1446/7',
                'برئاسة القاضي': 'عبدالعزيز ناصر الهصيصي',
                'الجلسة القادمة': '2026-10-21',
            },
        ]

        with (
            patch.object(court_bot, 'generate_sessions_pdf', return_value=SimpleNamespace(name=None)) as create_pdf,
            patch.object(court_bot.bot, 'send_document') as send_document,
            patch.object(court_bot.bot, 'send_chat_action'),
            patch.object(court_bot.sessions_rate_limiter, 'is_limited', return_value=False),
            patch.object(court_bot, 'send_welcome'),
        ):
            court_bot.process_daily_sessions_search(
                message,
                sessions,
                '2026-10-21',
                judge='عبدالعزيز ناصر الهصيصي',
            )

        create_pdf.assert_called_once_with(sessions, '2026-10-21')
        self.assertIn(
            'عبدالعزيز ناصر الهصيصي',
            send_document.call_args.kwargs['caption'],
        )

    def test_case_report_returns_to_the_main_menu_after_all_documents(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        matches = [
            {'رقم القضية': '1446/6'},
            {'رقم القضية': '1446/7'},
        ]

        with (
            patch.object(court_bot, 'generate_case_pdf', side_effect=[
                SimpleNamespace(name=None),
                SimpleNamespace(name=None),
            ]),
            patch.object(court_bot.bot, 'send_document') as send_document,
            patch.object(court_bot, 'send_welcome') as send_welcome,
        ):
            court_bot._send_case_records(message, matches)

        self.assertEqual(send_document.call_count, 2)
        send_welcome.assert_called_once_with(message, show_branding=False)

    @patch.object(court_bot.bot, 'edit_message_text')
    @patch.object(court_bot.bot, 'reply_to', return_value=SimpleNamespace(message_id=1))
    @patch.object(court_bot.bot, 'send_chat_action')
    def test_case_category_opens_judge_menu_filtered_to_category(
        self,
        send_chat_action,
        reply_to,
        edit_message_text,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'مدني', 'برئاسة القاضي': 'القاضي الأول'},
            {'نوع القضية': 'مدني', 'برئاسة القاضي': 'القاضي الأول'},
            {'نوع القضية': 'جزائي', 'برئاسة القاضي': 'القاضي الثاني'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.begin_records_search(message, case_type='مدني')

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'judge')
        self.assertEqual(state['case_type'], 'مدني')
        self.assertEqual(state['judges'], ['القاضي الأول'])
        markup = edit_message_text.call_args.kwargs['reply_markup']
        button_texts = [
            button.text
            for row in markup.keyboard
            for button in row
        ]
        self.assertIn('⚖️القاضي الأول', button_texts)
        self.assertIn('❓ البحث في كافة القضاة', button_texts)

    @patch.object(court_bot.bot, 'send_chat_action')
    @patch.object(court_bot.bot, 'reply_to', return_value=SimpleNamespace(message_id=1))
    @patch.object(court_bot.bot, 'edit_message_text')
    def test_selecting_a_display_case_type_filters_its_source_records(
        self,
        edit_message_text,
        reply_to,
        send_chat_action,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'تعرض للانحراف', 'برئاسة القاضي': 'قاضي الأحداث'},
            {'نوع القضية': 'جسيمة', 'برئاسة القاضي': 'قاضي الجنايات'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.begin_records_search(
                message,
                case_type='جزائية (أحداث)',
            )

        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['case_type'], 'جزائية (أحداث)')
        self.assertEqual(state['judges'], ['قاضي الأحداث'])


class WelcomeMessageTests(unittest.TestCase):
    @patch.object(court_bot.bot, 'send_message')
    @patch.object(court_bot.bot, 'send_sticker')
    def test_return_menu_sends_only_prompt_and_keyboard(
        self,
        send_sticker,
        send_message,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )

        with patch.object(court_bot, 'load_case_records', return_value=[]):
            court_bot.send_welcome(message, show_branding=False)

        send_sticker.assert_not_called()
        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args, (123, 'اختر الخدمة المطلوبة:'))
        self.assertIsInstance(
            send_message.call_args.kwargs['reply_markup'],
            court_bot.types.InlineKeyboardMarkup,
        )

    @patch.object(court_bot.bot, 'send_message')
    @patch.object(court_bot.bot, 'send_sticker')
    def test_sends_logo_sticker_and_welcome_text_with_ordered_case_menu(
        self,
        send_sticker,
        send_message,
    ):
        message = SimpleNamespace(chat=SimpleNamespace(id=123))
        records = [
            {'نوع القضية': 'تعرض للانحراف'},
            {'نوع القضية': 'جسيمة'},
            {'نوع القضية': 'غير جسيمة'},
            {'نوع القضية': 'مدنية'},
            {'نوع القضية': 'شخصية'},
            {'نوع القضية': 'مستعجل'},
            {'نوع القضية': 'تنفيذ'},
            {'نوع القضية': 'تنفيذية (منازعات)'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.send_welcome(message)

        send_sticker.assert_called_once()
        self.assertEqual(send_sticker.call_args.args[0], 123)
        self.assertEqual(
            os.path.abspath(send_sticker.call_args.args[1].name),
            court_bot.LOGO_STICKER_PATH,
        )
        send_message.assert_called_once()
        args, kwargs = send_message.call_args
        self.assertEqual(args[0], 123)
        self.assertEqual(args[1], court_bot.WELCOME_CAPTION)
        self.assertEqual(
            court_bot.WELCOME_CAPTION,
            'تقنية معلومات محكمة البيضاء الابتدائية للاستعلام الالكتروني عن '
            'القضايا والجلسات وانشاء الاعلانات القضائية\n'
            'اختر الخدمة المطلوبة:',
        )
        self.assertEqual(
            [
                [button.text for button in row]
                for row in kwargs['reply_markup'].keyboard
            ],
            [
                [court_bot.CASE_TYPES_HEADER],
                ['👥 شخصيـــة', '🏛️ مدنيـــة'],
                ['⚖️ جزائية (جسيمة)', '⚖️ جزائية (غير جسيمة)'],
                ['🏛️ مدنية (مستعجلة)', '📑 تنفيذية (منازعات)'],
                ['🧑\u200d🧒\u200d🧒 جزائية (أحداث)'],
                [court_bot.CASE_SEARCH_BUTTON, court_bot.ALL_CASES_BUTTON],
                [court_bot.OTHER_SERVICES_HEADER],
                [
                    court_bot.ATTENDANCE_AD_BUTTON,
                    court_bot.DAILY_SESSIONS_MENU_LABEL,
                ],
                [court_bot.SUPREME_COURT_BUTTON],
                ['ℹ️ معلومات النظام والملكية'],
            ],
        )
        markup = kwargs['reply_markup']
        self.assertEqual(markup.keyboard[0][0].style, 'primary')
        self.assertEqual(markup.keyboard[1][0].style, 'success')
        self.assertEqual(markup.keyboard[8][0].style, 'primary')
        self.assertEqual(
            markup.keyboard[1][0].callback_data,
            'main:type:0',
        )

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot, 'begin_records_search')
    def test_case_type_button_uses_the_displayed_menu_order(
        self,
        begin_records_search,
        answer_callback_query,
    ):
        call = SimpleNamespace(
            id='callback-id',
            data='main:type:0',
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'مدنية'},
            {'نوع القضية': 'شخصية'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.main_callback_handler(call)

        begin_records_search.assert_called_once_with(
            call.message,
            case_type='شخصيـــة',
            user_id=42,
        )
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot, 'begin_records_search')
    def test_urgent_civil_button_filters_urgent_cases(
        self,
        begin_records_search,
        answer_callback_query,
    ):
        call = SimpleNamespace(
            id='callback-id',
            data='main:type:0',
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'مستعجل'},
            {'نوع القضية': 'تنفيذ'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.main_callback_handler(call)

        self.assertEqual(begin_records_search.call_args.kwargs['case_type'], 'مدنية (مستعجلة)')
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot, 'begin_records_search')
    def test_execution_button_filters_records_named_execution(
        self,
        begin_records_search,
        answer_callback_query,
    ):
        call = SimpleNamespace(
            id='callback-id',
            data='main:type:1',
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            from_user=SimpleNamespace(id=42),
        )
        records = [
            {'نوع القضية': 'تنفيذ'},
            {'نوع القضية': 'مدنية'},
        ]

        with patch.object(court_bot, 'load_case_records', return_value=records):
            court_bot.main_callback_handler(call)

        self.assertEqual(
            begin_records_search.call_args.kwargs['case_type'],
            'قضايا التنفيذ (منازعات)',
        )
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot.bot, 'send_message')
    @patch.object(court_bot.bot, 'send_sticker')
    def test_system_info_message_includes_profile_photo_and_contact_links(
        self,
        send_sticker,
        send_message,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
            text='ℹ️ معلومات النظام والملكية',
        )
        with patch.object(court_bot, 'load_case_records', return_value=[]):
            court_bot.handle_menu(message)

        send_sticker.assert_called_once()
        self.assertEqual(send_sticker.call_args.args[0], 123)
        self.assertEqual(
            os.path.abspath(send_sticker.call_args.args[1].name),
            court_bot.INFO_STICKER_PATH,
        )
        send_message.assert_called_once()
        args, kwargs = send_message.call_args
        self.assertEqual(args[0], 123)
        self.assertIn('⚙️ النظام الإلكتروني المطور للاستعلام', args[1])
        self.assertIn('https://wa.me/qr/QIQRW7LA325GG1', args[1])
        self.assertIn('t.me/Ahmed_Mahson', args[1])
        self.assertIsInstance(
            kwargs['reply_markup'],
            court_bot.types.InlineKeyboardMarkup,
        )

    @patch.object(court_bot.bot, 'send_message')
    @patch.object(court_bot.bot, 'send_sticker')
    @patch.object(court_bot.bot, 'answer_callback_query')
    def test_system_info_inline_button_sends_profile_photo(
        self,
        answer_callback_query,
        send_sticker,
        send_message,
    ):
        call = SimpleNamespace(
            id='callback-id',
            data='main:info',
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            from_user=SimpleNamespace(id=42),
        )

        court_bot.main_callback_handler(call)

        send_sticker.assert_called_once()
        self.assertEqual(send_sticker.call_args.args[0], 123)
        self.assertEqual(
            os.path.abspath(send_sticker.call_args.args[1].name),
            court_bot.INFO_STICKER_PATH,
        )
        send_message.assert_called_once()
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot.bot, 'answer_callback_query')
    @patch.object(court_bot, 'begin_supreme_court_search')
    def test_supreme_court_button_starts_supreme_court_search(
        self,
        begin_supreme_court_search,
        answer_callback_query,
    ):
        call = SimpleNamespace(
            id='callback-id',
            data='main:supreme',
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            from_user=SimpleNamespace(id=42),
        )

        court_bot.main_callback_handler(call)

        begin_supreme_court_search.assert_called_once_with(call.message, user_id=42)
        answer_callback_query.assert_called_once_with('callback-id')

    @patch.object(court_bot, 'search_supreme_court_cases', return_value=[
        ('الطاعن', 'أحمد'),
        ('المطعون ضده', 'محمد'),
        ('نوع القضية', 'طعن'),
        ('تاريخ الاستلام', '2025/01/02'),
        ('الرقم الخاص', '1446/6'),
        ('تاريخ الإرسال', '2025/01/03'),
    ])
    @patch.object(court_bot, 'generate_search_report_pdf', return_value=BytesIO())
    @patch.object(court_bot.search_rate_limiter, 'is_limited', return_value=False)
    @patch.object(court_bot.bot, 'send_document')
    @patch.object(court_bot.bot, 'reply_to')
    @patch.object(court_bot, 'send_welcome')
    def test_supreme_court_search_uses_all_defaults_and_returns_report(
        self,
        send_welcome,
        reply_to,
        send_document,
        is_limited,
        generate_report,
        search,
    ):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=42),
        )
        court_bot.begin_supreme_court_search(message)
        state = court_bot._get_records_state(123, 42)
        self.assertEqual(state['step'], 'supreme_name')
        self.assertEqual(
            reply_to.call_args.args[1],
            'أدخل اسم الطاعن/الملتمس:',
        )
        self.assertEqual(
            len(reply_to.call_args.kwargs['reply_markup'].keyboard),
            1,
        )

        name_message = SimpleNamespace(
            chat=message.chat,
            from_user=message.from_user,
            text='أحمد',
        )
        court_bot.handle_records_query(name_message, state)

        search.assert_called_once_with('أحمد', '0', '0', '')
        generate_report.assert_called_once()
        send_document.assert_called_once()
        self.assertEqual(
            send_document.call_args.args[0],
            message.chat.id,
        )
        self.assertIsNone(court_bot._get_records_state(123, 42))
        send_welcome.assert_called_once_with(
            name_message,
            user_id=42,
            show_branding=False,
        )


class UserRateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.limiter = court_bot.UserRateLimiter(
            limit=3,
            window_seconds=60,
            clock=lambda: self.now[0],
        )

    def test_blocks_requests_after_limit_until_window_expires(self):
        self.assertFalse(self.limiter.is_limited(42))
        self.assertFalse(self.limiter.is_limited(42))
        self.assertFalse(self.limiter.is_limited(42))
        self.assertTrue(self.limiter.is_limited(42))

        self.now[0] += 60
        self.assertFalse(self.limiter.is_limited(42))

    def test_tracks_users_independently(self):
        for _ in range(3):
            self.assertFalse(self.limiter.is_limited(42))
        self.assertTrue(self.limiter.is_limited(42))
        self.assertFalse(self.limiter.is_limited(43))


if __name__ == '__main__':
    unittest.main()
