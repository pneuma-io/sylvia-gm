"""Keep the ordinary stream attached, matching the frontend generation handshake."""
from threading import Event, Lock, Thread


def request_attached(harness, identity, method, params, *, campaign_id, attempt_id):
    stopped, lock = Event(), Lock()
    identities, replies = [], {}
    generation = None
    successful = 0
    target = None
    harness.send(identity, {"method": method, "params": params})

    def renew():
        while not stopped.is_set():
            with lock:
                heartbeat_id = identity + "-heartbeat-" + str(len(identities))
                identities.append(heartbeat_id)
                payload = {"campaign_id": campaign_id, "attempt_id": attempt_id}
                if generation is not None:
                    payload["attachment_generation"] = generation
            harness.send(heartbeat_id, {"method": "stream_heartbeat", "params": payload})
            stopped.wait(2.0)

    def accept(reply):
        nonlocal generation, successful
        if reply["ok"]:
            assert reply["result"]["attachment_deadline"] > 0
            returned = reply["result"]["attachment_generation"]
            assert returned > 0
            with lock:
                if generation is not None:
                    assert returned == generation
                generation = returned
            successful += 1
        else:
            # The same benign race exists when the frontend first attaches or
            # the request finishes before its last queued heartbeat is handled.
            assert reply["error"]["message"] in {
                "Narration attachment is unavailable",
                "Narration attempt is not actively attached",
            }, reply

    worker = Thread(target=renew, daemon=True)
    worker.start()
    try:
        while target is None:
            incoming = harness._read_stream()
            if incoming["id"] == identity:
                target = incoming
            elif incoming["id"] in identities:
                accept(incoming)
                replies[incoming["id"]] = incoming
            else:
                harness._pending_replies[incoming["id"]] = incoming
    finally:
        stopped.set()
        worker.join(timeout=5)
        assert not worker.is_alive(), "Owned QA heartbeat worker did not exit"
        for heartbeat_id in identities:
            while heartbeat_id not in replies:
                incoming = harness._read_stream()
                if incoming["id"] in identities:
                    accept(incoming)
                    replies[incoming["id"]] = incoming
                else:
                    harness._pending_replies[incoming["id"]] = incoming
        if len(identities) > 1:
            assert successful, "No ordinary attachment heartbeat was acknowledged"
    assert target["ok"] is True, target
    return target["result"]
