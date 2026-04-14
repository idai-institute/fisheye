import asyncio
import pytest
from fisheye.runtime import build_default_runtime
from fisheye.tests.test_privacy_and_auth import config


def test_langchain_real_tool_parallel_and_failure(tmp_path):
    pytest.importorskip('langchain_core')
    from langchain_core.tools import tool
    from fisheye.adapters.langchain import LangChainAdapter
    @tool
    async def add(a:int,b:int)->int:
        """Add two numbers."""
        return a+b
    @tool
    async def broken(a:int)->int:
        """Always fail."""
        raise ValueError('test')
    async def run():
        runtime=build_default_runtime(config(tmp_path))
        async with runtime:
            callbacks=[LangChainAdapter(runtime,'native','r').as_callback_handler()]
            assert await asyncio.gather(add.ainvoke({'a':1,'b':2},config={'callbacks':callbacks}),
                                       add.ainvoke({'a':3,'b':4},config={'callbacks':callbacks}))==[3,7]
            with pytest.raises(ValueError):
                await broken.ainvoke({'a':1},config={'callbacks':callbacks})
            await runtime.drain(5)
            rows=await runtime.store.journal_events()
            events=[r['event'] for r in rows]
            ends=[e for e in events if e['event_type']=='tool.call.end']
            assert len(ends)==2 and all(len(e['links'])==1 for e in ends)
            assert any(e['event_type']=='tool.call.error' for e in events)
    asyncio.run(run())


def test_openai_agents_native_runner_with_fake_model(tmp_path):
    pytest.importorskip('agents')
    from agents import Agent,Runner,Model,ModelResponse,Usage,RunConfig,function_tool
    from openai.types.responses import ResponseFunctionToolCall,ResponseOutputMessage,ResponseOutputText
    from fisheye.adapters.openai_agents import OpenAIAgentsAdapter
    class Fake(Model):
        def __init__(self): self.calls=0
        async def get_response(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:
                output=[ResponseFunctionToolCall(type='function_call',name='double',call_id='c1',arguments='{"value":3}',id='fc1')]
            else:
                output=[ResponseOutputMessage(type='message',id='m1',status='completed',role='assistant',
                    content=[ResponseOutputText(type='output_text',text='6',annotations=[])])]
            return ModelResponse(output=output,usage=Usage(requests=1,input_tokens=1,output_tokens=1,total_tokens=2),response_id=None)
        async def stream_response(self,*args,**kwargs):
            raise NotImplementedError
            yield
    @function_tool
    async def double(value:int)->int:
        return value*2
    async def run():
        runtime=build_default_runtime(config(tmp_path))
        async with runtime:
            adapter=OpenAIAgentsAdapter(runtime,'native','r')
            result=await Runner.run(Agent(name='worker',model=Fake(),tools=[double]),'double 3',
                hooks=adapter.as_run_hooks(),run_config=RunConfig(tracing_disabled=True))
            assert result.final_output=='6'
            await runtime.drain(5)
            events=[r['event'] for r in await runtime.store.journal_events()]
            assert {'agent.start','tool.call.start','tool.call.end','agent.stop'} <= {e['event_type'] for e in events}
            assert all(e['agent_id']=='worker' for e in events)
    asyncio.run(run())
