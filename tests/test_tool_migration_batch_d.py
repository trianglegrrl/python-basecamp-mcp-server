"""Batch D migration: cards / columns / check-ins."""
import inspect


def test_every_batch_d_tool_takes_ctx_first():
    names = [
        'get_card_tables', 'get_card_table', 'get_columns', 'get_cards',
        'create_card', 'get_column', 'create_column', 'move_card',
        'complete_card', 'get_card', 'update_card', 'get_daily_check_ins',
        'get_question_answers', 'update_column', 'move_column',
        'update_column_color', 'put_column_on_hold', 'remove_column_hold',
        'watch_column', 'unwatch_column', 'uncomplete_card',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"
