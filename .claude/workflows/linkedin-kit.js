export const meta = {
  name: 'linkedin-kit',
  description: 'Research, draft 4 candidate posts, judge with 3 lenses, fact-check, assemble, write, render and lint one LinkedIn kit',
  whenToUse: 'Evening run. args: { repo: "/abs/path", date: "YYYY-MM-DD", day_type: "story|carousel|contrarian|build_in_public|debate" }',
  phases: [
    { title: 'Research', detail: '4 researchers: niche news, LinkedIn platform, India+US market, target creators' },
    { title: 'Draft', detail: '4 ghostwriters, one angle each' },
    { title: 'Judge', detail: '3 lenses per candidate: hook/dwell, credibility, audience fit' },
    { title: 'Verify', detail: '2 refuters attack every number and claim in the winner; rewrite if needed' },
    { title: 'Assemble', detail: 'creator comments, connections, DMs, ideas; write JSON, render PDF, lint' },
  ],
}

if (!args || !args.repo || !args.date || !args.day_type) throw new Error('args must be {repo, date, day_type}')
const { repo, date, day_type } = args

const CTX = `Repository: ${repo}. First read ${repo}/config.yaml (persona, niche, audience, offer, proof_points, stories, target_creators, banned_phrases, weekly_mix) and ${repo}/data/memory.md if it exists (everything already posted or banked: never repeat a hook or idea, do build on what performed). Post date ${date}, day type "${day_type}". Audience is India first, US second. Return only the structured output.`

// ---------------------------------------------------------------- schemas ----
const INTEL = {
  type: 'object',
  properties: {
    bullets: { type: 'array', items: { type: 'object', properties: { fact: { type: 'string' }, source: { type: 'string' } }, required: ['fact', 'source'] } },
    questions: { type: 'array', items: { type: 'string' } },
    hooks: { type: 'array', items: { type: 'string' } },
  },
  required: ['bullets', 'questions', 'hooks'],
}
const SLIDE = { type: 'object', properties: { title: { type: 'string' }, body: { type: 'string' } }, required: ['title', 'body'] }
const CAROUSEL = { type: 'object', properties: { title: { type: 'string' }, subtitle: { type: 'string' }, cta: { type: 'string' }, slides: { type: 'array', items: SLIDE } }, required: ['title', 'subtitle', 'cta', 'slides'] }
const CANDIDATE = {
  type: 'object',
  properties: {
    angle: { type: 'string' }, hook: { type: 'string' }, text: { type: 'string' },
    self_comments: { type: 'array', items: { type: 'string' } },
    carousel: CAROUSEL,
  },
  required: ['angle', 'hook', 'text', 'self_comments'],
}
const SCORE = {
  type: 'object',
  properties: { hook: { type: 'number' }, dwell: { type: 'number' }, specificity: { type: 'number' }, fit: { type: 'number' }, total: { type: 'number' }, notes: { type: 'string' } },
  required: ['total', 'notes'],
}
const VERIFY = {
  type: 'object',
  properties: { claims: { type: 'array', items: { type: 'object', properties: { claim: { type: 'string' }, verified: { type: 'boolean' }, fix: { type: 'string' } }, required: ['claim', 'verified'] } } },
  required: ['claims'],
}
const ENGAGEMENT = { type: 'object', properties: { engagement_comments: { type: 'array', items: { type: 'object', properties: { creator: { type: 'string' }, topic: { type: 'string' }, comment: { type: 'string' } }, required: ['creator', 'topic', 'comment'] } } }, required: ['engagement_comments'] }
const NETWORK = {
  type: 'object',
  properties: {
    connection_requests: { type: 'array', items: { type: 'object', properties: { who: { type: 'string' }, why: { type: 'string' }, message: { type: 'string' } }, required: ['who', 'why', 'message'] } },
    dms: { type: 'array', items: { type: 'object', properties: { to: { type: 'string' }, message: { type: 'string' } }, required: ['to', 'message'] } },
    ideas_bank: { type: 'array', items: { type: 'string' } },
  },
  required: ['connection_requests', 'dms', 'ideas_bank'],
}
const RESULT = { type: 'object', properties: { path: { type: 'string' }, lint_ok: { type: 'boolean' }, lint_output: { type: 'string' }, pdf: { type: 'string' } }, required: ['path', 'lint_ok', 'lint_output'] }

