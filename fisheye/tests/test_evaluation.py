import asyncio

from fisheye.evaluation import compare, evaluate, replay
from fisheye.evaluation.scenarios import corpus


def test_labeled_corpus_and_deterministic_replay():
    async def run():
        cases = corpus()
        report = await evaluate(cases)
        assert report["passed"] == report["scenarios"]
        assert report["false_findings_per_100_benign"] == 0
        one = await replay(cases[0]["events"])
        two = await replay(cases[0]["events"])
        assert one["findings"] == two["findings"]
        assert compare(one, two) == dict(added=[], removed=[], changed=[])

    asyncio.run(run())
