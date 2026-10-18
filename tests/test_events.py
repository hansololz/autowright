"""EventHub — thread-safe publish feeding the §19 WebSocket."""
import asyncio
import threading

import pytest


@pytest.fixture()
def loop():
    """Event loop running in a background thread, like the server's own loop."""
    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(timeout=5)
    assert not t.is_alive()
    lp.close()


def _get(loop, q, timeout=5):
    """Fetch one message from a subscriber queue on the hub's loop."""
    return asyncio.run_coroutine_threadsafe(q.get(), loop).result(timeout)


def test_publish_reaches_subscriber(loop):
    from autowright.events import EventHub

    hub = EventHub()
    hub.bind_loop(loop)
    q = hub.subscribe()
    hub.publish("exec_update", id="e-1", status="running")
    assert _get(loop, q) == {"event": "exec_update", "id": "e-1", "status": "running"}


def test_publish_without_loop_is_silent_noop():
    from autowright.events import EventHub

    hub = EventHub()
    q = hub.subscribe()
    hub.publish("exec_update", id="e-1")  # no loop bound → dropped, no exception
    assert q.qsize() == 0


def test_full_queue_drops_but_others_still_receive(loop):
    from autowright.events import EventHub

    hub = EventHub()
    hub.bind_loop(loop)
    full = hub.subscribe()
    for i in range(full.maxsize):  # slow consumer: queue at capacity
        full.put_nowait({"event": "old", "i": i})
    healthy = hub.subscribe()

    hub.publish("tick", n=1)
    # _fanout delivers to every subscriber in one loop callback, so once the
    # healthy queue has the message the drop on the full one already happened.
    assert _get(loop, healthy) == {"event": "tick", "n": 1}
    assert full.qsize() == full.maxsize  # dropped silently, nothing raised

    # hub keeps working afterwards for further publishes
    hub.publish("tick", n=2)
    assert _get(loop, healthy) == {"event": "tick", "n": 2}


def test_unsubscribe_stops_delivery(loop):
    from autowright.events import EventHub

    hub = EventHub()
    hub.bind_loop(loop)
    gone = hub.subscribe()
    kept = hub.subscribe()
    hub.unsubscribe(gone)

    hub.publish("tick", n=1)
    assert _get(loop, kept) == {"event": "tick", "n": 1}
    assert gone.qsize() == 0

    # unsubscribing an unknown/already-removed queue is a no-op
    hub.unsubscribe(gone)
    hub.publish("tick", n=2)
    assert _get(loop, kept) == {"event": "tick", "n": 2}


def test_subscriber_roles_and_has_role():
    """§19 role param: the hub keeps each subscriber's role so the §6 carrier
    rule can ask whether a shell is connected; unsubscribe clears it."""
    from autowright.events import EventHub

    hub = EventHub()
    renderer = hub.subscribe()
    assert hub.has_role("shell") is False
    assert hub.has_role("renderer") is True
    shell = hub.subscribe("shell")
    assert hub.has_role("shell") is True
    hub.unsubscribe(shell)
    assert hub.has_role("shell") is False
    hub.unsubscribe(renderer)
    assert hub.has_role("renderer") is False


def test_every_role_receives_every_event(loop):
    """§19: roles grant nothing — a shell and a renderer both get each event."""
    from autowright.events import EventHub

    hub = EventHub()
    hub.bind_loop(loop)
    shell = hub.subscribe("shell")
    renderer = hub.subscribe("renderer")
    hub.publish("automation.changed", automationId="a-1")
    assert _get(loop, shell) == {"event": "automation.changed", "automationId": "a-1"}
    assert _get(loop, renderer) == {"event": "automation.changed", "automationId": "a-1"}
