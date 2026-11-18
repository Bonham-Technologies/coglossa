import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coglossa.config import ConfigError
from coglossa.schema import Block, BlockKind, Provenance, SourceDocument
from coglossa.segment.chunking import ChunkBudget, chunk_segments, compute_budget
from coglossa.segment.segmenter import segment_document
from coglossa.segment.text_metrics import count_words, estimate_tokens
from tests.conftest import BASE_CONFIG

SENTENCE = "Lorem ipsum dolor sit amet."  # 5 words


def _doc(*blocks: tuple[BlockKind, str]) -> SourceDocument:
    return SourceDocument(
        source_path="x",
        format="txt",
        sha256="0",
        blocks=[
            Block(id=f"b{i + 1}", kind=k, text=t, provenance=Provenance(source_path="x", index=i))
            for i, (k, t) in enumerate(blocks)
        ],
    )


def _para(sentences: int) -> tuple[BlockKind, str]:
    return BlockKind.PARAGRAPH, " ".join([SENTENCE] * sentences)


def _chunks(doc, budget):
    segs = segment_document(doc)
    by_id = {s.id: s for s in segs}
    return segs, [[by_id[i] for i in c.segment_ids] for c in chunk_segments(doc, segs, budget)]


BIG = ChunkBudget(max_words=400, target_words=250, max_tokens=100_000, bytes_per_token=2.5)


def test_compute_budget_default_roster(make_config):
    budget = compute_budget(make_config())
    assert budget == ChunkBudget(
        max_words=2000, target_words=1500, max_tokens=int((32768 - 3000) / 4.5), bytes_per_token=2.5
    )


def test_extra_translators_and_small_models_shrink_budget(make_config):
    models = {
        **BASE_CONFIG["models"],
        "small": {"backend": "fake", "context_window": 16384, "bytes_per_token": 2.0},
    }
    roles = {
        **BASE_CONFIG["roles"],
        "extra_translators": [{"name": "x", "prompt": "contextual", "model": "small"}],
    }
    budget = compute_budget(make_config(models=models, roles=roles))
    assert budget.max_tokens == int((16384 - 3000) / 5.5)
    assert budget.bytes_per_token == 2.0


def test_too_small_context_is_a_clear_config_error(make_config):
    models = {**BASE_CONFIG["models"], "model-a": {"backend": "fake", "context_window": 4096}}
    with pytest.raises(ConfigError, match=r"model-a.*4096.*reserve_tokens"):
        compute_budget(make_config(models=models))


def _limited(**limits: int) -> dict:
    """BASE_CONFIG's models, plus a max_output_tokens for the named ones."""
    return {
        key: {**model, "max_output_tokens": limits[key]} if key in limits else model
        for key, model in BASE_CONFIG["models"].items()
    }


def test_small_output_limit_caps_the_budget(make_config):
    # model-a writes the literal translation and the reconciled one: 4000 tokens out at most
    budget = compute_budget(make_config(models=_limited(**{"model-a": 4000})))
    assert budget.max_tokens == int(4000 / 1.5)  # under the context's (32768 - 3000) / 4.5
    assert budget.max_tokens * 1.5 <= 4000  # the reconcile answer (~1.5x the chunk) fits


def test_roundtrip_output_limit_caps_the_budget(make_config):
    models = {
        **BASE_CONFIG["models"],
        "model-c": {"backend": "fake", "context_window": 32768, "max_output_tokens": 3000},
    }
    roles = {**BASE_CONFIG["roles"], "roundtrip": {"model": "model-c"}}
    assert compute_budget(make_config(models=models, roles=roles)).max_tokens == 2000


def test_judge_and_guide_output_limits_leave_the_budget_alone(make_config):
    # their answers (error lists, a guide) do not grow with the chunk
    small = {"backend": "fake", "context_window": 32768, "max_output_tokens": 1000}
    models = {**BASE_CONFIG["models"], "model-g": small, "model-j": small}
    roles = {
        **BASE_CONFIG["roles"],
        "profiler_guide": {"model": "model-g"},
        "judge": {"model": "model-j"},
    }
    assert compute_budget(make_config(models=models, roles=roles)) == compute_budget(make_config())


