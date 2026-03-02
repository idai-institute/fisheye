# fisheye

fisheye is an anomaly detection system for tool-calling agents, both single-agent and multi-agent.
It operates through a mix of two approaches:
- Typical behavior modelling: the system collects metrics about the typical behavior of the system, creates a (constantly updating) model of the typical behavior, and flags large deviations
- Specific detectors: these are detection systems (either rule-based or trained) that identify specific attacks and report it


The structure of the system is as follows:
- Adapter: the adapter takes whatever agent framework is being used (e.g. LangChain) and collects the relevant metrics.
- Pre-processor: this system preprocesses the raw metrics. The main reasons are a) privacy (and the preprocessing can hide private information, e.g. by using embeddings instead of sentences, redacting names, or providing very broad informations like message lengths)
- Event buses: these are asynchronous buses that send the metrics (raw or not) to a given target. Each agent can send to multiple targets, each one with different metrics. The entities that receive events are called collectors. The buses have a breadth of schemas matching typical events, but they also support custom events
- Loggers: loggers are a type of collectors who receive and store the events, making them available for later. They can be used just for logging's sake, but they can also facilitate looking through data for other collectors
- Detectors: detectors are collectors designed to detect specific anomalies. They can be trained or rule-based
- Behavior monitors: these model the typical behavior of a system and flag unusual deviations
It's important to note that some collectors might also accept information from multiple agents at the same time


This pipeline-like setup enables a lot of flexibility: raw vs preprocessed, full access vs obfuscated, single vs multi-agent, specific detector vs behavioral model... this is intentional.

Some example events:
- User/system calling the LLM
- Messages between LLMs
- LLM calling a tool
- State update
- Agent startup
- Agent shutdown
- Agent failing
You are absolutely welcome to think more. We can use OpenTelemetry as schema base.

Some example preprocessors:
- Buffering
- Message embedding using an embedder
- Anonymizer
Again, feel free to think more

For detectors and behavior monitors, you figure it out.