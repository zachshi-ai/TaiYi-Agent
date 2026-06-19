# From a GitHub Upload Pitfall: The Capability Boundary of AI Agents and the Underlying Engineering Thinking

> Original: WeChat Official Account / 2026.06.19

## I. Origin: A Seemingly Successful Failed Upload

Today I used **OpenClaw** to push a **GitHub** repo, and stepped into a non-trivial pitfall.

The task itself was simple: push the local project files to a remote repository. The tool executed smoothly, returning the repo link in seconds — by the surface metric, the task was **100%** complete. But when I clicked into the link, I found that the author identity in the commit log was neither my **GitHub** account nor the locally configured user info — the tool had unilaterally replaced the identity, recording my code under someone else's name.

An ordinary user might stop at the link, never even opening the commit record to check the author. But because I have a legal background and am naturally sensitive to issues of authorship and first-publication rights, I noticed this detail. The fix itself is not hard, but the deeper I think about it, the more I realize this is not a single tool's **bug** — it's a systemic issue across the entire **AI Agent** industry. (Honestly, it's because I argued with openclaw all day, only to discover once again that he never listens, and after I cooled down from that fury, this deeper question crystallized.)

I've done the same operation many times with **Trae**, and this kind of issue never came up. It's not that **Trae** has a stronger model — its execution logic is more conservative: it respects the local **Git** configuration by default, and never modifies user identity information on its own. Two tools doing the same thing — one pursuing "**fastest completion**", the other pursuing "**minimum overreach**" — sit behind completely different product design priorities. And users, when choosing tools, can barely see any explanation of these underlying differences — they can only learn by stepping into pitfalls.

## II. First-Layer Analysis: Right Result ≠ Right Job

The essence of this pitfall is that the **Agent**'s acceptance criteria and the user's acceptance criteria are simply not on the same dimension. The default logic of every current **Agent** is **surface-result-oriented**: as long as the explicit action in the user's instruction (e.g. "**upload to GitHub**") is completed, the task counts as successful. But in the real world, every task has two layers of acceptance criteria:

**Surface criteria**: whether functionality is implemented, whether results are delivered. For example: getting the repo link, code that runs, a page that opens. This part is visible, and is what all tools optimize for.

**Hidden criteria**: whether the process is compliant, whether authorship is clear, whether risks are controllable, whether the work is maintainable long-term. This part is invisible, depends heavily on the user's professional background and cognitive level, and almost no tool actively covers it. The hidden criteria for the same "**upload to GitHub**" demand differ vastly across users:

- An ordinary user: getting the link and being able to share/clone it is enough.
- A user with a legal background: must ensure the committer identity matches the actual person, retain evidence of authorship, and avoid downstream copyright disputes.
- A professional developer: also needs to verify commit conventions, branching strategy, sensitive info leakage, license compatibility, and more.

**Agent** does not actively recognize these differences — it simply executes at the lowest surface standard. If the user is not aware of the hidden risks, the pitfalls just sit there forever — they may never explode, or they may turn into a fatal problem much later.

This leads to an ironic reality: the less experienced the user, the more they find **AI** easy to use; the more professional the user, the more effort they spend cleaning up **AI**'s mess. A non-coder uses **AI** to write a webpage, sees full features and a good-looking interface, and thinks it's a godsend. But a professional developer looks at it and sees logic holes, security risks, and redundant code everywhere — the time to fix it might be longer than writing it from scratch.

## III. Second-Layer Inquiry: Why the Whole Industry is Building "Half-Baked" Agents

Digging deeper, this is not a problem of any single product — it's a problem of the entire industry's pace of development. After **OpenClaw** went viral, almost overnight, everyone rushed into the **Agent** arena. Big cloud vendors released cloudified versions, the open-source community produced countless variations — everyone is competing on who supports more tools, who executes faster, who interrupts the user less, and who can autonomously complete more complex tasks. "**Fully autonomous**" and "**zero intervention**" became the biggest selling points. But very few people paused to answer the most basic question: what actually determines an **Agent**'s capability boundary?

The industry's current default answer is "**foundation model**". Everyone is racing on which model to pick, how big the context window is, how fast the inference is — as if a stronger model automatically means a better **Agent**. But this pitfall taught me that model capability is just one of many influencing factors, and may not even be the most critical one.

At least these factors, whose impact on the final result has been severely underestimated:

1. **The Agent's hard-coded rules**: which operations are allowed to run autonomously, which require user confirmation, which are absolute no-go red lines. These are baked into the code, with priority far above the user's natural-language instructions. Writing "don't change my identity" ten thousand times in the context is nowhere near as effective as one line of pre-validation at the tool's underlying layer.
2. **The engineering implementation approach**: are rules placed in the context and relying on the model's memory, or pushed down to system-level mandatory checks? Does tool invocation rely on the model freelancing, or on standardized interfaces and permission control? The former relies on the model's self-discipline, with errors being the norm; the latter is a hard constraint, and almost never fails.
3. **The default design priority**: is it efficiency-first, with minimal user interruption and autonomous task completion? Or safety-first, with mandatory user confirmation at critical nodes, choosing slow-but-sure over fast-but-wrong? There's no absolute right or wrong, but users have the right to know what orientation the tool they're using takes.

Even more interesting: the industry still lacks a unified **Agent** capability evaluation framework. Whether it's academic benchmarks like **AgentBench, GAIA**, or the various community comparisons, almost all measure "**task completion rate**" — can the thing finish, and if it finishes, it wins. Nobody measures "**rule compliance**", nobody measures "**hidden risk coverage**", and nobody measures "**autonomous decision overreach rate**".

Evaluation direction determines product direction. Everyone competes toward "**can-do**", and naturally no one goes deep into "**doing-it-right**". Users just follow public opinion to pick tools — today thinking this one is good, tomorrow switching to another — when in fact it's just different fits for different scenarios, with no absolute good or bad.

## IV. Third-Layer Reflection: Multi-Agent Is Not a Universal Cure

Many people say: if a single **Agent** doesn't think things through, just use multi-agent — one for execution, one for security, one for legal, one for code quality, with expert teams collaborating, surely all issues can be considered. The logic is sound, but at this stage, multi-agent simply cannot solve the core problem. Most current multi-agent frameworks are essentially "**having a few Agents chat with each other in natural language**". Expert **Agent**s can offer opinions, but whether they're heard, whether they're acted on, all depends on the executor **Agent**'s "**mood**" — there's no mandatory constraint, no rigid gate, and in essence the executor **Agent** is still a one-voice autocracy. When the expert says "this identity is wrong, there's an authorship risk", the executor **Agent**, eager to finish the task, can simply choose to ignore it — and the user ends up with a flawed result.

The points you care about, the executor **Agent** doesn't care about. The risks the expert cares about, the executor **Agent** doesn't care about. This is the core bottleneck of multi-agent: **collaboration without gates is no collaboration at all**.

A truly effective multi-agent system must delineate permission boundaries in advance:

- **Red-line dimension**: e.g. data security, authorship compliance, core rules — the expert **Agent** in the corresponding domain has veto power. If the determination fails, the task must pause and be corrected, and absolutely must not enter the next stage — even at the cost of efficiency.
- **Optimization dimension**: e.g. code performance, interaction experience, implementation elegance — the expert only has advisory power, and whether to adopt it can be decided by the executor **Agent** or escalated to the user.

A rule-priority arbitration mechanism must also be in place. If multiple experts' red-line rules conflict — e.g. the security expert demands no network access, while the business expert demands network access — the system cannot deadlock; it must arbitrate by preset priority, and in extreme cases escalate to human decision.

Without this set of gates and arbitration, multi-agent is just a pretty-looking demo toy, and fails the moment it hits production.

## V. Systematic Reconstruction: An Engineering Framework for Production-Grade Agents

Following these questions all the way through, I've gradually formed a complete judgment: the reason current **Agent**s aren't reliable is that the industry is still building tools with a "**foundation-model appendage**" mindset, rather than treating the **Agent** as an industrial-grade execution system. If we treat it as an industrial system, we should follow the full chain of "**goal definition → resource supply → scheduling governance → output validation → iterative optimization**", and break it into engineering modules with distinct functions — not haphazardly assemble a few parallel concepts with confused dimensions.

### Terminology Mapping Note

The many "**XX Engineering**" terms popular in the current AI industry are generalized buzzwords formed during technological development, with inconsistent dimensions and fuzzy boundaries. This article does a strict decomposition and reconstruction along "**function + lifecycle**":

- **Context Engineering** → decomposed into Knowledge Engineering + Scenario & Constraint Engineering + Task Information Engineering
- **Harness Engineering** → corresponds to "**Execution Governance Engineering**", with an additional architectural correction: "**separation of governance authority and scheduling authority**"
- **Output validation concepts** → unified as **Output Validation Engineering**

### (1) Input & Scenario Layer: Defining "Under What Environment, Doing What"

This is the starting point of every task, and determines the standard for all subsequent execution.

**1. Prompt Engineering**
The most basic demand input, solving the problem of "what is the goal". This is the most-researched part of the industry, but also just a small piece of the whole system.

**2. Scenario & Constraint Engineering**
This is the most easily overlooked but most impactful module. The same demand, under different background environments, has vastly different acceptance criteria. For example, for dumplings: when hungry, you want speed; with seafood allergies, you want ingredient safety; at a pastry competition, you want aesthetics and taste. The industry used to scatter these constraints everywhere with no unified management, leading to chaotic scenario-switching. All quality requirements, compliance rules, time limits, and taboo restrictions are essentially environment variables, and should be managed uniformly, switching with the scenario.

**3. Knowledge Engineering**
Structured management of long-term domain knowledge, rule libraries, and standard libraries — corresponding to the LLM Wiki direction proposed by Karpathy. Its core difference from task information: task information is ephemeral, single-use data for a single task; knowledge is universal, long-term accumulation across scenarios, e.g. authorship rules, code conventions, compliance clauses. The "rules written in docs but still ignored" issue I encountered earlier is essentially a case of knowledge not being engineered — relying on naive RAG recall alone, with very poor stability.

**4. Task Information Engineering**
Injection of ephemeral information for a single task — e.g. this run's repo address, specific requirement details, temporary special requirements — valid only for that single task.

### (2) Capability & Resource Layer: What Capabilities and Resources to Use

This is the **Agent**'s capability supply module, on which all execution actions depend.

**1. Tool Engineering**
Standardized encapsulation and permission management of general-purpose tools. Tools are not textual descriptions scattered in the context — they are a capability substrate with unified interfaces, permission tiers, input/output specifications, and invocation auditing. For example, **Git** operations, file read/write, code execution — all should have engineering-grade invocation protocols and security gates, rather than telling the model how to use them via **prompt** each time. Currently, the vast majority of **Agent** tool invocations stay at the "**descriptive level**", without engineering-grade governance — which is the root cause of many security overreach issues.

**2. Standard & Skill Engineering**
Reusable, standardized encapsulation of how to do things. What people commonly call **Skill** is not scattered **prompt** fragments — it's standardized workflows + quality validation standards. Its core is to encapsulate the method of "**how to do something**" into modules with clear inputs/outputs, applicable boundaries, and quality baselines, which can be aggregated, reused, and invoked across **Agent**s — rather than letting the model derive it from scratch every time.

### (3) Scheduling & Governance Layer: What Rules Govern Capability Scheduling

This layer is the backbone of the whole system, determining its baseline reliability. A separation must be made here: **governance authority and scheduling authority must be separated**. If the same module is responsible for both scheduling tasks and validating rules, it naturally has the motivation to bypass validation in order to complete the task — a logical loophole.

**1. Execution Governance Engineering**
Solely responsible for rigid governance and gate validation, a neutral rule executor with only "**block/allow**" authority and no scheduling decision authority. Its core is pre-hooks, step gates, exception circuit-breaking, and global rule enforcement — regardless of whether the model thought of it, at each node validation must execute, and if it fails, block immediately. For example, before a **Git** commit, force-validate the author identity; if it doesn't match the user config, block it immediately, never giving the model a chance to freelance. This is the only core means of turning "**soft rules**" into "**hard constraints**".

**2. Task Adaptation & Scheduling Mechanism**
An independent decision module, based on task type + environmental background, matching the optimal tools, skills, knowledge, and models. It is responsible for "**what to choose to do**", but cannot bypass the governance layer's validation.

### (4) Output & Validation Layer: Whether the Result Actually Passes

Corresponds to **Output Validation Engineering**. The core logic: turn the **80%** of objective quality standards from "**relying on the model's self-reflection**" into "**engineering-mandated validation**". First, generate an objective validation checklist (authorship, format, compliance, functionality, etc.) based on the task + environment; then use an independent validation logic to check the output, and if it fails, automatically bounce it back for correction until the baseline quality bar is met, and only then hand it over to the user for the remaining **20%** subjective preference tuning.

> We don't need **AI** to reach 100, and it can't. But at the very least, hold the line on the 80-point objective baseline, and leave the remaining 20% of human taste and subjective judgment to humans. This is the sensible boundary of human-machine collaboration.

### (5) Iteration & Optimization Layer: How to Keep Getting Better

The core is **Loop Engineering**. It is not a capability item parallel to the modules above, but an iteration-loop mechanism that spans all layers. It's not just a single **Agent**'s self-reflection, but continuous optimization of all modules across the full chain: prompt optimization, environmental rule completion, tool iteration, skill upgrade, knowledge update, validation standard refinement. Its value is to let the entire system evolve dynamically, continuously closing gaps based on each task's results and feedback, rather than being a static set of rules.

## VI. Boundaries & Reflection: This Is Not AGI, It's the Pragmatic Choice of the Moment

Having come this far, I must first dispel the myth and clarify the boundaries of this framework, to avoid blowing it up into a universal truth.

**First, this is an engineering solution for Agents, not a requirement for AGI.**

The essence of this framework is to use engineering scaffolding to fill in the current deficiencies in the inherent capabilities of large models. Because models can't remember long-term rules, we need Knowledge Engineering. Because models don't actively perceive scenarios, we need Scenario & Constraint Engineering. Because models don't self-validate their baseline, we need Output Validation Engineering. If **AGI** truly arrives, with autonomous cognition, scenario perception, and value judgment, the vast majority of these peripheral engineering modules would be replaced by inherent capabilities. It is a transitional implementation for the current technological stage, not some eternal underlying law.

**Second, this framework only applies to deterministic production tasks, not all AI scenarios.**

The premise of "**80% objective standards + 20% subjective preferences**" is that the task has clear objective acceptance criteria. For production tasks like code development, transaction processing, compliance auditing, and workflow execution, this system works extremely well. But for purely creative tasks — literature, art design, deep strategic consulting — objective standards may be less than **20%**, with the core value entirely in the subjective part, where this framework essentially fails. It is the infrastructure of production-grade **Agent**s, not the answer to general-purpose **AI**.

**Third, the value of these modules will decay as model capabilities improve.**

The Knowledge Engineering and Output Validation we put huge effort into today, if the model's rule-following and self-validation capabilities take a quantum leap in the future, will see their value drop significantly. But that doesn't mean doing this today is meaningless — technological evolution is always stepped, and within the current capability boundary, engineering is the most pragmatic and cost-effective implementation path.

## VII. Conclusion: Slowing Down Is the Fastest Path

The original intention of this reflection is not to deny any tool. On the contrary, I think the current open-source projects are all very well done, each with its own strengths and applicable scenarios. It's just that the whole industry is moving too fast — everyone is racing forward on efficiency and piling on features, and very few people pause to obsess over "**reliability**".

We always say **AI** needs to go from toy to production tool, but the core of a production tool has never been "**how much it can do**" — it's "**how clear the boundaries are, how controllable the risks are**". A tool with few features but clear boundaries — users dare to use it in production with confidence. A tool with powerful features but hidden pitfalls everywhere — can only ever be used for demos and play.

For me, this pitfall was more like a reminder: rather than chasing the latest model and the hottest tool, getting the underlying logic clear, the capability boundaries drawn, and the risk baseline held — that, paradoxically, is the fastest path.

After all, the one that goes far is always the one that walks steady.
