# **AI Policy Attributes**

This document outlines a schema designed for governing AI agents, providing a standardized way to track identity, security context, and the flow of information between users, agents, tools, and models.

These message definitions should be accompanied by the function
implementations declared in the [common_env.yaml](common_env.yaml),
and provide attributes for either an [agent policy](agent_env.yaml)
or [tool call policy](tool_call_env.yaml) or some combination thereof
which most closely matches the needs of the use case.

## **Core Definitions**

### **Message: Agent**

The top-level container representing the AI System. It encapsulates both static configuration (Manifests, Identity) and dynamic runtime state (Context, Inputs, Outputs).

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| name | string | The unique resource name of the agent (e.g. agents/finance-helper). |
| description | string | Human-readable description of the agent's purpose. |
| version | string | The semantic version of the agent definition. |
| model | Model | The underlying model family backing this agent. |
| provider | AgentProvider | The provider or vendor responsible for hosting/managing this agent. |
| auth | AgentAuth | Identity of the Agent itself (Service Account / Principal). |
| context | AgentContext | The accumulated security context (Trust, Sensitivity, Data Sources). |
| input | AgentMessage | The current turn's input (Prompt \+ Attachments). |
| output | AgentMessage | The pending response (if evaluating egress/output policies). |

### **Message: AgentAuth**

