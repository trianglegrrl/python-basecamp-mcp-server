"""Comment/Message edit tools: update_comment / update_message.

Final batch of the 18-tool port from the deprecated Node MCP repo to the
Python upstream. Mocked unit tests only — live lifecycle tests live in
tests/live/test_lifecycle_comment_message_edit.py and are gated by the
`live` marker.

Covers:
  - ctx-first parameter guard on both tools
  - parametrized auth-error path on both tools
  - happy-path per tool with mocked client return values
  - error-propagation per tool
  - update_message fetch-then-merge proof: when only `content` is patched,
    the PUT body must carry the CURRENT `subject` (BC3 requires `subject`
    on every PUT) and the CURRENT `category_id`
  - update_comment arg-order bridge regression: the wrapper takes the
    conventional (project_id, comment_id, content) signature but the
    pre-existing client method takes (comment_id, bucket_id, content) —
    the wrapper must bridge correctly
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


COMMENT_MESSAGE_EDIT_TOOL_NAMES = [
    'update_comment',
    'update_message',
]


# ----------------------------------------------------------------------------
# Signature / dispatch contract.
# ----------------------------------------------------------------------------

def test_every_comment_message_edit_tool_takes_ctx_first():
    """Every migrated tool MUST take ctx as its first positional parameter so
    the FastMCP dispatcher can supply the request context. See PR T3."""
    import basecamp_fastmcp as bc
    for name in COMMENT_MESSAGE_EDIT_TOOL_NAMES:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,kwargs", [
    ("update_comment", {"project_id": "1", "comment_id": "99", "content": "<p>x</p>"}),
    ("update_message", {"project_id": "1", "message_id": "42"}),
])
async def test_comment_message_edit_tool_auth_error_path_accepts_ctx(tool_name, kwargs):
    """When credentials are unavailable, each edit tool returns the
    auth-error dict via _get_auth_error_response(ctx) — the helper must accept ctx,
    not TypeError."""
    import basecamp_fastmcp as bc
    tool = getattr(bc, tool_name)
    provider = MagicMock(name='CredentialProvider')
    provider.credentials_for.return_value = None  # no creds -> client is None
    fake_ctx = MagicMock(name='Context')
    fake_ctx.request_context.lifespan_context = {"provider": provider}
    with patch('basecamp_fastmcp.token_storage') as mock_storage:
        mock_storage.is_token_expired.return_value = True
        result = await tool(fake_ctx, **kwargs)
    assert isinstance(result, dict)
    assert 'error' in result


# ----------------------------------------------------------------------------
# update_comment
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_comment_happy_path_returns_comment():
    from basecamp_fastmcp import update_comment

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_comment.return_value = {
        'id': 99, 'content': '<p>new content</p>',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_comment(
            fake_ctx, project_id='12345', comment_id='99', content='<p>new content</p>',
        )

    assert result['status'] == 'success'
    assert result['comment']['id'] == 99
    assert '99' in result['message']


@pytest.mark.asyncio
async def test_update_comment_bridges_arg_order_quirk():
    """REGRESSION: the pre-existing client.update_comment uses the
    unconventional (comment_id, bucket_id, content) arg order. The new
    wrapper takes the conventional (project_id, comment_id, content) signature
    and MUST bridge to the quirk order — otherwise PUT lands on the wrong
    bucket and BC3 404s (or worse, mutates a comment in the wrong project).
    """
    from basecamp_fastmcp import update_comment

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_comment.return_value = {'id': 99, 'content': '<p>x</p>'}
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        await update_comment(
            fake_ctx, project_id='PROJ_42', comment_id='COMMENT_99', content='<p>x</p>',
        )

    # Quirk order: (comment_id, bucket_id, content) — i.e. comment_id first.
    fake_client.update_comment.assert_called_once_with(
        'COMMENT_99', 'PROJ_42', '<p>x</p>',
    )


@pytest.mark.asyncio
async def test_update_comment_propagates_client_errors():
    from basecamp_fastmcp import update_comment

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_comment.side_effect = Exception(
        'Failed to update comment: 404 - Not Found',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_comment(
            fake_ctx, project_id='1', comment_id='99', content='<p>x</p>',
        )

    assert result.get('error') == 'Execution error'
    assert '404' in result['message']


# ----------------------------------------------------------------------------
# update_message — tool wrapper
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_message_happy_path_returns_message():
    from basecamp_fastmcp import update_message

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_message.return_value = {
        'id': 42, 'subject': 'Renamed', 'content': '<p>x</p>',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_message(
            fake_ctx, project_id='12345', message_id='42', subject='Renamed',
        )

    assert result['status'] == 'success'
    # Envelope matches create_message: "message" is the resource, "result"
    # is the success string.
    assert result['message']['id'] == 42
    assert result['message']['subject'] == 'Renamed'
    assert '42' in result['result']
    fake_client.update_message.assert_called_once_with(
        '12345', '42',
        subject='Renamed', content=None, category_id=None,
    )


@pytest.mark.asyncio
async def test_update_message_propagates_client_errors():
    from basecamp_fastmcp import update_message

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_message.side_effect = Exception(
        'Failed to update message: 404 - Not Found',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_message(
            fake_ctx, project_id='12345', message_id='42', subject='nope',
        )

    assert result.get('error') == 'Execution error'
    assert '404' in result['message']


# ----------------------------------------------------------------------------
# update_message — client method (fetch-then-merge)
# ----------------------------------------------------------------------------

def test_client_update_message_preserves_subject_on_content_only_patch():
    """REGRESSION: BC3's PUT /buckets/{p}/messages/{id}.json requires the
    `subject` field even when only `content` is being changed. If the merge
    layer drops it, BC3 422s. The client must GET the current message and
    supply its `subject` when the patch omits it. Same fetch-then-merge
    contract as update_project / update_todo / update_schedule_entry."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_message = MagicMock(return_value={
        'id': 42,
        'subject': 'old subject',
        'content': '<p>old content</p>',
        'category_id': 'C1',
    })
    put_resp = MagicMock()
    put_resp.status_code = 200
    put_resp.json.return_value = {
        'id': 42, 'subject': 'old subject', 'content': '<p>new content</p>',
    }
    client.put = MagicMock(return_value=put_resp)

    result = client.update_message('PROJ', 42, content='<p>new content</p>')

    client.get_message.assert_called_once_with('PROJ', 42)
    # Endpoint + body assertions.
    call = client.put.call_args
    assert call.args[0] == 'buckets/PROJ/messages/42.json'
    body = call.args[1]
    assert body['subject'] == 'old subject'   # preserved from current
    assert body['content'] == '<p>new content</p>'  # from patch
    assert body['category_id'] == 'C1'        # preserved from current
    assert result['id'] == 42