// -------------------------------------------------------------- research ----
phase('Research')
const RESEARCH = [
  { key: 'news', prompt: 'Use WebSearch (3 to 5 queries) for the newest news in the niche from the last 7 days: product launches, numbers, reversals, funding, incidents. Return 5 bullets each with a source URL, 3 real questions people are asking about this (search forums, Reddit, Quora, LinkedIn), and 3 hook patterns you saw working.' },
  { key: 'platform', prompt: 'Use WebSearch (3 queries) for what is changing on LinkedIn this month: algorithm updates, formats that get reach (documents, text, video), hook styles that win, penalties. Return 5 bullets with source URLs, 3 questions creators ask, and 5 hook patterns currently working.' },
  { key: 'market', prompt: 'Use WebSearch (3 to 4 queries) for the India and US job and business market angle on the niche this month: hiring, layoffs, budgets, adoption stats, regulation. Return 5 bullets with source URLs, 3 questions professionals are asking, and 3 hook patterns.' },
  { key: 'creators', prompt: 'For each creator in config.target_creators, use WebSearch ("<name> LinkedIn <topic> 2026") to find what they have posted or argued about recently. Return one bullet per creator formatted "Creator name: current theme or take" with the best source URL, 3 questions their audiences ask, and 3 hook patterns they use.' },
]
const intel = (await parallel(RESEARCH.map(r => () =>
  agent(`${CTX}\n\nYou are the Content Intelligence agent (${r.key}). ${r.prompt}`, { label: `research:${r.key}`, phase: 'Research', schema: INTEL })
))).filter(Boolean)
const bullets = intel.flatMap(i => i.bullets).filter(b => b.source && b.source.startsWith('http'))
const questions = intel.flatMap(i => i.questions)
const hookPatterns = intel.flatMap(i => i.hooks)
log(`research: ${bullets.length} sourced facts, ${questions.length} questions, ${hookPatterns.length} hook patterns`)
const INTEL_TEXT = JSON.stringify({ bullets, questions, hooks: hookPatterns }, null, 1)

// ----------------------------------------------------------------- draft ----
phase('Draft')
const RULES = `Structure that beats the 2026 LinkedIn ranking model (360Brew: profile-topic match, dwell time, saves, substantive comments):
- Line 1: hook, at most 7 words, a pain or a result, never a question.
- Lines 2 and 3: a personal story or observation. Use only config.stories and config.proof_points as facts. If nothing fits, write it as an observation ("A founder told me last week...") and never invent a client name or metric.
- Body: 3 lessons or steps, numbered, one concrete detail each (a number, a tool, a ticket type).
- End: one question that invites argument or a story, plus one soft CTA (comment a word, DM, save).
- 600 to 1300 characters. One sentence per line. Blank line between blocks. No em-dashes. No external links. At most 3 hashtags at the end or none. No phrase from config.banned_phrases. First person. Numbers over adjectives. Works for an Indian and a US reader alike.
- self_comments: exactly 2, posted in the first 30 minutes: one adds a surprising detail, one asks the reader a question.
- carousel: ${day_type === 'carousel' ? 'REQUIRED' : 'include only if the body is naturally a list'}: title is a bold claim of at most 10 words, subtitle, cta of at most 8 words, 5 to 8 slides, each slide title at most 8 words and body at most 40 words.`
const ANGLES = [
  { key: 'story', brief: 'Story-first: open on a specific moment, make the lesson earned, keep the data to one number.' },
  { key: 'data', brief: 'Data-led: build the post around the single most surprising sourced number in the intel and what it means for the reader on Monday morning.' },
  { key: 'contrarian', brief: 'Contrarian: take the popular take in the intel and argue the opposite with evidence, respectfully, without strawmen.' },
  { key: 'operator', brief: 'Operator checklist: the exact steps a practitioner would run this week, with the trap at each step.' },
]
const draft = (a) => agent(`${CTX}\n\nYou are the Ghostwriter. Angle: ${a.brief}\n\nIntel (sourced facts, audience questions, hook patterns):\n${INTEL_TEXT}\n\n${RULES}\n\nWrite one post for ${date} (${day_type}). Set angle="${a.key}".`,
  { label: `draft:${a.key}`, phase: 'Draft', schema: CANDIDATE })

// ----------------------------------------------------------------- judge ----
const LENSES = [
  { key: 'hook-dwell', ask: 'Would a scrolling ops leader stop at line 1 and keep reading past line 3? Score hook (0-10) and dwell (0-10) on structure and rhythm. Set specificity and fit to 0.' },
  { key: 'credibility', ask: 'Is every claim concrete, sourced in the intel or config, and free of invented clients or numbers? Is the voice an operator, not a marketer? Score specificity (0-10). Set hook, dwell and fit to 0.' },
  { key: 'audience-action', ask: 'Does it fit founders and ops/CX leaders in India and the US, match the persona\'s niche so the ranking model trusts the author, and end with something people will actually comment or save? Score fit (0-10). Set hook, dwell and specificity to 0.' },
]
const judged = await pipeline(ANGLES,
  a => draft(a),
  async (cand, a) => {
    if (!cand) return null
    const votes = (await parallel(LENSES.map(l => () =>
      agent(`${CTX}\n\nYou are a judge (${l.key} lens). ${l.ask} total = the sum of the scores you set. notes: two sentences, the single biggest weakness.\n\nIntel for reference:\n${INTEL_TEXT}\n\nCandidate (angle ${cand.angle}):\n${JSON.stringify(cand, null, 1)}`,
        { label: `judge:${a.key}:${l.key}`, phase: 'Judge', schema: SCORE })
    ))).filter(Boolean)
    const total = votes.reduce((s, v) => s + (v.total || 0), 0)
    log(`judge: ${a.key} scored ${total} (${votes.map(v => v.notes.split('.')[0]).join(' | ')})`)
    return { cand, total, notes: votes.map(v => v.notes) }
  })