Represents the identity of the Agent itself. This is independent of the end-user credentials.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| principal | string | The principal of the agent, preferably in [SPIFFE format](https://spiffe.io/docs/latest/spiffe/concepts/#spiffe-identifiers). |
| claims | google.protobuf.Struct | Map of structured claims about the agent (e.g. issuer, audience, expiration) typically found in [JWT tokens (RFC 7519\)](https://datatracker.ietf.org/doc/html/rfc7519). |
| oauth\_scopes | repeated string | The OAuth scopes granted to the agent. |

## **Security & Trust Context**

### **Message: AgentContext**

Represents the aggregate security and data governance state of the agent's context window.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| trust | TrustLevel | Aggregated trust level associated with relevant data in the window. |
| sources | repeated DataSource | Origin and lineage tracking for data included in the context. |
| prompt | string | The flattened text content of the current prompt. |

#### **Nested Message: DataSource**

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| id | string | Unique id describing the originating data source (e.g. bigquery:sales\_table). |
| provenance | string | The category of origin for this data (e.g. UserPrompt, PublicWeb). |

### **Message: TrustLevel**

Describes the integrity or veracity of the data.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| level | string | The trust level of the data (e.g. untrusted, trusted). |
| findings | repeated Finding | Findings which support or are associated with this trust level. |

### **Message: ClassificationLabel**

Describes the classification of data within the context, such as sensitivity or safety hints.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| name | string | Common labels (e.g. pii, internal, child\_safety). |
| category | Category (Enum) | The category of the label (SENSITIVITY, SAFETY, THREAT). |
| findings | repeated Finding | Findings associated with this specific label. |

### **Message: Finding**

Describes confidence measures and reasoning associated with a label or trust level.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| value | string | The name of the confidence measure (e.g. picc\_score). |
| confidence | double | The confidence score between 0 and 1\. |
| explanation | string | An optional explanation for the confidence score. |

## **Messaging & Content**

### **Message: AgentMessage**

Represents a single turn in the conversation, acting as a container for multimodal content.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| role | string | The actor who constructed the message (e.g. user, model, tool). |
| parts | repeated Part | The ordered sequence of content parts. |
| metadata | google.protobuf.Struct | Arbitrary metadata associated with the message turn. |
| time | google.protobuf.Timestamp | Message creation time. |

#### **Nested Message: Part (oneof)**

| Field | Type | Purpose |
| :---- | :---- | :---- |
| prompt | ContentPart | User or System text input. |
| tool\_call | ToolCall | A request to execute a specific tool. |
| attachment | ContentPart | A file or multimodal object (Image, PDF). |
| error | ErrorPart | An error that occurred during processing. |

### **Message: ContentPart**

A catch-all message type for encapsulating multimodal or structured content.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| id | string | Unique identifier for this content part. |
| type | string | The type of content (e.g. text, file, json). |
| mime\_type | string | The MIME type of the content (e.g. image/png). |
| content | string | String serialized representation of the content. |
| data | bytes | Binary representation of the content. |
| structured\_content | google.protobuf.Struct | JSON object representation of the content. |
| time | google.protobuf.Timestamp | Timestamp associated with the content part. |

## **Tooling & Execution**

### **Message: Tool**

Describes a specific function or capability available to the agent.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| name | string | The unique name of the tool (e.g. weather\_lookup). |
| description | string | Human readable description of what the tool does. |
| input\_schema | google.protobuf.Struct | JSON Schema defining the expected arguments. |
| output\_schema | google.protobuf.Struct | JSON Schema defining the expected output. |
| annotations | ToolAnnotations | Behavioral hints about the tool. |

### **Message: ToolAnnotations**

Hints describing a tool's behavior, informed by conventions like the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) spec.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| read\_only | bool | If true, the tool does not modify its environment. |
| destructive | bool | If true, the tool may perform destructive updates. |
| idempotent | bool | If true, repeated calls with same args have no additional effect. |
| open\_world | bool | If true, tool interacts with an "open world" of external entities. |
| async | bool | If true, this tool is intended to be called asynchronously. |
| output\_trust | TrustLevel | The trust level of the tool's output. |

### **Message: ToolCall**

Represents a specific invocation of a tool by the agent.

| Attribute | Type | Purpose |
| :---- | :---- | :---- |
| id | string | Unique identifier used to correlate call with results/errors. |
| name | string | The name of the tool being called. |
| params | google.protobuf.Struct | The arguments provided to the tool call. |
| result | ContentPart | The successful output of the tool (if status is result). |
| error | ErrorPart | The error encountered during execution (if status is error). |
| user\_confirmed | bool | Indicates if the user explicitly confirmed this action (HITL). |

## **Policy Expressions & Use Cases**

Policy authors use Common Expression Language (CEL) to inspect the agent's state and enforce governance rules. Below are common use cases and examples based on the cel.expr.ai environment.

### **1\. Data Sensitivity & PII Governance**

Ensure that sensitive data (like PII) is handled correctly across different parts of the agent context.

* **Retrieving Sensitivity Labels:**

      // Get optional list of sensitivity findings for the 'pii' label.  
      agent.context.sensitivityFindings("pii")

* **Restricting High-Confidence PII in Tool Calls:**

      // Fail if any PII finding has a confidence score greater than 0.5.  
      tool.call.sensitivityFindings("pii").orValue(\[\])  
        .all(finding, finding.confidence \<= 0.5)

* **Checking for Specific Data Types:**

      // Returns true if the tool call contains both 'phone\_number' and 'ssn' findings.  
      tool.call.sensitivityFindings("pii").hasAll(\["phone\_number", "ssn"\])

### **2\. Safety & Threat Mitigation**

Identify and block malicious inputs or unsafe model outputs.

* **Detecting Prompt Injections or Jailbreaks:**

      // Returns true if any specified threats are detected with high confidence.  
      agent.context.threatFindings().hasAll(\["injection", "jailbreak", "malicious\_uri"\])

* **Filtering Output for Responsible AI Safety:**

      // Check if the model output contains hate speech or sexually explicit content.  
      agent.output.safetyFindings("responsible\_ai")  
        .hasAll(\["hate\_speech", "sexually\_explicit"\])

### **3\. Context & History Analysis**

Analyze previous turns in the conversation to enforce policies over time.

* **Filtering History by Role and Time:**

      // Check for threats in user messages from the last 5 minutes.  
      agent.history  
          .role("user")  
          .after(now \- duration('5m'))  
          .threatFindings().hasAll(\["injection", "jailbreak"\])

* **Inspecting Tool History:**

      // Find all JSON-based tool results in the agent's history.  
      agent.history.role("agent").toolCalls("get\_weather").resultType("json")

### **4\. Advanced Tool Governance**

Control how tools interact with the world based on their definitions.

* **The "Lethal Trifecta" Check:**

      // Block tool calls that are destructive, interact with the open world, and produce untrusted output.  
      agent.input.parts.exists(part,  
        has(part.tool\_call) &&  
        \!part.tool\_call.spec().annotations.output\_trust.level in \['trusted', 'trusted\_1p'\]  
      )

### **5\. Utility Functions**

* **Creating Manual Findings:** `ai.finding("picc_score", 0.5)`  
* **Casting Content:** `agent.input.parts[0].asType(bigquery.QueryRequest)`  
* **Union of Findings:** 
Combine findings from context and tool calls, keeping the highest confidence score:

      agent.context.sensitivityFindings("pii")
        .union(tool.call.sensitivityFindings("pii"))
