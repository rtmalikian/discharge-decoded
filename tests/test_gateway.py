"""Contract tests for the embedded OpenAI-compatible gateway."""

from app import gateway


def test_models_list_shape(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list) and data["data"]
    model = data["data"][0]
    assert model["object"] == "model"
    assert model["id"]  # non-empty id


def test_chat_completions_shape(client):
    r = client.post("/v1/chat/completions", json={
        "model": "discharge-decoded-stub-1",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["id"].startswith("chatcmpl-")
    assert isinstance(data["created"], int)
    choice = data["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert choice["finish_reason"] == "stop"
    usage = data["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_chat_completions_deterministic(client):
    payload = {"messages": [{"role": "user", "content": "repeatable input"}]}
    a = client.post("/v1/chat/completions", json=payload).json()
    b = client.post("/v1/chat/completions", json=payload).json()
    assert (a["choices"][0]["message"]["content"]
            == b["choices"][0]["message"]["content"])


def test_chat_completions_rejects_empty_messages(client):
    r = client.post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 400


def test_embeddings_shape(client):
    r = client.post("/v1/embeddings", json={"input": "aspirin 81 mg daily"})
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    emb = data["data"][0]
    assert emb["object"] == "embedding"
    assert emb["index"] == 0
    assert isinstance(emb["embedding"], list) and len(emb["embedding"]) > 0
    assert all(isinstance(x, float) for x in emb["embedding"])


def test_embeddings_batch_and_deterministic(client):
    payload = {"input": ["lisinopril", "albuterol"]}
    a = client.post("/v1/embeddings", json=payload).json()
    b = client.post("/v1/embeddings", json=payload).json()
    assert [d["index"] for d in a["data"]] == [0, 1]
    assert a["data"][0]["embedding"] == b["data"][0]["embedding"]
    # different inputs -> different vectors
    assert a["data"][0]["embedding"] != a["data"][1]["embedding"]


def test_unknown_backend_errors():
    import os
    old = os.environ.get("AI_BACKEND")
    os.environ["AI_BACKEND"] = "nope"
    try:
        try:
            gateway.dispatch_chat([{"role": "user", "content": "x"}])
        except Exception as exc:
            assert "unknown AI_BACKEND" in str(exc.detail)
        else:
            raise AssertionError("expected HTTPException")
    finally:
        if old is None:
            del os.environ["AI_BACKEND"]
        else:
            os.environ["AI_BACKEND"] = old
