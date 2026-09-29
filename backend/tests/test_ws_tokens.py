from __future__ import annotations

import unittest

from meeting_scribe.server.ws_tokens import OneTimeWebSocketTokenStore


class WebSocketTokenTests(unittest.TestCase):
    def test_token_is_single_use(self) -> None:
        now = [100.0]
        store = OneTimeWebSocketTokenStore(clock=lambda: now[0])
        token = store.issue()
        self.assertTrue(store.consume(token))
        self.assertFalse(store.consume(token))

    def test_token_expires(self) -> None:
        now = [100.0]
        store = OneTimeWebSocketTokenStore(
            ttl_seconds=60.0,
            clock=lambda: now[0],
        )
        token = store.issue()
        now[0] = 161.0
        self.assertFalse(store.consume(token))


if __name__ == '__main__':
    unittest.main()
