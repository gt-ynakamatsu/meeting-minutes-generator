from types import SimpleNamespace

from backend import smtp_notify


class _SMTPBase:
    def __init__(self):
        self.logged_in = False
        self.sent = 0
        self.tls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, user, pw):
        self.logged_in = True

    def send_message(self, msg):
        self.sent += 1

    def starttls(self, context=None):
        self.tls += 1


def _set_smtp_env(monkeypatch, **kwargs):
    base = {
        "MM_SMTP_HOST": "smtp.example.com",
        "MM_SMTP_FROM": "noreply@example.com",
        "MM_SMTP_PORT": "587",
        "MM_SMTP_USER": "",
        "MM_SMTP_PASSWORD": "",
        "MM_SMTP_STARTTLS": "1",
    }
    base.update(kwargs)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def test_smtp_configured(monkeypatch):
    monkeypatch.delenv("MM_SMTP_HOST", raising=False)
    monkeypatch.delenv("MM_SMTP_FROM", raising=False)
    assert smtp_notify.smtp_configured() is False
    monkeypatch.setenv("MM_SMTP_HOST", "h")
    monkeypatch.setenv("MM_SMTP_FROM", "f")
    assert smtp_notify.smtp_configured() is True


def test_send_task_completion_email_branches(monkeypatch):
    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: False)
    smtp_notify.send_task_completion_email("to@example.com", "a.wav", "tid")

    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: True)
    smtp_notify.send_task_completion_email("   ", "a.wav", "tid")

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="bad")
    smtp_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj)
    monkeypatch.setattr(smtp_notify.ssl, "create_default_context", lambda: object())
    smtp_notify.send_task_completion_email("to@example.com", "a.wav", "tid")
    assert smtp_obj.sent == 1
    assert smtp_obj.tls == 1
    assert smtp_obj.logged_in is False

    # non-SSL + user あり（login 分岐）
    _set_smtp_env(monkeypatch, MM_SMTP_PORT="587", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_obj2 = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj2)
    smtp_notify.send_task_completion_email("to@example.com", "a.wav", "tid")
    assert smtp_obj2.logged_in is True

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="465", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_ssl_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP_SSL", lambda *a, **k: smtp_ssl_obj)
    smtp_notify.send_task_completion_email("to@example.com", "a.wav", "tid")
    assert smtp_ssl_obj.sent == 1
    assert smtp_ssl_obj.logged_in is True

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="587", MM_SMTP_STARTTLS="0")
    class _FailSMTP(_SMTPBase):
        def send_message(self, msg):
            raise RuntimeError("fail")

    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: _FailSMTP())
    smtp_notify.send_task_completion_email("to@example.com", "a.wav", "tid")  # exception swallow


def test_send_task_failure_email_branches(monkeypatch):
    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: False)
    smtp_notify.send_task_failure_email("to@example.com", "a.wav", "tid", "err")

    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: True)
    smtp_notify.send_task_failure_email("", "a.wav", "tid", "err")

    _set_smtp_env(monkeypatch)
    smtp_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj)
    monkeypatch.setattr(smtp_notify.ssl, "create_default_context", lambda: object())
    smtp_notify.send_task_failure_email("to@example.com", "a.wav", "tid", "x" * 6000)
    assert smtp_obj.sent == 1

    # port parse fallback(79-80) + non-SSL login 分岐(115)
    _set_smtp_env(monkeypatch, MM_SMTP_PORT="bad", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_obj2 = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj2)
    smtp_notify.send_task_failure_email("to@example.com", "a.wav", "tid", "err")
    assert smtp_obj2.logged_in is True

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="465", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_ssl_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP_SSL", lambda *a, **k: smtp_ssl_obj)
    smtp_notify.send_task_failure_email("to@example.com", "a.wav", "tid", "err")
    assert smtp_ssl_obj.sent == 1
    assert smtp_ssl_obj.logged_in is True

    class _FailSMTP(_SMTPBase):
        def send_message(self, msg):
            raise RuntimeError("x")

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="587")
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: _FailSMTP())
    smtp_notify.send_task_failure_email("to@example.com", "a.wav", "tid", "err")


def test_send_plain_email_to_recipients_branches(monkeypatch):
    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: False)
    smtp_notify.send_plain_email_to_recipients(["a@example.com"], "s", "b")

    monkeypatch.setattr(smtp_notify, "smtp_configured", lambda: True)
    smtp_notify.send_plain_email_to_recipients(["", "   "], "s", "b")

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="bad")
    smtp_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj)
    monkeypatch.setattr(smtp_notify.ssl, "create_default_context", lambda: object())
    smtp_notify.send_plain_email_to_recipients(["a@example.com", " b@example.com "], "s" * 2000, "x" * 600000)
    assert smtp_obj.sent == 1

    # non-SSL + user あり（login 分岐 163）
    _set_smtp_env(monkeypatch, MM_SMTP_PORT="587", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_obj2 = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: smtp_obj2)
    smtp_notify.send_plain_email_to_recipients(["a@example.com"], "subject", "body")
    assert smtp_obj2.logged_in is True

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="465", MM_SMTP_USER="u", MM_SMTP_PASSWORD="p")
    smtp_ssl_obj = _SMTPBase()
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP_SSL", lambda *a, **k: smtp_ssl_obj)
    smtp_notify.send_plain_email_to_recipients(["a@example.com"], "subject", "body")
    assert smtp_ssl_obj.sent == 1
    assert smtp_ssl_obj.logged_in is True

    class _FailSMTP(_SMTPBase):
        def send_message(self, msg):
            raise RuntimeError("x")

    _set_smtp_env(monkeypatch, MM_SMTP_PORT="587")
    monkeypatch.setattr(smtp_notify.smtplib, "SMTP", lambda *a, **k: _FailSMTP())
    smtp_notify.send_plain_email_to_recipients(["a@example.com"], "subject", "body")
