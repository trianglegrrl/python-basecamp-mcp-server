"""Batch C migration: messages / campfire / comments / inbox / forwards."""
import inspect


def test_every_batch_c_tool_takes_ctx_first():
    names = [
        'get_comments', 'create_comment', 'get_campfire_lines',
        'get_message_board', 'get_messages', 'get_message',
        'get_message_categories', 'create_message', 'get_inbox',
        'get_forwards', 'get_forward', 'get_inbox_replies',
        'get_inbox_reply', 'trash_forward',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"
