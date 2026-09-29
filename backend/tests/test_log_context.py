import logging

from meeting_scribe.log_context import install_log_context, meeting_log_context


def test_log_records_are_correlated_and_websocket_tokens_redacted():
    install_log_context()
    with meeting_log_context(115, 'session-115'):
        record = logging.getLogger('meeting-scribe').makeRecord(
            'meeting-scribe', logging.INFO, __file__, 1,
            'connected to /meetings/115/live/ws?ws_token=private-token', (), None)
    assert 'meeting_id=115 session_id=session-115' in record.getMessage()
    assert 'private-token' not in record.getMessage()


def test_access_log_remains_compatible_with_uvicorn_formatter():
    install_log_context()
    record = logging.getLogger('uvicorn.access').makeRecord(
        'uvicorn.access', logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ('127.0.0.1', 'GET', '/meetings/115/live/ws?ws_token=secret', '1.1', 101), None)
    assert 'meeting_id=115' in record.getMessage()
    assert 'secret' not in record.getMessage()
