"""Batch F migration: documents / uploads / webhooks / events."""
import inspect


def test_every_batch_f_tool_takes_ctx_first():
    names = [
        'get_events', 'get_webhooks', 'create_webhook', 'delete_webhook',
        'get_documents', 'get_document', 'create_document', 'update_document',
        'trash_document', 'get_uploads', 'get_upload',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"
