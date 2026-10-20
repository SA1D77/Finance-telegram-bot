from types import SimpleNamespace

import pytest

from feedback import errors as feedback_errors
from feedback import handlers as fb
from feedback.handlers import FeedbackState
from telegram import main_handlers

from conftest import USER_ID, make_callback, make_message


def requests_of(harness, name):
    return [r for r in harness.session.requests if type(r).__name__ == name]


async def test_feedback_reaches_the_owner_with_reply_button(harness):
    await harness.feed(make_message('/feedback'))
    assert await harness.state() == FeedbackState.message.state

    await harness.feed(make_message('Не работает экспорт, и хочу тёмную тему'))

    header = [r for r in requests_of(harness, 'SendMessage') if r.text.startswith('📩 Сообщение от')]
    assert header and header[0].chat_id == USER_ID and 'id <code>1001</code>' in header[0].text
    copy = requests_of(harness, 'CopyMessage')[0]
    assert copy.chat_id == USER_ID and copy.reply_markup.inline_keyboard[0][0].callback_data == f'fb:reply:{USER_ID}'
    assert harness.session.texts()[-1] == 'Спасибо! Сообщение отправлено разработчику.'
    assert await harness.state() is None


async def test_feedback_button_in_settings(harness):
    await harness.feed(make_message('⚙️ Настройки'))
    data = [b.callback_data for row in harness.session.requests[-1].reply_markup.inline_keyboard for b in row]
    assert 'feedback' in data

    await harness.feed(make_callback('feedback'))

    assert await harness.state() == FeedbackState.message.state


async def test_too_long_feedback_is_refused(harness):
    await harness.set_state(FeedbackState.message)

    await harness.feed(make_message('я' * 2001))

    assert requests_of(harness, 'CopyMessage') == []
    assert await harness.state() == FeedbackState.message.state


async def test_owner_replies_to_a_user(harness):
    await harness.feed(make_callback('fb:reply:777'))
    assert await harness.state() == FeedbackState.reply.state

    await harness.feed(make_message('Спасибо, починю в выходные'))

    sent = requests_of(harness, 'SendMessage')
    assert any(r.chat_id == 777 and r.text == '📬 Ответ разработчика:' for r in sent)
    assert requests_of(harness, 'CopyMessage')[0].chat_id == 777
    assert harness.session.texts()[-1] == '✅ Ответ отправлен.'


async def test_only_owner_can_start_a_reply(monkeypatch):
    answered = []
    state_calls = []

    class State:
        async def set_state(self, value):
            state_calls.append(value)

        async def update_data(self, **kwargs):
            state_calls.append(kwargs)

    async def answer(*args, **kwargs):
        answered.append(True)

    callback = SimpleNamespace(data='fb:reply:777', from_user=SimpleNamespace(id=2002), answer=answer)
    await fb.reply_start(callback, State())

    assert state_calls == [] and answered == [True]


async def test_cooldown_for_regular_users(monkeypatch):
    monkeypatch.setattr(fb, '_last_sent', {2002: 0.0})
    monkeypatch.setattr(fb.time, 'monotonic', lambda: 30.0)
    answers = []

    async def answer(text, **kwargs):
        answers.append(text)

    message = SimpleNamespace(from_user=SimpleNamespace(id=2002, full_name='X', username=None, language_code='ru'),
                              text='идея', caption=None, photo=None, answer=answer)
    from i18n import i18n
    with i18n.context(), i18n.use_locale('ru'):
        await fb.feedback_message(message, SimpleNamespace())

    assert answers == ['Подождите минуту перед следующим сообщением.']


@pytest.fixture
def broken_balance(monkeypatch):
    monkeypatch.setattr(feedback_errors, '_last_report', {})

    async def boom(user_id):
        raise ZeroDivisionError('secret amount 123456')

    monkeypatch.setattr(main_handlers, '_get_balance', boom)


async def test_error_apologises_and_notifies_owner_once(harness, broken_balance):
    await harness.feed(make_message('📊 Баланс'))
    await harness.feed(make_message('📊 Баланс'))

    texts = harness.session.texts()
    assert texts.count('⚠️ Что-то пошло не так. Разработчик уже получил отчёт, попробуйте ещё раз чуть позже.') == 2
    notices = [t for t in texts if t.startswith('🐞 Ошибка')]
    assert len(notices) == 1  # the same error is reported once per 10 minutes
    assert '<code>ZeroDivisionError</code>' in notices[0] and 'test_feedback.py' in notices[0]  # deepest project frame
    assert '123456' not in notices[0]  # the exception message (may hold user data) is not sent