def test_large_output_limit_leaves_the_budget_alone(make_config):
    budget = compute_budget(make_config(models=_limited(**{"model-a": 16384, "model-b": 16384})))
    assert budget == compute_budget(make_config())


def test_too_small_output_limit_is_a_clear_config_error(make_config):
    models = _limited(**{"model-b": 1000})  # 666 tokens per chunk; one segment may need 960
    match = r"translator:contextual.*model-b.*max_output_tokens=1000"
    with pytest.raises(ConfigError, match=match):
        compute_budget(make_config(models=models))


@pytest.mark.parametrize(
    ("output_limit", "first", "second"),
    [
        (300, r"max_output_tokens=300", r"4096-token context window"),  # 200 < 243 tokens
        (600, r"4096-token context window", r"max_output_tokens=600"),  # 243 < 400 tokens
    ],
)
def test_the_tightest_short_limit_is_named_first(make_config, output_limit, first, second):
    models = {
        **_limited(**{"model-b": output_limit}),
        "model-a": {"backend": "fake", "context_window": 4096},  # (4096 - 3000) / 4.5 = 243
    }
    with pytest.raises(ConfigError, match=rf"^[^.]*{first}.* Also: [^.]*{second}"):
        compute_budget(make_config(models=models))


def test_a_short_output_limit_is_named_when_the_context_is_ample(make_config):
    with pytest.raises(ConfigError) as err:
        compute_budget(make_config(models=_limited(**{"model-b": 1000})))
    assert "context window" not in str(err.value)


def test_output_limit_is_the_role_models(make_config):
    cfg = make_config(models=_limited(**{"model-a": 4000}))
    assert cfg.output_limit(cfg.roles.reviewer) == 4000
    assert cfg.output_limit(cfg.roles.contextual_translator) is None


def _with_role_limit(role: str, limit: int) -> dict:
    """BASE_CONFIG's roles, with the named role's own max_output_tokens set."""
    roles = BASE_CONFIG["roles"]
    return {**roles, role: {**roles[role], "max_output_tokens": limit}}


def test_a_translators_own_output_limit_caps_the_budget(make_config):
    # model-b sets no limit; the contextual translator's own 3000 tokens cap the chunk
    roles = _with_role_limit("contextual_translator", 3000)
    assert compute_budget(make_config(roles=roles)).max_tokens == 2000


def test_a_roles_own_limit_under_its_models_caps_the_budget(make_config):
    models = _limited(**{"model-a": 16384, "model-b": 16384})
    roles = _with_role_limit("reviewer", 4000)
    assert compute_budget(make_config(models=models, roles=roles)).max_tokens == int(4000 / 1.5)


def test_an_extra_translators_own_output_limit_caps_the_budget(make_config):
    extra = {"name": "x", "prompt": "contextual", "model": "model-a", "max_output_tokens": 3000}
    roles = {**BASE_CONFIG["roles"], "extra_translators": [extra]}
    assert compute_budget(make_config(roles=roles)).max_tokens == 2000


def test_roles_without_their_own_limit_keep_the_models(make_config):
    # unset role limits: the model's limit caps the chunk, as before roles could set one
    models = _limited(**{"model-a": 4000})
    assert compute_budget(make_config(models=models)).max_tokens == int(4000 / 1.5)
    assert compute_budget(make_config()).max_tokens == int((32768 - 3000) / 4.5)


def test_the_judges_limits_leave_the_budget_alone(make_config):
    # the judge's 8192 default (5461 tokens a chunk at 1.5x) and its own limit do not count
    models = _limited(**{"model-a": 16384, "model-b": 16384})
    plain = compute_budget(make_config(models=models))
    for judge in ({"model": "model-a"}, {"model": "model-a", "max_output_tokens": 500}):
        roles = {**BASE_CONFIG["roles"], "judge": judge}
        assert compute_budget(make_config(models=models, roles=roles)) == plain


