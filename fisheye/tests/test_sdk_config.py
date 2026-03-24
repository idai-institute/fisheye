import asyncio
import pytest
from pydantic import ValidationError
from fisheye.config import FisheyeConfig
from fisheye.context import current_context
from fisheye.runtime import build_default_runtime
from fisheye.tests.test_privacy_and_auth import config


def test_config_precedence_and_redacted_snapshot(tmp_path):
    path=tmp_path/'config.toml'
    path.write_text('[bus]\nqueue_size=12\n')
    cfg=FisheyeConfig.load(path,{'bus':{'queue_size':30}}, {'FISHEYE__BUS__QUEUE_SIZE':'20','FISHEYE__API__API_KEY':'secret'})
    assert cfg.bus.queue_size==30
    assert cfg.public_dict()['api']['api_key']=='[REDACTED]'
    with pytest.raises(ValidationError):
        cfg.bus.queue_size=0
    with pytest.raises(ValidationError):
        FisheyeConfig.from_dict({'typo':True})


def test_workflow_sdk_propagates_identity_and_concurrent_call_links(tmp_path):
    async def run():
        runtime=build_default_runtime(config(tmp_path))
        async with runtime:
            async with runtime.workflow('w') as workflow:
                a,b=workflow.agent('a'),workflow.agent('b')
                source=await a.emit('llm.message',{'content':'hello'})
                message=await a.message(b,'forward',sources=[source])
                assert message.payload['source_event_ids']==[source.event_id]
                @b.tool()
                async def add(a,b):
                    await asyncio.sleep(0)
                    return a+b
                assert await asyncio.gather(add(1,2),add(3,4))==[3,7]
            assert current_context.get() is None
            await runtime.drain(5)
            rows=await runtime.store.journal_events()
            ends=[r['event'] for r in rows if r['event']['event_type']=='tool.call.end']
            assert len({r['payload']['call_id'] for r in ends})==2
            assert all(r['workflow_id']=='w' and len(r['links'])==1 for r in ends)
    asyncio.run(run())
