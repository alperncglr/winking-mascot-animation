"""Correlate application/dependency logs; redact one-time WebSocket credentials."""
import contextvars
import logging
import re
from contextlib import contextmanager

meeting_context = contextvars.ContextVar('meeting_id', default='-')
session_context = contextvars.ContextVar('session_id', default='-')


@contextmanager
def meeting_log_context(meeting_id, session_id='-'):
    m = meeting_context.set(str(meeting_id))
    s = session_context.set(str(session_id))
    try:
        yield
    finally:
        meeting_context.reset(m)
        session_context.reset(s)


def install_log_context():
    previous = logging.getLogRecordFactory()
    if getattr(previous, '_meeting_context_factory', False):
        return
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        message = record.getMessage()
        message = re.sub(r'(ws_token=)[^\s"&]+', r'\1[REDACTED]', message)
        match = re.search(r'/meetings/(\d+)', message)
        mid = meeting_context.get()
        if mid == '-' and match:
            mid = match.group(1)
        record.meeting_id = mid
        record.session_id = session_context.get()
        if record.name == 'uvicorn.access' and isinstance(record.args, tuple) and len(record.args) == 5:
            # Uvicorn's access formatter unpacks this tuple even when its handler
            # is installed *after* app import. Prefix the client field directly.
            client, method, path, version, status = record.args
            path = re.sub(r'(ws_token=)[^\s"&]+', r'\1[REDACTED]', str(path))
            record.args = (f'meeting_id={mid} session_id={record.session_id} {client}',
                           method, path, version, status)
            return record
        record.msg = f'meeting_id={mid} session_id={session_context.get()} {message}'
        record.args = ()
        return record
    factory._meeting_context_factory = True
    logging.setLogRecordFactory(factory)
