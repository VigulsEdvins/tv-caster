import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


def test_queue_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "QUEUE_FILE", str(tmp_path / "queue.json"))
    started = []
    monkeypatch.setattr(server, "handle_cast_request", lambda **kw: started.append(kw))

    assert server.load_queue() == []
    q = server.queue_add([{"url": "https://a/1", "title": "A"}, {"url": "https://b/2", "title": "B", "episode": "s01e02"}, {"url": ""}])
    assert [i["title"] for i in q] == ["A", "B"]

    server.queue_move(q[1]["id"], -1)
    assert [i["title"] for i in server.load_queue()] == ["B", "A"]

    ok, err = server.cast_neighbor(1)  # «next» takes the queue first
    assert ok and not err
    import time; time.sleep(0.2)
    assert started[0]["url"] == "https://b/2" and started[0]["episode"] == "s01e02"
    assert [i["title"] for i in server.load_queue()] == ["A"]

    server.queue_remove(server.load_queue()[0]["id"])
    assert server.cast_queue_item() == (False, "Очередь пуста")
