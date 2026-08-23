'''Feishu office client behavior without live network calls.'''

from __future__ import annotations

import asyncio

from forge.office.feishu import FeishuAPIError, FeishuOpenAPI


class FakeFeishu(FeishuOpenAPI):
    def __init__(self) -> None:
        super().__init__('app', 'secret')
        self.calls: list[tuple[str, str, object, object]] = []
        self.revision = 3

    async def request(self, method, path, body=None, *, query=None):
        self.calls.append((method, path, body, query))
        if path.endswith('/blocks'):
            return {
                'items': [
                    {
                        'block_id': 'block1',
                        'parent_id': 'doc1',
                        'block_type': 2,
                        'text': {
                            'elements': [
                                {'text_run': {'content': 'original'}}
                            ]
                        },
                    },
                    {
                        'block_id': 'table1',
                        'parent_id': 'doc1',
                        'block_type': 31,
                    },
                ],
                'has_more': False,
            }
        if method == 'GET':
            return {
                'document': {
                    'document_id': 'doc1',
                    'revision_id': self.revision,
                    'title': 'Doc',
                }
            }
        if '/messages' in path:
            chat_id = body['receive_id']
            if chat_id == 'bad':
                raise FeishuAPIError('forbidden', 'denied')
            if chat_id == 'unknown':
                raise FeishuAPIError('network_error', 'timeout')
            return {'message_id': 'msg-' + chat_id}
        if path.endswith('/blocks/batch_update'):
            self.revision += 1
            return {'document_revision_id': self.revision}
        return {}


def test_read_preserves_stable_ids_and_marks_unsupported_blocks() -> None:
    result = asyncio.run(FakeFeishu().read_document('doc1'))

    assert result['revision_id'] == 3
    assert result['blocks'][0]['content'] == 'original'
    assert result['unsupported_block_ids'] == ['table1']


def test_update_rejects_revision_conflict_and_unsupported_block() -> None:
    async def run() -> None:
        api = FakeFeishu()
        try:
            await api.update_document('doc1', 2, [])
        except FeishuAPIError as error:
            assert error.code == 'revision_conflict'
        else:
            raise AssertionError('expected revision conflict')

        try:
            await api.update_document(
                'doc1',
                3,
                [{'block_id': 'table1', 'content': 'no'}],
            )
        except FeishuAPIError as error:
            assert error.code == 'unsupported_block'
        else:
            raise AssertionError('expected unsupported block')

    asyncio.run(run())


def test_create_validates_all_blocks_before_creating_remote_document() -> None:
    async def run() -> None:
        api = FakeFeishu()
        try:
            await api.create_document(
                'New document',
                [{'type': 'table', 'content': 'unsupported'}],
            )
        except FeishuAPIError as error:
            assert error.code == 'unsupported_block'
        else:
            raise AssertionError('expected unsupported block')

        assert api.calls == []

    asyncio.run(run())


def test_update_rejects_non_atomic_multi_batch_changes() -> None:
    api = FakeFeishu()

    try:
        asyncio.run(
            api.update_document(
                'doc1',
                3,
                [
                    {'block_id': f'block{index}', 'content': 'value'}
                    for index in range(201)
                ],
            )
        )
    except FeishuAPIError as error:
        assert error.code == 'update_limit'
    else:
        raise AssertionError('expected update limit')

    assert api.calls == []


def test_message_send_deduplicates_targets_and_records_partial_failure() -> None:
    result = asyncio.run(
        FakeFeishu().send_message(['good', 'bad', 'good'], 'notice')
    )

    assert result['target_count'] == 2
    assert result['succeeded'] == 1
    assert result['failed'] == 1
    assert [item['chat_id'] for item in result['results']] == ['good', 'bad']
    assert result['result_unknown'] is False


def test_message_send_marks_uncertain_targets_without_retrying() -> None:
    api = FakeFeishu()

    result = asyncio.run(api.send_message(['good', 'unknown'], 'notice'))

    assert result['succeeded'] == 1
    assert result['failed'] == 1
    assert result['result_unknown'] is True
    assert result['results'][1] == {
        'chat_id': 'unknown',
        'success': False,
        'error_code': 'network_error',
        'result_unknown': True,
    }
    assert sum(1 for call in api.calls if '/messages' in call[1]) == 2
