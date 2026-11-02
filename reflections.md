## Written Reflection: Building an E-commerce Support AI Agent

Building and deploying my e-commerce customer support AI agent required balancing architectural flexibility with robust error handling across cloud services. 

### Design Decision
A key architectural decision I made was implementing dual-path routing logic within my AWS Lambda functions. Because the AgentCore Gateway interacts with target tools through direct tool invocations—passing arguments directly or via custom client context—while standard web requests rely on API Gateway proxy event structures (`resource`, `httpMethod`, `pathParameters`), my handlers needed to accommodate both paradigms. By normalizing incoming event payloads and supporting intelligent fallback parsing, a single Lambda deployment can process both REST endpoints and direct agent tool calls reliably.

### Challenge
During my deployment and testing lifecycle, I encountered significant integration friction, including `ImportModuleError` configuration mismatches, syntax errors, and payload discrepancies where direct AWS Console testing lacked expected client context headers. These discrepancies caused my agent to occasionally stumble across test cases—such as failing initial order lookups for `ORD-001` when parameter schemas mismatched. Resolving this required meticulous debugging of incoming event payloads through CloudWatch logs and refining my parameter extraction layers to handle multi-source payloads gracefully.

### Production Consideration
Transitioning this system from a demonstration prototype to a production-ready environment requires replacing hard-coded mock dictionaries with a durable persistence layer like Amazon DynamoDB. Production hardening also demands strict adherence to least-privilege IAM policies—such as resolving restricted memory permissions and implementing proper namespace fallbacks—alongside comprehensive observability, input sanitization, and automated CI/CD pipelines to ensure high availability, security, and low-latency performance under enterprise workloads.