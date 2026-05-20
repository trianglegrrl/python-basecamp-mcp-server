"""Batch E migration: card steps + attachments."""
import inspect


def test_every_batch_e_tool_takes_ctx_first():
    names = [
        'get_card_steps', 'create_card_step', 'get_card_step',
        'update_card_step', 'delete_card_step', 'complete_card_step',
        'uncomplete_card_step', 'create_attachment',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"
