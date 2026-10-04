# Cross-repo contract fixtures

Frozen payloads that two halves of this repo must agree on **without importing
each other**. The coordinator runs in `.venv`; the Telegram gateway runs in its
own venv and the backend venv has no `telegram` installed, so neither suite can
call the other's code.

The ecosystem already solved this once: CRA freezes its maker-grid model into
JSON that eeva-sol's suite asserts against, because an ADR forbids the two
importing each other. Same shape here.

Each fixture is asserted from BOTH sides:

| fixture | producer asserts | consumer asserts |
|---|---|---|
| `notification_payload.json` | `tests/backend/coordinator/test_notifications_route.py` emits exactly this shape | `services/telegram-gateway/tests/test_notifications.py` can parse it with the REAL `relay.extract_media` |

A change to the payload fails the producer test first, with instructions. Do
not regenerate a fixture to make a test pass without checking the consumer
still reads it — that is the whole point of the file.