const ranked = judged.filter(Boolean).sort((x, y) => y.total - x.total)
if (!ranked.length) throw new Error('no candidate survived drafting and judging')
let winner = ranked[0].cand
log(`winner: ${winner.angle} (${ranked[0].total}) over ${ranked.slice(1).map(r => `${r.cand.angle} ${r.total}`).join(', ')}`)

// ---------------------------------------------------------------- verify ----
phase('Verify')
const REFUTERS = [
  { key: 'numbers', ask: 'Attack every number, percentage, date and count in the post and carousel. For each, say whether the intel or config supports it exactly; if not, give the corrected wording or "remove".' },
  { key: 'claims', ask: 'Attack every claim about a company, person, product or event. For each, say whether the intel supports it; if the intel is silent, WebSearch once to check; if still unsupported, give the corrected wording or "remove".' },
]
const verdicts = (await parallel(REFUTERS.map(r => () =>
  agent(`${CTX}\n\nYou are a fact-checker (${r.key}). Default to verified=false when uncertain. ${r.ask}\n\nIntel:\n${INTEL_TEXT}\n\nPost:\n${JSON.stringify(winner, null, 1)}`,
    { label: `verify:${r.key}`, phase: 'Verify', schema: VERIFY })
))).filter(Boolean)
const bad = verdicts.flatMap(v => v.claims).filter(c => !c.verified)
log(`verify: ${bad.length} unsupported claim(s)`)
if (bad.length) {
  const fixed = await agent(`${CTX}\n\nYou are the Ghostwriter doing a correction pass. Apply these fixes exactly, keep the angle, structure, hook length and character budget, and change nothing else:\n${JSON.stringify(bad, null, 1)}\n\n${RULES}\n\nPost to correct:\n${JSON.stringify(winner, null, 1)}`,
    { label: 'rewrite', phase: 'Verify', schema: CANDIDATE })
  if (fixed) winner = fixed
}

// -------------------------------------------------------------- assemble ----
phase('Assemble')
const [eng, net] = await parallel([
  () => agent(`${CTX}\n\nYou are the Engagement Engine. Write exactly 10 comments, one per creator in config.target_creators, to be posted on their newest post 15 minutes before our post goes live. Each: 2 to 4 lines, adds an insight, a number from the intel, or a respectful counterpoint tied to that creator's current theme (see the creators bullets in the intel). Never open with praise. End with a question when natural.\n\nIntel:\n${INTEL_TEXT}\n\nOur post today (for coherence):\n${winner.text}`,
    { label: 'engagement', phase: 'Assemble', schema: ENGAGEMENT }),
  () => agent(`${CTX}\n\nYou are the Network Builder. Return exactly 10 connection_requests (5 hiring managers or founders, 5 peers; describe who as "title @ company type", why they matter, and a message of at most 300 characters on the template "Hey [Name], saw your post on [Topic]. The point about [Specific] hit me. Building in the same space, would love to connect." with [Specific] personalised each time), exactly 5 dms for people who engage with today's post (pure value, a checklist or number, zero pitch), and exactly 5 ideas_bank hooks for future posts (at most 7 words each, contrarian or story-based, none already in memory.md).\n\nIntel:\n${INTEL_TEXT}\n\nOur post today:\n${winner.text}`,
    { label: 'network', phase: 'Assemble', schema: NETWORK }),
])
if (!eng || !net) throw new Error('assemble agents failed')

const kit = {
  date, day_type,
  post: { hook: winner.hook, text: winner.text },
  self_comments: winner.self_comments.slice(0, 2),
  ...(winner.carousel && winner.carousel.slides && winner.carousel.slides.length ? { carousel: winner.carousel } : {}),
  engagement_comments: eng.engagement_comments.slice(0, 10),
  connection_requests: net.connection_requests.slice(0, 10),
  dms: net.dms.slice(0, 5),
  ideas_bank: net.ideas_bank.slice(0, 5),
  intel: bullets.slice(0, 8).map(b => `${b.fact} — ${b.source}`),
  pipeline: { winner: winner.angle, scores: ranked.map(r => ({ angle: r.cand.angle, total: r.total })), corrections: bad.length },
}

const result = await agent(`Write this JSON exactly (pretty-printed, UTF-8, ensure_ascii off) to ${repo}/content/${date}.json, then run \`cd ${repo} && python studio.py run content/${date}.json\`. If lint prints violations, fix the JSON minimally (keep meaning and structure) and re-run, up to 3 attempts. Return path, lint_ok, the final lint output, and the PDF path if one was rendered.\n\n${JSON.stringify(kit, null, 1)}`,
  { label: 'write+lint', phase: 'Assemble', schema: RESULT })
if (!result) throw new Error('write+lint agent failed')
log(`kit ${result.lint_ok ? 'passed' : 'FAILED'} lint: ${result.path}`)
return { ...result, winner: winner.angle, hook: winner.hook, scores: kit.pipeline.scores, corrections: bad.length }
