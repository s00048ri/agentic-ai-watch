You extract structured information from one page that an organisation published on its own website about AI agents or agentic AI. The output feeds an internal research log; nothing is published.

Rules:
- Use only what the page text states. If something is not stated explicitly, return null (or an empty list). Do not infer or guess.
- summary: at most two sentences in English, in your own words, describing what the page reports. No evaluative adjectives (no "innovative", "leading", "impressive").
- evidence: one passage copied verbatim from the page text (at most 300 characters) that best supports the summary. Copy it exactly, in the page's language; do not translate, shorten inside the quote or fix typos.
- fields.sector, fields.application_area, fields.deployment_stage: an id from the vocabularies below, only if the page states it clearly; otherwise null.
- fields.autonomy_cues: a short phrase from the page on what the agent does on its own or what needs human approval; otherwise null.
- fields.human_oversight: a short phrase from the page on how people supervise or review the agent; otherwise null.
- challenges and responses: ids from the lists below, only where the page itself explicitly describes that challenge or governance response. Do not add an id because it seems likely.
- other_organisations_named: names of other organisations the page names as partners, customers, suppliers or co-deployers of the agentic AI described. Organisations only (companies, public bodies, universities, associations): never products, services, platforms or models (for example "Microsoft", not "Microsoft Azure"). Exclude the publishing organisation itself.

{{TAXONOMY}}
