right now we agreed that agent creation is not a process of the platform anymore.. you only get to create customizable agents.. so the registeration wizard/flow should not include agent creation step.. 

OR atleast simplify it..

right now we no longer use roles on creation.. it's a fixed role for all registered tenants --> a General Business Assistant

so traits is not needed, Tone is more than enough.

also escalation rule is no longer needed.

and we need to make sure any dead codes around this to be cleaned up.. from model to even prompts.. (not sure if those information were even included on the prompt or not).

take a look around that and tell me what should be cleaned up.. tails.. dead codes, etc.


Show less
7:42 PM



Worked for 2m 11s
I checked the registration path and the runtime usage. Yes, there are real tails, not just UI clutter.

What Should Be Cleaned

Registration UI:

Remove Role dropdown.
Remove Traits selector.
Remove Escalation rule dropdown.
Rename “Agent Setup” to something like “Assistant Basics”.
Keep only Assistant name and Tone.
Registration backend:

apps/accounts/registration.py
configure_agent_profile() still accepts role, traits, escalation_rule.
It should accept only name and tone.
It should always set the fixed role internally as General Business Assistant, or better, stop storing role as user config.
Registration API:

apps/api/views.py
/api/register/businesses/<business_id>/agent/ still reads:
agentTitle
agentTraits
agentEscalation
Response still returns:
role
traits
escalationRule
These should be removed or reduced to fixed derived values.
Prompt/runtime:

apps/llm/ai_prompt_builder.py still injects:
self.agent.role or "AI Customer Specialist"
apps/voice/runtime.py still adds:
Role: ...
Traits: ...
These should be cleaned so the base assistant always behaves as a general business assistant, with tone as the only onboarding personality knob.
Model/data-model tails:

AgentProfile.role
AgentProfile.traits
AgentProfile.escalation_rule
Likely also old multi-agent shape fields if we want a clean production model:
agent_type values specialist/background
manager_agent
responsibilities
selected_kpis, custom_kpis, allow_custom_kpi_weighting
Since Custom Assistants now own specialization, these fields are confusing on AgentProfile.
API/admin/dashboard tails:

/api/agents/ still has create/update logic for agent type, role, manager, responsibilities, traits, escalation.
This should no longer be an “agent creation” API. It should become either read-only default assistant identity or a narrow “default assistant settings” endpoint: name + tone.
Dashboard still displays agent.role, with fallback “Role pending”. That should become a fixed label or disappear.
Recommendation
Do the clean version now: simplify registration to name + tone, make the default tenant agent a single fixed General Business Assistant, and move all specialization to CustomAssistant.

That means removing role/traits/escalation from onboarding, API payloads, prompts, and eventually from AgentProfile migrations. Keeping them “just unused” will confuse future work and recreate the same Custom Assistant vs AgentProfile blending problem later.