def test_a_too_small_own_limit_names_the_role_not_the_model(make_config):
    # needs 960 tokens a chunk, so 1440 out
    roles = _with_role_limit("reviewer", 1000)
    with pytest.raises(ConfigError) as err:
        compute_budget(make_config(roles=roles))
    message = str(err.value)
    assert "role 'reviewer' sets its own max_output_tokens=1000" in message
    assert "Raise the role's max_output_tokens to 1440 or more, or remove it." in message
    assert "model 'model-a'" not in message


def test_a_too_small_own_limit_also_names_a_short_model_limit(make_config):
    roles = _with_role_limit("reviewer", 1000)
    with pytest.raises(ConfigError, match=r"1440 or more \(and model 'model-a''s, which is 1200\)"):
        compute_budget(make_config(models=_limited(**{"model-a": 1200}), roles=roles))


def test_chunks_close_at_paragraph_boundaries_after_target():
    doc = _doc(*[_para(20) for _ in range(5)])  # 100 words each
    _, chunks = _chunks(doc, BIG)
    assert [sum(count_words(s.text) for s in c) for c in chunks] == [300, 200]


def test_section_starts_close_half_full_chunks():
    # 150w | heading: 150 >= 125 -> close | "Chapter Two" + 150w | break: 152 >= 125 -> close
    # | 150w + 10w -> closed at end. Three chunks.
    doc = _doc(
        _para(30),
        (BlockKind.HEADING, "Chapter Two"),
        _para(30),
        (BlockKind.BREAK, "* * *"),
        _para(30),
        _para(2),
    )
    _, chunks = _chunks(doc, BIG)
    assert [c[0].text for c in chunks] == [SENTENCE, "Chapter Two", SENTENCE]
    assert len(chunks) == 3


def test_huge_paragraph_is_split_at_sentence_boundaries():
    doc = _doc(_para(200))  # 1000 words in one block
    _, chunks = _chunks(doc, BIG)
    assert all(sum(count_words(s.text) for s in c) <= 400 for c in chunks)
    assert len(chunks) == 3


def test_token_budget_limits_cjk_chunks():
    doc = _doc((BlockKind.PARAGRAPH, "今日は晴れです。" * 200))
    budget = ChunkBudget(max_words=10_000, target_words=5000, max_tokens=500, bytes_per_token=2.5)
    _, chunks = _chunks(doc, budget)
    assert len(chunks) > 1
    assert all(sum(estimate_tokens(s.text, 2.5) for s in c) <= 500 for c in chunks)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(st.sampled_from(["p", "h", "b"]), st.integers(1, 60)), min_size=1, max_size=40
    ),
    st.integers(50, 400),
)
def test_every_segment_lands_in_exactly_one_chunk_in_order(spec, target):
    blocks = []
    for kind, n in spec:
        if kind == "p":
            blocks.append(_para(n))
        elif kind == "h":
            blocks.append((BlockKind.HEADING, "Heading"))
        else:
            blocks.append((BlockKind.BREAK, "***"))
    doc = _doc(*blocks)
    budget = ChunkBudget(
        max_words=max(target, 200), target_words=target, max_tokens=2000, bytes_per_token=2.5
    )
    segs = segment_document(doc)
    chunks = chunk_segments(doc, segs, budget)
    assert [i for c in chunks for i in c.segment_ids] == [s.id for s in segs]
    assert [c.id for c in chunks] == [f"c{i + 1}" for i in range(len(chunks))]
    by_id = {s.id: s for s in segs}
    for c in chunks:
        texts = [by_id[i].text for i in c.segment_ids]
        assert c.segment_ids
        assert sum(estimate_tokens(t, 2.5) for t in texts) <= budget.max_tokens
        if len(texts) > 1:
            assert sum(count_words(t) for t in texts) <= budget.max_words