def test_client_update_message_applies_full_patch_when_all_fields_given():
    """When every whitelisted field is supplied, the merge step is a no-op
    overlay — the PUT body equals the patch."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_message = MagicMock(return_value={
        'id': 42, 'subject': 'old', 'content': '<p>old</p>', 'category_id': 'C1',
    })
    put_resp = MagicMock()
    put_resp.status_code = 200
    put_resp.json.return_value = {'id': 42, 'subject': 'new', 'content': '<p>new</p>'}
    client.put = MagicMock(return_value=put_resp)

    client.update_message(
        'PROJ', 42,
        subject='new', content='<p>new</p>', category_id='C2',
    )

    body = client.put.call_args.args[1]
    assert body == {
        'subject': 'new',
        'content': '<p>new</p>',
        'category_id': 'C2',
    }


def test_client_update_message_skips_missing_current_fields():
    """If the current message JSON lacks a whitelisted field (e.g. no
    category_id was ever set), the merge should NOT inject `None` into the
    body — BC3 422s on null category_id. Only forwards fields that are
    present-and-non-None in either the patch or the current."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_message = MagicMock(return_value={
        'id': 42, 'subject': 'old', 'content': '<p>old</p>',
        # No category_id field at all.
    })
    put_resp = MagicMock()
    put_resp.status_code = 200
    put_resp.json.return_value = {'id': 42, 'subject': 'old'}
    client.put = MagicMock(return_value=put_resp)

    client.update_message('PROJ', 42, content='<p>new</p>')

    body = client.put.call_args.args[1]
    assert 'category_id' not in body, \
        f"category_id should be absent when neither patch nor current has it; got {body!r}"
    assert body['subject'] == 'old'
    assert body['content'] == '<p>new</p>'


def test_client_update_message_raises_on_non_200():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_message = MagicMock(return_value={'id': 42, 'subject': 'x'})
    put_resp = MagicMock()
    put_resp.status_code = 422
    put_resp.text = 'subject required'
    client.put = MagicMock(return_value=put_resp)

    with pytest.raises(Exception, match='422'):
        client.update_message('PROJ', 42, subject='new')
