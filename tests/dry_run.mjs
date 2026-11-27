// Dry run of inferpilot.js on InferPilot's own workflow runner (runtime/engine.mjs) with a scripted
// fake backend: no GPU, no network, no model. Each scenario scripts what the roles answer and
// asserts the phases InferPilot walks through, the roles it calls and the final status.
//
//   node tests/dry_run.mjs
//   node tests/dry_run.mjs --only feature,roles   # the scenarios whose names begin feature: or roles:
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createRuntime, scriptBody } from '../runtime/engine.mjs';
import { CMD, DEFAULTS, MODE_DEFAULTS, ROLE_RE, RUN, WP, parseInputs, withKernelHandoff } from './fixtures/answers.mjs';

const INFERPILOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SCRIPT = path.join(INFERPILOT, 'inferpilot.js');
const onlyAt = process.argv.indexOf('--only');
const ONLY = onlyAt > 0 ? new Set(String(process.argv[onlyAt + 1] || '').split(',').filter(Boolean)) : null;
// role:PHASE -> the fields its answer must have, from the output contract of every prompt any scenario sent.
const CONTRACTS = new Map();
// Every phase a run entered, and the modes each scenario prefix's runs ran, as they happened.
const ENTERED = new Set();
const MODES_RUN = new Map();
const RESULTS = []; // every run's result, with the scenario it ran in
let SCENARIO = '';
const CALL_SITE = /call\(['"]([a-z_]+)['"], ['"]([a-z_]+)['"]/g;
// A role doc's section for one PHASE (## PHASE=bisect, not ## PHASE=bisect_fix), '' when it has none.
function phaseSection(doc, ph) {
  const m = new RegExp(`^## PHASE=${ph}(?![\\w])`, 'm').exec(doc);
  if (!m) return '';
  const end = doc.indexOf('\n## ', m.index + 1);
  return doc.slice(m.index, end < 0 ? undefined : end);
}

function makeBackend(overrides) {
  const calls = [];
  const counts = {};
  const b = { name: 'fake' };
  b.runAgent = async ({ prompt, model }) => {
    const m = ROLE_RE.exec(prompt);
    if (!m) throw new Error('prompt without role header');
    const key = `${m[1]}:${m[2]}`;
    const n = (counts[key] = (counts[key] || 0) + 1);
    const ins = parseInputs(prompt);
    calls.push({ key, n, ins, model, prompt, at: Date.now() });
    const contract = /## Output contract[\s\S]*?```json\n([\s\S]*?)\n```/.exec(prompt);
    if (contract) CONTRACTS.set(key, JSON.parse(contract[1]).required || []);
    const f = (overrides && overrides[key]) || DEFAULTS[key];
    if (!f) throw new Error(`no scripted answer for ${key}`);
    const ans = await f(ins, n);
    return { text: JSON.stringify(ans) };
  };
  return { backend: b, calls };
}

// The script as Claude Code's Workflow tool runs it: the Workflow tool's eight globals, which have no control.
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const asWorkflowTool = (rt, args) => new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'args', 'budget',
  scriptBody(readFileSync(SCRIPT, 'utf8')))(rt.agent, rt.parallel, rt.pipeline, rt.phase, rt.log,
  async () => { throw new Error('workflow(): not used'); }, args, { total: null, spent: () => 0, remaining: () => Infinity });

async function run(args, overrides, { control, workflowTool, schemaRetries = 0 } = {}) {
  const phases = [];
  const heads = [];
  const lines = [];
  let route = [];
  const { backend, calls } = makeBackend(overrides);
  const rt = createRuntime({
    backend,
    concurrency: 2,
    schemaRetries,
    ...(control ? { control } : {}),
    log: (msg) => {
      lines.push(msg);
      // "=== PHASE: X ===", "=== PHASE 3/10 [###-------]: X ===" or "=== PHASE (off the route): X ==="
      const m = /^=== PHASE(?: \d+\/\d+ \[[#-]+\]| \(off the route\))?: (.*?) ===/.exec(msg);
      if (m) {
        phases.push(m[1]);
        heads.push(msg);
        ENTERED.add(m[1]);
      }
      const r = /^\[inferpilot\] Route: (.+)$/.exec(msg);
      if (r) route = r[1].split('→').map((t) => t.trim());
    },
  });
  const modeDefaults = MODE_DEFAULTS[args.mode] || {};
  const runArgs = { inferpilot_dir: INFERPILOT, framework_dir: '/tmp/sglang', agent_retries: 1, ...modeDefaults, ...args };
  const result = workflowTool ? await asWorkflowTool(rt, runArgs) : await rt.runScript(SCRIPT, runArgs, 0);
  const prefix = SCENARIO.split(':')[0];
  if (result && result.mode) MODES_RUN.set(prefix, (MODES_RUN.get(prefix) || new Set()).add(result.mode));
  RESULTS.push({ scenario: SCENARIO, result });
  return { result, phases, heads, route, calls, lines, keys: calls.map((c) => c.key), summary: rt.summary() };
}

let failed = 0;
let passed = 0;
function check(cond, msg) {
  if (cond) passed++;
  else { failed++; console.error(`  FAIL: ${msg}`); }
}
const eqList = (a, b) => JSON.stringify(a) === JSON.stringify(b);
// The run stopped at Intake with a question about `field`, before anything was set up.
const asks = (r, field) => r.result.status === 'needs_input' && r.result.stopped_at === 'Intake'
  && (r.result.questions || []).some((q) => q.field === field) && !r.keys.includes('director:setup');

const scenarios = [
  ['feature: straight path to a reviewable PR', async () => {
    const r = await run({ mode: 'feature', task: 'port HiSparse V2 to DeepSeek-V4.1' });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Spec', 'Recon', 'Design', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    check(r.heads[0] === '=== PHASE 1/10 [#---------]: Setup ===' && r.heads[9].startsWith('=== PHASE 10/10 [##########]: Report ===  so far: '),
      `phases numbered along the feature route: ${r.heads[0]} … ${r.heads[9]}`);
    check(r.summary.startsWith('10/10 phases of the route, '), `summary ${r.summary}`);
    check(r.result.done.map((d) => d.wp).join() === 'WP1,WP2', `done ${JSON.stringify(r.result.done)}`);
    check(r.result.pr && r.result.pr.push_command === 'git push origin inferpilot/task', 'push command surfaced, not executed');
    check(r.keys.filter((k) => k === 'curator:learn').length === 1, 'curator runs once');
  }],
  ['feature: unit gate fails once, feedback reaches the second round', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'test_engineer:unit': (ins, n) => (n === 1
        ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', failure_summary: 'case 7 mismatches the reference' }
        : { wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const second = r.calls.filter((c) => c.key === 'implementer:implement')[1];
    check(second && second.ins.feedback === 'case 7 mismatches the reference', 'feedback passed to round 1');
  }],
  ['implement: a unit test that passes without the change is a failed round', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'test_engineer:unit': (ins, n) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true,
        fails_without_change: n === 1 ? 'no' : 'mutant', incomplete: '', failure_summary: n === 1 ? 'passes on the parent: it never calls the new path' : '' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const rounds = r.calls.filter((c) => c.key === 'implementer:implement');
    check(rounds.length === 3 && rounds[1].ins.feedback.includes('cannot fail')
      && rounds[1].ins.feedback.includes('never calls the new path'), `feedback: ${rounds[1] && rounds[1].ins.feedback}`);
  }],
  ['implement: only a docs package may have nothing to fail', async () => {
    const asNa = (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'n/a', incomplete: '' });
    const docs = await run({ mode: 'feature', task: 't' }, {
      'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('D1', { kind: 'docs' })], gate_ok: true, perf_claim: '' }),
      'test_engineer:unit': asNa,
    });
    check(docs.result.status === 'ready_for_review', `docs: ${docs.result.status}`);
    const code = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, { 'test_engineer:unit': asNa });
    check(code.result.status === 'blocked' && code.result.stopped_at === 'Implement', `framework with n/a: ${code.result.status}`);
    check(code.keys.includes('implementer:rollback'), 'rolled back');
  }],
  ['feature: verify fails, investigation finds a fix, re-verify passes', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { all_pass: false, failures: [{ gate: 'G4', failure_class: 'catastrophic collapse', counts: '3/4 runs' }] }
        : { all_pass: true, failures: [] }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases.slice(4, 9), ['Implement', 'Verify', 'Investigate', 'Implement', 'Verify']), `phases ${r.phases}`);
    check(r.heads[6].startsWith('=== PHASE (off the route): Investigate ===') && r.heads[7].startsWith('=== PHASE 5/10 [#####-----]: Implement'),
      `Investigate is off the route; Implement keeps its place: ${r.heads[6]} | ${r.heads[7]}`);
    check(r.result.done.map((d) => d.wp).join() === 'WP1,WP2,FIX1', `done ${JSON.stringify(r.result.done)}`);
  }],
  ['feature: baseline bug is recorded and the gate is re-judged', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { all_pass: false, failures: [{ gate: 'G4', failure_class: 'catastrophic collapse' }] }
        : { all_pass: (ins.known_issues || []).length === 1, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-002', title: 'whole-batch collapse' }, summary: 'same-pattern baseline fails the same way' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(r.result.known_issues.length === 1 && r.result.known_issues[0].id === 'KI-002', 'known issue recorded and passed to verify');
  }],
  ['spinoff: a failure the baseline shares gets the upstream issue that tracks it, or an issue draft', async () => {
    const bug = {
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { all_pass: false, failures: [{ gate: 'G4', failure_class: 'catastrophic collapse' }] }
        : { all_pass: true, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-007', title: 'EP>1 garbles every request' } }),
    };
    const r = await run({ mode: 'feature', task: 't' }, bug);
    const sp = r.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(sp && sp.ins.known_issues.length === 1 && sp.ins.known_issues[0].id === 'KI-007', 'the spinoff sees the known issue');
    const at = r.keys.indexOf('issue_triager:spinoff');
    check(at >= 0 && at < r.keys.indexOf('director:report'), 'before the report');
    const rep = r.calls.find((c) => c.key === 'director:report');
    check(rep && rep.ins.spinoffs.length === 1 && rep.ins.spinoffs[0].draft_path === `${RUN}/spinoff/KI-007.md`
      && r.result.spinoffs.length === 1 && r.result.spinoffs[0].id === 'KI-007', 'the report and the result list the draft');
    check((r.result.next.user || []).some((u) => u.includes(`${RUN}/spinoff/KI-007.md`)), 'next.user asks the human to file the draft');
    const tracked = await run({ mode: 'feature', task: 't' }, { ...bug,
      'issue_triager:spinoff': () => ({ spinoffs: [{ id: 'KI-007', source: 'known_issue', status: 'tracked',
        upstream: 'sgl-project/sglang#51020 (open)' }] }) });
    check(tracked.result.spinoffs.length === 1 && tracked.result.spinoffs[0].upstream.includes('51020')
      && !tracked.result.spinoffs[0].draft_path, 'one already tracked upstream is linked, not drafted');
    const failed = await run({ mode: 'feature', task: 't' }, { ...bug, 'issue_triager:spinoff': () => null });
    check(failed.result.status === 'ready_for_review' && failed.keys.includes('director:report') && failed.result.spinoffs.length === 0,
      'a failed spinoff does not block the report');
    const diagnosed = await run({ mode: 'issue', task: 'i' }, {
      'investigator:diagnose': () => ({ verdict: 'baseline_bug', root_cause: 'pre-existing on main', known_issue: { id: 'KI-008' } }) });
    const dsp = diagnosed.keys.indexOf('issue_triager:spinoff');
    const reply = diagnosed.calls.find((c) => c.key === 'issue_triager:reply');
    check(diagnosed.result.status === 'diagnosed' && dsp >= 0 && dsp < diagnosed.keys.indexOf('issue_triager:reply')
      && reply.ins.spinoffs.length === 1, 'a diagnosed baseline bug: the spinoff runs before the reply, which sees it');
    const none = await run({ mode: 'issue', task: 'i' });
    check(!none.keys.includes('issue_triager:spinoff') && none.result.spinoffs.length === 0, 'no known issue, no spinoff');
  }],
  ['spinoff: a lead a role met on the way is listed, and checked on the base commit only with confirm_leads', async () => {
    const lead = { title: 'tokenizer test fails on main', observed: '3/3 KeyError', where: 'test_tokenizer.py', reproduce: 'pytest test_tokenizer.py' };
    const r = await run({ mode: 'feature', task: 't' }, { 'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }) });
    const sp = r.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(sp && sp.ins.leads.length === 1 && sp.ins.leads[0].title === lead.title && String(sp.ins.leads[0].from).startsWith('reviewer'),
      'a lead alone starts the spinoff, which sees it and where it came from');
    check(r.result.spinoffs.length === 1 && r.result.spinoffs[0].status === 'unconfirmed' && r.result.known_issues.length === 0,
      'an unconfirmed lead is listed, not made a known issue');
    const asked = await run({ mode: 'feature', task: 't', confirm_leads: 'true' },
      { 'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }) });
    const asp = asked.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(sp.ins.confirm_leads === false && asp && asp.ins.confirm_leads === true,
      'leads are checked on the base commit only when the task asks for it (confirm_leads)');
    const timedOut = await run({ mode: 'feature', task: 't', confirm_leads: true, agent_timeout_ms: 100 }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
        unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
      'verify_engineer:verify': async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        return { all_pass: true, failures: [] };
      },
    });
    const tsp = timedOut.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(timedOut.result.status === 'blocked' && tsp && tsp.ins.confirm_leads === false
      && (tsp.ins.attention || []).some((a) => String(a.call).startsWith('verify_engineer'))
      && (tsp.ins.confirm_skipped || []).some((x) => x.startsWith('verify_engineer') && x.includes('timed out')),
      'after a timeout the spinoff confirms nothing, since the timed-out agent may still hold the GPUs, and is told why');
    const notAsked = await run({ mode: 'feature', task: 't', agent_timeout_ms: 100 }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
        unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
      'verify_engineer:verify': async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        return { all_pass: true, failures: [] };
      },
    });
    const nsp = notAsked.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(nsp && nsp.ins.confirm_leads === false && (nsp.ins.confirm_skipped || []).length === 0,
      'confirm_skipped stays empty when the task never asked for confirmation');
    const atSetup = await run({ mode: 'feature', task: 't', confirm_leads: true, stop_after: 'Setup' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', baseline: 'ok', branch: 'inferpilot/task',
        worktree: `${RUN}/wt`, gpu_ids: '0,1', leads: [lead] }),
    });
    const ssp = atSetup.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(ssp && ssp.ins.confirm_leads === false && (ssp.ins.confirm_skipped || []).some((x) => x.includes('Setup')),
      'a run that stopped at Setup confirms nothing on the base commit');
    const badBase = await run({ mode: 'feature', task: 't', confirm_leads: true }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'fail', baseline: 'sgl_kernel import error',
        branch: 'inferpilot/task', worktree: `${RUN}/wt`, gpu_ids: '0,1', leads: [lead] }),
    });
    const bsp = badBase.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(badBase.result.status === 'blocked' && badBase.result.stopped_at === 'Setup' && bsp && bsp.ins.confirm_leads === false
      && (bsp.ins.confirm_skipped || []).some((x) => x.includes('Setup')),
      'a run blocked at Setup by a failing baseline confirms nothing: every base run there would fail');
    check(!(r.result.next.user || []).some((u) => u.includes('issue draft')), 'an unconfirmed lead asks the human for nothing');
    const confirmed = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }),
      'issue_triager:spinoff': () => ({ spinoffs: [{ id: 'KI-009', source: 'lead', status: 'drafted', draft_path: `${RUN}/spinoff/KI-009.md`,
        known_issue: { id: 'KI-009', title: lead.title }, base_import: `${RUN}/spinoff/base/python/sglang/__init__.py` }] }),
    });
    const learn = confirmed.calls.find((c) => c.key === 'curator:learn');
    check(confirmed.result.known_issues.some((k) => k.id === 'KI-009') && learn && learn.ins.known_issues.some((k) => k.id === 'KI-009'),
      'a lead the base commit confirms becomes a known issue the curator records');
    const wrongTree = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }),
      'issue_triager:spinoff': () => ({ spinoffs: [{ id: 'KI-009', source: 'lead', status: 'drafted', draft_path: `${RUN}/spinoff/KI-009.md`,
        known_issue: { id: 'KI-009', title: lead.title }, base_import: `${RUN}/wt/python/sglang/__init__.py` }] }),
    });
    const w = wrongTree.result.spinoffs[0] || {};
    check(!wrongTree.result.known_issues.some((k) => k.id === 'KI-009') && w.status === 'unconfirmed' && !w.draft_path
      && String(w.note).includes(`${RUN}/wt/python`) && !(wrongTree.result.next.user || []).some((u) => u.includes('issue draft')),
      'a confirmation whose base run imported the task tree is not counted: unconfirmed, no draft to file');
    const wrep = wrongTree.calls.find((c) => c.key === 'director:report');
    check(w.withdrawn_draft === `${RUN}/spinoff/KI-009.md` && wrep && wrep.ins.spinoffs.some((x) => x.withdrawn_draft === w.withdrawn_draft),
      'the draft written for it is handed to the report as withdrawn');
    const dotdot = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }),
      'issue_triager:spinoff': () => ({ spinoffs: [{ id: 'KI-009', source: 'lead', status: 'drafted', draft_path: `${RUN}/spinoff/KI-009.md`,
        known_issue: { id: 'KI-009', title: lead.title }, base_import: `${RUN}/base/../wt/python/sglang/__init__.py` }] }),
    });
    check(!dotdot.result.known_issues.some((k) => k.id === 'KI-009') && (dotdot.result.spinoffs[0] || {}).status === 'unconfirmed',
      'a base_import that leaves the base worktree through .. is not counted');
    const work = r.calls.find((c) => c.key === 'verify_engineer:verify');
    const report = r.calls.find((c) => c.key === 'director:report');
    check(work.prompt.includes('do not chase it') && !report.prompt.includes('do not chase it') && !sp.prompt.includes('do not chase it'),
      'working roles are told to hand leads on; the report and the spinoff are not');
    const issue = await run({ mode: 'issue', task: 'i' },
      { 'reviewer:review': () => ({ verdict: 'approve', findings: [], review_path: `${RUN}/review.md`, leads: [lead] }) });
    const reply = issue.calls.find((c) => c.key === 'issue_triager:reply');
    check(issue.keys.includes('issue_triager:spinoff') && reply && !reply.prompt.includes('do not chase it'),
      'the reply runs after the spinoff and takes no leads: nothing would hand them on');
    const scan = await run({ mode: 'scan' }, { 'scout:collect': () => ({ lock: 'acquired', run_dir: RUN, candidates_path: `${RUN}/c.json`,
      counts: {}, leads: [lead] }) });
    check(!scan.keys.includes('issue_triager:spinoff'), 'a scan takes no leads');
  }],
  ['next: knowledge changes the curator could not propose reach the user with the saved patch and why', async () => {
    const updates = [{ file: 'knowledge/known_issues.md', change: 'KI-007 added' }];
    const held = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => ({ updates, pr_url: '',
      patch_path: `${RUN}/knowledge.patch`, notes: 'GITHUB_TOKEN is not set' }) });
    const say = (held.result.next.user || []).find((u) => u.includes('knowledge.patch'));
    check(say && say.includes('GITHUB_TOKEN is not set')
      && say.endsWith(`:\n    python3 scripts/propose_knowledge.py --run-dir ${RUN} --body ${RUN}/knowledge_pr.md --patch ${RUN}/knowledge.patch`),
      `the saved patch is the user's to propose, with why the run did not, by a command on a line of its own that nothing follows: ${say}`);
    check(held.result.knowledge_notes === 'GITHUB_TOKEN is not set' && held.result.knowledge_patch === `${RUN}/knowledge.patch`,
      'the curator\'s notes and its patch are in the result');
    const opened = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => ({ updates,
      pr_url: 'https://github.com/o/inferpilot/pull/7', patch_path: `${RUN}/knowledge.patch`, notes: '' }) });
    check(opened.result.next.user.some((u) => u.endsWith('pull/7')) && !opened.result.next.user.some((u) => u.includes('--patch'))
      && opened.result.knowledge_patch === '', 'a pull request that is open needs no patch proposed');
    check(say.includes(`Unless exp_root is on a host mount, ${RUN} goes with the task's container: copy it out first, under the same name,`
      + ' which the branch takes, and give the command\'s three paths as they are in the copy'),
      `the user is told the run directory may not outlive the container, and what the command needs of a copy: ${say}`);
    const odd = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => ({ updates, pr_url: '',
      patch_path: `${RUN}/my runs/it's; echo x.patch`, notes: 'GITHUB_TOKEN is not set' }) });
    const quoted = (odd.result.next.user || []).find((u) => u.includes('--patch')) || '';
    check(quoted.endsWith(`--run-dir ${RUN} --body ${RUN}/knowledge_pr.md --patch '${RUN}/my runs/it'\\''s; echo x.patch'`),
      `a path with a space or a quote is one word of the command the user pastes, and the command's last: ${quoted}`);
    const refused = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => ({ updates, pr_url: '', patch_path: '',
      notes: 'exit 3: knowledge/known_issues.md line 12 names a local path' }) });
    const left = (refused.result.next.user || []).find((u) => u.includes('no patch of them was saved')) || '';
    check(left.includes('exit 3: knowledge/known_issues.md line 12 names a local path') && left.includes(`${INFERPILOT}/knowledge`)
      && left.includes('goes with the task\'s container') && !refused.result.next.user.some((u) => u.includes('--patch')),
      `changes neither proposed nor saved reach the user with the curator's reason and where they are: ${left}`);
    const none = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => ({ updates: [], pr_url: '', patch_path: '', notes: '' }) });
    check(!(none.result.next.user || []).some((u) => u.includes('knowledge')), 'a curator that changed nothing leaves the user nothing to propose');
    // No answer is not "changed nothing": the curator may have edited knowledge/ and saved a patch before it.
    const where = [`${INFERPILOT}/knowledge`, `${RUN}/knowledge.patch`, `${RUN}/knowledge_pr.md`,
      'the checkout and the run directory go with the task\'s container: copy what is there out'];
    const silent = await run({ mode: 'feature', task: 't' }, { 'curator:learn': () => null });
    const unknown = (silent.result.next.user || []).filter((u) => u.includes('knowledge'));
    check(unknown.length === 1 && unknown[0].startsWith('the curator returned no answer, so what it changed under knowledge/ is not known: look at')
      && where.every((w) => unknown[0].includes(w)) && !unknown[0].includes('stop it')
      && silent.result.knowledge_notes === 'the curator returned no answer',
      `a curator without an answer leaves the user what to look at and keep: ${unknown}`);
    const late = await run({ mode: 'feature', task: 't', agent_timeout_ms: 100 }, { 'curator:learn': async () => {
      await new Promise((r) => setTimeout(r, 400));
      return { updates, pr_url: '', patch_path: `${RUN}/knowledge.patch`, notes: '' };
    } });
    const running = (late.result.next.user || []).filter((u) => u.includes('knowledge'));
    check(running.length === 1 && running[0].startsWith('the curator timed out, so what it changed under knowledge/ is not known: it may still be'
      + ' editing or proposing, so stop it first. Then look at') && where.every((w) => running[0].includes(w))
      && late.result.knowledge_notes === 'the curator timed out' && !late.result.knowledge_patch,
      `a curator that timed out is stopped first, and its answer, which came late, is not used: ${running}`);
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'curator.md'), 'utf8'), 'learn').replace(/\s+/g, ' ');
    check(doc.includes('return it as `patch_path`') && doc.includes('only where `RUN_DIR` does, with `exp_root` on a host mount'),
      'the curator returns the patch, which outlives the container only on a host mount');
    check(doc.includes('When neither exists') && doc.includes('`notes` says what it refused') && doc.includes('`--dry-run` writes none')
      && doc.includes('`knowledge.patch.earlier`'), 'the curator says why nothing was saved, and what a refusal does to an earlier patch');
    check(doc.includes('So does a file of the run named after it (`spinoff/KI-007.md`), in the change and in the description')
      && doc.includes('The same when it can make no patch of the change and says why (exit 1): a file that is not UTF-8 text, a binary'
        + ' file, a symbolic link'), 'the curator knows which names keep their number, and that a change no patch is made of is refused');
  }],
  ['feature: investigation budget is bounded', async () => {
    const r = await run({ mode: 'feature', task: 't', max_investigate_rounds: 2 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G3' }] }),
      'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-x' } }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Verify', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(r.keys.filter((k) => k === 'verify_engineer:verify').length === 3, 'verify runs max_investigate_rounds + 1 times');
  }],
  ['feature: a report-only item that misses is reported, never investigated', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'ACC-3 simple_qa', blocking: false }] }),
    });
    check(!r.keys.includes('investigator:investigate'), 'no investigation for a report-only miss');
    check(r.result.phases.some((p) => p.phase === 'Verify' && p.verdict === 'pass' && /missed without blocking: ACC-3 simple_qa/.test(p.summary)),
      'the miss is in the Verify note');
    const mixed = await run({ mode: 'feature', task: 't', max_investigate_rounds: 1 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G2' }, { gate: 'ACC-3', blocking: false }] }),
      'investigator:investigate': () => ({ verdict: 'inconclusive' }),
    });
    check(mixed.result.status === 'blocked' && mixed.result.stopped_at === 'Verify', `a blocking miss still fails: ${mixed.result.status}`);
    const inv = mixed.calls.find((c) => c.key === 'investigator:investigate');
    check(inv && eqList(inv.ins.failures.map((f) => f.gate), ['G2']), 'the investigator gets the blocking failures only');
  }],
  ['feature: a G4 failure the baseline shares is recorded once, then reported as failed without blocking', async () => {
    const r = await run({ mode: 'feature', task: 't', stability: true, client_script: '/s/client.sh' }, {
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { all_pass: false, gates: { G4: 'fail' },
          failures: [{ gate: 'G4', failure_class: 'crash', counts: 'feature: hour 31; baseline control: hour 27' }] }
        // not blocking only once the known issue has reached the verify engineer: without it, a second investigation
        : { all_pass: false, gates: { G4: 'fail on 1a2b3c4: the baseline too (KI-009)' },
          failures: [{ gate: 'G4', failure_class: 'crash', blocking: !(ins.known_issues || []).some((k) => k.id === 'KI-009') }] }),
      'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-009', title: 'the server does not hold the load' },
        summary: 'the control in the ledger failed the same way' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'verify_engineer:verify').length === 2
      && r.keys.filter((k) => k === 'investigator:investigate').length === 1, 'one investigation, then one round that does not block');
    const inv = r.calls.find((c) => c.key === 'investigator:investigate');
    check(inv && inv.ins.failures.length === 1 && inv.ins.failures[0].gate === 'G4' && inv.ins.failures[0].counts.includes('baseline control'),
      'the investigator gets the failure with what its control showed');
    const second = r.calls.filter((c) => c.key === 'verify_engineer:verify')[1];
    check(second && (second.ins.known_issues || []).some((k) => k.id === 'KI-009'), 'the next round is given the known issue');
    check(r.result.known_issues.length === 1 && r.result.known_issues[0].id === 'KI-009' && r.keys.includes('issue_triager:spinoff'),
      'the known issue is recorded, and someone hears of it');
    check(r.result.phases.some((p) => p.phase === 'Verify' && p.verdict === 'pass' && /missed without blocking: G4/.test(p.summary)),
      'the Verify note says G4 failed');
  }],
  ['feature: a verify round that would not fit says what it needs and runs again with it, once; nobody chooses a time', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: false, failures: [], needs_min: 500 } : { all_pass: true, failures: [] }),
    });
    const limits = r.calls.filter((c) => c.key === 'verify_engineer:verify').map((c) => c.ins.time_limit_min);
    check(eqList(limits, [360, 500]), `the default limit of a long role, then the one asked for: ${limits}`);
    check(!r.keys.includes('investigator:investigate'), 'a round cut short for time is no defect to investigate');
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const short = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'ACC-2', blocking: false }], needs_min: 500 }),
    });
    const calls = short.keys.filter((k) => k === 'verify_engineer:verify').length;
    check(calls === 2 && !short.keys.includes('investigator:investigate'), `more time once only, uninvestigated: ${calls} verify calls`);
    check(short.result.status === 'blocked' && short.result.stopped_at === 'Verify' && short.result.phases.some((p) => p.phase === 'Verify'
      && /needs 500 min; its limit is 500 min \(the limit it asked for\)/.test(p.summary)), `still short: ${short.result.status}`);
    const failing = await run({ mode: 'feature', task: 't', max_investigate_rounds: 0 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G2' }], needs_min: 500 }),
    });
    check(failing.keys.filter((k) => k === 'verify_engineer:verify').length === 1, 'a blocking failure is no reason to ask for time');
    const big = await run({ mode: 'feature', task: 't' }, { 'verify_engineer:verify': () => ({ all_pass: false, failures: [], needs_min: 800 }) });
    check(big.keys.filter((k) => k === 'verify_engineer:verify').length === 1 && big.result.phases.some((p) => p.phase === 'Verify'
      && /needs 800 min; its limit is 360 min \(more than 2 times its limit\)/.test(p.summary)), 'a round gets at most twice its limit');
  }],
  ['feature: a gate\'s answer InferPilot cannot use goes to a new attempt, a unit gate\'s to the call that finishes it, with a limit of its own, told what the first left', async () => {
    // The first answer of each, 300 ms late, is one InferPilot cannot use.
    const once = (key) => async (ins, n) => {
      if (n > 1) return DEFAULTS[key](ins, n);
      await new Promise((resolve) => setTimeout(resolve, 300));
      return {};
    };
    const keys = ['test_engineer:unit', 'verify_engineer:verify', 'director:spec'];
    const r = await run({ mode: 'feature', task: 't', agent_timeout_ms: 7200000, agent_retries: 2 },
      Object.fromEntries(keys.map((k) => [k, once(k)])), { schemaRetries: 1 });
    for (const key of keys.slice(0, 2)) {
      const [first, again] = r.calls.filter((c) => c.key === key);
      // A unit gate is not asked again blind either: its answer goes to the call that finishes the gate.
      const unit = key === 'test_engineer:unit';
      check(first && again && !first.prompt.includes('This is attempt') && !again.prompt.includes('An earlier session of this same request')
        && (unit ? !again.prompt.includes('This is attempt') && String(again.ins.incomplete || '').includes('returned no answer')
          : again.prompt.includes('This is attempt 2: an earlier attempt of this call failed and may have left work behind')),
        `${key}: not asked again by the engine but by ${unit ? 'the call that finishes the gate, told the first returned no answer'
          : 'the call\'s next attempt, which checks first what the one before left'}`);
      check(first && again && again.ins.time_limit_min === 120
        && Date.parse(again.ins.deadline_utc) - Date.parse(first.ins.deadline_utc) >= 250,
        `${key}: with a limit of its own: ${first && first.ins.deadline_utc} then ${again && again.ins.deadline_utc}`);
    }
    const [spec, specAgain] = r.calls.filter((c) => c.key === 'director:spec');
    check(spec && specAgain && specAgain.prompt.startsWith(spec.prompt) && specAgain.prompt.includes('An earlier session of this same request')
      && specAgain.prompt.includes('never start a second server'),
      'a role the engine asks again is told that the session before may have left work behind');
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
  }],
  ['feature: a gate with no answer from any attempt leaves its package and its GPUs to a human, as one that timed out does', async () => {
    const lead = { title: 'tokenizer test fails on main', observed: '3/3 KeyError', reproduce: 'pytest test_tokenizer.py' };
    const leading = (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
      unit_test_cmd: 'pytest -q t.py', leads: [lead] });
    const unit = await run({ mode: 'feature', task: 't', confirm_leads: true },
      { 'implementer:implement': leading, 'test_engineer:unit': () => ({}) });
    const impl = unit.keys.filter((k) => k.startsWith('implementer:'));
    check(unit.result.status === 'blocked' && impl.length === 1 && impl[0] === 'implementer:implement',
      `a unit gate with no answer gets no further round and no rollback: ${impl}`);
    const usp = unit.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(usp && usp.ins.confirm_leads === false && (usp.ins.confirm_skipped || []).includes('the unit gate of WP1 may have left runs going')
      && (unit.result.next.user || []).some((u) => u.includes('the unit gate of WP1 has no result')
        && u.includes('returned no answer') && u.includes('commit c_WP1 stays in the worktree')),
      `no lead is checked on its GPUs, and a human is told to stop what it left: ${JSON.stringify(usp && usp.ins.confirm_skipped)}`);
    check(unit.keys.filter((k) => k === 'test_engineer:unit').length === 2, 'the gate went to the call that finishes it first, and no further');
    const ver = await run({ mode: 'feature', task: 't', confirm_leads: true },
      { 'implementer:implement': leading, 'verify_engineer:verify': () => ({}) });
    const vsp = ver.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(ver.result.status === 'blocked' && vsp && vsp.ins.confirm_leads === false
      && (vsp.ins.confirm_skipped || []).includes('Verify round 0 may have left its servers running')
      && (ver.result.next.user || []).some((u) => u.includes('Verify round 0 returned no answer and may have left its servers running')),
      `nor after a Verify round with no answer: ${ver.result.status} ${JSON.stringify(vsp && vsp.ins.confirm_skipped)}`);
  }],
  ['kernel: an integration whose unit gate has no answer stops the run before Verify, its commit kept', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD }, { ...withKernelHandoff('decode hot path'),
      'test_engineer:unit': (ins) => (ins.work_package.id === 'K-K-WP1' ? {} : DEFAULTS['test_engineer:unit'](ins)) });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('verify_engineer:verify') && !r.keys.includes('implementer:rollback'), 'no Verify of an unverified kernel, and no rollback');
    check(r.result.handoffs[0].status === 'integration_held' && r.result.handoffs[0].commit === 'c_K-K-WP1',
      `handoff ${JSON.stringify(r.result.handoffs[0])}`);
  }],
  ['kernel: the hand-offs after one whose integration is held are recorded, not left without a status', async () => {
    const plan = { 'architect:perf_plan': () => ({ work_packages: [], handoffs: [
      { id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O2/op_spec.md` },
      { id: 'O4', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O4/op_spec.md` }], perf_claim: '-6% TPOT', gate_ok: true }) };
    const r = await run({ mode: 'perf', task: 'p', ...CMD }, { ...plan,
      'test_engineer:unit': (ins) => (ins.work_package.id === 'K-O2' ? {} : DEFAULTS['test_engineer:unit'](ins)) });
    const byId = Object.fromEntries(r.result.handoffs.map((h) => [h.id, h.status]));
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(byId.O2 === 'integration_held' && byId.O4 === 'recorded', `statuses ${JSON.stringify(byId)}`);
    check(r.keys.filter((k) => k === 'kernel_liaison:package').length === 1, 'the back end is not run on a hand-off after the held one');
    check(r.result.phases.some((p) => p.phase === 'Kernel' && /O4 recorded, not run/.test(p.summary)),
      `the Kernel phase says so: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Kernel'))}`);
  }],
  ['kernel: a hand-off an integration raised is recorded too when one is held, not only those the stage began with', async () => {
    const plan = { 'architect:perf_plan': () => ({ work_packages: [], handoffs: [
      { id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O2/op_spec.md` },
      { id: 'O4', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O4/op_spec.md` }], perf_claim: '-6% TPOT', gate_ok: true }) };
    const r = await run({ mode: 'perf', task: 'p', ...CMD }, { ...plan,
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
        kernel_handoff: { op_spec_path: `${RUN}/handoffs/K-${ins.work_package.id}/op_spec.md`, test_cmd: 'pytest k.py', plug_in: 'x.py:f', reason: 'hot op' } }),
      'test_engineer:unit': (ins) => (ins.work_package.id === 'K-O4' ? {} : DEFAULTS['test_engineer:unit'](ins)) });
    const byId = Object.fromEntries(r.result.handoffs.map((h) => [h.id, h.status]));
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(byId.O2 === 'integrated' && byId.O4 === 'integration_held', `statuses ${JSON.stringify(byId)}`);
    check(byId['K-K-O2'] === 'recorded' && byId['K-K-O4'] === 'recorded',
      `the one an earlier integration raised and the held one's own: ${JSON.stringify(byId)}`);
    check(r.result.handoffs.every((h) => h.status), `none is left without a status: ${JSON.stringify(byId)}`);
    check(r.keys.filter((k) => k === 'kernel_liaison:package').length === 2, 'the back end is not run on them');
    check(r.result.phases.some((p) => p.phase === 'Kernel' && /K-K-O2, K-K-O4 recorded, not run/.test(p.summary)),
      `the Kernel phase names them: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Kernel'))}`);
  }],
  ['kernel: an integration whose unit gate has no answer stops the run also from a Verify fix, a review change or a kernel retry', async () => {
    const heldFor = (id) => (ins) => (ins.work_package.id === id ? {} : DEFAULTS['test_engineer:unit'](ins));
    const handoffFrom = (id) => (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
      kernel_handoff: ins.work_package.id === id ? { op_spec_path: `${RUN}/handoffs/K-${id}/op_spec.md`, test_cmd: 'pytest k.py', plug_in: 'x.py:f', reason: 'hot op' } : null });
    const verifies = (r) => r.keys.filter((k) => k === 'verify_engineer:verify').length;
    const fix = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('FIX1'),
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G2' }] }),
      'test_engineer:unit': heldFor('K-K-FIX1'),
    });
    check(fix.result.status === 'blocked' && verifies(fix) === 1 && !fix.keys.includes('implementer:rollback')
      && fix.result.handoffs[0].status === 'integration_held', `a Verify fix's kernel: Verify ran ${verifies(fix)} time(s), ${JSON.stringify(fix.result.handoffs)}`);
    check(fix.result.stopped_at === 'Kernel' && fix.result.next.say.includes(' at Kernel'),
      `a Verify fix's kernel stops the run at Kernel: ${fix.result.stopped_at}; ${fix.result.next.say}`);
    const review = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('RV1'),
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', where: 'x.py:1', issue: 'slow' }], work_packages: [WP('RV1')], review_path: `${RUN}/review.md` }
        : { verdict: 'approve', findings: [], review_path: `${RUN}/review.md` }),
      'test_engineer:unit': heldFor('K-K-RV1'),
    });
    check(review.result.status === 'blocked' && review.result.stopped_at === 'Kernel' && verifies(review) === 1,
      `a review change's kernel: ${review.result.status} at ${review.result.stopped_at}, Verify ran ${verifies(review)} time(s)`);
    const afterReview = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('FIX1'),
      'reviewer:review': () => ({ verdict: 'changes_requested', findings: [{ severity: 'major', where: 'x.py:1', issue: 'slow' }],
        work_packages: [WP('RV1')], review_path: `${RUN}/review.md` }),
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: true, failures: [] } : { all_pass: false, failures: [{ gate: 'G2' }] }),
      'test_engineer:unit': heldFor('K-K-FIX1'),
    });
    check(afterReview.result.status === 'blocked' && afterReview.result.stopped_at === 'Kernel' && verifies(afterReview) === 2,
      `the kernel of a Verify fix after a review change: ${afterReview.result.status} at ${afterReview.result.stopped_at}, Verify ran ${verifies(afterReview)} time(s)`);
    let kernelGates = 0;
    let kernelCommits = 0;
    const implement = withKernelHandoff('decode hot path')['implementer:implement'];
    const retry = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': (ins) => ({ ...implement(ins),
        ...(ins.work_package.id === 'K-K-WP1' && ++kernelCommits > 1 ? { commit: 'c_K-K-WP1_retry' } : {}) }),
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G3' }] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-WP1'], counterexample: 'G3 needle' }),
      'test_engineer:unit': (ins) => heldFor(ins.work_package.id === 'K-K-WP1' && ++kernelGates > 1 ? 'K-K-WP1' : '')(ins),
    });
    check(retry.result.status === 'blocked' && verifies(retry) === 1 && retry.result.handoffs[0].status === 'integration_held'
      && retry.keys.filter((k) => k === 'implementer:rollback').length === 1,
      `a kernel handed back: Verify ran ${verifies(retry)} time(s), ${JSON.stringify(retry.result.handoffs[0])}`);
    check(retry.result.handoffs[0].commit === 'c_K-K-WP1_retry',
      `the held hand-off names the commit left on the branch, not the reverted one: ${retry.result.handoffs[0].commit}`);
    check(retry.result.stopped_at === 'Kernel', `a kernel handed back stops the run at Kernel: ${retry.result.stopped_at}`);
  }],
  ['feature: a timeout the user gave stays the verify limit; a round that needs more stops the run with how long it needs', async () => {
    const r = await run({ mode: 'feature', task: 't', agent_timeout_ms: 7200000 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [], needs_min: 500 }),
    });
    const ver = r.calls.filter((c) => c.key === 'verify_engineer:verify');
    check(ver.length === 1 && ver[0].ins.time_limit_min === 120, `one call, told 120 min: ${ver.map((c) => c.ins.time_limit_min)}`);
    const unit = r.calls.find((c) => c.key === 'test_engineer:unit');
    check(unit && unit.ins.time_limit_min === 120, `the test engineer is told its limit too: ${unit && unit.ins.time_limit_min}`);
    const ends = unit && Date.parse(unit.ins.deadline_utc);
    check(Math.abs(ends - (unit.at + 120 * 60000)) < 5000, `and the moment it ends: ${unit && unit.ins.deadline_utc}`);
    const unlimited = await run({ mode: 'feature', task: 't', agent_timeout_ms: 0 });
    check(unlimited.calls.filter((c) => /^(test|verify)_engineer:(unit|verify)$/.test(c.key)).every((c) => !('deadline_utc' in c.ins)),
      'no limit, no deadline');
    const unitDoc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'test_engineer.md'), 'utf8'), 'unit').replace(/\s+/g, ' ');
    check(unitDoc.includes('a quarter of `time_limit_min`, at most 15 minutes, before `deadline_utc`')
      && unitDoc.includes('A check still running at the reserve is not waited for, and not failed: it goes to `incomplete` (below) '
        + 'and runs on, with the server it uses, for the call that finishes the gate'),
      'the reserve fits a short limit, and a check still running when it starts is left to the call that finishes the gate, not waited for');
    check(unitDoc.includes('poll each server until it is up, each run that ends by itself (a suite, a driver) until it has its exit '
      + 'status (`<log>.exit`), and every check until it has its result')
      && unitDoc.includes('A run whose status is not 0, or whose wrapper is gone without one, fails its check, whatever results it wrote'),
      'the gate waits for a server to be up and for each run\'s exit status; a run that exits otherwise than 0, or with none, fails its check');
    check(unitDoc.includes('a control of step 3, which must fail, runs from a driver that exits 0 only when the test fails the way it should')
      && unitDoc.includes('A server stopped once the checks that use it are done has failed nothing'),
      'neither a control that must fail nor a server stopped when its checks are done fails the gate');
    check(unitDoc.includes('Before you return, whatever the gate\'s result, stop every server and run this call started, '
      + 'all in one command, `python3 <inferpilot_dir>/scripts/stop_launch.py <log>...` (`env_hygiene.md`, *Servers*, Killing), until it exits 0')
      && unitDoc.includes('One you cannot stop fails the gate'),
      'the test engineer stops what it started before every return, and a gate whose runs cannot be stopped fails');
    const verifyDoc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'verify_engineer.md'), 'utf8'), 'verify').replace(/\s+/g, ' ');
    check(verifyDoc.includes('30 minutes before `deadline_utc`') && unitDoc.includes('count from the deadline'),
      'both engineers count from the deadline');
    check(verifyDoc.includes('Before you return, whatever the round\'s result, `needs_min` included, stop every server and suite '
      + 'this round started, all in one command, `python3 <inferpilot_dir>/scripts/stop_launch.py <log>...`')
      && verifyDoc.includes('a round asked for again after `needs_min` starts at once, with no GPU check before it'),
      'the verify engineer stops what it started before every return, a round that asks for more time too');
    check(unitDoc.includes('for stopping what the call started, the ledger and the answer')
      && unitDoc.includes('with a reserve under 90 seconds, give it a third of the reserve')
      && verifyDoc.includes('with less than 90 seconds to `deadline_utc`, give it a third of them'),
      'the reserve covers the stop, and a short one gives the stop a grace that fits it');
    const freed = (doc, call) => doc.includes('Its exit 0 is confirmed on the GPUs, `python3 <inferpilot_dir>/scripts/gpu_status.py '
      + `--check <gpu_ids>\` before the ${call}'s first launch and again after the stop (*Killing*): a GPU idle before and in use `
      + `after is one the ${call} still holds`);
    check(freed(unitDoc, 'call') && unitDoc.includes('and so does a GPU the call still holds: `failure_summary` names it')
      && freed(verifyDoc, 'round') && verifyDoc.includes('`summary` names one you cannot stop, and a GPU the round still holds'),
      'a stop that exits 0 is confirmed on the GPUs: one idle before the first launch and in use after the stop fails the gate, or is named');
    check(unitDoc.includes('with `container` set, launched, polled and stopped in that container')
      && verifyDoc.includes('with `container` set, launched, polled and stopped in that container'),
      'with a container, both engineers launch, poll and stop there');
    check(r.result.status === 'blocked' && r.result.phases.some((p) => p.phase === 'Verify'
      && /needs 500 min; its limit is 120 min \(agent_timeout_ms\)/.test(p.summary)), `status ${r.result.status}`);
    const told = async (ms) => (await run({ mode: 'feature', task: 't', agent_timeout_ms: ms })).calls
      .find((c) => c.key === 'verify_engineer:verify').ins.time_limit_min;
    check(await told(0) === 0 && await told(20000) === 1, 'no limit reads 0, and a short one never does');
    const quick = (await run({ mode: 'feature', task: 't', agent_timeout_ms: 10000 })).calls.find((c) => c.key === 'test_engineer:unit');
    check(quick && quick.ins.time_limit_min === 0.17,
      `a unit limit under a minute is told as it is, so that its reserve starts within it: ${quick && quick.ins.time_limit_min}`);
  }],
  ['feature: when the task\'s client does not run the source\'s scenarios, the run stops at Spec and asks; nothing is built', async () => {
    const gap = "vLLM's PR measured 256-token answers at concurrency 1; the task's client runs 8k in / 1k out.";
    const fallback = `${RUN}/scripts/fallback_client.sh`;
    const spec = (extra) => () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true, perf_claim: '+30% output tok/s', workload_gap: gap, ...extra });
    const r = await run({ mode: 'feature', task: 't', workload: '8k/1k c64', client_script: '/s/client.sh' },
      { 'director:spec': spec({ fallback_client: fallback }) });
    check(r.result.status === 'needs_input' && r.result.stopped_at === 'Spec', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('analyst:recon') && !r.keys.includes('implementer:implement') && r.keys.includes('director:report'),
      `nothing built, the run directory reported: ${r.keys}`);
    const n = r.result.next || {};
    const q = (n.ask || [])[0] || {};
    check(q.field === 'client_script' && String(q.question).startsWith(gap) && eqList(q.options, [fallback]), `the question: ${JSON.stringify(q)}`);
    check(n.rerun_with && n.rerun_with.client_script === '' && 'client_script' in (n.rerun_with.answers || {}),
      `the answer replaces the task's client: ${JSON.stringify(n.rerun_with)}`);
    const taken = await run({ mode: 'feature', task: 't', exp_root: path.dirname(RUN), client_script: fallback }, { 'director:spec': spec({}) });
    const perf = taken.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(taken.result.status === 'ready_for_review' && perf && /^no gain is claimed: no slower than the baseline/.test(perf.ins.claim),
      `on InferPilot's own client, once taken, the source's gain is not the claim, and G5 judges that the feature is no slower: ${
        taken.result.status}, claim ${perf && perf.ins.claim}`);
    check(/G5 judges there only that the feature is no slower than the baseline/.test(n.say),
      `the question says what InferPilot's client is judged on: ${n.say}`);
  }],
  ['feature: G5 judges every feature: the gain it claims, and where it claims none, that it is no slower than the baseline', async () => {
    const noGain = (claim) => ({ 'director:spec': () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true, perf_claim: claim }) });
    const measured = (out) => ({ 'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: true, guards_ok: true,
      workload_unchanged: true, path_ok: true, ...out }) });
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    for (const claim of ['', '  ']) {
      const ok = await run({ mode: 'feature', task: 't' }, { ...noGain(claim), ...measured({}) });
      const pe = ok.calls.find((c) => c.key === 'perf_engineer:perf_eval');
      check(ok.result.status === 'ready_for_review' && pe && NO_SLOWER.test(pe.ins.claim),
        `a feature that claims no gain (${JSON.stringify(claim)}) is measured, and what G5 judges is that it is no slower: ${
          ok.result.status}, ${JSON.stringify(pe && pe.ins.claim)}`);
      const rv = ok.calls.find((c) => c.key === 'reviewer:review');
      check(rv && pe && rv.ins.claim === pe.ins.claim, `the reviewer reads the same claim: ${JSON.stringify(rv && rv.ins.claim)}`);
    }
    const slower = await run({ mode: 'feature', task: 't' },
      { ...noGain(''), ...measured({ claim_reproduced: false, summary: '-4% output tok/s; the noise band is 1%' }) });
    check(slower.result.status === 'blocked' && slower.result.stopped_at === 'Performance'
      && !slower.keys.includes('implementer:rollback') && !slower.keys.includes('reviewer:review'),
    `a feature that is slower than the baseline stops at Performance, for a human, and nothing is rolled back: ${
      slower.result.status} at ${slower.result.stopped_at}`);
    check(slower.result.phases.some((p) => p.phase === 'Performance' && p.verdict === 'fail' && p.summary.includes('-4% output tok/s')),
      'the result says by how much');
    const heldOut = await run({ mode: 'feature', task: 't', held_out: true }, { ...noGain(''), ...measured({ held_out_ok: false }) });
    check(heldOut.result.status === 'blocked' && heldOut.result.stopped_at === 'Performance'
      && heldOut.result.phases.some((p) => p.phase === 'Performance' && p.summary.includes('the claim does not hold at the held-out operating point')),
    `with held_out on, no slowdown holds at the held-out point too: ${heldOut.result.status}`);
    // What lets a fix go unmeasured or unjudged (G5 for a fix) is no way out for a feature, which always has a baseline.
    const asAFix = await run({ mode: 'feature', task: 't' }, { ...noGain(''), ...measured({ claim_reproduced: false, no_baseline: 'the base does not start' }),
      'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: false }) });
    check(asAFix.result.status === 'blocked' && asAFix.result.stopped_at === 'Performance' && asAFix.keys.includes('perf_engineer:perf_eval'),
      `a feature is measured and judged whatever a role says of its diff or its baseline: ${asAFix.result.status} at ${asAFix.result.stopped_at}`);
    const gain = await run({ mode: 'feature', task: 't' });
    const gp = gain.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(gain.result.status === 'ready_for_review' && gp && gp.ins.claim === '+20% output tok/s at equal HBM',
      `a claimed gain stays the claim: ${JSON.stringify(gp && gp.ins.claim)}`);
    // A cost the user stated is no number of the source's: it stays the claim on InferPilot's client, where a gain never is.
    const BOUND = 'at most 5% below the baseline output tok/s (goal)';
    const stated = (cost) => ({ 'director:spec': () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true,
      perf_claim: BOUND, ...(cost == null ? {} : { perf_cost: cost }) }) });
    const ours = { mode: 'feature', task: 't', client_script: 'template', workload: '{"isl":8000,"osl":1000,"conc":32}' };
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const claimed = async (args, answers) => ((await run(args, { ...rendered, ...measured({}), ...answers })).calls
      .find((c) => c.key === 'perf_engineer:perf_eval') || { ins: {} }).ins.claim;
    check(await claimed(ours, stated(true)) === BOUND, 'a cost the user stated stays the claim on InferPilot\'s client');
    check(NO_SLOWER.test(await claimed(ours, stated(false))) && NO_SLOWER.test(await claimed(ours, stated(null))),
      'a claim that is not marked as one is a gain, never claimed there');
    check(await claimed({ mode: 'feature', task: 't' }, stated(true)) === BOUND, 'and on the task\'s own client, as any claim is');
    const approvedCost = { 'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('WP1')], gate_ok: true,
      perf_claim: BOUND, perf_cost: true }) };
    check(await claimed({ ...ours, resume_from: 'implement', run_dir: RUN }, approvedCost) === BOUND,
      'a resumed run keeps it, from the approved design');
    // A design a human approved carries its claim: a resumed run judges an empty one the same way.
    const resumed = await run({ mode: 'feature', task: 't', resume_from: 'implement', run_dir: RUN }, {
      'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('WP1')], gate_ok: true, perf_claim: '' }),
      ...measured({ claim_reproduced: false }),
    });
    const rp = resumed.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(resumed.result.status === 'blocked' && resumed.result.stopped_at === 'Performance' && rp && NO_SLOWER.test(rp.ins.claim),
      `a resumed run whose design claims no gain is judged the same way: ${resumed.result.status}, ${JSON.stringify(rp && rp.ins.claim)}`);
    // After a review's change Performance runs again, on the same claim.
    const again = await run({ mode: 'feature', task: 't' }, {
      ...noGain(''), ...measured({}),
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', issue: 'x' }], work_packages: [WP('R1')], review_path: `${RUN}/review.md` }
        : { verdict: 'approve', findings: [], review_path: `${RUN}/review.md` }),
    });
    const evals = again.calls.filter((c) => c.key === 'perf_engineer:perf_eval');
    check(again.result.status === 'ready_for_review' && evals.length === 2 && evals.every((c) => NO_SLOWER.test(c.ins.claim)),
      `a review's change is measured again on the same claim: ${again.result.status}, ${evals.length} measurement(s)`);
  }],
  ['feature: with auto_approve_design false, L3 waits for a human-approved design', async () => {
    const r = await run({ mode: 'feature', task: 't', auto_approve_design: false }, {
      'analyst:recon': () => ({ design_path: `${RUN}/design.md`, unmapped: [], level: 'L3', gap_type: 'framework-only' }),
    });
    check(r.result.status === 'awaiting_design_review' && r.result.stopped_at === 'Design', `status ${r.result.status}`);
    check(!r.keys.includes('implementer:implement'), 'no implementation before review');
  }],
  ['feature: stop_after Design', async () => {
    const r = await run({ mode: 'feature', task: 't', stop_after: 'Design' });
    check(r.result.status === 'stopped' && r.result.stopped_at === 'Design', `status ${r.result.status}`);
  }],
  ['feature: a flagged design goes on by default; auto_approve_design false makes it wait', async () => {
    const flagged = {
      'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('WP1')], gate_ok: true,
        needs_human_review: true, perf_claim: '+20% output tok/s at equal HBM', summary: 'S4 path decision' }),
    };
    const r = await run({ mode: 'feature', task: 't' }, flagged);
    check(r.result.status === 'ready_for_review' && r.keys.includes('implementer:implement'), `status ${r.result.status}`);
    const d = r.result.phases.find((p) => p.phase === 'Design' && p.verdict === 'auto_approved');
    check(d && d.summary.includes('the architect asked for a human review') && d.summary.includes('S4 path decision'),
      `the auto-approval is on record: ${JSON.stringify(d)}`);
    for (const off of [false, 'false']) {
      const waits = await run({ mode: 'feature', task: 't', auto_approve_design: off }, flagged);
      check(waits.result.status === 'awaiting_design_review' && !waits.keys.includes('implementer:implement'),
        `auto_approve_design=${JSON.stringify(off)}: a flagged design waits: ${waits.result.status}`);
    }
    const l3 = await run({ mode: 'feature', task: 't' }, {
      'analyst:recon': () => ({ design_path: `${RUN}/design.md`, unmapped: [], level: 'L3', gap_type: 'framework-only' }),
    });
    check(l3.result.status === 'ready_for_review'
      && l3.result.phases.some((p) => p.verdict === 'auto_approved' && p.summary.includes('L3 work')), `L3: ${l3.result.status}`);
    const stop = await run({ mode: 'feature', task: 't', stop_after: 'Design' }, flagged);
    check(stop.result.status === 'stopped' && stop.result.stopped_at === 'Design', `stop_after still stops: ${stop.result.status}`);
    const plain = await run({ mode: 'feature', task: 't' });
    check(!plain.result.phases.some((p) => p.verdict === 'auto_approved'), 'a design that needs no review is not marked');
  }],
  ['feature: resume_from implement after review', async () => {
    const r = await run({ mode: 'feature', task: 't', resume_from: 'implement', run_dir: RUN });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(!r.keys.includes('director:spec') && !r.keys.includes('analyst:recon'), 'spec and recon skipped');
    const d = r.calls.find((c) => c.key === 'architect:design');
    check(d && d.ins.approved_design === true, 'architect told the design is approved');
    const pe = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(pe && pe.ins.claim === '+20% output tok/s at equal HBM', `the resumed run measures the claim: ${pe && pe.ins.claim}`);
    const noClaim = await run({ mode: 'feature', task: 't', resume_from: 'implement', run_dir: RUN }, {
      'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('WP1')], gate_ok: true }),
    });
    check(noClaim.result.status === 'blocked' && noClaim.result.stopped_at === 'Design', `a design that does not state its claim: ${noClaim.result.status}`);
  }],
  ['feature: resume_from verify measures the claim the run had; nothing before Verify runs again', async () => {
    const r = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    check(!r.keys.some((k) => ['director:spec', 'analyst:recon', 'architect:design', 'implementer:implement', 'test_engineer:unit'].includes(k)),
      `nothing before Verify: ${r.keys}`);
    const setupIns = r.calls.find((c) => c.key === 'director:setup').ins;
    check(setupIns.resume_run_dir === RUN && setupIns.resume_from === 'verify', 'Setup reuses the run directory, told to check the resume before its smoke');
    const pe = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(pe && pe.ins.claim === '+20% output tok/s at equal HBM', `the claim the run had: ${pe && pe.ins.claim}`);
    check(r.result.done.map((d) => d.wp).join() === 'WP1,WP2', `the packages the run verified: ${JSON.stringify(r.result.done)}`);
    const rv = r.calls.find((c) => c.key === 'reviewer:review');
    check(rv && rv.ins.verified_work_packages.length === 2, 'the reviewer sees them');
    // The claim is read as the run's Spec read it (G5 for a feature): no slowdown where the run claimed no gain or
    // measures with InferPilot's client, and a cost the user stated on either client.
    const claimOn = async (restored, args = {}) => ((await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, ...args }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), ...restored }),
      'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }),
    })).calls.find((c) => c.key === 'perf_engineer:perf_eval') || { ins: {} }).ins.claim;
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    const BOUND = 'at most 5% below the baseline output tok/s (goal)';
    const ours = { client_script: 'template', workload: '{"isl":8000,"osl":1000,"conc":32}' };
    check(NO_SLOWER.test(await claimOn({ perf_required: false, perf_claim: '' })),
      'a run that claimed no gain is judged again on the same thing: that the feature is no slower than the baseline');
    check(NO_SLOWER.test(await claimOn({}, ours)), 'with InferPilot\'s client the source\'s gain is not the claim, as it was not in the run');
    check(await claimOn({ perf_claim: BOUND, perf_cost: true }, ours) === BOUND && await claimOn({ perf_claim: BOUND, perf_cost: true }) === BOUND,
      'a cost the user stated stays the claim, with InferPilot\'s client and with the task\'s own');
    check(NO_SLOWER.test(await claimOn({ perf_claim: BOUND }, ours)), 'a claim not marked as a cost is a gain, never claimed with InferPilot\'s client');
    // Spec's fallback client is InferPilot's wherever exp_root is now: it is in the run directory being resumed.
    for (const root of [path.dirname(RUN), '/another/root']) {
      const judged = (await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, exp_root: root,
        client_script: `${RUN}/scripts/fallback_client.sh` })).calls.find((c) => c.key === 'perf_engineer:perf_eval');
      check(judged && NO_SLOWER.test(judged.ins.claim), `the run's fallback client, exp_root now ${root}: the source's gain is not the claim (${judged && judged.ins.claim})`);
    }
    // So the answer always says which it is: one that leaves it out is no answer, and is asked for again.
    const unsaid = (ins) => Object.fromEntries(Object.entries({ ...DEFAULTS['director:resume'](ins), perf_claim: BOUND })
      .filter(([k]) => k !== 'perf_cost'));
    const resumeArgs = { mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, ...ours };
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const silent = await run(resumeArgs, { ...rendered, 'director:resume': unsaid });
    check(silent.result.status === 'blocked' && silent.result.stopped_at === 'Setup' && !silent.keys.includes('verify_engineer:verify')
      && silent.result.phases.some((p) => p.phase === 'Setup' && p.summary.includes('the director restored nothing from the run directory')),
      `an answer that does not say whether the claim is a cost restores nothing: ${silent.result.status} at ${silent.result.stopped_at}`);
    const saidLater = await run(resumeArgs, { ...rendered,
      'director:resume': (ins, n) => (n === 1 ? unsaid(ins) : { ...unsaid(ins), perf_cost: true }),
    }, { schemaRetries: 1 });
    const costClaim = (saidLater.calls.find((c) => c.key === 'perf_engineer:perf_eval') || { ins: {} }).ins.claim;
    check(saidLater.keys.filter((k) => k === 'director:resume').length === 2 && costClaim === BOUND,
      `asked again, it says so, and the cost stays the claim: ${costClaim}`);
    const late = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, stop_after: 'Design' });
    check(late.result.status === 'ready_for_review', `a stop before Verify never holds in a run resumed there: ${late.result.status}`);
    const approved = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), design_auto_approved: true }),
    });
    const rep = approved.calls.find((c) => c.key === 'director:report');
    check(rep && rep.ins.phases.some((p) => p.phase === 'Design' && p.verdict === 'auto_approved'),
      'a design no human approved is still reported as such');
  }],
  ['feature: resume_from verify stops at Setup for a task that would measure otherwise, or state the run directory lost', async () => {
    const resumed = (extra, args = {}) => run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, ...args }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), ...extra(ins) }) });
    const stoppedFor = (r, what) => r.result.status === 'blocked' && r.result.stopped_at === 'Setup' && !r.keys.includes('verify_engineer:verify')
      && r.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'fail' && p.summary.includes(what));
    const heldOut = await resumed((ins) => ({ task_definition: { ...ins.task_def, held_out: true } }));
    check(stoppedFor(heldOut, 'would measure otherwise than the one it resumes (held_out not as its run.json has them)'),
      `a held-out check the run had, dropped: ${JSON.stringify(heldOut.result.phases.at(-1))}`);
    // G3 and G4 are the task's to ask for, and G3's test the task's own: a resume that leaves one out, or names
    // another test, would be ready with less measured than the run was to measure.
    const g3 = { long_context: true, long_context_script: '/s/g3.sh' };
    const had = (fields) => (ins) => ({ task_definition: { ...ins.task_def, ...fields } });
    const noG3 = await resumed(had(g3));
    check(stoppedFor(noG3, '(long_context, long_context_script not as its run.json has them)'),
      `G3, which the run had, left out: ${JSON.stringify(noG3.result.phases.at(-1))}`);
    const otherG3 = await resumed(had(g3), { long_context: 'true', long_context_script: '/s/other.sh' });
    check(stoppedFor(otherG3, '(long_context_script not as its run.json has them)'),
      `G3 on another test than the run's: ${JSON.stringify(otherG3.result.phases.at(-1))}`);
    const noG4 = await resumed(had({ stability: true }));
    check(stoppedFor(noG4, '(stability not as its run.json has them)'), `G4, which the run had, left out: ${JSON.stringify(noG4.result.phases.at(-1))}`);
    const sameGates = await resumed(had({ long_context: true, stability: 'true' }), { ...g3, long_context: 'true', stability: true });
    const gated = sameGates.calls.find((c) => c.key === 'verify_engineer:verify');
    check(sameGates.result.status === 'ready_for_review' && gated && gated.ins.long_context === true && gated.ins.stability === true
      && gated.ins.long_context_script === '/s/g3.sh', `the same gates and the same test go on: ${sameGates.result.status}`);
    const workload = { input_len: 8192, output_len: 1024, concurrency: 64 };
    const sameRun = await resumed((ins) => ({ task_definition: { ...ins.task_def, workload: { concurrency: 64, output_len: 1024, input_len: 8192 },
      tune: 'false', apply_prs: '#46329' } }), { workload, client_script: '/s/client.sh', apply_prs: ['#46329'] });
    check(sameRun.result.status === 'ready_for_review', `the same settings written another way are the same task: ${sameRun.result.status}`);
    check(stoppedFor(await resumed(() => ({ task_definition: null })), 'records no task definition'), 'no run.json: nothing to hold the task against');
    for (const k of ['known_issues', 'handoffs']) {
      check(stoppedFor(await resumed(() => ({ [k]: null })), `no readable ${k}.json`), `${k}.json lost: the resume stops`);
    }
    check(stoppedFor(await resumed(() => ({ perf_claim: '' })), 'a claim its run directory does not record'), 'a claim the run had, lost');
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'resume').replace(/\s+/g, ' ');
    check(doc.includes('the commit the run\'s last Verify round ran on') && doc.includes('may be a revert InferPilot made after Implement'),
      'the head a resume holds the branch to is where the last Verify round began, a kernel revert included');
    check(doc.includes('and the gates the task asked for with the test G3 runs: `long_context`, `stability`, `long_context_script`'),
      'the director, who checks the task before its smoke, is told that the gates are held against run.json too');
    const verifyDoc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'verify_engineer.md'), 'utf8'), 'verify').replace(/\s+/g, ' ');
    check(verifyDoc.includes('Each round\'s first Run log row, written before anything starts, is the round and the commit it runs on (`git -C <worktree> rev-parse HEAD`, the worktree clean)')
      && doc.includes('each round\'s first row in the ledger\'s Run log'), 'each Verify round records the commit it starts on, where a resume reads it');
    // Verify again on the same commit would give G3 the directories of the round that had no result: what that
    // round wrote there is no part of this one's count.
    check(verifyDoc.includes('No run writes where an earlier one wrote: where a directory of that name is there already (a later round on the same '
      + 'commit, or a round that a call without a result left unfinished, which a run resumed from Verify starts again), leave it as it is and '
      + 'give this run `RUN_DIR/logs/g3_<arm>_c<n>_<run>-2`, and so on, as its `OUT_DIR`')
      && verifyDoc.includes('what the earlier run wrote would be counted with this run\'s, or stand for what this run did not write'),
      'a G3 run again on a commit writes in no directory an earlier run of it wrote to');
    const report = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'report').replace(/\s+/g, ' ');
    check(report.includes('it writes `RUN_DIR/run_report_<n>.md` instead') && report.includes('`run_report.md` stays the report of the run it resumed'),
      'a resume that stopped at Setup leaves the report it resumed from as it was');
  }],
  ['feature: resume_from verify restores the known issues and kernel hand-offs the run had', async () => {
    const ki = { id: 'KI-009', title: 'G4 collapse on the baseline too' };
    const h = { id: 'K-WP1', wp: 'WP1', owner: 'kernel_workflow', status: 'integrated', integrated_wp: 'K-K-WP1', commit: 'c_K', retries: 0 };
    const r = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), known_issues: [ki], handoffs: [h] }),
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: false, failures: [{ gate: 'G4', failure_class: 'collapse' }] } : { all_pass: true, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-WP1'], counterexample: 'bs=1', summary: 'the kernel' }),
    });
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && (v.ins.known_issues || []).some((k) => k.id === 'KI-009'), 'Verify knows the baseline failures the run proved');
    const rv = r.calls.find((c) => c.key === 'implementer:rollback' && c.ins.revert);
    check(rv && rv.ins.revert === 'c_K', `a kernel regression reverts the commit the run integrated: ${rv && JSON.stringify(rv.ins)}`);
    // A hand-off is restored as the file has it or not at all: one without its id would be left out of the
    // result and of the next report, and an integrated one without its commit would be reset, not reverted.
    const resumedWith = (handoffs) => run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), known_issues: [ki], handoffs }) });
    const stoppedFor = (x, what) => x.result.status === 'blocked' && x.result.stopped_at === 'Setup' && !x.keys.includes('verify_engineer:verify')
      && x.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'fail' && p.summary.includes(what));
    const recorded = { id: 'O2', owner: 'hyperloom', source: 'plan', status: 'recorded' };
    const without = (key) => Object.fromEntries(Object.entries(h).filter(([k]) => k !== key));
    for (const [what, entry] of [['its id', without('id')], ['an id that says anything', { ...h, id: ' ' }],
      ['the commit that integrated it', without('commit')], ['that commit, left empty', { ...h, commit: '' }],
      ['the package that integrated it', { ...h, integrated_wp: null }]]) {
      const partial = await resumedWith([recorded, entry]);
      check(stoppedFor(partial, 'handoffs.json holds a hand-off without its id, or an integrated one without the package or the commit that integrated it'),
        `a hand-off recorded without ${what} is neither left out nor rolled back another way: ${partial.result.status} at ${partial.result.stopped_at}`);
    }
    const pending = { id: 'K-WP2', wp: 'WP2', owner: 'kernel_workflow', source: 'implement', status: 'not_integrated' };
    const kept = await resumedWith([h, pending, recorded]);
    const back = kept.result.handoffs.map((x) => `${x.id}:${x.status}`);
    check(kept.result.status === 'ready_for_review' && eqList(back, ['K-WP1:integrated', 'K-WP2:not_integrated', 'O2:recorded'])
      && kept.result.known_issues.length === 1, `whole hand-offs come back as they were, one that needs no commit among them: ${back}`);
    const said = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'resume').replace(/\s+/g, ' ');
    check(said.includes('Give every entry as the file has it, never leaving one out and never completing one: a hand-off without its `id`, or an '
      + 'integrated one without the package and the commit that integrated it (`integrated_wp`, `commit`), stops the resume'),
      'the director returns a hand-off as its file has it');
  }],
  ['setup: a run resumed from Verify has its settings compared with their boundaries and as the harness renders them; the task and the goal stay the run\'s', async () => {
    const resumed = (mode, extra, args = {}) => run({ mode, task: 't', resume_from: 'verify', run_dir: RUN, ...args }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), ...extra(ins) }) });
    const saved = (fields) => (ins) => ({ task_definition: { ...ins.task_def, ...fields } });
    const stoppedFor = (r, what) => r.result.status === 'blocked' && r.result.stopped_at === 'Setup' && !r.keys.includes('verify_engineer:verify')
      && r.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'fail' && p.summary.includes(what));
    const split = await resumed('feature', saved({ harness: { DATASET: 'prompts.jsonl,SEED:2' } }),
      { harness: { DATASET: 'prompts.jsonl', SEED: 2 } });
    check(stoppedFor(split, '(harness not as its run.json has them)'), `a comma in a path is not a second setting: ${split.result.status}`);
    const load = { input_len: 8192, output_len: 1024, concurrency: 64 };
    const nested = await resumed('feature', saved({ workload: { ...load, dataset: 'a,dataset_seed:2' } }),
      { workload: { ...load, dataset: 'a', dataset_seed: 2 }, client_script: '/s/client.sh' });
    check(stoppedFor(nested, '(workload not as its run.json has them)'), `nor in a workload: ${nested.result.status}`);
    const flagOff = await resumed('feature', saved({ harness: { TOP_P: false } }), { harness: { TOP_P: '' } });
    check(stoppedFor(flagOff, '(harness not as its run.json has them)'), `a flag false renders 0, an empty line the default: ${flagOff.result.status}`);
    for (const [why, was, now] of [['a flag false and 0 render the same line', { PREFIX_CACHE: false }, { PREFIX_CACHE: 0 }],
      ['a flag true and 1', { PREFIX_CACHE: true }, '{"PREFIX_CACHE": "1"}'], ['a list and its items', { CONCS: [1, 8] }, { CONCS: '1 8' }],
      ['a line set to nothing, as "" or as null', { TP: 4, SEED: '' }, { TP: '4', SEED: null }], ['no harness and an empty one', {}, '']]) {
      const same = await resumed('feature', saved({ harness: was }), { harness: now });
      check(same.result.status === 'ready_for_review', `${why}: ${same.result.status}`);
    }
    // What the renderer keeps apart stays apart: accuracy.sh sends no reasoning effort for an empty line and its
    // default for an unset one, and a path's last space is part of the file's name.
    for (const [why, was, now] of [['a line set to nothing is a setting, and no unset line', { REASONING_EFFORT: '' }, {}],
      ['also next to other lines', { TP: 4, SEED: '' }, { TP: '4' }],
      ['a space at the end of a path is part of it', { DATASET_PATH: '/d/prompts.jsonl ' }, { DATASET_PATH: '/d/prompts.jsonl' }]]) {
      const differs = await resumed('feature', saved({ harness: was }), { harness: now });
      check(stoppedFor(differs, '(harness not as its run.json has them)'), `${why}: ${differs.result.status} at ${differs.result.stopped_at}`);
    }
    // So is any other string: the run takes a model or a script from the path as given, spaces included.
    for (const [key, was, now] of [['model_path', '/models/m ', '/models/m'], ['client_script', '/s/client.sh', '/s/client.sh '],
      ['accuracy_script', ' /s/accuracy.sh', '/s/accuracy.sh']]) {
      const moved = await resumed('feature', saved({ [key]: was }), { [key]: now });
      check(stoppedFor(moved, `(${key} not as its run.json has them)`), `${key} with a space is another path: ${moved.result.status} at ${moved.result.stopped_at}`);
    }
    const inLoad = await resumed('feature', saved({ workload: { ...load, dataset: '/d/a.jsonl ' } }),
      { workload: { ...load, dataset: '/d/a.jsonl' }, client_script: '/s/client.sh' });
    check(stoppedFor(inLoad, '(workload not as its run.json has them)'), `nor is a data set's path in a workload trimmed: ${inLoad.result.status}`);
    // What "false" or a list of one means inside a workload is its client's to say: there a value stays what it is.
    for (const [why, was, now] of [['"false" is a value, and no empty one', { dataset: 'false' }, { dataset: '' }],
      ['a list of one is not its item', { concurrencies: [8] }, { concurrencies: 8 }], ['null is no empty string', { dataset: null }, { dataset: '' }]]) {
      const inside = await resumed('feature', saved({ workload: { ...load, ...was } }), { workload: { ...load, ...now }, client_script: '/s/client.sh' });
      check(stoppedFor(inside, '(workload not as its run.json has them)'), `inside a workload, ${why}: ${inside.result.status} at ${inside.result.stopped_at}`);
    }
    const digits = await resumed('feature', saved({ workload: { ...load, concurrency: '64' } }), { workload: JSON.stringify(load), client_script: '/s/client.sh' });
    check(digits.result.status === 'ready_for_review', `a number written as its digits, in a workload given as a string, is the same workload: ${digits.result.status}`);
    // The fields with a meaning of their own keep it: a flag is on or off, and apply_prs a list also for one PR.
    const ownForms = await resumed('feature', saved({ held_out: 'True', guards: '', apply_prs: '["#46329"]' }), { held_out: true, apply_prs: '#46329' });
    check(ownForms.result.status === 'ready_for_review', `a flag and a list written another way are the same task: ${ownForms.result.status} at ${ownForms.result.stopped_at}`);
    // InferPilot's template is one answer however the word was written, and no path.
    const templated = { workload: '{"isl":8000,"osl":1000,"conc":32}' };
    const sameTemplate = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, ...templated, client_script: ' Template ' }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), task_definition: { ...ins.task_def, client_script: 'template' } }),
      'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }),
    });
    check(sameTemplate.result.status === 'ready_for_review', `the template taken again is the same client: ${sameTemplate.result.status} at ${sameTemplate.result.stopped_at}`);
    const goal = await resumed('feature', saved({ goal: 'no accuracy loss: also gpqa_diamond' }), { goal: 'quick check' });
    const v = goal.calls.find((c) => c.key === 'verify_engineer:verify');
    const pe = goal.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(v && v.ins.goal === 'no accuracy loss: also gpqa_diamond' && pe && pe.ins.goal === v.ins.goal,
      `Verify and Performance get the goal the run had, with the data sets it names: ${v && v.ins.goal}`);
    check(goal.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'resumed'
      && p.summary.includes('the goal it was given is not the run\'s, which stays as run.json has it')), 'and the run says so');
    const other = await resumed('issue', saved({ task: 'sglang#1: wrong logprobs' }), { task: 'sglang#2: a crash' });
    const reply = other.calls.find((c) => c.key === 'issue_triager:reply');
    check(other.calls.find((c) => c.key === 'verify_engineer:verify').ins.task === 'sglang#1: wrong logprobs'
      && reply && reply.ins.task === 'sglang#1: wrong logprobs', `the issue stays the one the run directory is about: ${reply && reply.ins.task}`);
    // The result names the task and the goal the run worked on: its task definition is no longer the one given.
    check(other.result.task_def.task === 'sglang#1: wrong logprobs' && other.calls.find((c) => c.key === 'director:resume').ins.task_def.task === 'sglang#2: a crash',
      `the result's task definition names the run's issue, and the director was given this run's to hold against it: ${other.result.task_def.task}`);
    check(goal.result.task_def.goal === 'no accuracy loss: also gpqa_diamond', `and the run's goal: ${goal.result.task_def.goal}`);
    // Every run has a task, and Setup records it: a run.json without one lost it, and the task given now would
    // take its place, with the triage record and the reproduction of another issue.
    for (const [why, lost] of [['has no task', (ins) => Object.fromEntries(Object.entries(ins.task_def).filter(([k]) => k !== 'task'))],
      ['has an empty one', (ins) => ({ ...ins.task_def, task: ' ' })], ['has null for it', (ins) => ({ ...ins.task_def, task: null })]]) {
      const untasked = await resumed('issue', (ins) => ({ task_definition: lost(ins) }), { task: 'sglang#2: a crash' });
      check(stoppedFor(untasked, 'run.json records no task'), `a run.json that ${why}: ${untasked.result.status} at ${untasked.result.stopped_at}`);
    }
    const unchanged = await resumed('feature', () => ({}), { goal: 'as before' });
    check(unchanged.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'resumed' && !p.summary.includes('not the run\'s')),
      'a goal that is the run\'s is not reported as another');
    // A run that had no goal records none: one named now would have Verify and Performance run data sets and
    // guards the run never did.
    const noGoal = await resumed('feature', (ins) => ({ task_definition: Object.fromEntries(Object.entries(ins.task_def).filter(([k]) => k !== 'goal')) }),
      { goal: 'no accuracy loss: also gpqa_diamond' });
    const nv = noGoal.calls.find((c) => c.key === 'verify_engineer:verify');
    const np = noGoal.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(nv && !nv.ins.goal && np && !np.ins.goal, `a run that had no goal is given none: ${nv && JSON.stringify(nv.ins.goal)}`);
    check(noGoal.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'resumed'
      && p.summary.includes('the goal it was given is not the run\'s, which stays as run.json has it (no goal)')), 'and the run says so');
    check(!('goal' in noGoal.result.task_def) && noGoal.result.task_def.task === 't',
      `nor does the result's task definition name the goal given now: ${JSON.stringify(noGoal.result.task_def.goal)}`);
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'resume').replace(/\s+/g, ' ');
    check(doc.includes('A `run.json` without its `task` lost it, as every run has one: return the definition as it is there, never with the task '
      + 'you were given in its place, and the resume stops'), 'the director gives back a run.json without its task as it is');
  }],
  ['setup: a run resumed from Verify stops without the records Verify and Reply read; no package left is a state of its own', async () => {
    const resumed = (mode, extra) => run({ mode, task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), ...extra }) });
    const stoppedFor = (r, what) => r.result.status === 'blocked' && r.result.stopped_at === 'Setup' && !r.keys.includes('verify_engineer:verify')
      && r.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'fail' && p.summary.includes(what));
    check(stoppedFor(await resumed('issue', { triage_path: '' }), 'records no issue_triage.md'), 'an issue without its triage record would skip Reply');
    check(stoppedFor(await resumed('feature', { spec_path: '' }), 'records no feature_spec.md'), 'a feature without its spec has no acceptance matrix');
    check(stoppedFor(await resumed('feature', { design_path: '' }), 'records no design.md'), 'nor, without its design, a test plan');
    check(stoppedFor(await resumed('perf', { analysis_path: '' }), 'records no perf_analysis.md'),
      'a perf run without its analysis has no plan for Verify and no path check for Performance');
    const analysed = (await resumed('perf', {})).calls.find((c) => c.key === 'verify_engineer:verify');
    check(analysed && analysed.ins.analysis_path === `${RUN}/perf_analysis.md`, `with it, Verify is given the analysis the run had: ${analysed && analysed.ins.analysis_path}`);
    // A package recorded without its name or its commit is one the run directory lost: left out, Verify and
    // Review would be given fewer packages than the run verified.
    const whole = DEFAULTS['director:resume']({ mode: 'feature' }).verified_work_packages;
    for (const [what, entry] of [['its commit', { wp: 'WP2', commit: '', evidence: 'ok' }], ['its name', { wp: '', commit: 'c_WP2', evidence: 'ok' }]]) {
      const partial = await resumed('feature', { verified_work_packages: [whole[0], entry] });
      check(stoppedFor(partial, 'records a verified work package without its name or its commit'),
        `a package recorded without ${what} is not dropped from the list: ${partial.result.status} at ${partial.result.stopped_at}`);
    }
    check(stoppedFor(await resumed('feature', { verified_work_packages: [{ wp: 'WP1', commit: '' }] }), 'records no verified work package'),
      'and a list with no whole entry records none');
    const rejected = { id: 'O2', owner: 'kernel_workflow', status: 'rejected', retries: 1 };
    const kernelOnly = await resumed('perf', { verified_work_packages: [], handoffs: [rejected], head: 'c_revert' });
    check(kernelOnly.keys.includes('verify_engineer:verify') && kernelOnly.result.stopped_at !== 'Setup',
      `a kernel-only plan whose integration was reverted is verified again: ${kernelOnly.result.status} at ${kernelOnly.result.stopped_at}`);
    check(kernelOnly.result.phases.some((p) => p.phase === 'Setup' && p.summary.includes('no work package left, its kernel integration reverted')),
      'and the run says what it verifies');
    check(stoppedFor(await resumed('perf', { verified_work_packages: null, handoffs: [rejected] }), 'records no verified work package'),
      'packages the run directory does not record are not none');
    check(stoppedFor(await resumed('perf', { verified_work_packages: [] }), 'records no verified work package'), 'an empty list without a kernel hand-off');
    check(stoppedFor(await resumed('feature', { verified_work_packages: [], handoffs: [rejected] }), 'records no verified work package'),
      'or outside perf');
    const setup = (await run({ mode: 'perf', task: 'p', apply_prs: ['#46329', '#46400'] })).calls.find((c) => c.key === 'director:setup');
    check(JSON.stringify(setup.ins.apply_prs) === '["#46329","#46400"]', `Setup is given apply_prs, which it records in run.json: ${JSON.stringify(setup.ins.apply_prs)}`);
    const one = (await run({ mode: 'perf', task: 'p', apply_prs: '#46329' })).calls.find((c) => c.key === 'architect:perf_plan');
    check(JSON.stringify(one.ins.apply_prs) === '["#46329"]', 'and the plan the same list');
    const doc = readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8').replace(/\s+/g, ' ');
    check(doc.includes('first check, reading only, everything the resume needs (PHASE=resume, steps 1 to 6)')
      && doc.includes('the verified work packages, each with its commit, the mode\'s gate command (`issue`, `ci`), its triage record (`issue`, `ci`), '
        + 'its spec and design (`feature`), its analysis (`perf`), the claim its Performance was to judge (a `feature`\'s with the spec\'s yes or no on '
        + 'whether it is a cost the user stated), and `known_issues.json`, `handoffs.json` and `run_state.json`')
      && doc.includes('run no smoke, so that nothing is measured'), 'Setup checks everything a resume needs before its smoke');
    // A fix's Setup measures no baseline, since its base may be the server that does not start: nor does its resume.
    check(doc.includes('An `issue` or a `ci` run whose Setup measured no baseline (step 4; the ledger\'s baseline section says so) runs no smoke on a resume either, '
      + 'and returns `baseline_status: "skip"` again'), 'the resume of a fix launches no base that its Setup did not');
    const skip = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), baseline_status: 'skip', baseline: 'no baseline: the base does not start' }) };
    const fixResumed = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, skip);
    check(fixResumed.keys.includes('verify_engineer:verify') && fixResumed.result.stopped_at !== 'Setup',
      `and that answer takes an issue's resume on to Verify: ${fixResumed.result.status} at ${fixResumed.result.stopped_at}`);
    const featureSkipped = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN }, skip);
    check(featureSkipped.result.status === 'blocked' && featureSkipped.result.stopped_at === 'Setup', 'a feature\'s, which measures against its baseline, not');
    // run.json is what the resume is held against: a Setup that wrote this run's inputs there would compare them with themselves.
    check(doc.includes('A run resumed from Verify changes nothing in `RUN_DIR/run.json`, at this step or at a later one, and reads its `task` and its `goal` there'),
      'Setup leaves run.json as the run it resumes wrote it');
    // A resume stopped at Setup because an earlier call still runs must not take the worktree that call measures on.
    const report = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'report').replace(/\s+/g, ' ');
    check(report.includes('A run that reused a run directory (`resume_run_dir`) and stopped at Setup leaves it where it is')
      && report.includes('a call of the run it resumes that has not ended (Setup, step 1) may be measuring on it still'),
      'the report of a resume that stopped at Setup leaves the baseline worktree');
    const stoppedReport = featureSkipped.calls.find((c) => c.key === 'director:report');
    check(stoppedReport && stoppedReport.ins.resume_run_dir === RUN && stoppedReport.ins.stopped_at === 'Setup',
      `and is told that it is one: ${stoppedReport && JSON.stringify([stoppedReport.ins.resume_run_dir, stoppedReport.ins.stopped_at])}`);
    // A script edited in place keeps its path, so the settings compare equal: Setup holds each against the copy
    // the run kept, before its smoke, and renders nothing again from a harness file that may have changed too.
    check(doc.includes('Check that every script the task names by its path (`server_script`, `client_script`, `accuracy_script`, '
      + '`output_script`, `long_context_script`), one that lies in the run directory too (Spec\'s fallback client, where the user took it), '
      + 'is still, byte for byte, the copy the run\'s Setup put in `RUN_DIR/scripts/task/`')
      && doc.includes('A script with no copy there fails the check as well')
      && doc.includes('copy the task\'s scripts, its `output_script` and its `long_context_script` among them, and one that lies in the run directory '
        + 'like any other (Spec\'s fallback client, where the user took it), to `RUN_DIR/scripts/task/` for the record, '
        + 'which a run resumed from Verify holds them against')
      && doc.includes('what the file at it holds, Setup held against the run\'s copy before its smoke'),
      'a script edited in place since the run is not the one the run measured with, wherever it lies');
    check(!doc.includes('from outside the run directory'), 'no script the task names is left out of that check for where it lies');
    check(doc.includes('measures what the one `run.json` recorded did, which still has its `task`')
      && doc.includes('`known_issues.json`, `handoffs.json` and `run_state.json`, every hand-off whole (step 6 there)'),
      'and Setup looks for the task in run.json and for whole hand-offs before its smoke as well');
    check(doc.includes('Nor does it render or write again a script the run has (step 3): it goes on with those in `RUN_DIR/scripts/` as they are, '
      + 'the ones the run measured with') && doc.includes('a `harness` file, a cookbook page or a template changed since is not read for them'),
      'and what a harness file holds now is not rendered into the scripts the run measured with');
    // Such a Setup stopped before its smoke, with no GPUs pinned and no scripts returned: the run stops for what it found.
    const edited = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, client_script: '/s/client.sh' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'fail', gpu_ids: '',
        baseline: 'client_script is not the copy in scripts/task: /s/client.sh was edited after the run' }) });
    check(stoppedFor(edited, 'client_script is not the copy in scripts/task') && !edited.keys.includes('director:resume')
      && edited.result.run_dir === RUN,
      `the stop says what Setup found, not what it did not get to: ${JSON.stringify(edited.result.phases.filter((p) => p.phase === 'Setup'))}`);
    // Spec's fallback client lies in the run directory, and is the task's client all the same once the user took it.
    const ownClient = `${RUN}/scripts/fallback_client.sh`;
    const editedInRun = await run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN, client_script: ownClient }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'fail', gpu_ids: '',
        baseline: `client_script is not the copy in scripts/task: ${ownClient} was edited after the run` }) });
    check(stoppedFor(editedInRun, `${ownClient} was edited after the run`) && !editedInRun.keys.includes('director:resume')
      && !editedInRun.keys.includes('perf_engineer:perf_eval'), `nor is the fallback client the user took, edited where it lies: ${editedInRun.result.status}`);
    check(doc.includes('one that timed out is not cancelled, and its agent may still be running and start more servers, which idle GPUs do not show')
      && doc.includes('one that timed out was not cancelled, which is why Setup looked for what it left before its smoke')
      && !doc.includes('its servers went with the call'), 'a timed-out Verify is not taken for gone');
    check(doc.includes('run it in the `container` too, when that is set, through `docker exec -e INFERPILOT_RUN_TAG`'),
      'the scan run in the container is given this run\'s tag, without which its own processes read as another run\'s');
    check(doc.includes('`verified_work_packages` is `[]` only when the report says none was left')
      && doc.includes('The `task` and the `goal` stay the ones `run.json` has') && doc.includes('a run that had no goal has none now')
      && doc.includes('a flag `false` is `0`, not an unset line') && doc.includes('a line set to nothing is not an unset one either')
      && doc.includes('A path, like any other string, is compared as written')
      && doc.includes('`"false"` is no empty value in it, and a list of one is not its item')
      && doc.includes('A package whose name or commit it does not record is returned with that field empty, never left out')
      && doc.includes('A resume without its triage record, a `feature` without its spec or its design, or a `perf` run without its analysis, stops'),
      'the director is told what each of these restores or stops');
    // What Verify gates on and Performance judges is what a phase returned: it is written word for word in a row
    // of that phase's record and read from that row, so a resume measures the gate and the claim the run had.
    const read = (...p) => readFileSync(path.join(INFERPILOT, ...p), 'utf8');
    const said = (role, phase) => phaseSection(read('roles', `${role}.md`), phase).replace(/\s+/g, ' ');
    for (const [record, rows, by, writes] of [
      ['issue_triage.md', ['reproduction command', 'performance claim'], said('issue_triager', 'reproduce'),
        ['Write `repro_cmd` word for word in the *reproduction command* row of `issue_triage.md`',
          'a `performance` issue\'s `perf_claim` in its *performance claim* row (`none` for any other issue)']],
      ['ci_triage.md', ['failing test command', 'performance claim'], said('ci_guardian', 'classify'),
        ['with `failing_test_cmd` word for word in its *failing test command* row',
          'for a performance problem, `perf_claim` word for word in its *performance claim* row (`none` for any other failure)']],
      ['feature_spec.md', ['performance claim', 'the claim is a cost the user stated'], said('director', 'spec'),
        ['Write `perf_claim` word for word in the spec\'s *performance claim* row (`none` when the feature claims none), '
          + 'and in the row under it whether it is a cost the user stated']],
      ['perf_analysis.md', ['claim'], said('architect', 'perf_plan'), ['with `perf_claim` word for word in its *claim* row']],
    ]) {
      const template = read('templates', record).split('\n');
      check(rows.every((row) => template.some((l) => l.startsWith(`| ${row} | `))) && writes.every((w) => by.includes(w)),
        `${record}: the phase writes its gate and its claim word for word, in rows of their own`);
    }
    const restore = said('director', 'resume');
    check(restore.includes('word for word from the *reproduction command* row of `issue_triage.md`')
      && restore.includes('word for word from the *failing test command* row of `ci_triage.md`')
      && restore.includes('word for word from the row its phase wrote it in: the *performance claim* row of `issue_triage.md`, `ci_triage.md` or '
        + '`feature_spec.md`, the *claim* row of the Plan in `perf_analysis.md`')
      && restore.includes('A row that says `none` is no claim')
      && restore.includes('as the row under the claim in `RUN_DIR/feature_spec.md` says (*the claim is a cost the user stated*: `yes` or `no`)'),
      'and a resume takes them from those rows');
  }],
  ['issue: resume_from verify runs Verify again on the commit the run verified, gated on its reproduction, then Reply', async () => {
    const r = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report']) && eqList(r.route, r.phases),
      `phases ${r.phases} | route ${r.route}`);
    check(!r.keys.some((k) => /^(issue_triager:(triage|reproduce)|investigator:diagnose|implementer:implement)$/.test(k)), `nothing before Verify: ${r.keys}`);
    // G5 for a fix, as in a first run: measured when it changes code the server runs, which this run's Verify says again.
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    const noServerCode = { 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: false }) };
    const speed = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(speed && NO_SLOWER.test(speed.ins.claim), `the fix is measured for speed, and the claim is that it is no slower: ${speed && speed.ins.claim}`);
    const testsOnly = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, noServerCode);
    check(testsOnly.result.status === 'ready_for_review' && !testsOnly.keys.includes('perf_engineer:perf_eval')
      && eqList(testsOnly.phases, ['Setup', 'Verify', 'Review', 'PR', 'Reply', 'Report']) && eqList(testsOnly.route, testsOnly.phases),
      `a fix to tests alone is not measured, and the route logged again is the resumed run's own: ${testsOnly.phases} | ${testsOnly.route}`);
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && eqList(v.ins.extra_gates, [{ gate: 'issue_repro', cmd: 'bash repro.sh', expect: 'fixed' }]), `the run's reproduction is the gate: ${v && JSON.stringify(v.ins.extra_gates)}`);
    const reply = r.calls.find((c) => c.key === 'issue_triager:reply');
    check(reply && reply.ins.triage_path === `${RUN}/issue_triage.md`, 'the reply is rewritten from the run\'s triage record');
    check(reply && reply.ins.resume_from === 'verify' && !reply.ins.reply_draft_path,
      'the triager knows the run ran no Triage, and the earlier draft is not this run\'s');
    check(r.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'resumed' && p.summary.includes('c_WP2')), 'the record says where it resumed');
    const noRewrite = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), reply_draft_path: `${RUN}/reply_draft.md` }),
      'issue_triager:reply': () => null,
    });
    check(noRewrite.result.reply_draft_path === '' && !(noRewrite.result.next.user || []).some((u) => u.includes('post the reply draft')),
      `a draft the resumed run never rewrote is not offered for posting: ${noRewrite.result.reply_draft_path}`);
    const fresh = await run({ mode: 'issue', task: 'i' });
    const freshReply = fresh.calls.find((c) => c.key === 'issue_triager:reply');
    check(freshReply && !freshReply.ins.resume_from, 'a run that ran Triage is no resume');
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'issue_triager.md'), 'utf8'), 'reply').replace(/\s+/g, ' ');
    check(doc.includes('in place of one an earlier attempt left there') && doc.includes('Only a run resumed from Verify (`resume_from: verify`), which ran no Triage, keeps the one there'),
      'a retry that ran Triage again keeps its own Triage draft');
    const perf = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, { ...noServerCode,
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), perf_required: true, perf_claim: 'TTFT back under 200 ms' }),
    });
    const pe = perf.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(pe && pe.ins.claim === 'TTFT back under 200 ms' && eqList(perf.phases, ['Setup', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report'])
      && eqList(perf.route, perf.phases), `a performance issue is measured again on its claim, whatever its fix changes: ${perf.phases} | ${perf.route}`);
    const lostClaim = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), perf_required: true, perf_claim: '' }),
    });
    check(lostClaim.result.stopped_at === 'Setup' && !lostClaim.keys.includes('verify_engineer:verify'),
      `a performance issue whose claim is lost is not passed unmeasured: ${lostClaim.result.status} at ${lostClaim.result.stopped_at}`);
  }],
  ['issue: resume_from verify measures nothing on a branch that moved, or without the gate the run recorded', async () => {
    const moved = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), head_ok: false, head: 'f00d', verified_work_packages: [{ wp: 'WP1', commit: 'c_WP1' }] }),
    });
    check(moved.result.status === 'blocked' && moved.result.stopped_at === 'Setup' && !moved.keys.includes('verify_engineer:verify'),
      `a moved branch: ${moved.result.status} at ${moved.result.stopped_at}`);
    check(moved.result.phases.some((p) => p.phase === 'Setup' && p.summary.includes('f00d')), 'the stop names the head');
    const noGate = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), head: 'c_WP1', verified_work_packages: [{ wp: 'WP1', commit: 'c_WP1' }], repro_cmd: '' }),
    });
    check(noGate.result.stopped_at === 'Setup' && !noGate.keys.includes('verify_engineer:verify'), 'no reproduction recorded: nothing to gate on');
    const nothing = await run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN }, { 'director:resume': () => null });
    check(nothing.result.stopped_at === 'Setup' && !nothing.keys.includes('verify_engineer:verify'), 'nothing restored: nothing measured');
  }],
  ['issue: a Verify that ended without a result offers to run Verify again in the same run directory', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, { 'verify_engineer:verify': () => null });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Verify', `status ${r.result.status}`);
    check(r.result.next.rerun_with && r.result.next.rerun_with.resume_from === 'verify' && r.result.next.rerun_with.run_dir === RUN,
      `rerun_with ${JSON.stringify(r.result.next.rerun_with)}`);
    check(r.result.next.say.startsWith('Verify ended without a result: the verify engineer returned nothing'),
      `say names why: ${r.result.next.say}`);
    check(r.result.next.rerun_with.stop_after === '', `a run with no stop goes on to its end: ${JSON.stringify(r.result.next.rerun_with)}`);
    // A stop the run was to make at Verify or after it is still ahead of the run that goes on, which keeps it:
    // without it that run would go on to Performance, hours of measurement the user never asked for.
    const unfinished = { 'verify_engineer:verify': () => null };
    for (const [stop, kept] of [['Verify', 'Verify'], ['performance', 'Performance'], ['Review', 'Review']]) {
      const s = await run({ mode: 'issue', task: 'i', stop_after: stop }, unfinished);
      check(s.result.stopped_at === 'Verify' && s.result.next.rerun_with.stop_after === kept,
        `a stop after ${kept} holds for the run that goes on: ${JSON.stringify(s.result.next.rerun_with)}`);
    }
    let verifying = false;
    const named = await run({ mode: 'issue', task: 'i' }, { 'verify_engineer:verify': () => { verifying = true; return null; } },
      { control: () => (verifying ? { stop_after: 'Performance' } : {}) });
    check(named.result.next.rerun_with.stop_after === 'Performance',
      `so does one the control file named while Verify ran: ${JSON.stringify(named.result.next.rerun_with)}`);
    let diagnosed = false;
    const lifted = await run({ mode: 'issue', task: 'i', stop_after: 'Diagnose' }, { ...unfinished,
      'investigator:diagnose': (ins) => { diagnosed = true; return DEFAULTS['investigator:diagnose'](ins); },
    }, { control: () => (diagnosed ? { stop_after: '' } : {}) });
    check(lifted.result.stopped_at === 'Verify' && lifted.result.next.rerun_with.stop_after === '',
      `a stop before Verify, which the control file lifted, is behind that run: ${lifted.result.stopped_at} ${JSON.stringify(lifted.result.next.rerun_with)}`);
    const first = await run({ mode: 'issue', task: 'i', stop_after: 'Verify' }, unfinished);
    const goesOn = await run({ mode: 'issue', task: 'i', stop_after: 'Verify', ...first.result.next.rerun_with });
    check(goesOn.result.status === 'stopped' && goesOn.result.stopped_at === 'Verify' && !goesOn.keys.includes('perf_engineer:perf_eval'),
      `and the run that goes on stops where the first was to: ${goesOn.result.status} at ${goesOn.result.stopped_at}`);
    const failed = await run({ mode: 'issue', task: 'i', max_investigate_rounds: 0 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'issue_repro', failure_class: 'crash' }] }),
    });
    check(failed.result.stopped_at === 'Verify' && !failed.result.next.rerun_with, 'a measured failure is not offered again as it is');
  }],
  ['ci: resume_from verify gates on the failing test the run recorded', async () => {
    const r = await run({ mode: 'ci', task: 'job', resume_from: 'verify', run_dir: RUN });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(!r.keys.includes('ci_guardian:classify') && !r.keys.includes('ci_guardian:bisect'), 'no classify, no bisect');
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && v.ins.extra_gates[0].gate === 'failing_test' && v.ins.extra_gates[0].cmd === 'pytest test_x.py', `gate ${v && JSON.stringify(v.ins.extra_gates)}`);
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    const speed = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(speed && NO_SLOWER.test(speed.ins.claim) && eqList(r.phases, ['Setup', 'Verify', 'Performance', 'Review', 'PR', 'Report'])
      && eqList(r.route, r.phases), `a CI fix is measured for speed as in a first run (G5 for a fix): ${r.phases} | ${speed && speed.ins.claim}`);
    const noServerCode = { 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: false }) };
    const testOnly = await run({ mode: 'ci', task: 'job', resume_from: 'verify', run_dir: RUN }, noServerCode);
    check(testOnly.result.status === 'ready_for_review' && !testOnly.keys.includes('perf_engineer:perf_eval')
      && eqList(testOnly.phases, ['Setup', 'Verify', 'Review', 'PR', 'Report']) && eqList(testOnly.route, testOnly.phases),
      `a fix to the test alone is not measured: ${testOnly.phases} | ${testOnly.route}`);
    const number = 'output throughput back above 1100 tok/s in test_bench_serving default';
    const perfFailure = await run({ mode: 'ci', task: 'job', resume_from: 'verify', run_dir: RUN }, { ...noServerCode,
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), perf_required: true, perf_claim: number }) });
    const ne = perfFailure.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(perfFailure.result.status === 'ready_for_review' && ne && ne.ins.claim === number && eqList(perfFailure.route, perfFailure.phases),
      `a failure on a number the test measures is measured again on that number, whatever its fix changes: ${ne && ne.ins.claim}`);
    const lostNumber = await run({ mode: 'ci', task: 'job', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), perf_required: true, perf_claim: '' }) });
    check(lostNumber.result.stopped_at === 'Setup' && !lostNumber.keys.includes('verify_engineer:verify'),
      `a performance failure whose number is lost is not passed unmeasured: ${lostNumber.result.status} at ${lostNumber.result.stopped_at}`);
    const rv = r.calls.find((c) => c.key === 'reviewer:review');
    check(v && v.ins.triage_path === `${RUN}/ci_triage.md` && rv && rv.ins.triage_path === `${RUN}/ci_triage.md`,
      `Verify and Review read the lanes it failed on: ${v && v.ins.triage_path}`);
    const noLanes = await run({ mode: 'ci', task: 'job', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), triage_path: '' }),
    });
    check(noLanes.result.stopped_at === 'Setup' && !noLanes.keys.includes('verify_engineer:verify'), 'no CI triage record: no lanes to pass on');
    const template = readFileSync(path.join(INFERPILOT, 'templates', 'ci_triage.md'), 'utf8');
    const classify = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'ci_guardian.md'), 'utf8'), 'classify').replace(/\s+/g, ' ');
    const resume = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8'), 'resume').replace(/\s+/g, ' ');
    check(/^\| failing test command \|/m.test(template) && classify.includes('with `failing_test_cmd` word for word in its *failing test command* row')
      && resume.includes('word for word from the *failing test command* row of `ci_triage.md`'),
      'the failing test command is written where a resume reads it');
    check(/^\| performance problem: yes \/ no \|/m.test(template) && resume.includes('a `ci` failure that `ci_triage.md` gives as a performance problem')
      && resume.includes('a fix of a problem that is not one of performance, are judged all the same, as no slower than the baseline'),
      'and so is whether the failure is a performance problem, which a resume measures on its number');
  }],
  ['setup: a run resumed from Verify goes on with what the run established before Verify, as its report wrote it down', async () => {
    const ended = (r) => `${r.result.status} at ${r.result.stopped_at}`;
    const given = (r, key) => (r.calls.find((c) => c.key === key) || { ins: {} }).ins;
    // What the run knew of its model is part of what every issue and ci run established: a text model, unless said otherwise.
    const resumedWith = (mode, state, more = {}) => run({ mode, task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), run_state: state && { text_model: true, ...state } }), ...more });
    const stoppedFor = (r, what) => r.result.status === 'blocked' && r.result.stopped_at === 'Setup' && !r.keys.includes('verify_engineer:verify')
      && r.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'fail' && p.summary.includes(what));
    // A ci run verifies someone's pull request for a failure on a score, and could not run a part of the task.
    // Its Verify returns nothing: the report is given what the run had established, to write down.
    const score = 'GSM8K back to 0.93 on GLM-5.3-Flash in test_gsm8k (0.94 in the last green run)';
    const theirs = { pr: '#50220', repo: 'sgl-project/sglang', author: 'alice', association: 'MEMBER', head: 'abc1234', state: 'open' };
    const part = 'the two tests on rocm724';
    const task = { mode: 'ci', task: 'c', apply_prs: ['#50220'] };
    const first = await run(task, { 'verify_engineer:verify': () => null,
      'ci_guardian:classify': () => ({ ...DEFAULTS['ci_guardian:classify'](), accuracy: true, accuracy_claim: score, unchecked: part, existing_prs: [theirs] }) });
    const state = given(first, 'director:report').run_state;
    check(first.result.stopped_at === 'Verify' && (first.result.next.rerun_with || {}).resume_from === 'verify'
      && eqList(state, { existing_prs: ['#50220'], existing_pr: theirs, accuracy_claim: score, unchecked: part, text_model: true }),
      `the report of a run whose Verify had no result is given what the run had established: ${JSON.stringify(state)}`);
    // The run that goes on is given it back, and is the run it resumes: the score is a gate of its own again.
    const again = (more = {}) => run({ ...task, ...first.result.next.rerun_with }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), run_state: state }), ...more });
    const noScore = await again();
    check(noScore.result.status === 'blocked' && noScore.result.stopped_at === 'Verify' && !noScore.keys.includes('reviewer:review')
      && given(noScore, 'verify_engineer:verify').accuracy_claim === score && /without the score coming back/.test(JSON.stringify(noScore.result.phases)),
      `a round without the score the fix must restore is not accepted in the resumed run either: ${ended(noScore)}`);
    const met = await again({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](),
      gates: { failing_test: 'pass', accuracy_claim: "pass 0.936, the claim is 0.93 (the test's GSM8K, 200 q)" } }) });
    const pe = given(met, 'pr_engineer:pr');
    const n = met.result.next || {};
    // The PR phase writes the comment for that pull request, and the result has nothing of the run's own to push.
    check(met.result.status === 'ready_for_review' && eqList(pe.existing_prs, ['#50220']) && (pe.existing_pr || {}).head === 'abc1234'
      && eqList((met.result.pr || {}).comment_for, ['#50220']) && /^#50220 is verified: the comment for it is drafted at /.test(n.say)
      && (n.user || []).some((u) => u.startsWith('post the verification comment on #50220')) && !(n.user || []).some((u) => /git push|^push /.test(u)),
      `the resumed run ends in the comment for the pull request the run verified, not in one of its own: ${ended(met)}, ${JSON.stringify(n.user)}`);
    check(met.result.unchecked === part && given(met, 'verify_engineer:verify').unchecked === part && given(met, 'director:report').unchecked === part,
      `Verify, the report and the result still name the part of the task the fix does not answer for: ${JSON.stringify(met.result.unchecked)}`);
    check(met.result.phases.some((p) => p.phase === 'Setup' && p.verdict === 'resumed'
      && p.summary.includes('#50220, the pull request it verified in place of a fix of its own') && p.summary.includes('the score its fix must restore')
      && p.summary.includes('the part of the task it does not answer for')), 'and the run record says what it went on with');
    check(!!state && eqList(given(met, 'director:report').run_state, state), 'its report is given the same to write down again, for a run that goes on from it');
    // An issue whose existing fix PR the run verified; one whose root cause is in an AMD component (a P0, so a workaround was made).
    const existing = await resumedWith('issue', { existing_prs: ['#40872'] });
    check(existing.result.status === 'ready_for_review' && eqList(given(existing, 'pr_engineer:pr').existing_prs, ['#40872'])
      && eqList((existing.result.pr || {}).comment_for, ['#40872'])
      && (existing.result.next.user || []).some((u) => u.startsWith('post the verification comment on #40872')),
      `an issue's too: ${ended(existing)}, ${JSON.stringify(existing.result.next.user)}`);
    const draft = `${RUN}/component_issue.md`;
    const cause = { component: 'aiter', draft_path: draft, summary: 'aiter 0.1.11 works meanwhile' };
    const inAiter = await resumedWith('issue', { component_issue: cause });
    check(eqList(inAiter.result.component_issue, cause) && eqList(given(inAiter, 'issue_triager:reply').component_issue, cause)
      && eqList(given(inAiter, 'director:report').component_issue, cause)
      && (inAiter.result.next.user || []).some((u) => u.includes("in aiter's repository") && u.includes(draft)),
      `the reply, the report and the result still name the component the root cause is in, and the draft to file: ${JSON.stringify(inAiter.result.next.user)}`);
    // A run that had none of it wrote an empty object: nothing is restored, and nothing is missing.
    const none = await resumedWith('ci', {});
    check(none.result.status === 'ready_for_review' && !(none.result.pr || {}).comment_for && !none.result.unchecked && !none.result.component_issue
      && !given(none, 'verify_engineer:verify').accuracy_claim && eqList(given(none, 'director:report').run_state, { text_model: true })
      && !none.result.phases.some((p) => p.summary.includes('run_state.json')),
      `a run that had established none of it goes on as a run of its own fix: ${ended(none)}`);
    // What the file lost is not none: the run stops before anything is measured.
    check(stoppedFor(await resumedWith('ci', null), 'no readable run_state.json'), 'a file that is missing or does not parse stops the resume');
    for (const [what, lost] of [['existing_prs', { existing_prs: '#40872' }], ['existing_prs', { existing_prs: [] }], ['existing_pr', { existing_pr: '#50220' }],
      // A pull request the list does not name, or without the commit the run verified it at, is not the one the run verified.
      ['existing_pr', { existing_pr: theirs }], ['existing_pr', { existing_prs: ['#50221'], existing_pr: theirs }],
      ['existing_pr', { existing_prs: ['#50220'], existing_pr: { ...theirs, head: '' } }],
      ['accuracy_claim', { accuracy_claim: ' ' }], ['component_issue', { component_issue: { component: 'cuda', draft_path: draft } }],
      ['component_issue', { component_issue: { component: 'aiter' } }], ['unchecked', { unchecked: ['test_a'] }],
      // Only a key the file does not have says that the run had none of it: one that is null is held to its form like any other.
      ['accuracy_claim', { accuracy_claim: null }], ['existing_prs', { existing_prs: null }], ['text_model', { text_model: null }],
      ['text_model', { text_model: 'no' }]]) {
      const garbled = await resumedWith('ci', lost);
      check(stoppedFor(garbled, `run_state.json does not hold ${what} as the report was given it`),
        `${JSON.stringify(lost)} is not what a report was given, and is not read as none: ${ended(garbled)}`);
    }
    // An issue or a ci run whose file does not say what its model is lost it; a feature has no such thing to say.
    const unsaid = (mode) => run({ mode, task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), run_state: {} }) });
    check(stoppedFor(await unsaid('ci'), 'run_state.json does not say whether the model answers text questions (text_model)')
      && (await unsaid('feature')).result.status === 'ready_for_review', 'what the model is, every issue and ci run knows: a file without it lost it');
    // The script that compares the outputs of a model that answers no text questions is one the run measures with.
    // was, now: the script in run.json and in this task; knew: what the run knew of the model; says: this run's Setup.
    const S1 = '/s/outputs.sh';
    const outputs = ({ was = '', now = '', knew = false, says }, more = {}) => run({ mode: 'issue', task: 'i', resume_from: 'verify', run_dir: RUN,
      ...(now ? { output_script: now } : {}) }, {
      'director:setup': () => ({ ...DEFAULTS['director:setup'](), ...(says == null ? {} : { text_model: says }) }),
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), run_state: { text_model: knew }, task_definition: { ...ins.task_def, output_script: was } }),
      ...more });
    const comparison = "pass: 16 images of 5d6e7f8 in RUN_DIR/outputs/fix-5d6e7f8 match the base's (output_script: PSNR >= 35 dB on each)";
    const compared = { 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), text_model: false, gates: { issue_repro: 'pass', G2: comparison } }) };
    const same = await outputs({ was: S1, now: S1 }, compared);
    check(same.result.status === 'ready_for_review' && given(same, 'verify_engineer:verify').output_script === S1,
      `the run's own output comparison is run again: ${ended(same)}`);
    for (const [was, now] of [[S1, '/s/outputs_v2.sh'], ['', S1]]) {
      check(stoppedFor(await outputs({ was, now }, compared), '(output_script not as its run.json has them)'),
        `a resume given ${now} where the run had ${was || 'none'} would compare otherwise, and measures nothing`);
    }
    // What the model is, a Verify round can find where Setup took it for a text model: the report is given what the
    // run knows by then, and the run that goes on knows it too, whatever its own Setup says and its Verify leaves out.
    const found = await run({ mode: 'issue', task: 'i', output_script: S1 }, compared);
    check(found.result.status === 'ready_for_review' && given(found, 'director:report').run_state.text_model === false,
      `the report is given what a Verify round found of the model, not what Setup said: ${JSON.stringify(given(found, 'director:report').run_state)}`);
    const silent = { 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), gates: { issue_repro: 'pass' } }) };
    const skipped = await outputs({ was: S1, now: S1, says: true }, silent);
    check(skipped.result.status === 'blocked' && skipped.result.stopped_at === 'Verify' && given(skipped, 'verify_engineer:verify').text_model === false
      && /accepted without the result of its output comparison/.test(JSON.stringify(skipped.result.phases))
      && skipped.result.phases.some((p) => p.verdict === 'resumed' && p.summary.includes('a model that answers no text questions, as the run had it, '
        + 'whatever Setup says of it now')),
      `a round that leaves the comparison out is not accepted in the resumed run either, though its Setup takes the model for a text model: ${ended(skipped)}`);
    // Without the script the run had none either: its Verify is the judge again, and no round with a change to the server passes.
    const bare = await outputs({});
    check(bare.result.status === 'blocked' && bare.result.stopped_at === 'Verify' && bare.keys.includes('director:resume')
      && /the model answers no text questions, and the task names no output_script/.test(JSON.stringify(bare.result.phases)),
      `a run that found such a model and has no script to compare with passes no round: ${ended(bare)}`);
    // Nor is the resumed run asked on its own Setup's word, where the run it resumes had the model for a text model.
    const mind = await outputs({ knew: true, says: false });
    check(mind.result.status === 'ready_for_review' && mind.keys.includes('director:resume') && given(mind, 'verify_engineer:verify').text_model === true
      && !mind.result.questions, `a resumed run is not asked for a script on its Setup's word: ${ended(mind)}`);
    // A feature's claim is a cost only where its spec says so. One that does not say is not read as no: on InferPilot's
    // client a cost taken for a gain is judged as no slowdown at all.
    const costOf = (cost, more = {}) => run({ mode: 'feature', task: 't', resume_from: 'verify', run_dir: RUN }, {
      'director:resume': (ins) => ({ ...DEFAULTS['director:resume'](ins), perf_cost: cost, ...more }) });
    check(stoppedFor(await costOf(null), 'does not record whether the claim is a cost the user stated'),
      'a feature whose spec does not say whether its claim is a cost stops the resume');
    const noClaim = await costOf(null, { perf_required: false, perf_claim: '' });
    check(noClaim.result.status === 'ready_for_review' && /no slower than the baseline/.test(given(noClaim, 'perf_engineer:perf_eval').claim || ''),
      `one that states no claim has no cost to lose, and goes on: ${ended(noClaim)}`);
    // The director is told what to write down, what to give back, and that neither is put together from other records.
    const read = (...at) => readFileSync(path.join(INFERPILOT, ...at), 'utf8');
    const said = (phase) => phaseSection(read('roles', 'director.md'), phase).replace(/\s+/g, ' ');
    check(said('report').includes('Write `handoffs`, `known_issues` and `run_state` as given to `RUN_DIR/handoffs.json`, `RUN_DIR/known_issues.json` and '
      + '`RUN_DIR/run_state.json`, which a run resumed from Verify restores: each whole and unchanged, and one you were not given as empty')
      && said('report').includes('A run that stopped at Setup writes none of them'), 'the report writes the run\'s state down as it is given');
    check(said('resume').includes('`run_state`, as the report wrote it to `RUN_DIR/run_state.json`, in the same way')
      && said('resume').includes('`null` for a file that is missing or does not parse')
      && said('resume').includes('Take none of it from another record, and add nothing to it: a key the file does not have is left out, never given as `null`')
      && said('resume').includes('whether the model of an `issue` or a `ci` task answers text questions (`text_model`), as the run\'s Setup said or a Verify '
        + 'round found since, which goes before what this run\'s Setup says of it')
      && said('resume').includes('the scripts, `output_script` among them'), 'and the resume gives it back as the file holds it');
    check(said('resume').includes('as the row under the claim in `RUN_DIR/feature_spec.md` says (*the claim is a cost the user stated*: `yes` or `no`)')
      && said('resume').includes('`null` when the spec has no such row, or the row says neither: never `false` for it'),
      'whether a claim is a cost is the spec\'s own yes or no, and no answer where the spec gives none');
    check(said('resume').includes('a command put together from the rest of the record is not the one Verify was given')
      && said('resume').includes('empty where the record has no such row, never the claim as the rest of the record words it')
      && !said('resume').includes('a record written before it had'), 'a gate or a claim whose row is missing is not put together from the rest of the record');
    check(read('README.md').includes('| `handoffs.json`, `known_issues.json`, `run_state.json` |')
      && read('README.zh-CN.md').includes('| `handoffs.json`、`known_issues.json`、`run_state.json` |'), 'both READMEs list the file among what a run directory holds');
  }],
  ['agents: a timed-out call is not retried and goes on the attention list', async () => {
    const r = await run({ mode: 'feature', task: 't', agent_retries: 3, agent_timeout_ms: 100 }, {
      'verify_engineer:verify': async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        return { all_pass: true, failures: [] };
      },
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Verify', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'verify_engineer:verify').length === 1, 'a timed-out call is not run twice');
    check(r.result.attention.some((a) => String(a.call).startsWith('verify_engineer') && a.issue.includes('may still be running')),
      `attention: ${JSON.stringify(r.result.attention)}`);
  }],
  ['agents: a timed-out implement or unit call starts no further round and no rollback', async () => {
    const slow = (ans) => async (ins) => {
      await new Promise((resolve) => setTimeout(resolve, 400));
      return ans(ins);
    };
    for (const role of ['implementer:implement', 'test_engineer:unit']) {
      const r = await run({ mode: 'feature', task: 't', agent_timeout_ms: 100 }, {
        [role]: slow(role === 'implementer:implement'
          ? (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py' })
          : () => ({ unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '' })),
      });
      check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement', `${role}: status ${r.result.status}`);
      check(r.keys.filter((k) => k === 'implementer:implement').length === 1, `${role}: the work package gets no second round`);
      check(!r.keys.includes('implementer:rollback'), `${role}: no rollback while the timed-out agent may still run`);
      check(r.result.phases.some((p) => p.phase === 'Implement' && p.summary.includes('timed out')), `${role}: the stop says why`);
      check(r.result.attention.some((a) => String(a.call).startsWith(role.split(':')[0]) && a.issue.includes('may still be running')),
        `${role}: attention ${JSON.stringify(r.result.attention)}`);
    }
  }],
  ['agents: a failed call is retried, and the retry checks what the first one left', async () => {
    const r = await run({ mode: 'feature', task: 't', agent_retries: 2 }, {
      'verify_engineer:verify': (ins, n) => (n === 1 ? { verdict: 'not the schema' } : { all_pass: true, failures: [] }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const calls = r.calls.filter((c) => c.key === 'verify_engineer:verify');
    check(calls.length === 2 && !calls[0].prompt.includes('This is attempt') && calls[1].prompt.includes('This is attempt 2'),
      'the second attempt is told to check what the first left');
  }],
  ['setup: the GPUs Setup pins reach every later role; the user\'s GPUs win', async () => {
    const picked = await run({ mode: 'feature', task: 't' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', worktree: `${RUN}/wt`, gpu_ids: '4,5',
        accuracy_script: `${RUN}/scripts/accuracy.sh` }),
    });
    const later = picked.calls.filter((c) => !['director:setup', 'intake:define'].includes(c.key));
    check(later.length > 5 && later.every((c) => c.ins.gpu_ids === '4,5'), 'every role after Setup sees gpu_ids=4,5');
    check(later[0].prompt.includes('HIP_VISIBLE_DEVICES'), 'roles are told to export HIP_VISIBLE_DEVICES');
    const given = await run({ mode: 'feature', task: 't', gpu_ids: '2' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', gpu_ids: '6,7',
        accuracy_script: `${RUN}/scripts/accuracy.sh` }),
    });
    check(String(given.calls.find((c) => c.key === 'verify_engineer:verify').ins.gpu_ids) === '2', 'the user\'s GPUs win');
    const none = await run({ mode: 'issue', task: 'i' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'skip', gpu_ids: '' }),
    });
    check(none.result.status === 'blocked' && none.result.stopped_at === 'Setup', `no GPUs pinned: ${none.result.status}`);
  }],
  ['lock: while another task holds the machine, a run ends busy and creates nothing', async () => {
    const busy = { lock: 'busy', run_dir: '', baseline_status: 'skip', gpu_ids: '',
      notes: 'held by /x/exp/perf_dsv41 (container fangyuan) since 09:12; release: scripts/run_lock.py release --force' };
    for (const mode of ['feature', 'issue', 'ci', 'perf']) {
      const r = await run({ mode, task: 't' }, { 'director:setup': () => busy });
      check(r.result.status === 'busy' && r.result.stopped_at === 'Setup' && eqList(r.keys, ['director:setup']),
        `${mode}: ${r.result.status}, calls ${r.keys}`);
      check(r.result.run_dir === '' && r.result.phases[0].summary.includes('release --force'), `${mode}: the holder is reported`);
    }
    const scan = await run({ mode: 'scan' }, {
      'scout:collect': () => ({ lock: 'busy', run_dir: '', candidates_path: '', notes: 'held by /x/exp/issue_41299' }),
    });
    check(scan.result.status === 'busy' && eqList(scan.keys, ['scout:collect']), `scan: ${scan.result.status}`);
    const setup = (await run({ mode: 'issue', task: 'i' })).calls.find((c) => c.key === 'director:setup');
    check(String(setup.ins.lock_idle_hours) === '24', `idle hours for the lock, twice the longest call with its most time: ${setup.ins.lock_idle_hours}`);
  }],
  ['image: a task on an image other than the newest daily one stops at Setup to ask; image_ok=true goes on', async () => {
    const older = (ins) => (ins.image_ok ? undefined : {
      lock: 'skipped', run_dir: '', baseline_status: 'skip', gpu_ids: '',
      image: { current: 'rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261105', wanted: 'rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261109', verdict: 'older' } });
    const ok = { lock: 'acquired', run_dir: RUN, baseline_status: 'pass', baseline: '3/3 runs within spread', branch: 'inferpilot/task',
      worktree: '/sgl-workspace/sglang', gpu_ids: '0,1' };
    for (const mode of ['feature', 'issue', 'ci', 'perf']) {
      const asked = await run({ mode, task: 't' }, { 'director:setup': older });
      check(asked.result.status === 'needs_input' && asked.result.stopped_at === 'Setup' && eqList(asked.keys, ['director:setup']),
        `${mode}: asks before anything is created: ${asked.result.status}, calls ${asked.keys}`);
      check(asked.result.questions[0].field === 'image_ok' && asked.result.questions[0].question.includes('20261109'),
        `${mode}: the question names the newest image`);
    }
    const goes = await run({ mode: 'issue', task: 't', answers: { image_ok: true } },
      { 'director:setup': (ins) => (ins.image_ok ? ok : older(ins)) });
    check(goes.result.status !== 'needs_input' && goes.calls.find((c) => c.key === 'director:setup').ins.image_ok === true,
      `answers={"image_ok": true} runs on: ${goes.result.status}`);
    const named = await run({ mode: 'issue', task: 't', image: 'rocm/sgl-dev:v0.5.22-rocm10-mi35x-20261021' });
    check(named.calls.find((c) => c.key === 'director:setup').ins.image === 'rocm/sgl-dev:v0.5.22-rocm10-mi35x-20261021',
      'a named image reaches Setup');
  }],
  ['pr: what the PR engineer finds missing goes back through implement, verify and review', async () => {
    const r = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': (ins, n) => (n === 1
        ? { branch: 'inferpilot/task', lint_pass: false, feature_off_final: true, content_unchanged: true, pr_body_path: `${RUN}/pr_body.md`,
          leak_scan_findings: [], launch_checked: true, work_packages: [WP('H1', { goal: 'register the new test in test/registered' })] }
        : { branch: 'inferpilot/task', lint_pass: true, feature_off_final: true, content_unchanged: true, pr_body_path: `${RUN}/pr_body.md`,
          leak_scan_findings: [], launch_checked: true }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const afterPr = r.phases.slice(r.phases.indexOf('PR'));
    check(eqList(afterPr, ['PR', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases after the first PR: ${afterPr}`);
    check(r.result.done.some((d) => d.wp === 'H1'), 'the hygiene package was verified');
    const changed = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': () => ({ branch: 'inferpilot/task', lint_pass: true, feature_off_final: true, content_unchanged: false,
        pr_body_path: `${RUN}/pr_body.md`, leak_scan_findings: [], launch_checked: true, work_packages: [WP('H2')] }),
    });
    check(changed.result.status === 'blocked' && changed.result.stopped_at === 'PR', `a branch that changed after review: ${changed.result.status}`);
    check(changed.keys.filter((k) => k === 'implementer:implement').length === 1, 'no loop on a branch that changed after review');
    const clean = { branch: 'inferpilot/task', lint_pass: true, feature_off_final: true, content_unchanged: true, pr_body_path: `${RUN}/pr_body.md`,
      leak_scan_findings: [], launch_checked: true };
    const leaky = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': (ins, n) => (n === 1
        ? { ...clean, leak_scan_findings: ['test/x.py:12 /home/alice/model'], work_packages: [WP('H3', { goal: 'take the local path out of the test' })] }
        : { ...clean, leak_scan_findings: [], diff_stat: '3 files, +40/-2' }),
    });
    check(leaky.result.status === 'ready_for_review' && leaky.result.done.some((d) => d.wp === 'H3'),
      `a leak goes back as a work package: ${leaky.result.status}`);
    const launch = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': (ins, n) => (n === 1 ? { ...clean, launch_checked: false, work_packages: [WP('H4')] } : { ...clean, launch_checked: true }),
    });
    check(launch.result.status === 'ready_for_review' && launch.result.done.some((d) => d.wp === 'H4'),
      `a launch command that fails goes back as a work package: ${launch.result.status}`);
    const lacking = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': (ins, n) => (n === 1 ? { ...clean, work_packages: [WP('H5', { goal: 'drop the refactor of shared code' })] } : clean),
    });
    check(lacking.result.status === 'ready_for_review' && lacking.result.done.some((d) => d.wp === 'H5'),
      `a work package with every check true is made too: ${lacking.result.status}`);
    const finding = 'a1b2c3d4e5 docs/x.md:3 /home/alice/run';
    const kept = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': () => ({ ...clean, leak_scan_findings: [finding] }),
    });
    const reported = kept.calls.find((c) => c.key === 'director:report');
    check(kept.result.status === 'blocked' && (kept.result.pr.leak_scan_findings || [])[0] === finding
      && !!reported && (reported.ins.leak_scan_findings || [])[0] === finding,
      `a leak the PR stopped on stays in the result and reaches the report: ${kept.result.status}`);
    const left = await run({ mode: 'ci', task: 'c' }, {
      'pr_engineer:pr': () => ({ ...clean, work_packages: [WP('H6', { goal: 'drop the refactor of shared code' })] }),
    });
    const told = left.calls.find((c) => c.key === 'director:report');
    check(left.result.status === 'blocked' && ((left.result.pr.work_packages || [])[0] || {}).id === 'H6'
      && !!told && ((told.ins.pr_work_packages || [])[0] || {}).id === 'H6',
      `what the second PR round still lacks stays in the result and reaches the report: ${left.result.status}`);
  }],
  ['stop_after: every mode stops after Setup; a phase the mode lacks is asked about', async () => {
    for (const mode of ['issue', 'ci', 'perf']) {
      const r = await run({ mode, task: 't', stop_after: 'Setup' });
      check(r.result.status === 'stopped' && r.result.stopped_at === 'Setup' && r.keys.at(-2) === 'director:report',
        `${mode}: ${r.result.status} at ${r.result.stopped_at}`);
      check(r.result.next.say.startsWith('Stopped after Setup, as stop_after asked.'), `${mode}: ${r.result.next.say}`);
    }
    check(asks(await run({ mode: 'feature', task: 't', stop_after: 'Reproduce' }), 'stop_after'), 'Reproduce is not a feature phase');
    check(asks(await run({ mode: 'ci', task: 't', stop_after: 'Reproduce' }), 'stop_after'), 'Reproduce is not a ci phase');
    const measured = await run({ mode: 'ci', task: 't', stop_after: 'Performance' });
    check(measured.result.status === 'stopped' && measured.result.stopped_at === 'Performance' && !measured.keys.includes('reviewer:review'),
      `a ci fix is measured, and the run can stop after it: ${measured.result.status} at ${measured.result.stopped_at}`);
    const ok = await run({ mode: 'issue', task: 'i', stop_after: 'reproduce' });
    check(ok.result.status === 'stopped' && ok.result.stopped_at === 'Reproduce', `case does not matter: ${ok.result.status}`);
  }],
  ['stop_after: the control file moves the stop while the run goes on; now stops at the next boundary', async () => {
    const moved = await run({ mode: 'feature', task: 't' }, undefined, { control: () => ({ stop_after: 'Spec' }) });
    check(moved.result.status === 'stopped' && moved.result.stopped_at === 'Spec', `moved: ${moved.result.status} at ${moved.result.stopped_at}`);
    check(moved.lines.some((l) => l === '[inferpilot] control: stop_after=spec'), 'the change is logged');
    check(moved.result.next.say.startsWith('Stopped after Spec, as the control file asked.')
      && moved.result.phases.some((p) => p.phase === 'Spec' && p.verdict === 'stopped' && p.summary === 'the control file asked: stop_after=spec'),
    `the result says the control file asked for the stop: ${moved.result.next.say}`);
    let verifying = false;
    const now = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': () => {
        verifying = true;
        return { all_pass: false, failures: [{ gate: 'G2' }] };
      },
    }, { control: () => (verifying ? { stop_after: 'now' } : {}) });
    check(now.result.status === 'stopped' && now.result.stopped_at === 'Verify', `now: ${now.result.status} at ${now.result.stopped_at}`);
    check(!now.keys.includes('investigator:investigate') && now.keys.includes('director:report'),
      'now during a failing Verify stops before Investigate, and the report still runs');
    const told = now.calls.find((c) => c.key === 'director:report');
    check(told && told.ins.phases.some((p) => p.verdict === 'stopped' && p.summary === 'the control file asked: stop_after=now'),
      'the report hears that the control file asked for the stop');
    let reviewed = false;
    const inLoop = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': () => {
        reviewed = true;
        return { verdict: 'changes_requested', findings: [{ severity: 'major', issue: 'x' }], work_packages: [WP('R9')],
          review_path: `${RUN}/review.md` };
      },
    }, { control: () => (reviewed ? { stop_after: 'Verify' } : {}) });
    check(inLoop.result.status === 'stopped' && inLoop.result.stopped_at === 'Verify' && !inLoop.keys.includes('pr_engineer:pr')
      && inLoop.keys.filter((k) => k === 'verify_engineer:verify').length === 2,
    `a stop named during the review repair round ends the run after its Verify: ${inLoop.result.status} at ${inLoop.result.stopped_at}`);
    let reviewing = false;
    const past = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': () => {
        reviewing = true;
        return DEFAULTS['reviewer:review']();
      },
    }, { control: () => (reviewing ? { stop_after: 'Spec' } : {}) });
    check(past.result.status === 'ready_for_review'
      && past.lines.includes('[inferpilot] control: stop_after=spec; the run is past Spec and stops there only if it comes back to it'),
    `a stop the run is past is named as such, and the run goes on: ${past.result.status}`);
    let designing = false;
    const resumed = await run({ mode: 'feature', task: 't', resume_from: 'implement', run_dir: RUN }, {
      'architect:design': (ins) => {
        designing = true;
        return DEFAULTS['architect:design'](ins);
      },
    }, { control: () => (designing ? { stop_after: 'Spec' } : {}) });
    check(resumed.result.status === 'ready_for_review'
      && resumed.lines.includes('[inferpilot] control: stop_after=spec; the run is past Spec and stops there only if it comes back to it'),
    `a resumed run is past the stops of the run it resumes: ${resumed.result.status}`);
    let again = false;
    const checkedNow = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', issue: 'x' }], work_packages: [WP('R9')], review_path: `${RUN}/review.md` }
        : DEFAULTS['reviewer:review']()),
      'verify_engineer:verify': (ins, n) => {
        again = n === 2;
        return DEFAULTS['verify_engineer:verify']();
      },
    }, { control: () => (again ? { stop_after: 'Verify' } : {}) });
    check(checkedNow.result.status === 'stopped' && checkedNow.result.stopped_at === 'Verify'
      && checkedNow.lines.includes('[inferpilot] control: stop_after=verify'),
    `a stop named while the run passes that phase again is not behind it: ${checkedNow.result.status} at ${checkedNow.result.stopped_at}`);
    const cleared = await run({ mode: 'issue', task: 'i', stop_after: 'Setup' }, undefined, { control: () => ({ stop_after: '' }) });
    check(cleared.result.stopped_at !== 'Setup' && cleared.phases.includes('Triage'), `cleared: ${cleared.result.status}`);
    const bad = await run({ mode: 'ci', task: 'c', stop_after: 'Verify' }, undefined, { control: () => ({ stop_after: 'Reproduce' }) });
    check(bad.lines.some((l) => /stop_after=reproduce is not a phase of mode=ci; the stop stays verify/.test(l))
      && bad.result.status === 'stopped' && bad.result.stopped_at === 'Verify', `a phase the mode lacks keeps the stop: ${bad.result.stopped_at}`);
  }],
  ['stop_after: a stop asked for now still runs the closing phases, Spinoff among them, without confirming leads', async () => {
    const lead = { title: 'tokenizer test fails on main', observed: '3/3 KeyError', reproduce: 'pytest test_tokenizer.py' };
    let verifying = false;
    const r = await run({ mode: 'feature', task: 't', confirm_leads: true }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
        unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
      'verify_engineer:verify': () => {
        verifying = true;
        return { all_pass: false, failures: [{ gate: 'G2' }] };
      },
    }, { control: () => (verifying ? { stop_after: 'now' } : {}) });
    check(r.result.status === 'stopped' && r.result.stopped_at === 'Verify', `now with a lead: ${r.result.status} at ${r.result.stopped_at}`);
    const sp = r.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(sp && r.keys.includes('director:report'), 'Spinoff and the report run after a stop asked for now');
    check(sp && sp.ins.confirm_leads === false && (sp.ins.confirm_skipped || []).some((x) => /stop/.test(x)),
      `no lead is run on the GPUs after a stop asked for now: ${JSON.stringify(sp && [sp.ins.confirm_leads, sp.ins.confirm_skipped])}`);
    let preparing = false;
    const last = await run({ mode: 'feature', task: 't', confirm_leads: true }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
        unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
      'pr_engineer:pr': () => {
        preparing = true;
        return DEFAULTS['pr_engineer:pr']();
      },
    }, { control: () => (preparing ? { stop_after: 'now' } : {}) });
    const spLast = last.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(last.result.status === 'ready_for_review' && spLast && spLast.ins.confirm_leads === false,
      `now during the last phase, which goes straight to the closing phases, runs no lead either: ${JSON.stringify(spLast && spLast.ins.confirm_leads)}`);
    let tuning = false;
    const kernel = await run({ mode: 'feature', task: 't', ...CMD }, {
      ...withKernelHandoff('hot op'),
      'kernel_liaison:run': (ins) => {
        tuning = true;
        return DEFAULTS['kernel_liaison:run'](ins);
      },
    }, { control: () => (tuning ? { stop_after: 'now' } : {}) });
    const statuses = (hs) => (hs || []).map((h) => h.status).join();
    const reported = kernel.calls.find((c) => c.key === 'director:report');
    check(kernel.result.status === 'stopped' && kernel.result.stopped_at === 'Kernel' && !kernel.phases.includes('Verify')
      && statuses(kernel.result.handoffs) === 'not_integrated' && reported && statuses(reported.ins.handoffs) === 'not_integrated',
    `now while the kernel back end runs leaves its gain not_integrated, as the report hears: ${statuses(kernel.result.handoffs)}`);
  }],
  ['stop_after: under the Workflow tool, which has no control global, a run goes as its arguments say', async () => {
    const lead = { title: 'tokenizer test fails on main', observed: '3/3 KeyError', reproduce: 'pytest test_tokenizer.py' };
    const whole = await run({ mode: 'feature', task: 't', confirm_leads: true }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
        unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
    }, { workflowTool: true });
    const sp = whole.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(whole.result.status === 'ready_for_review' && sp && sp.ins.confirm_leads === true,
      `a whole run, its lead confirmed in Spinoff: ${whole.result.status}, ${JSON.stringify(sp && sp.ins.confirm_leads)}`);
    const stopped = await run({ mode: 'feature', task: 't', stop_after: 'Verify' }, undefined, { workflowTool: true });
    check(stopped.result.status === 'stopped' && stopped.result.stopped_at === 'Verify'
      && stopped.result.next.say.startsWith('Stopped after Verify, as stop_after asked.'),
    `stop_after: ${stopped.result.status} at ${stopped.result.stopped_at}`);
    check(![...whole.lines, ...stopped.lines].some((l) => l.startsWith('[inferpilot] control:')), 'no control file, no control line');
  }],
  ['feature: a work package that never passes is rolled back', async () => {
    const r = await run({ mode: 'feature', task: 't', max_fix_rounds: 1 }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', failure_summary: 'still wrong' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'implementer:implement').length === 2, 'max_fix_rounds + 1 attempts');
    check(r.keys.includes('implementer:rollback'), 'rollback called');
    check(!r.keys.includes('verify_engineer:verify'), 'no verification of a failed package');
  }],
  ['implement: a unit gate with checks still running is finished by one more call, which spends no fix round', async () => {
    const r = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1
        ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent',
          incomplete: 'DCP8 runs 4-5 not finished: driver pid 4242 writes logs/unit_WP1_driver.log', evidence: 'runs 1-3 pass' }
        : { wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', evidence: 'runs 1-5 pass' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const units = r.calls.filter((c) => c.key === 'test_engineer:unit');
    check(units.length === 3, `WP1 twice, WP2 once: ${units.length}`);
    check(units[1] && units[1].ins.commit === 'c_WP1' && String(units[1].ins.incomplete).includes('pid 4242')
      && units[1].ins.earlier_evidence === 'runs 1-3 pass', 'the second call finishes the same commit, told what is left');
    check(r.keys.filter((k) => k === 'implementer:implement').length === 2 && !r.keys.includes('implementer:rollback'),
      'no fix round and no rollback for a gate without a result');
  }],
  ['implement: a unit gate still without a result stops the package for a human: not rolled back, its commit kept', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: 'DCP1 never started: the GPUs were taken' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'test_engineer:unit').length === 2, 'one call to finish it, no more');
    check(r.keys.filter((k) => k === 'implementer:implement').length === 1, 'no fix round');
    check(!r.keys.includes('implementer:rollback'), 'nothing measured went against the change: no rollback');
    const imp = r.result.phases.find((p) => p.phase === 'Implement');
    check(imp && imp.verdict === 'incomplete' && imp.summary.includes('DCP1 never started'), `the stop says why: ${imp && imp.summary}`);
    check(r.result.attention.some((a) => a.wp === 'WP1' && a.issue.includes('c_WP1') && a.issue.includes('unverified')),
      `attention: ${JSON.stringify(r.result.attention)}`);
    const nothing = await run({ mode: 'feature', task: 't' }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: 'run 3 still going' } : null),
    });
    check(nothing.result.stopped_at === 'Implement' && !nothing.keys.includes('implementer:rollback')
      && nothing.result.attention.some((a) => a.wp === 'WP1'), 'a finishing call that returns nothing leaves the gate without a result too');
    const failed = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: '', failure_summary: 'run 2 diverged' }),
    });
    check(failed.keys.includes('implementer:rollback') && failed.keys.filter((k) => k === 'test_engineer:unit').length === 1,
      'a check that failed is a failure: rolled back as before');
  }],
  ['implement: a unit call with no answer at all is finished by one more call, not a fix round or a rollback', async () => {
    const r = await run({ mode: 'feature', task: 't', agent_retries: 1, max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? null
        : { wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', evidence: 'ok' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const fin = r.calls.filter((c) => c.key === 'test_engineer:unit')[1];
    check(fin && fin.ins.commit === 'c_WP1' && String(fin.ins.incomplete).includes('returned no answer'),
      `the second call finishes the same commit, told the first gave no answer: ${fin && fin.ins.incomplete}`);
    check(!r.keys.includes('implementer:rollback') && r.keys.filter((k) => k === 'implementer:implement').length === 2, 'no fix round, no rollback');
    const none = await run({ mode: 'feature', task: 't', agent_retries: 1 }, { 'test_engineer:unit': () => null });
    check(none.result.stopped_at === 'Implement' && !none.keys.includes('implementer:rollback'), `no answer twice: held, not rolled back: ${none.result.status}`);
    const imp = none.result.phases.find((p) => p.phase === 'Implement' && p.verdict === 'incomplete');
    check(imp && imp.summary.includes('the call to finish it returned no answer'), `the stop names the missing finishing answer: ${imp && imp.summary}`);
    const lead = { title: 'warning', observed: 'x', where: 'y', evidence: 'z', reproduce: 'python -c 1' };
    const leads = await run({ mode: 'feature', task: 't', agent_retries: 1, confirm_leads: true }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
      'test_engineer:unit': () => null,
    });
    const sp = leads.calls.find((c) => c.key === 'issue_triager:spinoff');
    check(sp && sp.ins.confirm_leads === false && (sp.ins.confirm_skipped || []).some((x) => x.includes('unit gate of WP1')),
      `no lead runs on GPUs a gate without a result may still use: ${JSON.stringify(sp && sp.ins.confirm_skipped)}`);
  }],
  ['implement: a unit answer is never asked for again blind, and one with checks not run says so with null', async () => {
    const finishing = (r) => r.calls.filter((c) => c.key === 'test_engineer:unit')[1] || { ins: {} };
    const good = (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent',
      incomplete: '', evidence: 'ok' });
    const unusable = await run({ mode: 'feature', task: 't', agent_retries: 2, max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? { wp: ins.work_package.id, unit_pass: false } : good(ins)),
    }, { schemaRetries: 1 });
    check(String(finishing(unusable).ins.incomplete).includes('returned no answer') && unusable.result.status === 'ready_for_review',
      `an unusable answer goes to the call that finishes the gate, not to the same prompt again: ${finishing(unusable).ins.incomplete}`);
    const thrown = await run({ mode: 'feature', task: 't', agent_retries: 2, max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => {
        if (n === 1) throw new Error('the backend died');
        return good(ins);
      },
    });
    check(String(finishing(thrown).ins.incomplete).includes('returned no answer'), 'nor is a unit call that failed retried blind');
    const silent = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true,
        fails_without_change: 'parent', failure_summary: 'the driver still runs' } : good(ins)),
    });
    check((CONTRACTS.get('test_engineer:unit') || []).includes('incomplete') && !silent.keys.includes('implementer:rollback')
      && silent.keys.filter((k) => k === 'implementer:implement').length === 2,
      'an answer without incomplete is not a failed gate: no fix round and no rollback');
    const unrun = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: null,
        fails_without_change: null, incomplete: 'feature off and T1 not started: driver pid 4242 writes logs/unit_WP1.log' } : good(ins)),
    });
    check(String(finishing(unrun).ins.incomplete).includes('pid 4242') && unrun.result.status === 'ready_for_review',
      `checks not run are null, and the gate reaches its finishing call as it was: ${finishing(unrun).ins.incomplete}`);
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'test_engineer.md'), 'utf8'), 'unit').replace(/\s+/g, ' ');
    check(doc.includes('stop the runs this gate started with `stop_launch.py`, as above'), 'a failed gate stops its runs before it returns');
    const hygiene = readFileSync(path.join(INFERPILOT, 'knowledge', 'env_hygiene.md'), 'utf8').replace(/\s+/g, ' ');
    check(doc.includes('The one exception is what a check you return in `incomplete` still needs, which the call that finishes the gate takes over')
      && doc.includes('This call leaves nothing running: before it returns it stops what the call before left, by the `<log>`s that call gave')
      && hygiene.includes('A role returns before that only when its answer says what still runs and where it writes, for the call that takes it '
        + 'over and stops it (`incomplete`, `roles/test_engineer.md`)'),
      'what a gate without a result leaves running is the finishing call\'s to stop, and nothing else outlives the call that started it');
    check(doc.includes('Steps 1 to 4 run only the checks that never started'), 'the finishing call runs only what is missing');
  }],
  ['implement: a check given as null with nothing in incomplete is a gate without a result, not a failed one', async () => {
    const good = (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '' });
    const open = (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: null, fails_without_change: null, incomplete: '' });
    const units = (r) => r.calls.filter((c) => c.key === 'test_engineer:unit');
    const implements_ = (r) => r.keys.filter((k) => k === 'implementer:implement').length;
    const finished = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, { 'test_engineer:unit': (ins, n) => (n === 1 ? open(ins) : good(ins)) });
    check(finished.result.status === 'ready_for_review' && implements_(finished) === 2 && !finished.keys.includes('implementer:rollback'),
      `finished by one more call, no fix round and no rollback: ${finished.result.status}`);
    check(units(finished).length === 3 && units(finished)[1].ins.commit === 'c_WP1'
      && /the feature-off check and the check that the test can fail did not run/.test(units(finished)[1].ins.incomplete),
      `the finishing call is told which checks: ${units(finished)[1] && units(finished)[1].ins.incomplete}`);
    const one = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins, n) => (n === 1 ? { ...good(ins), fails_without_change: null } : good(ins)) });
    check(units(one)[1] && /^the check that the test can fail did not run/.test(units(one)[1].ins.incomplete),
      `only the check that is null is named: ${units(one)[1] && units(one)[1].ins.incomplete}`);
    const never = await run({ mode: 'feature', task: 't' }, { 'test_engineer:unit': open });
    check(never.result.status === 'blocked' && never.result.stopped_at === 'Implement' && units(never).length === 2
      && implements_(never) === 1 && !never.keys.includes('implementer:rollback'),
      `still null after the finishing call: the package stops, its commit kept: ${never.result.status} at ${never.result.stopped_at}`);
    check(never.result.attention.some((a) => a.wp === 'WP1' && a.issue.includes('c_WP1') && a.issue.includes('the feature-off check')),
      `attention: ${JSON.stringify(never.result.attention)}`);
    for (const [why, bad] of [['the unit test failed', { unit_pass: false }], ['the feature-off run changed', { feature_off_unchanged: false }],
      ['the test cannot fail', { fails_without_change: 'no' }]]) {
      const r = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, { 'test_engineer:unit': (ins) => ({ ...open(ins), ...bad }) });
      check(r.keys.includes('implementer:rollback') && units(r).length === 1,
        `${why}, whatever else did not run: a failure as before, rolled back with no finishing call`);
    }
    const doc = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', 'test_engineer.md'), 'utf8'), 'unit').replace(/\s+/g, ' ');
    check(doc.includes('Every answer has `incomplete`, `""` when every check has a result or one that ran has failed: a failed gate names '
      + 'nothing there, and a check it did not run stays `null`')
      && doc.includes('A gate with checks still without a result, none of those that ran having failed, returns `unit_pass` false'),
      'what the answer holds says the same as step 0: a failed gate leaves incomplete empty');
  }],
  ['kernel: a held integration whose implementer timed out has a commit not reported, which the report does not take for none', async () => {
    const implement = withKernelHandoff('decode hot path')['implementer:implement'];
    const r = await run({ mode: 'feature', task: 't', agent_timeout_ms: 100, ...CMD }, {
      'implementer:implement': async (ins) => {
        if (ins.work_package.id === 'K-K-WP1') await new Promise((resolve) => setTimeout(resolve, 400));
        return implement(ins);
      },
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(r.result.handoffs[0].status === 'integration_held' && r.result.handoffs[0].commit === '',
      `handoff ${JSON.stringify(r.result.handoffs[0])}`);
    const director = readFileSync(path.join(INFERPILOT, 'roles', 'director.md'), 'utf8').replace(/\s+/g, ' ');
    check(director.includes('An empty `commit` is one not reported, not one not made: the implementer\'s call timed out, and may have '
      + 'committed or still be running, so say that the branch is to be inspected once that call has stopped')
      && !director.includes('empty when the implementer made none'),
      'the director is told what an empty commit means');
  }],
  ['implement: a call finishing a unit gate that times out leaves the human its commit and what was still going', async () => {
    const r = await run({ mode: 'feature', task: 't', agent_timeout_ms: 100 }, {
      'test_engineer:unit': async (ins, n) => {
        if (n > 1) await new Promise((resolve) => setTimeout(resolve, 400));
        return { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent',
          incomplete: 'DCP8 runs 4-5: driver pid 4242 writes logs/unit_WP1_driver.log' };
      },
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement' && !r.keys.includes('implementer:rollback'),
      `status ${r.result.status} at ${r.result.stopped_at}`);
    check(r.result.attention.some((a) => a.wp === 'WP1' && a.issue.includes('c_WP1') && a.issue.includes('pid 4242')),
      `attention: ${JSON.stringify(r.result.attention)}`);
  }],
  ['spinoff: no lead runs on GPUs a Verify round that stopped for time may still use', async () => {
    const lead = { title: 'warning', observed: 'x', where: 'y', evidence: 'z', reproduce: 'python -c 1' };
    for (const [why, args] of [['a limit the user gave', { agent_timeout_ms: 7200000 }], ['the limit it asked for', {}]]) {
      const r = await run({ mode: 'feature', task: 't', confirm_leads: true, ...args }, {
        'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`,
          unit_test_cmd: 'pytest -q t.py', leads: [lead] }),
        'verify_engineer:verify': () => ({ all_pass: false, failures: [], needs_min: 500 }),
      });
      const sp = r.calls.find((c) => c.key === 'issue_triager:spinoff');
      check(r.result.stopped_at === 'Verify' && sp && sp.ins.confirm_leads === false
        && (sp.ins.confirm_skipped || []).some((x) => x.includes('Verify round 0 stopped for time')),
        `${why}: ${JSON.stringify(sp && sp.ins.confirm_skipped)}`);
    }
  }],
  ['kernel: an integration whose unit gate has no result stops the run before Verify, its commit kept', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD }, { ...withKernelHandoff('decode hot path'),
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: ins.work_package.id !== 'K-K-WP1', feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: ins.work_package.id === 'K-K-WP1' ? 'the kernel suite still runs' : '' }) });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('verify_engineer:verify') && !r.keys.includes('implementer:rollback'), 'no Verify of an unverified kernel, and no rollback');
    check(r.result.handoffs[0].status === 'integration_held' && r.result.handoffs[0].commit === 'c_K-K-WP1',
      `handoff ${JSON.stringify(r.result.handoffs[0])}`);
  }],
  ['kernel: the hand-offs after one whose integration is held are recorded, not left without a status', async () => {
    const plan = { 'architect:perf_plan': () => ({ work_packages: [], handoffs: [
      { id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O2/op_spec.md` },
      { id: 'O4', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O4/op_spec.md` }], perf_claim: '-6% TPOT', gate_ok: true }) };
    const r = await run({ mode: 'perf', task: 'p', ...CMD }, { ...plan,
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: ins.work_package.id !== 'K-O2', feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: ins.work_package.id === 'K-O2' ? 'the kernel suite still runs' : '' }) });
    const byId = Object.fromEntries(r.result.handoffs.map((h) => [h.id, h.status]));
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(byId.O2 === 'integration_held' && byId.O4 === 'recorded', `statuses ${JSON.stringify(byId)}`);
    check(r.keys.filter((k) => k === 'kernel_liaison:package').length === 1, 'the back end is not run on a hand-off after the held one');
    check(r.result.phases.some((p) => p.phase === 'Kernel' && /O4 recorded, not run/.test(p.summary)),
      `the Kernel phase says so: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Kernel'))}`);
  }],
  ['kernel: a hand-off an integration raised is recorded too when one is held, not only those the stage began with', async () => {
    const plan = { 'architect:perf_plan': () => ({ work_packages: [], handoffs: [
      { id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O2/op_spec.md` },
      { id: 'O4', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O4/op_spec.md` }], perf_claim: '-6% TPOT', gate_ok: true }) };
    const r = await run({ mode: 'perf', task: 'p', ...CMD }, { ...plan,
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
        kernel_handoff: { op_spec_path: `${RUN}/handoffs/K-${ins.work_package.id}/op_spec.md`, test_cmd: 'pytest k.py', plug_in: 'x.py:f', reason: 'hot op' } }),
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: ins.work_package.id !== 'K-O4', feature_off_unchanged: true,
        fails_without_change: 'parent', incomplete: ins.work_package.id === 'K-O4' ? 'the kernel suite still runs' : '' }) });
    const byId = Object.fromEntries(r.result.handoffs.map((h) => [h.id, h.status]));
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Kernel', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(byId.O2 === 'integrated' && byId.O4 === 'integration_held', `statuses ${JSON.stringify(byId)}`);
    check(byId['K-K-O2'] === 'recorded' && byId['K-K-O4'] === 'recorded',
      `the one an earlier integration raised and the held one's own: ${JSON.stringify(byId)}`);
    check(r.result.handoffs.every((h) => h.status), `none is left without a status: ${JSON.stringify(byId)}`);
    check(r.keys.filter((k) => k === 'kernel_liaison:package').length === 2, 'the back end is not run on them');
    check(r.result.phases.some((p) => p.phase === 'Kernel' && /K-K-O2, K-K-O4 recorded, not run/.test(p.summary)),
      `the Kernel phase names them: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Kernel'))}`);
  }],
  ['kernel: an integration without a unit result stops the run also from a Verify fix, a review change or a kernel retry', async () => {
    const heldFor = (id) => (ins) => ({ wp: ins.work_package.id, unit_pass: ins.work_package.id !== id, feature_off_unchanged: true,
      fails_without_change: 'parent', incomplete: ins.work_package.id === id ? 'the kernel suite still runs' : '' });
    const handoffFrom = (id) => (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
      kernel_handoff: ins.work_package.id === id ? { op_spec_path: `${RUN}/handoffs/K-${id}/op_spec.md`, test_cmd: 'pytest k.py', plug_in: 'x.py:f', reason: 'hot op' } : null });
    const verifies = (r) => r.keys.filter((k) => k === 'verify_engineer:verify').length;
    const fix = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('FIX1'),
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G2' }] }),
      'test_engineer:unit': heldFor('K-K-FIX1'),
    });
    check(fix.result.status === 'blocked' && verifies(fix) === 1 && !fix.keys.includes('implementer:rollback')
      && fix.result.handoffs[0].status === 'integration_held', `a Verify fix's kernel: Verify ran ${verifies(fix)} time(s), ${JSON.stringify(fix.result.handoffs)}`);
    check(fix.result.stopped_at === 'Kernel' && fix.result.next.say.includes(' at Kernel'),
      `a Verify fix's kernel stops the run at Kernel: ${fix.result.stopped_at}; ${fix.result.next.say}`);
    const review = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('RV1'),
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', where: 'x.py:1', issue: 'slow' }], work_packages: [WP('RV1')], review_path: `${RUN}/review.md` }
        : { verdict: 'approve', findings: [], review_path: `${RUN}/review.md` }),
      'test_engineer:unit': heldFor('K-K-RV1'),
    });
    check(review.result.status === 'blocked' && review.result.stopped_at === 'Kernel' && verifies(review) === 1,
      `a review change's kernel: ${review.result.status} at ${review.result.stopped_at}, Verify ran ${verifies(review)} time(s)`);
    const afterReview = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': handoffFrom('FIX1'),
      'reviewer:review': () => ({ verdict: 'changes_requested', findings: [{ severity: 'major', where: 'x.py:1', issue: 'slow' }],
        work_packages: [WP('RV1')], review_path: `${RUN}/review.md` }),
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: true, failures: [] } : { all_pass: false, failures: [{ gate: 'G2' }] }),
      'test_engineer:unit': heldFor('K-K-FIX1'),
    });
    check(afterReview.result.status === 'blocked' && afterReview.result.stopped_at === 'Kernel' && verifies(afterReview) === 2,
      `the kernel of a Verify fix after a review change: ${afterReview.result.status} at ${afterReview.result.stopped_at}, Verify ran ${verifies(afterReview)} time(s)`);
    let kernelGates = 0;
    let kernelCommits = 0;
    const implement = withKernelHandoff('decode hot path')['implementer:implement'];
    const retry = await run({ mode: 'feature', task: 't', ...CMD }, {
      'implementer:implement': (ins) => ({ ...implement(ins),
        ...(ins.work_package.id === 'K-K-WP1' && ++kernelCommits > 1 ? { commit: 'c_K-K-WP1_retry' } : {}) }),
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G3' }] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-WP1'], counterexample: 'G3 needle' }),
      'test_engineer:unit': (ins) => heldFor(ins.work_package.id === 'K-K-WP1' && ++kernelGates > 1 ? 'K-K-WP1' : '')(ins),
    });
    check(retry.result.status === 'blocked' && verifies(retry) === 1 && retry.result.handoffs[0].status === 'integration_held'
      && retry.keys.filter((k) => k === 'implementer:rollback').length === 1,
      `a kernel handed back: Verify ran ${verifies(retry)} time(s), ${JSON.stringify(retry.result.handoffs[0])}`);
    check(retry.result.handoffs[0].commit === 'c_K-K-WP1_retry',
      `the held hand-off names the commit left on the branch, not the reverted one: ${retry.result.handoffs[0].commit}`);
    check(retry.result.stopped_at === 'Kernel', `a kernel handed back stops the run at Kernel: ${retry.result.stopped_at}`);
  }],
  ['feature: baseline that does not reproduce blocks at Setup', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'fail', baseline: 'spread 9% > 2%', gpu_ids: '0' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Setup', `status ${r.result.status}`);
    check(r.keys.includes('director:report'), 'blocked runs still get a report');
  }],
  ['setup: a skipped baseline is enough only where none is required', async () => {
    const skip = { 'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'skip', baseline: 'not needed yet', gpu_ids: '0',
      server_script: `${RUN}/scripts/server.sh`, accuracy_script: `${RUN}/scripts/accuracy.sh` }) };
    const feature = await run({ mode: 'feature', task: 't' }, skip);
    check(feature.result.status === 'blocked' && feature.result.stopped_at === 'Setup', `feature with skip: ${feature.result.status}`);
    const issue = await run({ mode: 'issue', task: 'i' }, skip);
    check(issue.result.status === 'ready_for_review', `issue with skip: ${issue.result.status}`);
    const prose = await run({ mode: 'issue', task: 'i' }, { 'director:setup': () => ({ run_dir: RUN, baseline: 'PASS - 3/3' }) });
    check(prose.result.status === 'blocked' && prose.result.stopped_at === 'Setup', 'a baseline in prose without baseline_status is rejected');
  }],
  ['arguments: an issue or a ci task that can reach a fix names the model G2 measures it on; one that stops before the fix needs none', async () => {
    check(asks(await run({ mode: 'issue', task: 'i', model_path: '' }), 'model_path'), 'an issue without a model asked');
    check(asks(await run({ mode: 'ci', task: 'c', model_path: '' }), 'model_path'), 'a ci task without a model asked');
    check(asks(await run({ mode: 'issue', task: 'i', model_path: '', stop_after: 'Implement' }), 'model_path'),
      'a run that stops after Implement has a fix: asked');
    for (const [mode, stop] of [['issue', 'Triage'], ['issue', 'Reproduce'], ['issue', 'Diagnose'], ['ci', 'Classify'], ['ci', 'Bisect']]) {
      const r = await run({ mode, task: 't', model_path: '', stop_after: stop });
      check(r.result.status === 'stopped' && r.result.stopped_at === stop && !r.keys.includes('intake:define'),
        `${mode} stopping after ${stop} has no fix to measure and starts without a model: ${r.result.status} at ${r.result.stopped_at}`);
    }
    const bare = { 'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'skip', baseline: 'not needed yet', gpu_ids: '0' }) };
    const noAccuracy = await run({ mode: 'ci', task: 'c' }, bare);
    check(noAccuracy.result.status === 'blocked' && noAccuracy.result.stopped_at === 'Setup'
      && /no accuracy script/.test(JSON.stringify(noAccuracy.result.phases)),
      `a fix that G2 will measure needs the accuracy script from Setup on: ${noAccuracy.result.status} at ${noAccuracy.result.stopped_at}`);
    const early = await run({ mode: 'ci', task: 'c', model_path: '', stop_after: 'Classify' }, bare);
    check(early.result.status === 'stopped' && early.result.stopped_at === 'Classify', `a run with no fix needs none: ${early.result.status}`);
    const noServer = await run({ mode: 'issue', task: 'i' }, { 'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'skip',
      gpu_ids: '0', accuracy_script: `${RUN}/scripts/accuracy.sh` }) });
    check(noServer.result.status === 'blocked' && noServer.result.stopped_at === 'Setup'
      && /no server script/.test(JSON.stringify(noServer.result.phases)),
      `and the server script G2 serves it with: ${noServer.result.status} at ${noServer.result.stopped_at}`);
    for (const mode of ['issue', 'ci']) {
      const ve = (await run({ mode, task: 't' })).calls.find((c) => c.key === 'verify_engineer:verify');
      check(ve && ve.ins.model_path === '/models/m' && ve.ins.accuracy_script === `${RUN}/scripts/accuracy.sh`
        && ve.ins.server_script === `${RUN}/scripts/server.sh`,
        `${mode}: the model, the server script and the accuracy script reach the verify engineer, who measures the fix`);
    }
  }],
  ['stop_after: no fix of an issue or a ci task starts without a model, also when the stop before it was on a phase the route skips or the control file lifted it', async () => {
    const existing = { 'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'push_existing_pr', existing_prs: ['#40872'],
      triage_path: `${RUN}/t.md` }) };
    const skipped = await run({ mode: 'issue', task: 'i', model_path: '', stop_after: 'Diagnose' }, existing);
    check(skipped.result.status === 'stopped' && skipped.result.stopped_at === 'Reproduce' && !skipped.keys.includes('implementer:implement'),
      `an existing fix has no Diagnose: a stop named for it holds before the fix is applied (${skipped.result.status} at ${skipped.result.stopped_at})`);
    const applied = await run({ mode: 'issue', task: 'i', stop_after: 'Verify' }, existing);
    check(applied.result.status === 'stopped' && applied.result.stopped_at === 'Verify' && applied.keys.includes('implementer:implement'),
      `with a model and a later stop, the existing fix is applied as before: ${applied.result.status} at ${applied.result.stopped_at}`);
    const asked = (r) => (((r.result.next || {}).ask || [])[0] || {}).field === 'model_path'
      && 'model_path' in ((((r.result.next || {}).rerun_with || {}).answers) || {});
    let reproduced = false;
    const issue = await run({ mode: 'issue', task: 'i', model_path: '', stop_after: 'Diagnose' }, {
      'issue_triager:reproduce': () => {
        reproduced = true;
        return { reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'segfault 3/3' };
      },
    }, { control: () => (reproduced ? { stop_after: '' } : {}) });
    check(issue.result.status === 'needs_input' && issue.result.stopped_at === 'Diagnose' && asked(issue)
      && !issue.keys.includes('implementer:implement') && issue.keys.includes('director:report'),
      `issue: a stop the control file lifts starts no fix without a model; the run asks for it and reports: ${issue.result.status} at ${issue.result.stopped_at}`);
    let classified = false;
    const ci = await run({ mode: 'ci', task: 'c', model_path: '', stop_after: 'Bisect' }, {
      'ci_guardian:classify': () => {
        classified = true;
        return { failure_class: 'code_regression', failing_test_cmd: 'pytest test_x.py', window: 'a..b', evidence: 'new regression', triage_path: `${RUN}/ci_triage.md` };
      },
    }, { control: () => (classified ? { stop_after: '' } : {}) });
    check(ci.result.status === 'needs_input' && ci.result.stopped_at === 'Bisect' && asked(ci) && !ci.keys.includes('implementer:implement'),
      `ci: the same, after Bisect: ${ci.result.status} at ${ci.result.stopped_at}`);
  }],
  ['arguments: an issue or a ci task that can reach a fix names the client G5 measures it on; one that stops before the fix needs none', async () => {
    const fields = (r) => (r.result.questions || []).map((q) => q.field);
    for (const [mode, task] of [['issue', 'i'], ['ci', 'c']]) {
      const none = await run({ mode, task, client_script: '' });
      const q = (none.result.questions || []).find((x) => x.field === 'client_script') || {};
      check(asks(none, 'client_script') && eqList(fields(none), ['client_script']), `${mode}: without a client it is asked for, and nothing else is: ${fields(none)}`);
      check(/G5 measures the fix on/.test(q.question || '') && /no slower/.test(q.why || '') && !(q.options || []).length
        && !/template/i.test(q.question || ''), `${mode}: the question says what the client is for, and offers no template: ${JSON.stringify(q)}`);
      const both = await run({ mode, task, client_script: '', model_path: '' });
      check(eqList(fields(both).sort(), ['client_script', 'model_path']), `${mode}: a task with neither is asked for the model and the client: ${fields(both)}`);
    }
    check(asks(await run({ mode: 'issue', task: 'i', client_script: '', stop_after: 'Implement' }), 'client_script'),
      'a run that stops after Implement has a fix: asked');
    for (const [mode, stop] of [['issue', 'Triage'], ['issue', 'Reproduce'], ['issue', 'Diagnose'], ['ci', 'Classify'], ['ci', 'Bisect']]) {
      const r = await run({ mode, task: 't', client_script: '', stop_after: stop });
      check(r.result.status === 'stopped' && r.result.stopped_at === stop && !r.keys.includes('intake:define'),
        `${mode} stopping after ${stop} has no fix to measure and starts without a client: ${r.result.status} at ${r.result.stopped_at}`);
    }
    // InferPilot's client, named by the task all the same, runs the task's workload: G5 never judges a fix on lengths
    // and a concurrency of the template's own.
    const tpl = await run({ mode: 'ci', task: 'c', client_script: 'template' });
    check(asks(tpl, 'workload') && eqList(fields(tpl), ['workload']), `the template named without a workload: the workload is asked for: ${fields(tpl)}`);
    const part = await run({ mode: 'issue', task: 'i', client_script: 'template', workload: '{"conc":64}' });
    check(asks(part, 'workload') && /gives no input length and no output length/.test(JSON.stringify(part.result.questions)),
      `a workload that names a part of the load is asked for again: ${JSON.stringify(part.result.questions)}`);
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const load = await run({ mode: 'ci', task: 'c', client_script: 'template', workload: '{"isl":8192,"osl":1024,"conc":64}' }, rendered);
    const pe = load.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(load.result.status === 'ready_for_review' && pe && pe.ins.client_script === `${RUN}/scripts/perf.sh`,
      `with its workload the run starts, and the fix is measured on the rendered client: ${load.result.status} ${pe && pe.ins.client_script}`);
    for (const mode of ['issue', 'ci']) {
      const measured = (await run({ mode, task: 't' })).calls.find((c) => c.key === 'perf_engineer:perf_eval');
      check(measured && measured.ins.client_script === '/s/client.sh' && measured.ins.server_script === `${RUN}/scripts/server.sh`,
        `${mode}: the task's client and the run's server script reach the performance engineer, who measures the fix`);
    }
  }],
  ['stop_after: no fix of an issue or a ci task starts without a client either, nor on a workload InferPilot\'s client has only a part of, when the stop before it was on a phase the route skips or the control file lifted it', async () => {
    const asked = (r, want) => eqList(((r.result.next || {}).ask || []).map((q) => q.field), want)
      && want.every((f) => f in ((((r.result.next || {}).rerun_with || {}).answers) || {}));
    let reproduced = false;
    const issue = await run({ mode: 'issue', task: 'i', client_script: '', stop_after: 'Diagnose' }, {
      'issue_triager:reproduce': () => {
        reproduced = true;
        return { reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'segfault 3/3' };
      },
    }, { control: () => (reproduced ? { stop_after: '' } : {}) });
    check(issue.result.status === 'needs_input' && issue.result.stopped_at === 'Diagnose' && asked(issue, ['client_script'])
      && !issue.keys.includes('implementer:implement') && issue.keys.includes('director:report'),
      `issue: a stop the control file lifts starts no fix without a client; the run asks for it and reports: ${issue.result.status} at ${issue.result.stopped_at}`);
    check(/Give the client, and run the task again/.test(issue.result.next.say) && /G5 on the task's client/.test(issue.result.next.say),
      `and says what to give, and why: ${issue.result.next.say}`);
    let classified = false;
    const ci = await run({ mode: 'ci', task: 'c', client_script: '', model_path: '', stop_after: 'Bisect' }, {
      'ci_guardian:classify': () => {
        classified = true;
        return { failure_class: 'code_regression', failing_test_cmd: 'pytest test_x.py', window: 'a..b', evidence: 'new regression', triage_path: `${RUN}/ci_triage.md` };
      },
    }, { control: () => (classified ? { stop_after: '' } : {}) });
    check(ci.result.status === 'needs_input' && ci.result.stopped_at === 'Bisect' && asked(ci, ['model_path', 'client_script'])
      && !ci.keys.includes('implementer:implement'),
      `ci: with neither, the run asks for the model and the client together: ${JSON.stringify((ci.result.next || {}).ask)}`);
    check(/Give the model and the client, and run the task again/.test(ci.result.next.say), `and says so: ${ci.result.next.say}`);
    const existing = { 'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'push_existing_pr', existing_prs: ['#40872'],
      triage_path: `${RUN}/t.md` }) };
    let triaged = false;
    const pr = await run({ mode: 'issue', task: 'i', client_script: '', stop_after: 'Triage' }, {
      'issue_triager:triage': () => {
        triaged = true;
        return existing['issue_triager:triage']();
      },
    }, { control: () => (triaged ? { stop_after: '' } : {}) });
    check(pr.result.status === 'needs_input' && pr.result.stopped_at === 'Reproduce' && asked(pr, ['client_script'])
      && !pr.keys.includes('implementer:implement'),
      `an existing fix PR is not applied without a client: ${pr.result.status} at ${pr.result.stopped_at}`);
    // A task that stops before its fix is asked nothing about the workload of InferPilot's client, and Setup renders
    // the template with what the task had. Where the control file lifts the stop, the run asks for what the workload
    // leaves out before any code changes: G5 never judges a fix on lengths or a concurrency of the template's own.
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const lifting = (answers) => {
      let shown = false;
      return [{ ...rendered, ...answers, 'issue_triager:reproduce': () => {
        shown = true;
        return { reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'segfault 3/3' };
      } }, { control: () => (shown ? { stop_after: '' } : {}) }];
    };
    const partArgs = { mode: 'issue', task: 'i', client_script: 'template', workload: '{"conc":64}', stop_after: 'Diagnose' };
    const stays = await run(partArgs, rendered);
    check(stays.result.status === 'stopped' && stays.result.stopped_at === 'Diagnose',
      `a run that stops before its fix is asked nothing about the template's workload: ${stays.result.status} at ${stays.result.stopped_at}`);
    const part = await run(partArgs, ...lifting());
    const pq = ((part.result.next || {}).ask || [])[0] || {};
    check(part.result.status === 'needs_input' && part.result.stopped_at === 'Diagnose' && asked(part, ['workload'])
      && !part.keys.includes('implementer:implement') && part.keys.includes('director:report'),
      `the stop lifted, no fix starts on a workload the template has a part of: ${part.result.status} at ${part.result.stopped_at}, asks ${
        JSON.stringify(((part.result.next || {}).ask || []).map((q) => q.field))}`);
    check(/the task gives no input length and no output length for it/.test(pq.why || '') && /lengths, concurrency/.test(pq.question || ''),
      `the question says what the workload leaves out: ${JSON.stringify(pq)}`);
    const again = (part.result.next || {}).rerun_with || {};
    check(again.workload === '' && /Give the whole workload of InferPilot's client, and run the task again/.test(part.result.next.say),
      `the part the task gave is cleared for the next run, so that the answer takes its place: ${JSON.stringify(again)} | ${part.result.next.say}`);
    const whole = await run({ ...partArgs, ...again, answers: { workload: '{"isl":8192,"osl":1024,"conc":64}' } }, ...lifting());
    const pe = whole.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(whole.result.status === 'ready_for_review' && pe && JSON.stringify(pe.ins.workload) === '{"isl":8192,"osl":1024,"conc":64}'
      && pe.ins.client_script === `${RUN}/scripts/perf.sh`,
      `with the answer the fix goes on, and is measured on the whole workload: ${whole.result.status}, ${pe && JSON.stringify(pe.ins.workload)}`);
    const none = await run({ mode: 'issue', task: 'i', client_script: 'template', stop_after: 'Diagnose' }, ...lifting());
    const nq = ((none.result.next || {}).ask || [])[0] || {};
    check(none.result.status === 'needs_input' && asked(none, ['workload']) && /the task names none/.test(nq.why || '')
      && !('workload' in ((none.result.next || {}).rerun_with || {})),
      `a task that names the template and no workload at all is asked the same, with nothing to clear: ${JSON.stringify(none.result.next)}`);
    const inHarness = await run({ ...partArgs, harness: '{"ISL":8192,"OSL":1024}' }, ...lifting());
    check(inHarness.result.status === 'ready_for_review', `lengths given in harness count, as at Intake: ${inHarness.result.status}`);
    let classified2 = false;
    const ciPart = await run({ mode: 'ci', task: 'c', client_script: 'template', workload: '{"isl":8192}', model_path: '', stop_after: 'Bisect' }, {
      ...rendered,
      'ci_guardian:classify': () => {
        classified2 = true;
        return { failure_class: 'code_regression', failing_test_cmd: 'pytest test_x.py', window: 'a..b', evidence: 'new regression', triage_path: `${RUN}/ci_triage.md` };
      },
    }, { control: () => (classified2 ? { stop_after: '' } : {}) });
    check(ciPart.result.status === 'needs_input' && ciPart.result.stopped_at === 'Bisect' && asked(ciPart, ['model_path', 'workload'])
      && /Give the model and the whole workload of InferPilot's client/.test(ciPart.result.next.say),
      `ci: the same, asked together with what else the fix lacks: ${JSON.stringify((ciPart.result.next || {}).ask)} | ${ciPart.result.next.say}`);
  }],
  ['issue: the fix for a model that answers no text questions is measured by the task\'s own output comparison, which a task that names none is asked for after Setup', async () => {
    const noText = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), text_model: false }) };
    const asked = (r) => eqList(((r.result.next || {}).ask || []).map((q) => q.field), ['output_script'])
      && 'output_script' in ((((r.result.next || {}).rerun_with || {}).answers) || {});
    const none = await run({ mode: 'issue', task: 'i' }, noText);
    const q = ((none.result.next || {}).ask || [])[0] || {};
    check(none.result.status === 'needs_input' && none.result.stopped_at === 'Setup' && asked(none)
      && !none.keys.includes('issue_triager:triage') && none.keys.includes('director:report'),
      `Setup says the model answers no text questions and the task names no script: the run asks for it before anything else runs, and reports (${
        none.result.status} at ${none.result.stopped_at}, asks ${JSON.stringify(((none.result.next || {}).ask || []).map((x) => x.field))})`);
    check(/compares the model's outputs/.test(q.question || '') && /PORT/.test(q.question || '') && /OUT_DIR/.test(q.question || '')
      && /BASE_DIR/.test(q.question || '') && /InferPilot has no template for it/.test(q.question || '') && !(q.options || []).length,
      `the question says what the script does, and offers nothing in its place: ${JSON.stringify(q)}`);
    check(/\/models\/m answers no text questions, so gsm8k cannot be put to it/.test(q.why || '') && /the same inputs and seeds on the base and on the fix/.test(q.why || '')
      && /with stop_after set to a phase before the fix, no script is needed/.test(q.why || ''), `and why it is asked: ${q.why}`);
    check(/Give the script that compares the model's outputs, and run the task again/.test(none.result.next.say),
      `the result says what to give: ${none.result.next.say}`);
    const given = await run({ mode: 'issue', task: 'i', ...none.result.next.rerun_with, answers: { output_script: ' /s/outputs.sh ' } }, noText);
    const ve = given.calls.find((c) => c.key === 'verify_engineer:verify');
    check(given.result.status === 'ready_for_review' && ve && ve.ins.output_script === '/s/outputs.sh' && ve.ins.text_model === false,
      `with the answer the run goes to its end, and the verify engineer reads the script and what the model is: ${given.result.status}, ${
        ve && JSON.stringify([ve.ins.output_script, ve.ins.text_model])}`);
    check(given.result.task_def.output_script === ' /s/outputs.sh ', `the definition the run records names it: ${JSON.stringify(given.result.task_def.output_script)}`);
    // A model that answers text questions is measured on gsm8k: nothing is asked, with a script or without.
    const text = await run({ mode: 'issue', task: 'i' });
    const tv = text.calls.find((c) => c.key === 'verify_engineer:verify');
    check(text.result.status === 'ready_for_review' && tv && tv.ins.text_model === true && !tv.ins.output_script,
      `a Setup that does not say counts the model as one that answers text questions, and nothing is asked: ${text.result.status}, ${tv && tv.ins.text_model}`);
    const says = await run({ mode: 'issue', task: 'i' }, { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), text_model: true }) });
    check(says.result.status === 'ready_for_review', `nor of a model Setup says answers them: ${says.result.status}`);
    // A run that stops before its fix needs no script. Where the control file lifts the stop, the run asks before any code changes.
    const early = await run({ mode: 'issue', task: 'i', stop_after: 'Diagnose' }, noText);
    check(early.result.status === 'stopped' && early.result.stopped_at === 'Diagnose',
      `a run that stops before its fix is not asked: ${early.result.status} at ${early.result.stopped_at}`);
    const atSetup = await run({ mode: 'issue', task: 'i', stop_after: 'Setup' }, noText);
    check(atSetup.result.status === 'stopped' && atSetup.result.stopped_at === 'Setup', `nor one that stops after Setup: ${atSetup.result.status}`);
    let reproduced = false;
    const lifted = await run({ mode: 'issue', task: 'i', stop_after: 'Diagnose' }, {
      ...noText,
      'issue_triager:reproduce': () => {
        reproduced = true;
        return { reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'black images 3/3' };
      },
    }, { control: () => (reproduced ? { stop_after: '' } : {}) });
    check(lifted.result.status === 'needs_input' && lifted.result.stopped_at === 'Diagnose' && asked(lifted)
      && !lifted.keys.includes('implementer:implement') && lifted.keys.includes('director:report'),
      `a stop the control file lifts starts no fix without the script: ${lifted.result.status} at ${lifted.result.stopped_at}`);
    check(/G2 on the task's own output comparison, since the model answers no text questions/.test(lifted.result.next.say)
      && /Give the script that compares the model's outputs, and run the task again/.test(lifted.result.next.say),
      `and says what the fix lacks, and why: ${lifted.result.next.say}`);
    // Setup took the model for one that answers text questions, and the round finds that it does not: no gsm8k ran, and
    // there is no script to compare outputs with. The fix has no G2, and the round passes nothing.
    const found = (changed) => ({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), text_model: false, server_code_changed: changed,
      gates: { issue_repro: 'pass', G2: 'not run: the model answers no text questions, and the task names no output_script' } }) });
    const late = await run({ mode: 'issue', task: 'i' }, found(true));
    const why = (late.result.phases.filter((p) => p.phase === 'Verify').pop() || {}).summary || '';
    check(late.result.status === 'blocked' && late.result.stopped_at === 'Verify' && !late.keys.includes('perf_engineer:perf_eval')
      && !late.keys.some((k) => k.startsWith('pr_engineer')),
      `a round that finds such a model without the script does not pass, whatever it reports: ${late.result.status} at ${late.result.stopped_at}`);
    check(/the model answers no text questions, and the task names no output_script/.test(why) && /Name the script \(output_script\), and run the task again/.test(why)
      && /Name the script \(output_script\)/.test(late.result.next.say), `and the result says what to do: ${why} | ${late.result.next.say}`);
    // With the script, G2 is the comparison it makes, and the round is accepted only on its result: a pass, or the
    // outputs kept where the run has no baseline. A G2 the round skipped or left out passes nothing, script or not.
    const compared = (gates, more = {}) => ({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), text_model: false,
      server_code_changed: true, gates: { issue_repro: 'pass', ...gates }, ...more }) });
    const scripted = { mode: 'issue', task: 'i', output_script: '/s/outputs.sh' };
    for (const g2 of ["pass: 16 images of 5d6e7f8 in RUN_DIR/outputs/fix-5d6e7f8 match the base's (output_script: PSNR >= 35 dB on each)",
      "16 images of 5d6e7f8 written to RUN_DIR/outputs/fix-5d6e7f8, no baseline: the base's are the black images the issue reports"]) {
      const withScript = await run(scripted, compared({ G2: g2 }));
      check(withScript.result.status === 'ready_for_review', `with the task's script and its result (${g2.slice(0, 24)}...) the round is accepted: ${
        withScript.result.status}`);
    }
    for (const [said, gates, more] of [
      ['says it did not run the comparison', { G2: 'not run: the fix changes one line' }, {}],
      ['leaves G2 out', {}, {}],
      ['reports a comparison that failed without blocking', { G2: 'fail: 3 of 16 images differ beyond the tolerance (KI-002)' },
        { all_pass: false, failures: [{ gate: 'G2', blocking: false, summary: 'known issue KI-002' }] }],
      // "no baseline" is the documented result and nothing looser: the outputs that were kept, and where.
      ['says it did not run for want of a baseline', { G2: 'not run: no baseline available' }, {}],
      ['reports a failure that mentions no baseline', { G2: 'fail: 3 of 16 images differ from the last good commit; no baseline on the base' },
        { all_pass: false, failures: [{ gate: 'G2', blocking: false, summary: 'no baseline on the base' }] }],
      ['says there is no baseline without the outputs it kept', { G2: 'no baseline: the base does not start on this configuration' }, {}],
      ['says it skipped the comparison, with outputs somewhere', { G2: 'skipped, no baseline; an earlier round wrote RUN_DIR/outputs/fix-5d6e7f8' }, {}],
      ['says it did not run, and names a directory', { G2: 'not run: no baseline; RUN_DIR/outputs/fix-5d6e7f8 holds an earlier round\'s outputs' }, {}],
      ['says the comparison failed, with no baseline and a directory', { G2: 'fail: no baseline, outputs in RUN_DIR/outputs/fix-5d6e7f8' }, {}],
      // A comparison among the round's failures is not excused by a known issue, whatever the gate says.
      ['calls the comparison a pass and lists it among its failures', { G2: "pass: 13 of 16 images match the base's; 3 differ, as KI-002 says" },
        { all_pass: false, failures: [{ gate: 'G2', blocking: false, summary: 'known issue KI-002' }] }],
    ]) {
      const skipped = await run(scripted, compared(gates, more));
      const said2 = (skipped.result.phases.filter((p) => p.phase === 'Verify').pop() || {}).summary || '';
      check(skipped.result.status === 'blocked' && skipped.result.stopped_at === 'Verify' && !skipped.keys.includes('perf_engineer:perf_eval')
        && !skipped.keys.some((k) => k.startsWith('pr_engineer')),
        `with the script, a round that ${said} is not accepted: ${skipped.result.status} at ${skipped.result.stopped_at}`);
      check(/accepted without the result of its output comparison: G2 is ("|not reported)/.test(said2)
        && /G2 of such a fix is the task's output_script, run on the base and on the fix/.test(said2), `and the run record says why: ${said2}`);
    }
    // A difference the comparison reports is never the baseline's to keep: where the investigator finds the same on
    // the base, the script cannot tell the fix from noise. No known issue is recorded, which would excuse every
    // later miss, and the run stops for a human after that one round.
    const noisy = await run(scripted, { ...compared({ G2: 'fail: 3 of 16 images differ beyond the tolerance' },
      { all_pass: false, failures: [{ gate: 'G2', blocking: true, evidence: 'PSNR 31 dB on 3 of 16, the script asks 35' }] }),
    'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-7', title: 'the base differs from itself' },
      summary: 'two runs of the base differ as much' }) });
    const noisyWhy = (noisy.result.phases.filter((p) => p.phase === 'Investigate').pop() || {}).summary || '';
    check(noisy.result.status === 'blocked' && noisy.result.stopped_at === 'Verify' && !(noisy.result.known_issues || []).length
      && noisy.calls.filter((c) => c.key === 'verify_engineer:verify').length === 1 && !noisy.keys.some((k) => k.startsWith('pr_engineer'))
      && /no known issue excuses a difference the comparison reports/.test(noisyWhy) && /a human decides/.test(noisyWhy),
      `a comparison that differs on the base as well stops the run for a human, with no known issue: ${noisy.result.status} at ${
        noisy.result.stopped_at}, ${JSON.stringify(noisy.result.known_issues)} | ${noisyWhy}`);
    // gsm8k on a model that answers text questions is judged as before: a failure the baseline shares is a known issue.
    const shared = await run({ mode: 'issue', task: 'i' }, {
      'verify_engineer:verify': (ins, n) => (n === 1 ? { ...DEFAULTS['verify_engineer:verify'](), all_pass: false,
        failures: [{ gate: 'G2', blocking: true, evidence: 'gsm8k 0.84 on both arms' }] } : DEFAULTS['verify_engineer:verify']()),
      'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-8', title: 'gsm8k is low on the base too' }, summary: 'on the base too' }) });
    check(shared.result.status === 'ready_for_review' && (shared.result.known_issues || []).some((k) => k.id === 'KI-8'),
      `a text model's G2 that the baseline shares is a known issue, as before: ${shared.result.status}`);
    // The verify engineer has the server, and the last word on what the model is, either way. A model Setup took
    // for one that answers no text questions and that answers them after all is measured on gsm8k: the later
    // roles read it so, and the guard for the comparison does not apply. A round that does not say changes nothing.
    const afterAll = await run(scripted, { ...noText, 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), text_model: true,
      server_code_changed: true, gates: { issue_repro: 'pass', G2: 'pass 0.893 vs 0.895 (1319 q); the model answers text questions, output_script not run' } }) });
    const seen = (r, key) => (r.calls.find((c) => c.key === key) || { ins: {} }).ins.text_model;
    check(afterAll.result.status === 'ready_for_review' && seen(afterAll, 'verify_engineer:verify') === false
      && seen(afterAll, 'perf_engineer:perf_eval') === true && seen(afterAll, 'reviewer:review') === true,
      `a model that answers text questions after all is one for the roles after Verify: ${afterAll.result.status}, ${
        JSON.stringify([seen(afterAll, 'verify_engineer:verify'), seen(afterAll, 'perf_engineer:perf_eval'), seen(afterAll, 'reviewer:review')])}`);
    const silent = await run(scripted, { ...noText, 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: true,
      gates: { issue_repro: 'pass', G2: "pass: 16 images match the base's" } }) });
    check(silent.result.status === 'ready_for_review' && seen(silent, 'perf_engineer:perf_eval') === false && seen(silent, 'reviewer:review') === false,
      `a round that does not say leaves what Setup said: ${silent.result.status}, ${JSON.stringify([seen(silent, 'perf_engineer:perf_eval'), seen(silent, 'reviewer:review')])}`);
    const silentSkip = await run(scripted, { ...noText, 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: true,
      gates: { issue_repro: 'pass', G2: 'not run' } }) });
    check(silentSkip.result.status === 'blocked' && silentSkip.result.stopped_at === 'Verify',
      `and Setup's word is enough for the guard: a skipped comparison is not accepted (${silentSkip.result.status} at ${silentSkip.result.stopped_at})`);
    const testsOnly = await run({ mode: 'issue', task: 'i' }, found(false));
    check(testsOnly.result.status === 'ready_for_review' && !testsOnly.keys.includes('perf_engineer:perf_eval'),
      `a fix to tests, documentation or CI files alone has no G2, and needs no script: ${testsOnly.result.status}`);
  }],
  ['ci: the fix for a model that answers no text questions is asked for its output comparison as an issue\'s is', async () => {
    const noText = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), text_model: false }) };
    const none = await run({ mode: 'ci', task: 'c' }, noText);
    check(none.result.status === 'needs_input' && none.result.stopped_at === 'Setup'
      && eqList(((none.result.next || {}).ask || []).map((q) => q.field), ['output_script']) && !none.keys.includes('ci_guardian:classify'),
      `asked after Setup, before the failure is classified: ${none.result.status} at ${none.result.stopped_at}`);
    const given = await run({ mode: 'ci', task: 'c', output_script: '/s/outputs.sh' }, noText);
    const ve = given.calls.find((c) => c.key === 'verify_engineer:verify');
    check(given.result.status === 'ready_for_review' && ve && ve.ins.output_script === '/s/outputs.sh' && ve.ins.text_model === false,
      `with the script the fix is verified on it: ${given.result.status}, ${ve && ve.ins.output_script}`);
    for (const stop of ['Classify', 'Bisect']) {
      const r = await run({ mode: 'ci', task: 'c', stop_after: stop }, noText);
      check(r.result.status === 'stopped' && r.result.stopped_at === stop, `a run that stops after ${stop} is not asked: ${r.result.status} at ${r.result.stopped_at}`);
    }
    let classified = false;
    const lifted = await run({ mode: 'ci', task: 'c', stop_after: 'Bisect' }, {
      ...noText,
      'ci_guardian:classify': () => {
        classified = true;
        return { failure_class: 'code_regression', failing_test_cmd: 'pytest test_x.py', window: 'a..b', evidence: 'new regression', triage_path: `${RUN}/ci_triage.md` };
      },
    }, { control: () => (classified ? { stop_after: '' } : {}) });
    check(lifted.result.status === 'needs_input' && lifted.result.stopped_at === 'Bisect'
      && eqList(((lifted.result.next || {}).ask || []).map((q) => q.field), ['output_script']) && !lifted.keys.includes('implementer:implement'),
      `a stop the control file lifts starts no fix without it: ${lifted.result.status} at ${lifted.result.stopped_at}`);
  }],
  ['issue: a fix that changes code the server runs is measured for speed, and one to tests, documentation or CI files alone is not', async () => {
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    const verified = (changed) => ({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: changed }) });
    const measured = (out) => ({ 'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: true, guards_ok: true,
      workload_unchanged: true, path_ok: true, results: [], ...out }) });
    const perfNote = (r) => r.result.phases.filter((p) => p.phase === 'Performance');
    for (const [said, answers] of [['says nothing of its diff', {}], ['changes code the server runs', verified(true)]]) {
      const r = await run({ mode: 'issue', task: 'i' }, { ...answers, ...measured({ summary: '+0.4% output tok/s, within noise' }) });
      const pe = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
      const rv = r.calls.find((c) => c.key === 'reviewer:review');
      check(r.result.status === 'ready_for_review' && pe && NO_SLOWER.test(pe.ins.claim),
        `a fix that ${said} is measured, and the claim is that it is no slower: ${r.result.status}, ${pe && pe.ins.claim}`);
      check(rv && NO_SLOWER.test(rv.ins.claim) && perfNote(r).some((p) => p.verdict === 'pass' && p.summary.includes('within noise')),
        `the reviewer reads the same claim, and the run record has the measurement: ${rv && rv.ins.claim}`);
    }
    const tests = await run({ mode: 'issue', task: 'i' }, verified(false));
    const rv = tests.calls.find((c) => c.key === 'reviewer:review');
    check(tests.result.status === 'ready_for_review' && !tests.keys.includes('perf_engineer:perf_eval') && !tests.phases.includes('Performance'),
      `a fix to tests, documentation or CI files alone is not measured: ${tests.result.status}, ${tests.phases}`);
    check(eqList(perfNote(tests).map((p) => `${p.verdict}: ${p.summary}`), ['not_run: not run: no code the server runs changed']),
      `the run record says that G5 was not run, and why, once: ${JSON.stringify(perfNote(tests))}`);
    check(eqList(tests.route, tests.phases) && rv && !rv.ins.claim && rv.ins.server_code_changed === false,
      `the route is logged again without Performance, and the reviewer reads no claim: ${tests.route} | ${rv && JSON.stringify(rv.ins.claim)}`);
    const slower = await run({ mode: 'issue', task: 'i' }, measured({ claim_reproduced: false, summary: '-6% output tok/s (median of 3)' }));
    check(slower.result.status === 'blocked' && slower.result.stopped_at === 'Performance' && !slower.keys.includes('implementer:rollback')
      && !slower.keys.includes('reviewer:review'),
      `a fix that is slower than the base stops at Performance, for a human, and nothing is rolled back: ${slower.result.status} at ${slower.result.stopped_at}`);
    // The base cannot serve the configuration (the crash being fixed), and the run knows no commit that did: the
    // fix's numbers have nothing to be judged against, and are reported.
    const alone = await run({ mode: 'issue', task: 'i' }, measured({ claim_reproduced: false, no_baseline: 'the base does not start on this configuration',
      summary: '1820 output tok/s (median of 3)' }));
    check(alone.result.status === 'ready_for_review'
      && perfNote(alone).some((p) => p.verdict === 'pass' && p.summary.startsWith('reported, not judged: no baseline (the base does not start')),
      `a fix with no baseline is reported, not judged: ${alone.result.status}, ${JSON.stringify(perfNote(alone))}`);
    const perfIssue = { 'issue_triager:triage': () => ({ priority: 'P1', category: 'performance', action: 'reproduce', triage_path: `${RUN}/t.md` }),
      'issue_triager:reproduce': () => ({ reproduced: true, repro_cmd: 'bash bench.sh', perf_claim: 'decode tok/s back to 2400 at c32 (v0.5.21)',
        evidence: '1710 tok/s 3/3' }) };
    const number = await run({ mode: 'issue', task: 'i' }, { ...perfIssue, ...verified(false) });
    const ne = number.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(number.result.status === 'ready_for_review' && ne && ne.ins.claim === 'decode tok/s back to 2400 at c32 (v0.5.21)',
      `a performance issue is measured whatever its fix changes, on the number it must restore: ${number.result.status}, ${ne && ne.ins.claim}`);
    const unrestored = await run({ mode: 'issue', task: 'i' }, { ...perfIssue,
      ...measured({ claim_reproduced: false, no_baseline: 'the base does not start', summary: '1750 tok/s' }) });
    check(unrestored.result.status === 'blocked' && unrestored.result.stopped_at === 'Performance',
      `a number that must come back is its own measure: no baseline excuses it (${unrestored.result.status} at ${unrestored.result.stopped_at})`);
    // A stop named for Performance still ends a run whose fix is not measured.
    const stopped = await run({ mode: 'issue', task: 'i', stop_after: 'Performance' }, verified(false));
    check(stopped.result.status === 'stopped' && stopped.result.stopped_at === 'Verify' && !stopped.keys.includes('reviewer:review')
      && stopped.result.phases.some((p) => p.phase === 'Verify' && p.verdict === 'stopped' && /stop_after=Performance: G5 is not run/.test(p.summary)),
      `stop_after=Performance ends an unmeasured fix after Verify, and says why: ${stopped.result.status} at ${stopped.result.stopped_at}`);
    const stoppedMeasured = await run({ mode: 'issue', task: 'i', stop_after: 'Performance' });
    check(stoppedMeasured.result.status === 'stopped' && stoppedMeasured.result.stopped_at === 'Performance'
      && stoppedMeasured.keys.includes('perf_engineer:perf_eval'), `a measured one stops after Performance: ${stoppedMeasured.result.stopped_at}`);
    // What the review asks for can add code the server runs to a fix that had none: G5 runs once a Verify round
    // no longer says that the fix changes none, also when that round says nothing of the diff.
    const grown = await run({ mode: 'issue', task: 'i' }, {
      'verify_engineer:verify': (ins, n) => ({ ...DEFAULTS['verify_engineer:verify'](), ...(n > 1 ? {} : { server_code_changed: false }) }),
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', issue: 'the guard belongs in the scheduler' }], work_packages: [WP('R1')],
          review_path: `${RUN}/review.md` }
        : DEFAULTS['reviewer:review']()),
    });
    const claims = grown.calls.filter((c) => c.key === 'reviewer:review').map((c) => c.ins.claim);
    check(grown.result.status === 'ready_for_review'
      && eqList(grown.phases.slice(grown.phases.indexOf('Verify')), ['Verify', 'Review', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report']),
      `a fix that comes to change server code after a review is measured then: ${grown.phases}`);
    check(!claims[0] && NO_SLOWER.test(claims[1]) && grown.route.includes('Performance') && grown.route.length === 11,
      `the second review reads the claim, and the route has Performance again: ${JSON.stringify(claims)} | ${grown.route}`);
  }],
  ['ci: a fix is measured as an issue\'s is, and a failure on a number the test measures always is, on that number', async () => {
    const NO_SLOWER = /^no gain is claimed: no slower than the baseline beyond the noise band, on the run's client$/;
    const verified = (changed) => ({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), server_code_changed: changed }) });
    const classified = (out) => ({ 'ci_guardian:classify': () => ({ ...DEFAULTS['ci_guardian:classify'](), ...out }) });
    const fix = await run({ mode: 'ci', task: 'c' });
    const pe = fix.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(fix.result.status === 'ready_for_review' && pe && NO_SLOWER.test(pe.ins.claim),
      `a ci fix is measured, and the claim is that it is no slower: ${fix.result.status}, ${pe && pe.ins.claim}`);
    const testBug = await run({ mode: 'ci', task: 'c' }, { ...classified({ failure_class: 'test_bug' }), ...verified(false) });
    check(testBug.result.status === 'ready_for_review' && !testBug.keys.includes('perf_engineer:perf_eval')
      && testBug.result.phases.some((p) => p.phase === 'Performance' && p.verdict === 'not_run')
      && eqList(testBug.phases, ['Setup', 'Classify', 'Bisect', 'Implement', 'Verify', 'Review', 'PR', 'Report']) && eqList(testBug.route, testBug.phases),
      `a fix to a test alone is not measured, and the run says so: ${testBug.phases}`);
    const claim = 'output throughput back above 1100 tok/s in test_bench_serving default (1180 in the last green run)';
    const perf = { performance: true, perf_claim: claim };
    for (const [what, answers] of [['code the server runs', verified(true)], ['only the test', verified(false)]]) {
      const r = await run({ mode: 'ci', task: 'c' }, { ...classified(perf), ...answers });
      const e = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
      const rv = r.calls.find((c) => c.key === 'reviewer:review');
      check(r.result.status === 'ready_for_review' && e && e.ins.claim === claim && rv && rv.ins.claim === claim,
        `a performance failure whose fix changes ${what} is measured, on the number it must restore: ${r.result.status}, ${e && e.ins.claim}`);
      check(r.result.phases.some((p) => p.phase === 'Classify' && p.summary.startsWith('performance: ')), 'and the run record names it a performance failure');
    }
    // A threshold moved in the test restores nothing: the number is measured, and the run stops for a human.
    const moved = await run({ mode: 'ci', task: 'c' }, { ...classified({ ...perf, failure_class: 'test_bug' }), ...verified(false),
      'perf_engineer:perf_eval': () => ({ claim_reproduced: false, held_out_ok: true, guards_ok: true, workload_unchanged: true, path_ok: true,
        summary: '1012 tok/s on the fix, 1009 on the base, 1180 at the culprit\'s parent' }) });
    check(moved.result.status === 'blocked' && moved.result.stopped_at === 'Performance' && !moved.keys.includes('implementer:rollback'),
      `a fix that leaves the number where it fell stops at Performance, and nothing is rolled back: ${moved.result.status} at ${moved.result.stopped_at}`);
    const noNumber = await run({ mode: 'ci', task: 'c' }, classified({ performance: true }));
    check(noNumber.result.status === 'blocked' && noNumber.result.stopped_at === 'Classify' && !noNumber.keys.includes('ci_guardian:bisect')
      && /without a perf_claim/.test(JSON.stringify(noNumber.result.phases)),
      `a performance failure without the number to restore stops before the bisect: ${noNumber.result.status} at ${noNumber.result.stopped_at}`);
    const flaky = await run({ mode: 'ci', task: 'c' }, classified({ performance: true, failure_class: 'flaky', failing_test_cmd: '' }));
    check(flaky.result.status === 'handed_off', `a flaky one is handed over as any other, with no number asked of it: ${flaky.result.status}`);
  }],
  ['ci: a failure on a score the test measures is an accuracy problem: its score is measured whatever the fix changes, and no round is accepted without it', async () => {
    const claim = 'GSM8K back to 0.93 on GLM-5.3-Flash in test_gsm8k (0.94 in the last green run)';
    const classified = (out) => ({ 'ci_guardian:classify': () => ({ ...DEFAULTS['ci_guardian:classify'](), ...out }) });
    const verified = (out) => ({ 'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), ...out }) });
    const accuracy = classified({ accuracy: true, accuracy_claim: claim });
    const ended = (r) => `${r.result.status} at ${r.result.stopped_at}`;
    const restored = { accuracy_claim: "pass 0.936, the claim is 0.93 (the test's GSM8K, 200 q)" };
    const unmeasured = { gates: { G2: 'not run: no code the server runs changed' }, server_code_changed: false };
    const stopsAtVerify = (r) => r.result.status === 'blocked' && r.result.stopped_at === 'Verify' && !r.keys.includes('reviewer:review');
    const ok = await run({ mode: 'ci', task: 'c' }, { ...accuracy, ...verified({ gates: restored }) });
    const ve = ok.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ok.result.status === 'ready_for_review' && ve && ve.ins.accuracy_claim === claim,
      `the score the fix must restore reaches the verify engineer: ${ended(ok)}, ${ve && ve.ins.accuracy_claim}`);
    check(ok.result.phases.some((p) => p.phase === 'Classify' && p.summary.startsWith('accuracy: ')), 'and the run record names it an accuracy failure');
    // A threshold moved in the test: the failing test passes, the fix changes a test alone, and nothing measured the score.
    const moved = await run({ mode: 'ci', task: 'c' }, { ...classified({ accuracy: true, accuracy_claim: claim, failure_class: 'test_bug' }),
      ...verified(unmeasured) });
    check(stopsAtVerify(moved) && !moved.keys.includes('investigator:investigate') && /without the score coming back/.test(JSON.stringify(moved.result.phases)),
      `a round that passes without the score is not accepted, and nothing goes on to review: ${ended(moved)}`);
    const silent = await run({ mode: 'ci', task: 'c' }, { ...accuracy, ...verified({ gates: {} }) });
    check(stopsAtVerify(silent), `nor one that says nothing of it: ${ended(silent)}`);
    // gsm8k against the base is G2's, and is not the score of the claim: an MMMU claim is not met by it.
    const gsm8k = await run({ mode: 'ci', task: 'c' }, { ...accuracy, ...verified({ gates: { G2: 'pass 0.893 vs 0.895 (1319 q)' } }) });
    check(stopsAtVerify(gsm8k) && /accuracy_claim is not reported/.test(JSON.stringify(gsm8k.result.phases)),
      `gsm8k against the base does not stand in for the score to restore: ${ended(gsm8k)}`);
    // Only "pass" is a pass: no other word the role may write.
    for (const word of ['fail 0.871, the claim is 0.93', 'failed: 0.871, the claim is 0.93', 'skipped: the test took too long', 'not run: no code changed']) {
      const said = await run({ mode: 'ci', task: 'c' }, { ...accuracy, ...verified({ gates: { accuracy_claim: word }, all_pass: true, failures: [] }) });
      check(stopsAtVerify(said), `a round that calls itself passed with accuracy_claim "${word}" is not accepted: ${ended(said)}`);
    }
    // The other way a round is accepted: failures that do not block. It is held to the score as well.
    const reportOnly = [{ gate: 'G3', blocking: false, evidence: 'a report-only item missed' }];
    const lenient = await run({ mode: 'ci', task: 'c' }, { ...classified({ accuracy: true, accuracy_claim: claim, failure_class: 'test_bug' }),
      ...verified({ ...unmeasured, all_pass: false, failures: reportOnly }) });
    check(stopsAtVerify(lenient), `a round whose only failures do not block is not accepted without the score either: ${ended(lenient)}`);
    const measured = await run({ mode: 'ci', task: 'c' }, { ...accuracy, ...verified({ gates: restored, all_pass: false, failures: reportOnly }) });
    check(measured.result.status === 'ready_for_review', `with the score measured, a report-only miss stops nothing: ${ended(measured)}`);
    // A score below the claim is never excused as a known issue: the base scores low too, and that is the failure being fixed.
    const below = { gates: { accuracy_claim: 'fail 0.871, the claim is 0.93' }, all_pass: false };
    const excused = await run({ mode: 'ci', task: 'c' }, { ...accuracy,
      ...verified({ ...below, failures: [{ gate: 'accuracy_claim', blocking: false, evidence: 'the base scores 0.87 as well: known issue' }] }) });
    check(stopsAtVerify(excused), `a score that failed without blocking is not accepted: ${ended(excused)}`);
    const baseToo = () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-9', title: 'the base fails the same way' }, summary: 'the base fails the same way' });
    for (const gate of ['accuracy_claim', 'failing_test']) {
      // failing_test runs first: where the fix restores nothing, the round can end on it before the score is measured.
      const onBase = await run({ mode: 'ci', task: 'c' }, { ...accuracy,
        ...verified({ ...below, failures: [{ gate, blocking: true, evidence: '0.871 on the fix' }] }), 'investigator:investigate': baseToo });
      check(onBase.result.status === 'blocked' && onBase.keys.filter((k) => k === 'verify_engineer:verify').length === 1
        && !(onBase.result.known_issues || []).some((k) => k.id === 'KI-9') && !onBase.keys.includes('issue_triager:spinoff')
        && /not a known issue/.test(JSON.stringify(onBase.result.phases)),
        `${gate} failing as on the base is the failure itself: no known issue, no issue drafted for it, and the run stops for a human: ${ended(onBase)}`);
    }
    // A fix that restores too little gets its repair round: the investigator says what it misses, and only where it
    // cannot (or calls the failure the baseline's) does the run stop for a human.
    const repair = await run({ mode: 'ci', task: 'c' }, { ...accuracy,
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { ...DEFAULTS['verify_engineer:verify'](), ...below, failures: [{ gate: 'accuracy_claim', blocking: true, evidence: '0.871 on the fix' }] }
        : { ...DEFAULTS['verify_engineer:verify'](), gates: restored }),
      'investigator:investigate': () => ({ verdict: 'fix_needed', work_packages: [WP('FIX1')], summary: 'the fix misses the second code path' }) });
    check(repair.result.status === 'ready_for_review' && repair.keys.filter((k) => k === 'verify_engineer:verify').length === 2
      && repair.keys.filter((k) => k === 'implementer:implement').length === 2,
      `an incomplete fix is repaired and measured again: ${ended(repair)}`);
    const stuck = await run({ mode: 'ci', task: 'c' }, { ...accuracy,
      ...verified({ ...below, failures: [{ gate: 'accuracy_claim', blocking: true, evidence: '0.871 on the fix' }] }),
      'investigator:investigate': () => ({ verdict: 'inconclusive', summary: 'the threshold may be one no commit ever met' }) });
    check(stuck.result.status === 'blocked' && stuck.result.stopped_at === 'Verify' && !(stuck.result.known_issues || []).length,
      `where the investigator cannot say what the fix misses, the run stops for a human: ${ended(stuck)}`);
    // Any other gate that fails on the base as well is a known issue, as before: G2's gsm8k among them.
    for (const gate of ['G0', 'G2']) {
      const other = await run({ mode: 'ci', task: 'c' }, { ...accuracy,
        'verify_engineer:verify': (ins, n) => (n === 1
          ? { ...DEFAULTS['verify_engineer:verify'](), gates: restored, all_pass: false,
            failures: [{ gate, blocking: true, evidence: 'fails on the base too' }] }
          : { ...DEFAULTS['verify_engineer:verify'](), gates: restored }),
        'investigator:investigate': () => ({ verdict: 'baseline_bug', known_issue: { id: 'KI-8', title: 'on the base too' }, summary: 'on the base too' }) });
      check(other.result.status === 'ready_for_review' && (other.result.known_issues || []).some((k) => k.id === 'KI-8'),
        `${gate} failing on the base is still a known issue, and is not taken for the claim: ${ended(other)}`);
    }
    const plain = await run({ mode: 'ci', task: 'c' }, { ...classified({ failure_class: 'test_bug' }), ...verified(unmeasured) });
    const pv = plain.calls.find((c) => c.key === 'verify_engineer:verify');
    check(plain.result.status === 'ready_for_review' && pv && !pv.ins.accuracy_claim,
      `any other fix to a test alone goes without G2 as before: ${ended(plain)}`);
    const noScore = await run({ mode: 'ci', task: 'c' }, classified({ accuracy: true }));
    check(noScore.result.status === 'blocked' && noScore.result.stopped_at === 'Classify' && !noScore.keys.includes('ci_guardian:bisect')
      && /without an accuracy_claim/.test(JSON.stringify(noScore.result.phases)),
      `an accuracy failure without the score to restore stops before the bisect: ${ended(noScore)}`);
    const flaky = await run({ mode: 'ci', task: 'c' }, classified({ accuracy: true, failure_class: 'flaky', failing_test_cmd: '' }));
    check(flaky.result.status === 'handed_off', `a flaky one is handed over as any other, with no score asked of it: ${ended(flaky)}`);
    const both = await run({ mode: 'ci', task: 'c' }, { ...classified({ accuracy: true, accuracy_claim: claim, performance: true,
      perf_claim: 'output throughput back above 1100 tok/s' }), ...verified({ gates: restored }) });
    const pe = both.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(both.result.status === 'ready_for_review' && pe && pe.ins.claim === 'output throughput back above 1100 tok/s'
      && both.result.phases.some((p) => p.phase === 'Classify' && p.summary.startsWith('performance: accuracy: ')),
      `a test that measures a speed and a score has both to restore: ${ended(both)}`);
  }],
  ['implement: done without a commit is a failed round', async () => {
    const r = await run({ mode: 'feature', task: 't', max_fix_rounds: 1 }, {
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: '', unit_test_cmd: 'pytest -q t.py' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement', `status ${r.result.status}`);
    check(!r.keys.includes('test_engineer:unit'), 'nothing to test without a commit');
    const imp = r.result.phases.find((p) => p.phase === 'Implement');
    check(imp && imp.summary.includes('without a commit'), `reason recorded: ${imp && imp.summary}`);
  }],
  ['rollback: an unclean rollback goes on the attention list of the report', async () => {
    const r = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', failure_summary: 'wrong' }),
      'implementer:rollback': () => ({ wp: 'WP1', reset_to: 'base', clean: false }),
    });
    check(r.result.status === 'blocked' && r.result.attention.length === 1 && r.result.attention[0].wp === 'WP1',
      `attention ${JSON.stringify(r.result.attention)}`);
    const rep = r.calls.find((c) => c.key === 'director:report');
    check(rep && Array.isArray(rep.ins.attention) && rep.ins.attention.length === 1, 'the report sees what a human must clean up');
    const clean = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', failure_summary: 'wrong' }),
    });
    check(clean.result.attention.length === 0, 'a clean rollback needs no attention');
  }],
  ['issue: reproduce, diagnose, fix, verify with the reproduction as a gate', async () => {
    const r = await run({ mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Triage', 'Reproduce', 'Diagnose', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report']),
      `phases ${r.phases}`);
    check(r.heads[9].startsWith('=== PHASE 10/11 [##########-]: Reply ===') && r.heads[10].startsWith('=== PHASE 11/11 [###########]: Report ==='),
      `the issue route numbers Reply and Report: ${r.heads[9]} | ${r.heads[10]}`);
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && JSON.stringify(v.ins.extra_gates).includes('issue_repro'), 'issue_repro gate passed to verify');
    check(r.result.priority === 'P0', 'priority recorded');
  }],
  ['issue: a reproduction has a long call\'s limit; one whose arms would not fit says what it needs and runs again with it, once', async () => {
    const left = { reproduced: false, repro_cmd: 'bash repro.sh', evidence: 'arm 1 of 3 clean', needs_min: 500 };
    const r = await run({ mode: 'issue', task: 't' }, {
      'issue_triager:reproduce': (ins, n) => (n === 1 ? left : { reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'arm 3 failed' }),
    });
    const limits = r.calls.filter((c) => c.key === 'issue_triager:reproduce').map((c) => c.ins.time_limit_min);
    check(eqList(limits, [360, 500]), `the long default, then the limit asked for: ${limits}`);
    check(r.result.phases.some((p) => p.phase === 'Reproduce' && p.verdict === 'more_time'
      && /the reproduction needs 500 min and had 360: run again with a 500-min limit/.test(p.summary)), 'the extension is in the report');
    check(r.keys.includes('investigator:diagnose') && r.result.status === 'ready_for_review', `goes on once reproduced: ${r.result.status}`);
    const short = await run({ mode: 'issue', task: 't' }, { 'issue_triager:reproduce': () => left });
    check(short.keys.filter((k) => k === 'issue_triager:reproduce').length === 2 && !short.keys.includes('investigator:diagnose'),
      'more time once only');
    check(short.result.status === 'blocked' && short.result.stopped_at === 'Reproduce' && short.result.phases.some((p) => p.phase === 'Reproduce'
      && /needs 500 min; its limit is 500 min \(the limit it asked for\)/.test(p.summary)), `still short is not "not reproduced": ${short.result.status}`);
    const done = await run({ mode: 'issue', task: 't' }, {
      'issue_triager:reproduce': () => ({ reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'arm 1 failed', needs_min: 500 }),
    });
    check(done.keys.filter((k) => k === 'issue_triager:reproduce').length === 1, 'a reproduction that showed the bug needs no more time');
    // the reporter's arm failed before the time ran out; the call with more time ran only main's and left it out
    const split = await run({ mode: 'issue', task: 't' }, {
      'issue_triager:reproduce': (ins, n) => (n === 1
        ? { reproduced: false, reproduced_on_reporter: true, repro_cmd: 'bash repro.sh', evidence: 'reporter arm failed', needs_min: 500 }
        : { reproduced: false, repro_cmd: 'bash repro.sh', evidence: 'main arm clean' }),
    });
    const second = split.calls.filter((c) => c.key === 'issue_triager:reproduce')[1];
    check(second && second.ins.earlier_answer && second.ins.earlier_answer.reproduced_on_reporter === true,
      'the call with more time gets the earlier answer');
    check(split.result.status === 'fixed_on_main', `the earlier call's reporter arm still counts: ${split.result.status}`);
    const repPhases = split.result.phases.filter((p) => p.phase === 'Reproduce');
    check(repPhases.some((p) => p.verdict === 'reproduced_on_reporter' && /reporter arm failed/.test(p.summary))
      && repPhases.some((p) => /main arm clean/.test(p.summary)), 'each call\'s evidence is an entry of its own');
    const again = await run({ mode: 'issue', task: 't' }, {
      'issue_triager:reproduce': (ins, n) => (n === 1
        ? { reproduced: false, reproduced_on_reporter: true, repro_cmd: 'bash repro.sh', evidence: 'reporter arm failed', needs_min: 500 }
        : { reproduced: false, reproduced_on_reporter: false, repro_cmd: 'bash repro.sh', evidence: 'reporter and main arms clean' }),
    });
    check(again.result.status === 'fixed_on_main', `a reporter arm that failed once has shown it: ${again.result.status}`);
    // the call with more time may leave out what the earlier one gave: no schema retry, the earlier's repro_cmd stands
    const terse = await run({ mode: 'issue', task: 't' }, {
      'issue_triager:reproduce': (ins, n) => (n === 1 ? { ...left, reproduced_on_reporter: true } : { reproduced: false }),
    });
    check(terse.keys.filter((k) => k === 'issue_triager:reproduce').length === 2 && terse.result.status === 'fixed_on_main',
      `a continuation that names only its result is taken: ${terse.result.status}`);
    // stopped for time: the arms both calls ran stay in the result, and the closing calls get the repro_cmd
    const kept = short.result.phases.filter((p) => p.phase === 'Reproduce' && p.verdict === 'partial');
    check(kept.length === 2 && kept.every((p) => /arm 1 of 3 clean/.test(p.summary)), `partial answers kept: ${kept.length}`);
    const reply = short.calls.find((c) => c.key === 'issue_triager:reply');
    check(reply && reply.ins.repro_cmd === 'bash repro.sh', `the reply knows the reproduction: ${reply && reply.ins.repro_cmd}`);
    // asking for more than twice its limit: no extension the run lock would not cover; a human gives a timeout
    const big = await run({ mode: 'issue', task: 't' }, { 'issue_triager:reproduce': () => ({ ...left, needs_min: 800 }) });
    check(big.keys.filter((k) => k === 'issue_triager:reproduce').length === 1 && big.result.status === 'blocked'
      && big.result.phases.some((p) => /needs 800 min; its limit is 360 min \(more than 2 times its limit\)/.test(p.summary)),
    'more time is at most twice the limit');
    const given = await run({ mode: 'issue', task: 't', agent_timeout_ms: 7200000 }, { 'issue_triager:reproduce': () => left });
    const rep = given.calls.filter((c) => c.key === 'issue_triager:reproduce');
    check(rep.length === 1 && rep[0].ins.time_limit_min === 120 && given.result.status === 'blocked' && given.result.phases.some((p) =>
      p.phase === 'Reproduce' && /needs 500 min; its limit is 120 min \(agent_timeout_ms\)/.test(p.summary)), 'a timeout the user gave stays the limit');
  }],
  ['issue: the reply draft is rewritten from how the run ended, before the report', async () => {
    const r = await run({ mode: 'issue', task: 'i' });
    const reply = r.calls.find((c) => c.key === 'issue_triager:reply');
    check(reply && reply.ins.status === 'ready_for_review' && reply.ins.pr_body_path, 'the reply sees the outcome and the PR body');
    const at = r.keys.indexOf('issue_triager:reply');
    check(at >= 0 && at < r.keys.indexOf('director:report'), 'before the report');
    const rep = r.calls.find((c) => c.key === 'director:report');
    check(rep && rep.ins.reply_draft_path === `${RUN}/reply_draft.md` && r.result.reply_draft_path === `${RUN}/reply_draft.md`,
      'the report and the result name the rewritten draft');
    const failed = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:reply': () => null });
    check(failed.result.status === 'ready_for_review' && failed.result.reply_draft_path === `${RUN}/reply.md`,
      `a failed rewrite keeps the Triage draft and the report: ${failed.result.reply_draft_path}`);
    const fixed = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:reproduce': () => ({ reproduced: false,
      reproduced_on_reporter: true, repro_cmd: 'bash repro.sh', evidence: 'reporter 3/3 crash; main 6/6 pass' }) });
    const fr = fixed.calls.find((c) => c.key === 'issue_triager:reply');
    check(fr && fr.ins.status === 'fixed_on_main' && fr.ins.stopped_at === 'Reproduce', 'a conclusion without a fix gets its reply too');
    const early = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': () => null });
    check(!early.keys.includes('issue_triager:reply'), 'no triage, no reply to rewrite');
  }],
  ['issue: the reviewer checks the reply draft against the run; what it finds is rewritten once, and what is left the user hears of', async () => {
    const clean = await run({ mode: 'issue', task: 'i' });
    const chk = clean.calls.find((c) => c.key === 'reviewer:reply');
    check(chk && chk.ins.reply_draft_path === `${RUN}/reply_draft.md` && chk.ins.status === 'ready_for_review',
      'the reviewer reads the rewritten draft and how the run ended');
    check(clean.keys.indexOf('reviewer:reply') > clean.keys.indexOf('issue_triager:reply')
      && clean.keys.indexOf('reviewer:reply') < clean.keys.indexOf('director:report'), 'after the draft, before the report');
    check(!chk.prompt.includes('Return it in\n`leads`'), 'a closing phase hands on no leads');
    check(clean.result.next.user.some((u) => u.startsWith('post the reply draft on the issue:')), 'a clean draft is the user\'s to post');
    const finding = { claim: 'a human reviewed the fix', issue: 'nobody did: the PR is a draft for review', evidence: 'run.json' };
    const fixed = await run({ mode: 'issue', task: 'i', review_model: 'other-model' }, {
      'reviewer:reply': (ins, n) => ({ findings: n === 1 ? [finding] : [], check_path: `${RUN}/reply_check.md` }),
      'issue_triager:reply': (ins, n) => ({ reply_draft_path: `${RUN}/reply_draft${n === 1 ? '' : '_2'}.md` }),
    });
    const rewrite = fixed.calls.filter((c) => c.key === 'issue_triager:reply')[1];
    check(rewrite && JSON.stringify(rewrite.ins.reply_findings).includes('a human reviewed the fix'), 'the findings go back to the triager');
    check(fixed.calls.filter((c) => c.key === 'reviewer:reply').map((c) => c.model).join() === 'other-model,other-model',
      'review_model reaches the reply check too');
    check(fixed.result.reply_draft_path === `${RUN}/reply_draft_2.md` && fixed.result.next.user.some((u) => u.startsWith('post the reply draft')),
      `the rewritten draft, checked clean: ${fixed.result.reply_draft_path}`);
    check(fixed.result.phases.some((p) => p.phase === 'Reply' && p.verdict === 'checked'), 'the check is in the record');
    const left = await run({ mode: 'issue', task: 'i' }, { 'reviewer:reply': () => ({ findings: [finding], check_path: `${RUN}/reply_check.md` }) });
    check(left.keys.filter((k) => k === 'reviewer:reply').length === 2 && left.keys.filter((k) => k === 'issue_triager:reply').length === 2,
      'one rewrite, checked once more, no further round');
    const say = (left.result.next.user || []).find((u) => u.includes('reply draft'));
    check(say && say.startsWith('read the reply draft against what the run showed before posting it') && say.includes('nobody did'),
      `what is left reaches the user: ${say}`);
    const unchecked = await run({ mode: 'issue', task: 'i' }, { 'reviewer:reply': () => null });
    const u = (unchecked.result.next.user || []).find((x) => x.includes('reply draft'));
    check(unchecked.result.status === 'ready_for_review' && u && u.includes('not checked'), `a draft nobody checked is not offered for posting as is: ${u}`);
    const blocked = await run({ mode: 'issue', task: 'i', max_investigate_rounds: 0 }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'issue_repro', failure_class: 'crash' }] }),
    });
    check(blocked.result.stopped_at === 'Verify' && blocked.keys.includes('reviewer:reply')
      && !blocked.result.next.say.includes('only what the run showed'),
      `next.say names where the run ended, not the reply check after it: ${blocked.result.next.say}`);
    const replyLine = (r) => (r.result.next.user || []).find((x) => x.includes('reply draft'));
    const doc = (role) => phaseSection(readFileSync(path.join(INFERPILOT, 'roles', `${role}.md`), 'utf8'), 'reply').replace(/\s+/g, ' ');
    check(doc('reviewer').includes('no promised date (a date the run recorded, such as an image tag\'s, is a fact'),
      'a date the run recorded is told; only a promised one is out');
    const noRecord = replyLine(await run({ mode: 'issue', task: 'i' }, { 'reviewer:reply': () => ({ findings: [], check_path: ' ' }) }));
    check(noRecord && !noRecord.startsWith('post the reply draft') && noRecord.includes('wrote no reply_check.md'),
      `a check that wrote no record of itself is none: ${noRecord}`);
    const stale = replyLine(await run({ mode: 'issue', task: 'i' }, {
      'reviewer:reply': (ins, n) => (n === 1 ? { findings: [finding], check_path: `${RUN}/reply_check.md` } : { findings: [], check_path: '' }),
      'issue_triager:reply': (ins, n) => ({ reply_draft_path: `${RUN}/reply_draft${n === 1 ? '' : '_2'}.md` }) }));
    check(stale && stale.includes('wrote no reply_check.md') && !stale.includes(`(${RUN}/reply_check.md)`),
      `the first round's record is not given for the second check: ${stale}`);
    const claimless = await run({ mode: 'issue', task: 'i' }, {
      'reviewer:reply': () => ({ findings: [{ claim: '', issue: 'the ledger has 2/2, the draft 3/3' }], check_path: `${RUN}/reply_check.md` }) });
    const cl = replyLine(claimless);
    check(claimless.keys.filter((k) => k === 'issue_triager:reply').length === 2 && cl && !cl.startsWith('post the reply draft')
      && cl.includes('a claim the reviewer did not quote: the ledger has 2/2'), `a finding that quotes no claim is a finding still: ${cl}`);
    const blank = await run({ mode: 'issue', task: 'i' }, {
      'reviewer:reply': () => ({ findings: [{ claim: '', issue: '', evidence: 'experiment_ledger.md, Unit tests' }], check_path: `${RUN}/reply_check.md` }) });
    const bl = replyLine(blank);
    check(blank.keys.filter((k) => k === 'issue_triager:reply').length === 2 && bl && !bl.startsWith('post the reply draft')
      && bl.includes('a claim the reviewer did not quote: the reviewer did not say what the run shows (its evidence: experiment_ledger.md, Unit tests)'),
      `a finding with neither claim nor issue is a finding still, not a clean check: ${bl}`);
    check(blank.result.reply_findings.length === 1 && blank.result.reply_findings[0].evidence === 'experiment_ledger.md, Unit tests',
      'and what it did give is kept');
    for (const [given, what] of [['not written', 'words that are no path'], [`${RUN}/reply_draft.md`, 'the draft\'s own path'],
      [`${RUN}/../other_run/reply_check.md`, 'another run\'s record']]) {
      const elsewhere = replyLine(await run({ mode: 'issue', task: 'i' }, { 'reviewer:reply': () => ({ findings: [], check_path: given }) }));
      check(elsewhere && !elsewhere.startsWith('post the reply draft') && elsewhere.includes(`gave ${given} as its record, which is not this run's reply_check.md`)
        && !elsewhere.includes(`(${given})`), `${what} as check_path is no record of this run's check: ${elsewhere}`);
    }
    const slashes = replyLine(await run({ mode: 'issue', task: 'i' }, { 'reviewer:reply': () => ({ findings: [], check_path: `${RUN}//reply_check.md ` }) }));
    check(slashes && slashes.startsWith('post the reply draft'), `the record's path written with a doubled slash is the record still: ${slashes}`);
    const recheck = (second) => run({ mode: 'issue', task: 'i', agent_timeout_ms: 100 }, {
      'reviewer:reply': (ins, n) => (n === 1 ? { findings: [finding], check_path: `${RUN}/reply_check.md` } : second()),
      'issue_triager:reply': (ins, n) => ({ reply_draft_path: `${RUN}/reply_draft${n === 1 ? '' : '_2'}.md` }) });
    for (const [second, what] of [[() => null, 'returned nothing'],
      [async () => { await new Promise((r) => setTimeout(r, 400)); return { findings: [], check_path: `${RUN}/reply_check.md` }; }, 'timed out']]) {
      const gone = replyLine(await recheck(second));
      check(gone && gone.includes('not checked since it was rewritten: the reviewer returned nothing') && gone.includes(`${RUN}/reply_draft_2.md`)
        && !gone.includes('reply_check.md'), `a second check that ${what} does not leave the first draft's record beside the rewritten one: ${gone}`);
    }
    check(doc('reviewer').includes('with any other value the draft counts as not checked') && doc('reviewer').includes('Every entry of `findings` is a finding'),
      'the reviewer is told what its record\'s path and an empty finding mean');
    const halfway = replyLine(await run({ mode: 'issue', task: 'i' }, {
      'reviewer:reply': () => ({ findings: [finding], check_path: `${RUN}/reply_check.md` }),
      'issue_triager:reply': (ins, n) => (n === 1 ? { reply_draft_path: `${RUN}/reply_draft.md` } : null) }));
    check(halfway && halfway.includes('nobody did') && halfway.includes('rewritten halfway') && !halfway.includes('still be writing'),
      `a rewrite that returned nothing may have left the draft half done: ${halfway}`);
    const writing = replyLine(await run({ mode: 'issue', task: 'i', agent_timeout_ms: 100 }, {
      'reviewer:reply': () => ({ findings: [finding], check_path: `${RUN}/reply_check.md` }),
      'issue_triager:reply': async (ins, n) => {
        if (n > 1) await new Promise((r) => setTimeout(r, 400));
        return { reply_draft_path: `${RUN}/reply_draft.md` };
      } }));
    check(writing && writing.includes('issue_triager:reply#fix timed out and may still be writing it: stop it before reading or posting'),
      `a rewrite that timed out may be writing the draft still: ${writing}`);
    const cause = await run({ mode: 'issue', task: 'i' }, {
      'investigator:diagnose': () => ({ verdict: 'baseline_bug', root_cause: 'pre-existing on main', known_issue: { id: 'KI-008' } }) });
    const cr = cause.calls.find((c) => c.key === 'reviewer:reply');
    const ct = cause.calls.find((c) => c.key === 'issue_triager:reply');
    check(cause.result.known_issues.length === 1 && cause.result.known_issues[0].cause_of_this_issue === true
      && cr && cr.ins.known_issues[0].cause_of_this_issue === true,
      'the baseline bug Diagnose found is marked as the issue\'s own cause');
    check(ct && doc('reviewer').includes('the one with `cause_of_this_issue` is what Diagnose found the issue\'s own failure to be')
      && doc('issue_triager').includes('The one with `cause_of_this_issue` is the exception'),
      'the reply explains the issue\'s own cause as that, and the check does not flag it');
  }],
  ['issue: a report of a failure that another open issue has is answered, and is not worked on twice', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'duplicate', duplicate_of: '#38849',
        reason: 'the same signature and failing path, on a second model', triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md` }),
    });
    check(r.result.status === 'triaged' && r.result.stopped_at === 'Triage', `a duplicate ends at Triage: ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('issue_triager:reproduce') && !r.keys.includes('implementer:implement'), 'nothing is reproduced or fixed for it');
    check(!r.keys.includes('issue_triager:reply') && r.keys.includes('director:report') && r.result.reply_draft_path === `${RUN}/reply.md`,
      `the draft written at Triage is its reply: ${r.keys.filter((k) => /reply|report/.test(k))}, ${r.result.reply_draft_path}`);
    check(r.result.duplicate_of === '#38849'
      && r.result.phases.some((p) => p.phase === 'Triage' && p.verdict === 'duplicate' && p.summary.includes('duplicate of #38849; ')),
      `the result and the run record name the issue that has the failure: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Triage'))}`);
    // A duplicate ends before the Reply phase: without the original, or without the draft, it would end with no answer.
    for (const [lacks, drop] of [['the issue it duplicates', { duplicate_of: '' }], ['the issue it duplicates', { duplicate_of: undefined }],
      ['the issue it duplicates', { duplicate_of: 'unknown' }], ['the issue it duplicates', { duplicate_of: '#n' }],
      ['a reply draft', { reply_draft_path: '' }]]) {
      const bare = await run({ mode: 'issue', task: 'i' }, {
        'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'duplicate', duplicate_of: '#38849', reason: 'the same failure',
          triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md`, ...drop }),
      });
      check(bare.result.status === 'blocked' && bare.result.stopped_at === 'Triage'
        && bare.result.phases.some((p) => p.phase === 'Triage' && p.verdict === 'fail' && p.summary.includes(`a duplicate without ${lacks}`)),
        `a duplicate without ${lacks} is not accepted (${JSON.stringify(drop)}): ${bare.result.status} at ${bare.result.stopped_at}`);
    }
    // duplicate_of is read with a duplicate only: any other action is as it was.
    const other = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'needs_info', duplicate_of: '#1', triage_path: `${RUN}/t.md`,
        reply_draft_path: `${RUN}/reply.md` }),
    });
    check(other.result.status === 'triaged' && !other.result.duplicate_of, 'another action does not take it');
    // The original is named as an issue is: #<n>, <owner>/<repo>#<n>, or its URL.
    for (const [given, kept] of [['#38849', '#38849'], ['sgl-project/sglang#38849', 'sgl-project/sglang#38849'],
      ['https://github.com/sgl-project/sglang/issues/38849', 'sgl-project/sglang#38849']]) {
      const named = await run({ mode: 'issue', task: 'i' }, {
        'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'duplicate', duplicate_of: given, reason: 'the same failure',
          triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md` }),
      });
      check(named.result.status === 'triaged' && named.result.duplicate_of === kept, `${given} names the original: ${named.result.duplicate_of}`);
    }
  }],
  ['issue: the other problems of a report with several are offered as runs of their own', async () => {
    const others = [{ problem: 'GSM8K loses 4 points with the same flags', priority: 'P1', task: 'sgl-project/sglang#41299: the accuracy loss' },
      { problem: 'the server hangs on shutdown', priority: 'P2' }];
    const r = await run({ mode: 'issue', task: 'sgl-project/sglang#41299', model_path: '/models/m', stop_after: 'Reproduce' }, {
      'issue_triager:triage': () => ({ priority: 'P0', category: 'crash', action: 'reproduce', reason: 'the crash, the most urgent of three',
        other_problems: others, triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md` }),
    });
    const start = r.result.next.start || [];
    check(start.length === 2 && start[0].task === others[0].problem && start[0].args.task === 'sgl-project/sglang#41299: the accuracy loss',
      `each other problem is offered with its task: ${JSON.stringify(start)}`);
    check(start[1].args.task === 'sgl-project/sglang#41299: the server hangs on shutdown',
      `one without a task of its own takes the issue and the problem's name: ${start[1] && start[1].args.task}`);
    check(start.every((x) => x.args.mode === 'issue' && x.args.inferpilot_dir === INFERPILOT && x.args.model_path === '/models/m'
      && !('stop_after' in x.args) && !('run_dir' in x.args) && !('resume_from' in x.args)),
      `each starts on this run's definition, and not where this run stops: ${JSON.stringify(start.map((x) => x.args))}`);
    check(r.result.other_problems.length === 2, 'the result lists them');
    const one = await run({ mode: 'issue', task: 'i' });
    check(!one.result.next.start && !one.result.other_problems, 'a report with one problem offers no run');
  }],
  ['issue: needs_info ends at triage with a reply draft', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'needs_info', triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md` }),
    });
    check(r.result.status === 'triaged' && r.result.stopped_at === 'Triage', `status ${r.result.status}`);
    check(!r.keys.includes('issue_triager:reply') && r.result.reply_draft_path === `${RUN}/reply.md`, 'the Triage draft is the reply');
    const u = (r.result.next.user || []).find((x) => x.includes('reply draft'));
    check(u && !u.startsWith('post the reply draft') && u.includes('ended at Triage'), `a draft no reviewer checked is not offered as is: ${u}`);
    const noRewrite = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:reply': () => null });
    const v = (noRewrite.result.next.user || []).find((x) => x.includes('reply draft'));
    check(!noRewrite.keys.includes('reviewer:reply') && v && !v.startsWith('post the reply draft')
      && v.includes('an earlier one or one rewritten halfway') && !v.includes('still be writing'),
      `a draft the rewrite returned nothing for is not offered as is: ${v}`);
    const slowRewrite = await run({ mode: 'issue', task: 'i', agent_timeout_ms: 100 }, { 'issue_triager:reply': async () => {
      await new Promise((r) => setTimeout(r, 400));
      return { reply_draft_path: `${RUN}/reply_draft.md` };
    } });
    const w = (slowRewrite.result.next.user || []).find((x) => x.includes('reply draft'));
    check(!slowRewrite.keys.includes('reviewer:reply') && w && w.includes('issue_triager:reply timed out and may still be writing it'),
      `nothing checks or posts a draft a timed-out rewrite may still be writing: ${w}`);
  }],
  ['issue: a fix whose change cannot be put into effect on this machine stops for a human, and is not investigated', async () => {
    // A version the fix pins does not install in this image: nothing was measured, and no change to the code repairs that.
    const cannot = { gate: 'issue_repro', failure_class: 'environment', blocking: true, evidence: 'aiter 0.1.12 cannot be installed in this image' };
    const r = await run({ mode: 'issue', task: 'i' }, {
      'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), all_pass: false, failures: [cannot] }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Verify', `the run stops at Verify: ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('investigator:investigate') && !r.keys.includes('perf_engineer:perf_eval') && !r.keys.includes('reviewer:review')
      && r.keys.filter((k) => k === 'verify_engineer:verify').length === 1, 'nothing is investigated, repaired, measured or reviewed after it');
    check(r.result.phases.some((p) => p.phase === 'Verify' && /issue_repro:environment/.test(p.summary)),
      `the run record names the gate and its class: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Verify'))}`);
    check(/could not be put into effect on this machine \(issue_repro\): aiter 0\.1\.12 cannot be installed/.test(r.result.next.say),
      `the user is told what could not be done: ${r.result.next.say}`);
    // Any other blocking failure is investigated as before, and one of that class that does not block stops nothing.
    const usual = await run({ mode: 'issue', task: 'i' }, {
      'verify_engineer:verify': (ins, n) => (n === 1
        ? { ...DEFAULTS['verify_engineer:verify'](), all_pass: false, failures: [{ gate: 'G0', failure_class: 'crash', blocking: true }] }
        : DEFAULTS['verify_engineer:verify']()),
    });
    check(usual.keys.includes('investigator:investigate'), 'another class of failure goes to the investigator as before');
    const mild = await run({ mode: 'issue', task: 'i' }, {
      'verify_engineer:verify': () => ({ ...DEFAULTS['verify_engineer:verify'](), all_pass: false, failures: [{ ...cannot, gate: 'G3', blocking: false }] }),
    });
    check(mild.result.status === 'ready_for_review', `a report-only item of that class stops nothing: ${mild.result.status}`);
  }],
  ['issue: close_fixed that no longer reproduces', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P2', category: 'crash', action: 'close_fixed', triage_path: `${RUN}/t.md` }),
      'issue_triager:reproduce': (ins) => ({ reproduced: false, repro_cmd: 'bash repro.sh', evidence: `expect_fixed=${ins.expect_fixed}; clean 3/3` }),
    });
    check(r.result.status === 'fixed_on_main', `status ${r.result.status}`);
  }],
  ['issue: the reporter\'s code fails and main passes: fixed on main, not "not reproduced"', async () => {
    const onReporter = { 'issue_triager:reproduce': () => ({ reproduced: false, reproduced_on_reporter: true, repro_cmd: 'bash repro.sh',
      evidence: 'reporter commit 3/3 crash; main 6/6 pass' }) };
    const r = await run({ mode: 'issue', task: 'i' }, onReporter);
    check(r.result.status === 'fixed_on_main' && r.result.stopped_at === 'Reproduce', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('investigator:diagnose') && !r.keys.includes('ci_guardian:bisect_fix'), 'no diagnosis, no bisect unless asked');
    const neither = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:reproduce': () => ({ reproduced: false, repro_cmd: 'bash repro.sh', evidence: 'main 6/6 pass; reporter image not run' }),
    });
    check(neither.result.status === 'not_reproduced', `without a reporter result: ${neither.result.status}`);

    const bis = await run({ mode: 'issue', task: 'i', bisect_fixed: true }, {
      ...onReporter,
      'ci_guardian:bisect_fix': (ins) => ({ fix_commit: 'abc1234 (#44302)', confirmed: ins.repro_cmd === 'bash repro.sh',
        evidence: 'fails at parent 3/3, passes at fix 3/3' }),
    });
    check(bis.result.status === 'fixed_on_main' && bis.result.fixed_by === 'abc1234 (#44302)', `bisect: ${bis.result.status} ${bis.result.fixed_by}`);
    check(bis.phases.indexOf('Bisect') === bis.phases.indexOf('Reproduce') + 1 && !bis.phases.includes('Diagnose')
      && bis.phases[bis.phases.length - 1] === 'Report', `phases ${bis.phases}`);
    const rep = bis.calls.find((c) => c.key === 'director:report');
    check(rep && rep.ins.fixed_by === 'abc1234 (#44302)', 'the report names the fix');
    const unconfirmed = await run({ mode: 'issue', task: 'i', bisect_fixed: 'true' }, {
      ...onReporter, 'ci_guardian:bisect_fix': () => ({ fix_commit: 'abc1234', confirmed: false }),
    });
    check(unconfirmed.result.status === 'fixed_on_main' && !unconfirmed.result.fixed_by
      && unconfirmed.result.phases.some((p) => p.phase === 'Bisect' && p.verdict === 'fail'), 'an unconfirmed bisect names nothing');
    const stopped = await run({ mode: 'issue', task: 'i', bisect_fixed: true, stop_after: 'Reproduce' }, onReporter);
    check(stopped.result.status === 'fixed_on_main' && !stopped.keys.includes('ci_guardian:bisect_fix'),
      `stop_after=Reproduce runs no bisect: ${stopped.result.status}`);
  }],
  ['issue: an existing fix PR is reproduced on main first and gated on that reproduction', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'push_existing_pr', existing_prs: ['#40872'], triage_path: `${RUN}/t.md` }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Triage', 'Reproduce', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report']), `phases ${r.phases}`);
    const im = r.calls.find((c) => c.key === 'implementer:implement');
    check(im && im.ins.work_package.kind === 'apply_pr' && im.ins.work_package.goal.includes('#40872'), 'apply_pr work package');
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && v.ins.extra_gates[0].cmd === 'bash repro.sh', `issue_repro gate carries the reproduction: ${JSON.stringify(v && v.ins.extra_gates)}`);
    // The run ends in a comment for that pull request, as a ci run's does: the PR engineer is told which, and the
    // result says to post the comment, not to push a branch that has no pull request of its own.
    const pe = r.calls.find((c) => c.key === 'pr_engineer:pr');
    const n = r.result.next;
    check(pe && eqList(pe.ins.existing_prs, ['#40872']) && eqList(r.result.pr.comment_for, ['#40872']),
      `the PR engineer knows which pull request the comment is for: ${JSON.stringify(pe && pe.ins.existing_prs)}`);
    check(/^#40872 is verified: the comment for it is drafted at /.test(n.say) && /nothing is to be pushed/.test(n.say)
      && n.user.some((u) => u.startsWith('post the verification comment on #40872')) && !n.user.some((u) => /git push|^push /.test(u)),
      `the user is told to post the comment, not to push a branch: ${JSON.stringify(n)}`);
    // One pull request is verified in a run: several applied together would show nothing of each.
    const several = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'crash', action: 'push_existing_pr', existing_prs: ['#40872', ' #40901 '],
        triage_path: `${RUN}/t.md` }) });
    const first = several.calls.find((c) => c.key === 'implementer:implement').ins.work_package.goal;
    check(first.includes('#40872') && !first.includes('#40901') && eqList(several.result.pr.comment_for, ['#40872'])
      && several.result.phases.some((p) => p.phase === 'Reproduce' && p.summary === '#40872 is verified. Not verified in this run, which takes one: #40901'),
      `of several the first is verified and the others are named: ${first}`);
  }],
  ['issue: a root cause in an AMD component gets an issue drafted for that repository, and a workaround in the framework only for a P0', async () => {
    const draft = `${RUN}/component_issue.md`;
    const wps = [{ id: 'FIX1', goal: 'the reference path for the shapes that fail, HIP-only', kind: 'framework', files: ['python/sglang/a.py'], test: 't', depends_on: [] }];
    const advice = 'aiter 0.1.9 does not have it; --disable-aiter-moe avoids the path';
    const inAiter = (extra) => ({ verdict: 'fix_needed', root_cause: 'aiter: the fused MoE kernel reads past the last block',
      component: 'aiter', component_issue_path: draft, summary: advice, ...extra });
    const triage = (priority) => () => ({ priority, category: 'crash', action: 'reproduce', triage_path: `${RUN}/t.md`, reply_draft_path: `${RUN}/reply.md` });
    const filed = (r) => (r.result.next.user || []).find((x) => x.includes('component_issue.md'));

    // Not a P0: no workaround, whatever the diagnosis planned. The run ends with the root cause, the reply and the draft.
    const p1 = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1'), 'investigator:diagnose': () => inAiter({ work_packages: wps }) });
    check(p1.result.status === 'diagnosed' && p1.result.stopped_at === 'Diagnose', `a P1 ends at Diagnose: ${p1.result.status} at ${p1.result.stopped_at}`);
    check(!p1.keys.includes('implementer:implement'), 'nothing is implemented for it, although the diagnosis planned a workaround');
    check(p1.result.component_issue && p1.result.component_issue.component === 'aiter' && p1.result.component_issue.draft_path === draft,
      `the result names the component and the draft: ${JSON.stringify(p1.result.component_issue)}`);
    check(/in aiter's repository/.test(filed(p1) || ''), `the user is told to file the draft there: ${filed(p1)}`);
    const reply = p1.calls.find((c) => c.key === 'issue_triager:reply');
    check(reply && reply.ins.component_issue && reply.ins.component_issue.component === 'aiter', 'the reply is written knowing where the root cause is');
    // What the reporter can do meanwhile is said in the diagnosis' summary only: the reply and the report get it.
    const report = p1.calls.find((c) => c.key === 'director:report');
    check(reply.ins.component_issue.summary === advice && report.ins.component_issue.summary === advice && p1.result.component_issue.summary === advice
      && p1.result.phases.some((p) => p.phase === 'Diagnose' && p.verdict === 'component' && p.summary.includes(advice)),
      `the diagnosis' advice for the reporter reaches the reply, the report and the run record: ${JSON.stringify(p1.result.component_issue)}`);
    check(p1.result.phases.some((p) => p.phase === 'Diagnose' && p.verdict === 'component' && /only a P0/.test(p.summary)),
      `the run record says why no workaround was made: ${JSON.stringify(p1.result.phases.filter((p) => p.phase === 'Diagnose'))}`);

    // A P0 gets the workaround, as a fix like any other: reproduced, verified with the reproduction, measured.
    const p0 = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P0'), 'investigator:diagnose': () => inAiter({ work_packages: wps }) });
    check(p0.result.status === 'ready_for_review' && p0.keys.includes('implementer:implement') && p0.keys.includes('perf_engineer:perf_eval'),
      `a P0 gets the workaround, verified and measured: ${p0.result.status}`);
    check(/in aiter's repository/.test(filed(p0) || ''), 'and the draft for the component is still the user\'s to file: a workaround is not the fix');
    const impl = p0.calls.find((c) => c.key === 'implementer:implement');
    check(impl.ins.component_issue && impl.ins.component_issue.draft_path === draft, 'the implementer knows which issue the workaround is for');
    // A P0 no workaround serves ends as any diagnosis without a fix.
    const none = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P0'), 'investigator:diagnose': () => inAiter({ work_packages: [] }) });
    check(none.result.status === 'diagnosed' && !!filed(none) && none.result.component_issue.summary === advice,
      `a P0 with no workaround to make ends diagnosed, with the draft and why: ${none.result.status}`);
    // Only the four components are components: an empty one is a root cause in the framework, fixed at any priority.
    const empty = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1'),
      'investigator:diagnose': () => inAiter({ component: '', component_issue_path: '', work_packages: wps }) });
    check(empty.result.status === 'ready_for_review' && !empty.result.component_issue, `no component named: the framework's own fix goes on: ${empty.result.status}`);
    // A word that is no component ("none", a third-party library) is not an answer: it would stop the framework's own fix.
    for (const word of ['none', 'mooncake']) {
      const odd = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1'),
        'investigator:diagnose': () => inAiter({ component: word, work_packages: wps }) });
      check(odd.result.status === 'blocked' && odd.result.stopped_at === 'Diagnose' && !odd.result.component_issue
        && odd.result.phases.some((p) => p.phase === 'Diagnose' && /no diagnosis/.test(p.summary)),
        `"${word}" is refused as an answer, not taken for a component: ${odd.result.status} at ${odd.result.stopped_at}`);
    }

    // A component named without a draft: the diagnosis is not done.
    const bare = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P0'),
      'investigator:diagnose': () => inAiter({ component_issue_path: '', work_packages: wps }) });
    check(bare.result.status === 'blocked' && bare.result.stopped_at === 'Diagnose' && !bare.keys.includes('implementer:implement'),
      `a component named without its draft stops at Diagnose: ${bare.result.status} at ${bare.result.stopped_at}`);

    // Only a diagnosis that establishes the root cause names a component: a suspicion is not an issue to file.
    for (const verdict of ['inconclusive', 'not_amd_side']) {
      const unsure = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1'),
        'investigator:diagnose': () => inAiter({ verdict, work_packages: [] }) });
      check(unsure.result.status === 'diagnosed' && !unsure.result.component_issue && !filed(unsure),
        `${verdict}: no component is recorded, and no draft is given to file: ${unsure.result.status}, ${JSON.stringify(unsure.result.component_issue)}`);
      const reply = unsure.calls.find((c) => c.key === 'issue_triager:reply');
      check(reply && !reply.ins.component_issue, `${verdict}: nor does the reply name one`);
    }
    const onBase = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1'),
      'investigator:diagnose': () => inAiter({ verdict: 'baseline_bug', work_packages: [], known_issue: { id: 'KI-7', title: 'aiter reads past the block' } }) });
    check(onBase.result.status === 'diagnosed' && onBase.result.component_issue && !!filed(onBase),
      `the baseline's own bug, found in a component, has its draft: ${onBase.result.status}`);
    // A root cause in the framework is unchanged, at any priority.
    const own = await run({ mode: 'issue', task: 'i' }, { 'issue_triager:triage': triage('P1') });
    check(own.result.status === 'ready_for_review' && !own.result.component_issue && !filed(own), 'a root cause in the framework is fixed as before');

  }],
  ['issue: a reproduction without a command stops before any change', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:reproduce': () => ({ reproduced: true, repro_cmd: '  ', evidence: 'segfault 3/3' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Reproduce', `status ${r.result.status}`);
    check(!r.keys.includes('investigator:diagnose'), 'no diagnosis without a reproduction command');
  }],
  ['issue: only a category from the list is accepted, and performance runs the evaluation', async () => {
    const odd = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P1', category: 'Performance', action: 'reproduce', triage_path: `${RUN}/t.md` }),
    });
    check(odd.result.status === 'blocked' && odd.result.stopped_at === 'Triage', `category Performance: ${odd.result.status}`);
    const perfTriage = { 'issue_triager:triage': () => ({ priority: 'P1', category: 'performance', action: 'reproduce', triage_path: `${RUN}/t.md` }) };
    const perf = await run({ mode: 'issue', task: 'i' }, {
      ...perfTriage,
      'issue_triager:reproduce': () => ({ reproduced: true, repro_cmd: 'bash bench.sh', perf_claim: 'decode tok/s back to 2400 at c32 (v0.5.21)', evidence: '1710 tok/s 3/3' }),
    });
    check(perf.phases.includes('Performance') && perf.result.status === 'ready_for_review', `performance issue: ${perf.phases}`);
    const pe = perf.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(pe && pe.ins.claim === 'decode tok/s back to 2400 at c32 (v0.5.21)', `the issue's number is the claim: ${pe && pe.ins.claim}`);
    const noNumber = await run({ mode: 'issue', task: 'i' }, perfTriage);
    check(noNumber.result.status === 'blocked' && noNumber.result.stopped_at === 'Reproduce', `no number to restore: ${noNumber.result.status}`);
  }],
  ['ci: infrastructure is handed over', async () => {
    const r = await run({ mode: 'ci', task: 'tracker #45847 cluster NIXL' }, {
      'ci_guardian:classify': () => ({ failure_class: 'infrastructure', failing_test_cmd: '', evidence: 'UCX interface errno 19', triage_path: `${RUN}/ci.md` }),
    });
    check(r.result.status === 'handed_off', `status ${r.result.status}`);
    check(!r.keys.includes('ci_guardian:bisect'), 'no bisect for infrastructure');
  }],
  ['ci: an open pull request that already fixes the failure is verified on the user\'s word, not fixed a second time', async () => {
    const classified = (out) => ({ 'ci_guardian:classify': () => ({ ...DEFAULTS['ci_guardian:classify'](), ...out }) });
    const ended = (r) => `${r.result.status} at ${r.result.stopped_at}`;
    const mine = { pr: '#50220', repo: 'sgl-project/sglang', author: 'alice', association: 'MEMBER', head: 'abc1234', state: 'open' };
    const theirs = { pr: '#50231', repo: 'sgl-project/sglang', author: 'mallory', association: 'CONTRIBUTOR', head: 'def5678', state: 'open' };
    const applied = (r) => (r.calls.find((c) => c.key === 'implementer:implement') || { ins: {} }).ins.work_package || {};
    const r = await run({ mode: 'ci', task: 'c', apply_prs: ['#50220'] }, classified({ existing_prs: [mine] }));
    check(r.result.status === 'ready_for_review' && !r.keys.includes('ci_guardian:bisect'), `no culprit is searched for: ${ended(r)}`);
    check(eqList(r.phases, ['Setup', 'Classify', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    check(applied(r).kind === 'apply_pr' && applied(r).goal.startsWith('apply the existing fix #50220 at abc1234 ')
      && /failing test passes on every lane/.test(applied(r).test), `the pull request is applied at its head, not a fix planned: ${JSON.stringify(applied(r))}`);
    const ve = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ve && ve.ins.extra_gates.length === 1 && ve.ins.extra_gates[0].gate === 'failing_test' && ve.ins.extra_gates[0].cmd === 'pytest test_x.py',
      `the failing test is the gate it must pass: ${JSON.stringify(ve && ve.ins.extra_gates)}`);
    const pe = r.calls.find((c) => c.key === 'pr_engineer:pr');
    check(pe && eqList(pe.ins.existing_prs, ['#50220']) && pe.ins.existing_pr.head === 'abc1234',
      `the PR engineer knows which pull request the comment is for, and at which commit: ${JSON.stringify(pe && pe.ins.existing_pr)}`);
    check(/^no gain is claimed: no slower than the baseline/.test(r.calls.find((c) => c.key === 'perf_engineer:perf_eval').ins.claim),
      'its speed is measured as a fix of the run\'s own is: no slower than the base');
    check(r.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'existing_fix' && p.summary.startsWith('#50220 by alice (member): open')),
      `the run record names it and its author: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Classify'))}`);
    // The run ends in a comment for that pull request: there is nothing of its own to push.
    const n = r.result.next;
    check(eqList(r.result.pr.comment_for, ['#50220']) && /^#50220 is verified: the comment for it is drafted at /.test(n.say)
      && /nothing is to be pushed/.test(n.say) && n.user.some((u) => u.startsWith('post the verification comment on #50220'))
      && !n.user.some((u) => /git push|^push /.test(u)), `the user is told to post the comment, not to push a branch: ${JSON.stringify(n)}`);

    // A pull request runs its author's code here, next to the keys the agents hold: the run applies one only on the
    // user's word, whoever wrote it. What the classification says of the author is a model's report of an
    // association, which does not show that the author can put code on main.
    for (const who of [mine, { ...mine, association: 'OWNER' }, { ...theirs, association: 'COLLABORATOR' }, theirs, { ...theirs, association: '' }]) {
      const asked = await run({ mode: 'ci', task: 'c' }, classified({ existing_prs: [who] }));
      const q = (asked.result.next.ask || [])[0] || {};
      check(asked.result.status === 'needs_input' && asked.result.stopped_at === 'Classify' && !asked.keys.includes('implementer:implement')
        && !asked.keys.includes('ci_guardian:bisect') && q.field === 'apply_prs' && eqList(q.options, [who.pr, 'none'])
        && /so the run applies one only on your word/.test(q.question),
        `a pull request by ${who.association || 'an author of unknown standing'} is not run without the user's word: ${ended(asked)}, ${JSON.stringify(q.options)}`);
      // The answer takes the place of a field the task left blank, which would otherwise win over it.
      check(asked.result.next.rerun_with.apply_prs === '' && asked.result.next.rerun_with.answers.apply_prs === null,
        `the rerun clears the field and waits for the answer: ${JSON.stringify(asked.result.next.rerun_with)}`);
    }
    const allowed = await run({ mode: 'ci', task: 'c', apply_prs: ['#50231'] }, classified({ existing_prs: [theirs] }));
    check(allowed.result.status === 'ready_for_review' && applied(allowed).goal.includes('#50231 at def5678') && !allowed.keys.includes('ci_guardian:bisect'),
      `named in the task, it is verified: ${ended(allowed)}`);
    const answered = await run({ mode: 'ci', task: 'c', answers: { apply_prs: '#50231' } }, classified({ existing_prs: [theirs] }));
    check(answered.result.status === 'ready_for_review' && applied(answered).goal.includes('#50231'), `the answer to the question does the same: ${ended(answered)}`);
    // The question asks for the number: a bare number, "PR 50231" and the pull request's URL all name it.
    for (const word of ['50231', ' #50231 ', 'PR 50231', 'pr #50231', 'https://github.com/sgl-project/sglang/pull/50231',
      'https://github.com/sgl-project/sglang/pull/50231/files', 'https://github.com/SGL-Project/SGLang/pull/50231', 'sgl-project/sglang#50231']) {
      const bare = await run({ mode: 'ci', task: 'c', answers: { apply_prs: word } }, classified({ existing_prs: [theirs] }));
      check(bare.result.status === 'ready_for_review' && applied(bare).goal.includes('#50231 at def5678') && !bare.keys.includes('ci_guardian:bisect'),
        `the answer ${JSON.stringify(word)} names the pull request: ${ended(bare)}, ${applied(bare).goal}`);
    }
    // A field the task set blank does not stand in the answer's way, in this run or in the one after the question.
    for (const blank of [[], '  ', ['']]) {
      const empty = await run({ mode: 'ci', task: 'c', apply_prs: blank }, classified({ existing_prs: [theirs] }));
      check(empty.result.status === 'needs_input' && empty.result.next.rerun_with.apply_prs === '',
        `a blank apply_prs (${JSON.stringify(blank)}) is no word: the run asks (${ended(empty)})`);
      const then = await run({ mode: 'ci', task: 'c', apply_prs: blank, ...empty.result.next.rerun_with, answers: { apply_prs: '#50231' } },
        classified({ existing_prs: [theirs] }));
      check(then.result.status === 'ready_for_review' && applied(then).goal.includes('#50231'), `and the answer is taken: ${ended(then)}`);
      const direct = await run({ mode: 'ci', task: 'c', apply_prs: blank, answers: { apply_prs: 'none' } }, classified({ existing_prs: [theirs] }));
      check(direct.result.status === 'ready_for_review' && direct.keys.includes('ci_guardian:bisect'),
        `"none" as the answer next to a blank field is taken too: ${ended(direct)}`);
    }
    const refused = await run({ mode: 'ci', task: 'c', apply_prs: 'none' }, classified({ existing_prs: [theirs] }));
    check(refused.result.status === 'ready_for_review' && refused.keys.includes('ci_guardian:bisect') && applied(refused).kind !== 'apply_pr'
      && !refused.result.pr.comment_for && refused.result.next.user.some((u) => /push/.test(u))
      && refused.result.phases.some((p) => p.phase === 'Classify' && /#50231 by mallory \(contributor\): open, and not applied/.test(p.summary)),
      `"none" has the run fix the failure itself, and nothing is asked again: ${ended(refused)}`);
    // The user's word alone decides which: a pull request the task does not name is not applied, whoever wrote it.
    const narrowed = await run({ mode: 'ci', task: 'c', apply_prs: ['#50231'] }, classified({ existing_prs: [mine, theirs] }));
    check(applied(narrowed).goal.includes('#50231') && !applied(narrowed).goal.includes('#50220'), `the task's word decides which: ${applied(narrowed).goal}`);

    // What the Intake read into a request is a model's reading, not the user's word: the run asks, and names it first.
    const read = { 'intake:define': (ins) => ({ status: 'ready', questions: [], summary: 'as read',
      task_def: { ...ins.given, mode: 'ci', task: 'c', apply_prs: ['#50231'] } }) };
    const inferred = await run({ mode: 'ci', task: 'c', request: 'the red job; someone opened 50231 for it' },
      { ...read, ...classified({ existing_prs: [mine, theirs] }) });
    const iq = (inferred.result.next.ask || [])[0] || {};
    check(inferred.result.status === 'needs_input' && inferred.result.stopped_at === 'Classify' && !inferred.keys.includes('implementer:implement')
      && eqList(iq.options, ['#50231', '#50220', 'none']) && /The request seems to name #50231: say so here to confirm it\./.test(iq.question),
      `a pull request the Intake read into the request is asked about, not applied: ${ended(inferred)}, ${JSON.stringify(iq.options)}`);
    const confirmed = await run({ mode: 'ci', task: 'c', request: 'the red job; someone opened 50231 for it', answers: { apply_prs: '#50231' } },
      { ...read, ...classified({ existing_prs: [mine, theirs] }) });
    check(confirmed.result.status === 'ready_for_review' && applied(confirmed).goal.includes('#50231 at def5678'),
      `confirmed in the answer, it is verified: ${ended(confirmed)}`);

    // What the user names is looked up: Classify is told, and only one it returns open, with the commit its branch
    // is at, is applied. A closed or a merged one is not reported as an open fix, and no revision is guessed.
    const told = (x) => (x.calls.find((c) => c.key === 'ci_guardian:classify') || { ins: {} }).ins.named_prs;
    check(eqList(told(allowed), ['#50231']) && eqList(told(r), ['#50220']) && told(refused) === undefined && eqList(told(inferred), ['#50231'])
      && eqList(told(await run({ mode: 'ci', task: 'c', answers: { apply_prs: 'https://github.com/sgl-project/sglang/pull/777' } }, classified({}))),
        ['sgl-project/sglang#777'])
      && told(await run({ mode: 'ci', task: 'c', answers: { apply_prs: 'not sure' } }, classified({}))) === undefined,
      `Classify is given the pull requests the task or the request names: ${JSON.stringify([told(allowed), told(r), told(refused), told(inferred)])}`);
    const pointed = await run({ mode: 'ci', task: 'c', apply_prs: ['#777'] },
      classified({ existing_prs: [{ pr: '#777', author: 'carol', association: 'NONE', head: '7770001', state: 'open' }] }));
    check(pointed.result.status === 'ready_for_review' && applied(pointed).goal.startsWith('apply the existing fix #777 at 7770001 ')
      && !pointed.keys.includes('ci_guardian:bisect'), `a pull request the task names is verified once Classify has looked it up: ${ended(pointed)}`);
    for (const [found, why] of [
      [[], '#777: the classification did not find it as a pull request of this repository'],
      [[{ pr: '#777', author: 'carol', head: '7770001', state: 'closed' }], '#777: it is closed'],
      [[{ pr: '#777', author: 'carol', head: '7770001', state: 'merged' }], '#777: it is merged'],
      [[{ pr: '#777', author: 'carol', head: '7770001' }], '#777: it is not said to be open'],
      [[{ pr: '#777', author: 'carol', state: 'open' }], '#777: the commit its branch is at is not known'],
      [[{ pr: 'ROCm/aiter#777', author: 'carol', head: '7770001', state: 'open' }], '#777: the classification did not find it as a pull request of this repository'],
    ]) {
      const gone = await run({ mode: 'ci', task: 'c', apply_prs: ['#777'] }, classified({ existing_prs: found }));
      check(gone.result.status === 'ready_for_review' && gone.keys.includes('ci_guardian:bisect') && applied(gone).kind !== 'apply_pr'
        && !gone.result.pr.comment_for
        && gone.result.phases.some((p) => p.phase === 'Classify' && p.summary === `named for verification, and not applied: ${why}`),
        `a named pull request that is not open with a known head (${why}) is not applied, and the run fixes the failure itself: ${ended(gone)}, ${
          JSON.stringify(gone.result.phases.filter((p) => p.phase === 'Classify').map((p) => p.summary))}`);
    }
    // A word that names its repository keeps it. Its number alone names another pull request in the framework's
    // repository, and a word given for aiter's #777 must not run sglang's #777 next to the keys: Classify is told
    // the repository, and only a pull request it places there is the one the word names.
    const unrelated = { pr: '#777', repo: 'sgl-project/sglang', author: 'dave', association: 'NONE', head: '7770002', state: 'open' };
    for (const word of ['https://github.com/ROCm/aiter/pull/777', 'ROCm/aiter#777', 'https://github.com/ROCm/aiter/pull/777/files']) {
      const other = await run({ mode: 'ci', task: 'c', answers: { apply_prs: word } }, classified({ existing_prs: [unrelated] }));
      check(other.result.status === 'ready_for_review' && other.keys.includes('ci_guardian:bisect') && applied(other).kind !== 'apply_pr'
        && !other.result.pr.comment_for && eqList(told(other), ['ROCm/aiter#777'])
        && other.result.phases.some((p) => p.phase === 'Classify'
          && p.summary === 'named for verification, and not applied: ROCm/aiter#777: the classification did not find it as a pull request of this repository'),
        `a word for another repository's pull request (${word}) does not run the framework's pull request of that number: ${ended(other)}, ${
          JSON.stringify(other.result.phases.filter((p) => p.phase === 'Classify').map((p) => p.summary))}`);
    }
    // The repository's name is compared without regard to case, as GitHub reads it, whichever side writes it how.
    const cased = await run({ mode: 'ci', task: 'c', apply_prs: ['https://github.com/sgl-project/sglang/pull/50231'] },
      classified({ existing_prs: [{ ...theirs, repo: 'SGL-Project/SGLang' }] }));
    check(cased.result.status === 'ready_for_review' && applied(cased).goal.includes('#50231 at def5678'),
      `a repository the classification writes in other letters is the same repository: ${ended(cased)}`);
    // Returned under its full name, as Classify is told to return it, it is one of another repository: not applied either.
    const elsewhere = await run({ mode: 'ci', task: 'c', apply_prs: ['ROCm/aiter#777'] },
      classified({ existing_prs: [{ pr: 'ROCm/aiter#777', repo: 'ROCm/aiter', author: 'carol', head: '7770001', state: 'open' }] }));
    check(elsewhere.result.status === 'ready_for_review' && elsewhere.keys.includes('ci_guardian:bisect') && applied(elsewhere).kind !== 'apply_pr',
      `nor is the other repository's own pull request applied to this tree: ${ended(elsewhere)}`);
    // Where the word names a repository and the classification does not say which one a pull request is in, it is not that one.
    const unplaced = await run({ mode: 'ci', task: 'c', apply_prs: ['https://github.com/sgl-project/sglang/pull/50231'] },
      classified({ existing_prs: [{ ...theirs, repo: undefined }] }));
    check(unplaced.result.status === 'ready_for_review' && unplaced.keys.includes('ci_guardian:bisect') && applied(unplaced).kind !== 'apply_pr',
      `a pull request the classification does not place in the repository the word names is not applied: ${ended(unplaced)}`);

    // An answer decides when it names a pull request or says none. Any other ("not sure", both at once) decides
    // nothing: taken for "none", it would send the run to a fix of its own without a word. The run asks again.
    for (const [word, said] of [['not sure', '"not sure"'], [['none', '#50231'], '"none, #50231"'], ['#50231 if it is safe', '"#50231 if it is safe"'],
      ['no', '"no"']]) {
      const vague = await run({ mode: 'ci', task: 'c', answers: { apply_prs: word } }, classified({ existing_prs: [theirs] }));
      const vq = (vague.result.next.ask || [])[0] || {};
      check(vague.result.status === 'needs_input' && vague.result.stopped_at === 'Classify' && !vague.keys.includes('ci_guardian:bisect')
        && !vague.keys.includes('implementer:implement') && vq.field === 'apply_prs' && eqList(vq.options, ['#50231', 'none'])
        && (vq.question || '').includes(`${said} does not say which: it names no pull request, or one and "none" at once.`)
        && vague.result.next.rerun_with.answers.apply_prs === null,
        `an answer that names no pull request and does not say none (${said}) is asked about again: ${ended(vague)}, ${vq.question}`);
    }
    for (const word of ['none', 'None', '"none"', ['none']]) {
      const no = await run({ mode: 'ci', task: 'c', answers: { apply_prs: word } }, classified({ existing_prs: [theirs] }));
      check(no.result.status === 'ready_for_review' && no.keys.includes('ci_guardian:bisect') && applied(no).kind !== 'apply_pr',
        `${JSON.stringify(word)} is the word for a fix of the run's own: ${ended(no)}`);
    }
    // With no open pull request to choose from, such an answer has nothing to decide: the run fixes the failure.
    const nothing = await run({ mode: 'ci', task: 'c', answers: { apply_prs: 'not sure' } }, classified({}));
    check(nothing.result.status === 'ready_for_review' && nothing.keys.includes('ci_guardian:bisect') && !(nothing.result.next.ask || []).length,
      `where no pull request is open, nothing is asked: ${ended(nothing)}`);

    // Nor is one the classification found itself asked about when it is not open with a head.
    const stale = await run({ mode: 'ci', task: 'c' }, classified({ existing_prs: [{ ...theirs, state: 'closed' }, { ...mine, head: '' }] }));
    check(stale.result.status === 'ready_for_review' && stale.keys.includes('ci_guardian:bisect') && !(stale.result.next.ask || []).length,
      `nothing is asked about a pull request that cannot be applied: ${ended(stale)}`);

    // One pull request is verified in a run: several applied together would show nothing of each.
    const two = await run({ mode: 'ci', task: 'c', apply_prs: ['#50220', '#50231'] }, classified({ existing_prs: [mine, theirs,
      { pr: 'ROCm/aiter#812', author: 'bob', association: 'MEMBER', state: 'open', head: '8120001' }] }));
    check(applied(two).goal.includes('#50220') && !applied(two).goal.includes('#50231') && eqList(two.result.pr.comment_for, ['#50220'])
      && two.result.phases.some((p) => p.phase === 'Classify' && /Not verified in this run, which takes one: #50231/.test(p.summary))
      && two.result.phases.some((p) => p.phase === 'Classify' && p.summary.startsWith('ROCm/aiter#812: named as a fix, but not as a pull request of this repository')),
      `the first is verified, the others are named: ${applied(two).goal}`);
    const foreign = await run({ mode: 'ci', task: 'c' }, classified({ existing_prs: [{ pr: 'ROCm/aiter#812', author: 'bob', association: 'MEMBER',
      state: 'open', head: '8120001' }] }));
    check(foreign.result.status === 'ready_for_review' && foreign.keys.includes('ci_guardian:bisect') && applied(foreign).kind !== 'apply_pr',
      `one of another repository cannot be applied to this tree: the culprit is found and fixed as before: ${ended(foreign)}`);
    // None named: as before.
    for (const none of [{}, { existing_prs: [] }]) {
      const plain = await run({ mode: 'ci', task: 'c' }, classified(none));
      check(plain.keys.includes('ci_guardian:bisect') && plain.result.status === 'ready_for_review' && !plain.result.pr.comment_for,
        `no open fix (${JSON.stringify(none)}): the bisect runs`);
    }
    // What is handed over is handed over, whatever fix is named for it.
    const flaky = await run({ mode: 'ci', task: 'c', apply_prs: ['#50220'] }, classified({ failure_class: 'flaky', existing_prs: [mine] }));
    check(flaky.result.status === 'handed_off' && !flaky.keys.includes('implementer:implement'), `a flaky failure is handed over all the same: ${ended(flaky)}`);
    // A failure on a number: the pull request must restore it.
    const perf = await run({ mode: 'ci', task: 'c', apply_prs: ['#50220'] }, classified({ existing_prs: [mine], performance: true,
      perf_claim: 'output throughput back above 1100 tok/s' }));
    check(perf.result.status === 'ready_for_review' && perf.calls.find((c) => c.key === 'perf_engineer:perf_eval').ins.claim === 'output throughput back above 1100 tok/s',
      `a performance failure is judged on its number with the existing fix too: ${ended(perf)}`);
    // An existing fix has no Bisect: a stop named for it holds before the fix is applied or asked about, and no fix starts without a model.
    for (const word of [['#50220'], undefined]) {
      const skipped = await run({ mode: 'ci', task: 'c', model_path: '', stop_after: 'Bisect', ...(word ? { apply_prs: word } : {}) },
        classified({ existing_prs: [mine] }));
      check(skipped.result.status === 'stopped' && skipped.result.stopped_at === 'Classify' && !skipped.keys.includes('implementer:implement')
        && !(skipped.result.next.ask || []).length,
        `stop_after=Bisect holds before the existing fix is ${word ? 'applied' : 'asked about'}: ${ended(skipped)}`);
    }
    let classifiedOnce = false;
    const lifted = await run({ mode: 'ci', task: 'c', model_path: '', stop_after: 'Bisect', apply_prs: ['#50220'] }, {
      'ci_guardian:classify': () => {
        classifiedOnce = true;
        return { ...DEFAULTS['ci_guardian:classify'](), existing_prs: [mine] };
      },
    }, { control: () => (classifiedOnce ? { stop_after: '' } : {}) });
    check(lifted.result.status === 'needs_input' && lifted.result.stopped_at === 'Classify' && !lifted.keys.includes('implementer:implement')
      && (((lifted.result.next || {}).ask || [])[0] || {}).field === 'model_path',
      `a stop the control file lifts starts no existing fix without a model: ${ended(lifted)}`);
    const later = await run({ mode: 'ci', task: 'c', stop_after: 'Verify', apply_prs: ['#50220'] }, classified({ existing_prs: [mine] }));
    check(later.result.status === 'stopped' && later.result.stopped_at === 'Verify' && later.keys.includes('implementer:implement'),
      `with a later stop the existing fix is applied and verified: ${ended(later)}`);
  }],
  ['ci: code regression to a fix PR', async () => {
    const r = await run({ mode: 'ci', task: 'tracker cluster _check ImportError' });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Classify', 'Bisect', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && JSON.stringify(v.ins.extra_gates).includes('failing_test'), 'failing_test gate passed to verify');
  }],
  ['ci: a defect of an AMD component is drafted as an issue for that repository, and the lane is fixed as any other', async () => {
    const draft = `${RUN}/component_issue.md`;
    const filed = (r) => (r.result.next.user || []).find((x) => x.includes('component_issue.md'));
    const ciWps = [{ id: 'WP1', goal: 'pin aiter to the last version that passed', kind: 'framework', files: ['docker/rocm.Dockerfile'], test: 't', depends_on: [] }];
    const ci = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:bisect': () => ({ culprit: 'abc (#1)', confirmed: true, work_packages: ciWps, component: 'aiter', component_issue_path: draft }) });
    check(ci.result.status === 'ready_for_review' && ci.keys.includes('implementer:implement'), `a CI failure is still fixed: ${ci.result.status}`);
    check(/in aiter's repository/.test(filed(ci) || '') && ci.result.component_issue.component === 'aiter', `and the component's defect is drafted: ${filed(ci)}`);
    const ciBare = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:bisect': () => ({ culprit: 'abc (#1)', confirmed: true, work_packages: ciWps, component: 'aiter' }) });
    check(ciBare.result.status === 'blocked' && ciBare.result.stopped_at === 'Bisect', `without the draft the bisect is not done: ${ciBare.result.status}`);
    // A defect the lane cannot be fixed for here: the run stops, and the draft for the component is still the user's to file.
    const noFix = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:bisect': () => ({ culprit: 'the image of 11-25 (aiter 0.1.12)', confirmed: true, work_packages: [], component: 'aiter',
        component_issue_path: draft, evidence: 'fails with aiter 0.1.12, passes with 0.1.11; no tag of 0.1.11 for rocm10' }) });
    check(noFix.result.status === 'blocked' && noFix.result.stopped_at === 'Bisect' && !noFix.keys.includes('implementer:implement')
      && noFix.result.component_issue && noFix.result.component_issue.draft_path === draft && /in aiter's repository/.test(filed(noFix) || '')
      && noFix.result.component_issue.summary.includes('passes with 0.1.11'),
      `with no fix planned, the draft and its evidence survive: ${noFix.result.status}, ${filed(noFix)}`);
    const unsure = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:bisect': () => ({ culprit: 'abc (#1)', confirmed: false, work_packages: ciWps, component: 'aiter', component_issue_path: draft }) });
    check(unsure.result.status === 'blocked' && !unsure.result.component_issue, 'a culprit that is not confirmed names no component');
  }],
  ['ci: unconfirmed culprit blocks', async () => {
    const r = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:bisect': () => ({ culprit: 'maybe', confirmed: false, work_packages: [WP('WP1')] }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Bisect', `status ${r.result.status}`);
  }],
  ['ci: a regression without a failing test command stops before the bisect', async () => {
    const r = await run({ mode: 'ci', task: 'c' }, {
      'ci_guardian:classify': () => ({ failure_class: 'code_regression', failing_test_cmd: '', evidence: 'red since 10-21', triage_path: `${RUN}/ci.md` }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Classify', `status ${r.result.status}`);
    check(!r.keys.includes('ci_guardian:bisect'), 'no bisect without the failing test');
  }],
  ['ci: main is judged first, on the lane\'s own image: a test that passes there has nothing to fix, and a lane this machine cannot be is handed over', async () => {
    const classified = (out, drop = []) => ({ 'ci_guardian:classify': () => {
      const a = { ...DEFAULTS['ci_guardian:classify'](), ...out };
      for (const k of drop) delete a[k];
      return a;
    } });
    const ended = (r) => `${r.result.status} at ${r.result.stopped_at}`;
    const stale = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'passes',
      evidence: 'the job ran rocm720 of 11-17; the test passes on rocm720 of 11-25' }, ['failure_class']));
    check(stale.result.status === 'not_reproduced' && stale.result.stopped_at === 'Classify'
      && !stale.keys.includes('ci_guardian:bisect') && !stale.keys.includes('implementer:implement') && stale.keys.includes('director:report'),
      `a test that passes on main in the lane's newest image has nothing to fix: ${ended(stale)}`);
    check(stale.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'passes_on_main' && p.summary.includes('rocm720 of 11-25')),
      `the run record says which image it passes on: ${JSON.stringify(stale.result.phases.filter((p) => p.phase === 'Classify'))}`);
    check(!stale.keys.some((k) => k.startsWith('issue_triager:reply')) && !stale.result.reply_draft_path, 'and a ci run writes no reply');
    const merged = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'passes', fixed_by: '#50220',
      evidence: 'fails at the job\'s commit, passes on main' }));
    check(merged.result.status === 'fixed_on_main' && merged.result.fixed_by === '#50220'
      && merged.result.phases.some((p) => p.phase === 'Classify' && p.summary.startsWith('fixed by #50220: ')),
      `a fix that merged since is named: ${ended(merged)}, ${merged.result.fixed_by}`);
    const other = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'not_run',
      evidence: 'the lane is MI300X on rocm724; this machine is MI355X on rocm10' }));
    check(other.result.status === 'handed_off' && other.result.stopped_at === 'Classify' && !other.keys.includes('ci_guardian:bisect')
      && other.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'handed_off' && p.summary.includes(': the lane is MI300X')),
      `a lane this machine cannot be is handed over, with what it needs: ${ended(other)}`);
    for (const out of [{ on_main: 'fails' }, {}]) {
      const fails = await run({ mode: 'ci', task: 'c' }, classified(out));
      check(fails.result.status === 'ready_for_review' && fails.keys.includes('ci_guardian:bisect'),
        `a failure main shows there (${JSON.stringify(out)}) is classified and fixed as before: ${ended(fails)}`);
    }
    const noClass = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'fails' }, ['failure_class']));
    check(noClass.result.status === 'blocked' && noClass.result.stopped_at === 'Classify' && /has no class/.test(JSON.stringify(noClass.result.phases)),
      `a failure of main without a class stops before the bisect: ${ended(noClass)}`);
    // What only a fix needs is not asked of a failure that needs none: the answer may leave it out altogether.
    const bare = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'passes', performance: true }, ['failure_class', 'failing_test_cmd']));
    check(bare.result.status === 'not_reproduced' && !bare.calls.some((c) => c.key === 'ci_guardian:classify' && c.n > 1),
      `neither a class, a command to verify a fix with, nor a number to restore, and the answer is taken as it is: ${ended(bare)}`);
    const away = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'not_run', evidence: 'the lane is rocm724' }, ['failure_class', 'failing_test_cmd']));
    check(away.result.status === 'handed_off', `nor of a lane this machine cannot be: ${ended(away)}`);
    // A cluster is several tests on one lane or more: what passed says nothing of what was not run.
    const part = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'passes', unchecked: 'the two tests on rocm724',
      evidence: 'all five pass on rocm720 of 11-25' }, ['failure_class']));
    check(part.result.status === 'handed_off' && part.result.stopped_at === 'Classify' && !part.keys.includes('ci_guardian:bisect')
      && part.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'handed_off'
        && p.summary.includes('part of the task was not run: the two tests on rocm724') && p.summary.includes('all five pass')),
      `a task of which a part was not run is handed over, not called resolved: ${ended(part)}`);
    // A failure of the CI's own, or an intermittent one, is handed over whatever a rerun of the test shows here.
    for (const cls of ['infrastructure', 'flaky']) {
      for (const verdict of ['passes', 'not_run', 'fails']) {
        const own = await run({ mode: 'ci', task: 'c' }, classified({ on_main: verdict, failure_class: cls, failing_test_cmd: '',
          evidence: 'the runner lost communication at 02:14' }));
        check(own.result.status === 'handed_off' && own.result.stopped_at === 'Classify' && !own.keys.includes('ci_guardian:bisect')
          && own.result.phases.some((p) => p.phase === 'Classify' && p.verdict === cls && p.summary.includes('the runner lost communication')),
          `${cls} with main "${verdict}" is handed over with its class and evidence, not ended as nothing to fix: ${ended(own)}`);
      }
    }
    // Three passes here do not undo an intermittence the lane itself has shown on that commit and image: Classify
    // answers "fails" with the class flaky, its command and the lane's runs, and the run hands that over. It is
    // neither ended as nothing to fix nor bisected and fixed.
    const lane = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'fails', failure_class: 'flaky',
      evidence: 'passes 3 of 3 here; the lane ran commit 4f1c2aa on rocm720 of 11-25 five times: 2 failed, 3 passed' }));
    check(lane.result.status === 'handed_off' && lane.result.stopped_at === 'Classify' && !lane.keys.includes('ci_guardian:bisect')
      && !lane.keys.includes('implementer:implement')
      && lane.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'flaky' && p.summary.includes('2 failed, 3 passed')),
      `a test that passes here and is intermittent in the lane's own runs is handed over as flaky: ${ended(lane)}`);
    // The part not run is kept whole in the result however the run ends: a note keeps 600 characters of it.
    const long = `the tests on rocm724: ${'test_x, '.repeat(120)}and more`;
    for (const out of [{ on_main: 'passes' }, { on_main: 'not_run' }, { on_main: 'fails' }]) {
      const kept = await run({ mode: 'ci', task: 'c' }, classified({ ...out, unchecked: long }));
      const report = kept.calls.find((c) => c.key === 'director:report');
      check(kept.result.unchecked === long && report && report.ins.unchecked === long,
        `main "${out.on_main}": the part not run reaches the result and the report whole (${kept.result.unchecked.length} characters): ${ended(kept)}`);
    }
    // Where part fails and part was not run, what fails is fixed, and the run says what the fix does not answer for.
    const mixed = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'fails', unchecked: 'the two tests on rocm724' }));
    check(mixed.result.status === 'ready_for_review' && mixed.keys.includes('ci_guardian:bisect') && mixed.result.unchecked === 'the two tests on rocm724'
      && mixed.result.phases.some((p) => p.phase === 'Classify' && p.verdict === 'unchecked' && p.summary.includes('the two tests on rocm724')),
      `a failure of main with a part not run is fixed, and the part is kept in the run record and the result: ${ended(mixed)}`);
    const report = mixed.calls.find((c) => c.key === 'director:report');
    check(report && report.ins.unchecked === 'the two tests on rocm724', 'the report is written knowing it');
    // The failure came in after the task's base: a fix on that base would fix nothing.
    const later = await run({ mode: 'ci', task: 'c' }, classified({ on_main: 'not_run',
      evidence: 'fails on main at abc1234, passes on the task worktree at 0ld5ha: start the task on main' }, ['failure_class', 'failing_test_cmd']));
    check(later.result.status === 'handed_off' && !later.keys.includes('ci_guardian:bisect')
      && later.result.phases.some((p) => p.phase === 'Classify' && p.summary.includes('passes on the task worktree at 0ld5ha')),
      `a failure main shows and the task's own tree does not is not fixed on that tree: ${ended(later)}`);
    // Every test three times can outlast the call: it is told its limit, and what it did not reach is unchecked.
    const told = stale.calls.find((c) => c.key === 'ci_guardian:classify');
    check(told.ins.time_limit_min > 0 && !Number.isNaN(Date.parse(told.ins.deadline_utc)),
      `the classification is told its limit and when it ends: ${told.ins.time_limit_min} min, ${told.ins.deadline_utc}`);
    // A task that only asks whether main still fails stops at Classify, and needs neither a model nor a client.
    const ask = await run({ mode: 'ci', task: 'c', model_path: '', client_script: '', stop_after: 'Classify' },
      classified({ on_main: 'passes', evidence: 'passes on rocm720 of 11-25' }, ['failure_class', 'failing_test_cmd']));
    check(ask.result.status === 'not_reproduced' && !(ask.result.questions || []).length,
      `stop_after=Classify gets the verdict without a model or a client: ${ended(ask)}`);
  }],
  ['perf: a hand-off owner outside the list is rejected', async () => {
    const r = await run({ mode: 'perf', task: 'p' }, {
      'architect:perf_plan': () => ({ work_packages: [WP('WP1')], handoffs: [{ id: 'O2', owner: 'kernel-workflow' }], gate_ok: true }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Plan', `status ${r.result.status}`);
  }],
  ['perf: no measured win drops the change', async () => {
    const r = await run({ mode: 'perf', task: 'DeepSeek-V4.1 decode on MI355X' }, {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: false, held_out_ok: true, guards_ok: true, workload_unchanged: true, path_ok: true, summary: '-0.3% within noise' }),
    });
    check(r.result.status === 'dropped' && r.result.stopped_at === 'Performance', `status ${r.result.status}`);
    check(r.keys.includes('implementer:rollback'), 'change rolled back');
    check(r.result.handoffs.some((h) => h.owner === 'kernel_workflow'), 'kernel opportunity handed off');
  }],
  ['perf: a measurement not run because another job held a GPU drops nothing and judges nothing', async () => {
    const r = await run({ mode: 'perf', task: 'DeepSeek-V4.1 decode on MI355X' }, {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: false, held_out_ok: true, guards_ok: true, workload_unchanged: true, path_ok: true,
        not_run: 'GPU 5: pid 91234 holds 61440 MiB', summary: 'not run' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Performance', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(!r.keys.includes('implementer:rollback'), 'nothing is rolled back on a measurement that did not run');
    check(r.result.phases.some((p) => p.phase === 'Performance' && p.summary === 'not run: GPU 5: pid 91234 holds 61440 MiB'),
      'the note says who held which GPU');
  }],
  ['perf: a profile not run because another job used a GPU stops at Profile and names the job, not the serving path', async () => {
    const r = await run({ mode: 'perf', task: 'p' }, {
      'perf_engineer:profile': () => ({ analysis_path: '', path_ok: false, opportunities: [], not_run: 'GPU 3: VRAM 88%, GPU 100%, pid 4242' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Profile', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(r.result.phases.some((p) => p.phase === 'Profile' && p.summary === 'not run: GPU 3: VRAM 88%, GPU 100%, pid 4242'),
      `the note: ${JSON.stringify(r.result.phases.filter((p) => p.phase === 'Profile'))}`);
  }],
  ['feature: another job on a pinned GPU warns the user before the Verify round starts, and the round goes on', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:gpus': () => ({ in_use: 'GPU 1: VRAM 12%, GPU 40%, pid 777' }),
    });
    check(r.result.status === 'ready_for_review' && !r.keys.includes('investigator:investigate'), `status ${r.result.status}`);
    check(r.result.phases.some((p) => p.phase === 'Verify' && p.verdict === 'warning' && p.summary.includes('GPU 1: VRAM 12%')),
      'the warning is in the run record, for the report');
    const warned = r.lines.findIndex((l) => l.includes('warning: another job uses a pinned GPU before Verify round 0'));
    const started = r.lines.findIndex((l) => l.includes('verify_engineer#0'));
    check(warned >= 0 && started > warned, `in the log before the round starts: warning at ${warned}, round at ${started}`);
    check(eqList(r.keys.filter((k) => k.startsWith('verify_engineer:')), ['verify_engineer:gpus', 'verify_engineer:verify']),
      `one check per round: ${r.keys.filter((k) => k.startsWith('verify_engineer:'))}`);
  }],
  ['feature: a GPU check that cannot run stops Setup as blocked with its reason, not as a busy machine', async () => {
    const why = "ROCR_VISIBLE_DEVICES is set ('1'): it renumbers the GPUs every server sees; unset it where InferPilot runs";
    const r = await run({ mode: 'feature', task: 't' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: '', baseline_status: 'fail', baseline: why, gpu_ids: '' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Setup', `status ${r.result.status} at ${r.result.stopped_at}`);
    check(r.result.phases.some((p) => p.phase === 'Setup' && p.summary === why), 'the reason is kept');
    check((r.result.next || {}).say.includes('Fix what it names') && !(r.result.next || {}).rerun_with, `next: ${JSON.stringify(r.result.next)}`);
  }],
  ['perf: with held_out on, a gain that vanishes at the held-out operating point is dropped', async () => {
    const r = await run({ mode: 'perf', task: 'p', held_out: true }, {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: false, guards_ok: true, workload_unchanged: true, path_ok: true,
        summary: '+9% at c64, -0.2% at the held-out c32 / seed 2' }),
    });
    check(r.result.status === 'dropped' && r.result.stopped_at === 'Performance', `status ${r.result.status}`);
    check(r.keys.includes('implementer:rollback'), 'the fitted change is rolled back');
    check(r.result.phases.some((p) => p.phase === 'Performance' && p.summary.includes('held-out')), 'the report says why');
    const feature = await run({ mode: 'feature', task: 't', held_out: true }, {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: false, guards_ok: true, workload_unchanged: true, path_ok: true }),
    });
    check(feature.result.status === 'blocked' && feature.result.stopped_at === 'Performance', `feature: ${feature.result.status}`);
    const noClaim = await run({ mode: 'feature', task: 't' }, {
      'director:spec': () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true, perf_claim: '' }),
      'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: false, guards_ok: true, workload_unchanged: true, path_ok: true }),
    });
    check(noClaim.result.status === 'ready_for_review' && noClaim.keys.includes('perf_engineer:perf_eval'),
      `no claimed gain, held_out off: no slowdown reproduced is enough: ${noClaim.result.status}`);
  }],
  ['perf: held_out, guards, sweep and tune are off by default, and every role sees them', async () => {
    const vanish = {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: false, guards_ok: true, workload_unchanged: true, path_ok: true }),
    };
    const perf = await run({ mode: 'perf', task: 'p' }, vanish);
    check(perf.result.status === 'ready_for_review', `perf, held_out off: ${perf.result.status}`);
    const feature = await run({ mode: 'feature', task: 't' }, vanish);
    check(feature.result.status === 'ready_for_review', `feature, held_out off: ${feature.result.status}`);
    const pe = feature.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(pe && pe.ins.held_out === false && pe.ins.guards === false && pe.ins.sweep === false && pe.ins.tune === false,
      `off for the perf engineer: ${JSON.stringify(pe && [pe.ins.held_out, pe.ins.guards, pe.ins.sweep, pe.ins.tune])}`);
    const asked = await run({ mode: 'feature', task: 't', held_out: 'true', guards: true, sweep: true, tune: 'true' });
    const sp = asked.calls.find((c) => c.key === 'director:spec');
    check(sp && sp.ins.held_out === true && sp.ins.guards === true && sp.ins.sweep === true, 'on for the spec when the task sets them');
    const ar = asked.calls.find((c) => c.key === 'architect:design');
    check(ar && ar.ins.tune === true, 'tune reaches the architect, which adds a tuning package only then');
  }],
  ['feature: long_context and stability are off by default, and every role sees them', async () => {
    const plain = await run({ mode: 'feature', task: 't' });
    const off = ['director:spec', 'verify_engineer:verify', 'investigator:investigate', 'reviewer:review']
      .map((k) => plain.calls.find((c) => c.key === k)).filter(Boolean);
    check(off.length >= 3 && off.every((c) => c.ins.long_context === false && c.ins.stability === false),
      `off for every role: ${JSON.stringify(off.map((c) => [c.key, c.ins.long_context, c.ins.stability]))}`);
    check(!('long_context' in plain.result.task_def) && !('stability' in plain.result.task_def), 'and not in the definition the run records');
    const asked = await run({ mode: 'feature', task: 't', long_context: true, stability: 'true', client_script: '/s/client.sh',
      long_context_script: '/s/niah.sh' });
    const sp = asked.calls.find((c) => c.key === 'director:spec');
    const ve = asked.calls.find((c) => c.key === 'verify_engineer:verify');
    check(sp && sp.ins.long_context === true && sp.ins.stability === true, 'on for the spec when the task sets them');
    check(ve && ve.ins.long_context === true && ve.ins.stability === true, 'on for the verify engineer, which runs G3 and G4 only then');
    check(sp && sp.ins.long_context_script === '/s/niah.sh' && ve && ve.ins.long_context_script === '/s/niah.sh',
      `and both read the script G3 runs: ${JSON.stringify(ve && ve.ins.long_context_script)}`);
    const one = await run({ mode: 'feature', task: 't', stability: true, client_script: '/s/client.sh' });
    const ov = one.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ov && ov.ins.stability === true && ov.ins.long_context === false, 'each on its own');
  }],
  ['perf: measured win to a PR', async () => {
    const r = await run({ mode: 'perf', task: 'p', apply_prs: ['#46329'] });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Profile', 'Plan', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    const p = r.calls.find((c) => c.key === 'architect:perf_plan');
    check(p && eqList(p.ins.apply_prs, ['#46329']), 'apply_prs reach the planner');
  }],
  ['perf: only kernel-level opportunities are handed off', async () => {
    const r = await run({ mode: 'perf', task: 'p' }, {
      'architect:perf_plan': () => ({ work_packages: [], handoffs: [{ id: 'O2', owner: 'kernel_workflow' }], gate_ok: true }),
    });
    check(r.result.status === 'handed_off' && r.result.stopped_at === 'Plan', `status ${r.result.status}`);
  }],
  ['scan: collect and dispatch without code changes', async () => {
    const r = await run({ mode: 'scan', framework_dir: '' });
    check(r.result.status === 'queued', `status ${r.result.status}`);
    check(eqList(r.phases, ['Collect', 'Dispatch', 'Report']), `phases ${r.phases}`);
    check(!r.keys.some((k) => k.startsWith('implementer')), 'no implementer in scan');
    check(r.result.queue && r.result.queue[0].priority === 'P0', 'queue surfaced');
    const collect = r.calls.find((c) => c.key === 'scout:collect').ins;
    check(eqList(collect.sources, ['ci', 'issues', 'upstream']) && collect.rescan === false,
      `the daily report's sources by default, no rescan: ${collect.sources} ${collect.rescan}`);
    check(r.result.daily_report_url === '' && (r.result.next.user || []).some((u) => u.includes(`${RUN}/daily_report.md`)),
      `a daily report the director did not share is the user's to share: ${JSON.stringify(r.result.next.user)}`);
    const director = r.calls.find((c) => c.key === 'director:report');
    check(director.ins.share_daily_report === true, 'a queued scan has the director share its daily report');
    const contract = JSON.parse(/## Output contract[\s\S]*?```json\n([\s\S]*?)\n```/.exec(director.prompt)[1]).required;
    check(['daily_report_url', 'daily_report_action'].every((f) => contract.includes(f)),
      `a scan's report must say how its daily report went out, empty when it did not: ${contract}`);
    const url = 'https://github.com/At1a8/inferpilot/issues/7';
    const report = (action) => () => ({ report_path: `${RUN}/run_report.md`, summary: 'done', daily_report_url: url, daily_report_action: action });
    const shared = await run({ mode: 'scan', framework_dir: '' }, { 'director:report': report('created') });
    check(shared.result.daily_report_url === url && shared.result.next.say.includes(url)
      && !(shared.result.next.user || []).some((u) => u.includes('daily_report.md')),
      `one the director shared is named, and not asked of the user: ${JSON.stringify(shared.result.next)}`);
    const before = await run({ mode: 'scan', framework_dir: '' }, { 'director:report': report('exists') });
    check(before.result.next.say.includes('before this scan') && before.result.next.say.includes(`${RUN}/daily_report.md`)
      && !(before.result.next.user || []).some((u) => u.includes('daily_report.md')),
      `that day's report, shared before this scan, is named as such, and this scan's stays a file: ${before.result.next.say}`);
    // A scan names what it found, not what the user allows: the pull request a ci run may apply is not offered
    // with the task, so that the run started from it asks. A perf task's candidates are the task itself.
    const offers = await run({ mode: 'scan', framework_dir: '' }, { 'dispatcher:dispatch': () => ({ queue_path: `${RUN}/task_queue.md`, matrix_changes: 0, top: [
      { rank: 1, task: 'rocm720 unit lane red', type: 'ci', priority: 'P0', source: 'ci',
        task_def: { mode: 'ci', task: 'https://github.com/sgl-project/sglang/actions/runs/1', framework_dir: '/sgl-workspace/sglang', apply_prs: ['#50231'],
          // what is no field of a task definition: an answer or a request here would stand for the user's word in the run it starts
          answers: { apply_prs: '#50231' }, request: 'the red lane; verify 50231 for it', approved_by: 'the scan' } },
      { rank: 2, task: 'A/B two decode PRs', type: 'perf', priority: 'P2', source: 'upstream',
        task_def: { mode: 'perf', task: 'decode throughput', framework_dir: '/sgl-workspace/sglang', apply_prs: ['#46329'] } }] }) });
    const [ciOffer, perfOffer] = offers.result.next.start || [];
    check(ciOffer && ciOffer.args.mode === 'ci' && !('apply_prs' in ciOffer.args) && ciOffer.args.framework_dir === '/sgl-workspace/sglang'
      && perfOffer && eqList(perfOffer.args.apply_prs, ['#46329']),
      `a ci task a scan offers names no pull request to apply: ${JSON.stringify(offers.result.next.start)}`);
    check(eqList(Object.keys(ciOffer.args).sort(), ['framework_dir', 'inferpilot_dir', 'mode', 'task']),
      `and carries the fields of a task definition and nothing else, no answer and no request, so that the run it starts asks (the ci scenario): ${
        JSON.stringify(Object.keys(ciOffer.args))}`);
    const failed = await run({ mode: 'scan', framework_dir: '' }, { 'dispatcher:dispatch': () => null });
    check(failed.result.status === 'blocked' && failed.result.stopped_at === 'Dispatch'
      && failed.calls.find((c) => c.key === 'director:report').ins.share_daily_report === false,
      `a scan whose Dispatch failed shares nothing: ${failed.result.status} at ${failed.result.stopped_at}`);
    const late = await run({ mode: 'scan', framework_dir: '', agent_timeout_ms: 100 }, {
      'director:report': async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        return report('created')();
      },
    });
    check((late.result.next.user || []).some((u) => u.includes('may still share') && u.includes(`${RUN}/daily_report.md`)),
      `a director that timed out may still share the report: the user waits for it first: ${JSON.stringify(late.result.next.user)}`);
  }],
  ['scan: a report from the last 24 hours makes the run ask first; rescan=true scans again', async () => {
    const recent = (ins) => (ins.rescan
      ? { lock: 'acquired', run_dir: RUN, candidates_path: `${RUN}/candidates.json`, counts: { ci: 5, issues: 30, upstream: 40 } }
      : { lock: 'skipped', run_dir: '', candidates_path: '',
        recent: { at: '2026-11-09T01:12:00Z', url: 'https://github.com/At1a8/inferpilot/issues/3', run_dir: '' } });
    const asked = await run({ mode: 'scan' }, { 'scout:collect': recent });
    check(asked.result.status === 'needs_input' && asked.result.stopped_at === 'Collect' && eqList(asked.keys, ['scout:collect']),
      `asks before scanning again: ${asked.result.status}, calls ${asked.keys}`);
    check(asked.result.questions[0].field === 'rescan' && asked.result.questions[0].question.includes('issues/3'),
      'the question names the recent report');
    const again = await run({ mode: 'scan', answers: { rescan: true } }, { 'scout:collect': recent });
    check(again.result.status === 'queued' && again.calls.find((c) => c.key === 'scout:collect').ins.rescan === true,
      `answers={"rescan": true} scans again: ${again.result.status}`);
    const vllm = [{ name: 'vllm', url: 'https://github.com/vllm-project/vllm' }];
    const more = await run({ mode: 'scan', upstream_repos: vllm });
    check(more.calls.find((c) => c.key === 'scout:collect').ins.upstream_repos[0].name === 'vllm', 'upstream_repos reach the scout');
  }],
  ['kernel: the default back end records hand-offs and runs nothing', async () => {
    const r = await run({ mode: 'feature', task: 't' }, withKernelHandoff('decode hot path'));
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(r.result.handoffs.length === 1 && r.result.handoffs[0].status === 'recorded', `handoffs ${JSON.stringify(r.result.handoffs)}`);
    check(!r.keys.some((k) => k.startsWith('kernel_liaison')) && !r.phases.includes('Kernel'), 'no liaison, no Kernel phase');
    check(r.calls.filter((c) => !c.key.startsWith('intake')).every((c) => c.ins.kernel_backend === 'none'), 'every role sees the back end');
  }],
  ['kernel: a command back end runs the hand-off through the liaison and the gain is integrated', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD, gpu_ids: '3' }, withKernelHandoff('decode hot path'));
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases.slice(4, 8), ['Implement', 'Kernel', 'Implement', 'Verify']), `phases ${r.phases}`);
    const h = r.result.handoffs[0];
    check(h && h.id === 'K-WP1' && h.status === 'integrated', `handoff ${JSON.stringify(h)}`);
    const runCall = r.calls.find((c) => c.key === 'kernel_liaison:run');
    check(runCall && runCall.ins.task_dir === `${RUN}/kernel/K-WP1/task` && runCall.ins.kernel_backend_command.includes('{task_dir}')
      && String(runCall.ins.gpu_ids) === '3', `the liaison gets the task, the command and the GPUs: ${runCall && JSON.stringify(runCall.ins)}`);
    const col = r.calls.find((c) => c.key === 'kernel_liaison:collect');
    check(col && col.ins.backend_result.exit_code === 0, 'the run record reaches collect');
    check(r.result.done.map((d) => d.wp).includes('K-K-WP1'), 'integration package verified by the test engineer');
  }],
  ['kernel: the geak interface runs a GEAK checkout as an outside program through the liaison', async () => {
    const r = await run({ mode: 'feature', task: 't', kernel_backend: 'geak', kernel_backend_dir: '/opt/GEAK/', gpu_ids: '3' },
      withKernelHandoff('decode hot path'));
    check(r.result.status === 'ready_for_review' && r.result.handoffs[0].status === 'integrated', `status ${r.result.status}`);
    const pkg = r.calls.find((c) => c.key === 'kernel_liaison:package');
    check(pkg && pkg.ins.kernel_backend === 'geak' && pkg.ins.kernel_backend_dir === '/opt/GEAK', 'the package phase writes GEAK lane arguments');
    const runCall = r.calls.find((c) => c.key === 'kernel_liaison:run');
    check(runCall && runCall.ins.kernel_backend_command === 'node /opt/GEAK/interface/runtime/engine/run_workflow.mjs '
      + '/opt/GEAK/kernel_workflow/kernel_lane.js --args-file {task_dir}/lane_args.json --result-file {out_dir}/result.json',
    `the default GEAK command: ${runCall && runCall.ins.kernel_backend_command}`);
    const own = await run({ mode: 'feature', task: 't', kernel_backend: 'geak', kernel_backend_dir: '/opt/GEAK', kernel_backend_command: 'geak-lane {task_dir} {out_dir}' },
      withKernelHandoff('decode hot path'));
    check(own.calls.find((c) => c.key === 'kernel_liaison:run').ins.kernel_backend_command === 'geak-lane {task_dir} {out_dir}', 'a given command replaces it');
    const plain = await run({ mode: 'feature', task: 't', ...CMD }, withKernelHandoff('decode hot path'));
    check(plain.calls.every((c) => !c.ins.kernel_backend_dir), 'no GEAK path reaches a role unless geak is chosen');
  }],
  ['kernel: an integrated kernel that fails Verify goes back to the functional kernel and is retried once with the failing case', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD, gpu_ids: '3' }, {
      ...withKernelHandoff('decode hot path'),
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: false, failures: [{ gate: 'G3' }] } : { all_pass: true, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-WP1'],
        counterexample: 'G3 needle at 128k: wrong token at step 7, bf16, TP4', summary: 'goes away with K-K-WP1 reverted' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const inv = r.calls.find((c) => c.key === 'investigator:investigate');
    const ik = inv ? inv.ins.integrated_kernels : [];
    check(ik.length === 1 && ik[0].handoff === 'K-WP1' && ik[0].commit === 'c_K-K-WP1', `the investigator sees the integrated kernel: ${JSON.stringify(ik)}`);
    const revert = r.calls.find((c) => c.key === 'implementer:rollback');
    check(revert && revert.ins.revert === 'c_K-K-WP1', `only the kernel's commit is reverted: ${revert && revert.ins.revert}`);
    const packages = r.calls.filter((c) => c.key === 'kernel_liaison:package');
    check(packages.length === 2 && String(packages[1].ins.handoff.counterexamples).includes('needle'), 'the retry carries the failing case');
    const h = r.result.handoffs[0];
    check(h.status === 'integrated' && h.retries === 1, `handoff ${JSON.stringify(h)}`);
    check(r.keys.filter((k) => k === 'verify_engineer:verify').length === 2, 'verified again after the retry');
  }],
  ['kernel: a kernel that fails Verify again after its retry is rejected; the functional kernel stays', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD, gpu_ids: '3' }, {
      ...withKernelHandoff('decode hot path'),
      'verify_engineer:verify': (ins, n) => (n <= 2 ? { all_pass: false, failures: [{ gate: 'G3' }] } : { all_pass: true, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-WP1'], counterexample: 'G3 needle at 128k' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const h = r.result.handoffs[0];
    check(h.status === 'rejected' && h.counterexamples.length === 2, `handoff ${JSON.stringify(h)}`);
    check(r.keys.filter((k) => k === 'kernel_liaison:package').length === 2, 'one retry only');
    check(r.keys.filter((k) => k === 'implementer:rollback').length === 2 && !r.result.done.some((d) => d.wp === 'K-K-WP1'),
      'both integrations reverted; the functional kernel stays');
  }],
  ['kernel: a kernel_regression that names no integrated kernel stops', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': () => ({ all_pass: false, failures: [{ gate: 'G2' }] }),
      'investigator:investigate': () => ({ verdict: 'kernel_regression', kernel_handoffs: ['K-X'], counterexample: 'c' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Verify', `status ${r.result.status}`);
    check(!r.keys.includes('implementer:rollback'), 'nothing reverted');
  }],
  ['kernel: a hand-off raised by a fix reaches the Kernel phase', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'verify_engineer:verify': (ins, n) => (n === 1 ? { all_pass: false, failures: [{ gate: 'G2' }] } : { all_pass: true, failures: [] }),
      'investigator:investigate': () => ({ verdict: 'fix_needed', work_packages: [WP('FIX1', { kind: 'kernel' })] }),
      'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
        kernel_handoff: ins.work_package.id === 'FIX1' ? { op_spec_path: `${RUN}/handoffs/K-FIX1/op_spec.md`, reason: 'the fixed kernel is slow' } : null }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const h = r.result.handoffs.find((x) => x.id === 'K-FIX1');
    check(h && h.status === 'recorded', `the fix's hand-off reached the Kernel phase: ${JSON.stringify(h)}`);
  }],
  ['kernel: a failing back end does not block the task', async () => {
    const r = await run({ mode: 'feature', task: 't', ...CMD }, withKernelHandoff('FAIL'));
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(r.result.handoffs[0].status === 'failed', `handoff ${JSON.stringify(r.result.handoffs[0])}`);
    check(!r.result.done.some((d) => d.wp.startsWith('K-')), 'nothing integrated');
    const silent = await run({ mode: 'feature', task: 't', ...CMD }, { ...withKernelHandoff('decode hot path'), 'kernel_liaison:run': () => null });
    const col = silent.calls.find((c) => c.key === 'kernel_liaison:collect');
    check(silent.result.status === 'ready_for_review' && col && /did not run/.test(col.ins.backend_result.error),
      `a run that returns nothing is a back end that did not run: ${silent.result.status}`);
  }],
  ['kernel: a perf plan with only kernel hand-offs runs on the selected back end', async () => {
    const plan = { 'architect:perf_plan': () => ({ work_packages: [], handoffs: [{ id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/handoffs/O2/op_spec.md` },
      { id: 'O3', owner: 'hyperloom' }], perf_claim: '-6% TPOT', gate_ok: true }) };
    const r = await run({ mode: 'perf', task: 'p', ...CMD }, plan);
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Profile', 'Plan', 'Kernel', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    const byId = Object.fromEntries(r.result.handoffs.map((h) => [h.id, h.status]));
    check(byId.O2 === 'integrated' && byId.O3 === 'recorded', `statuses ${JSON.stringify(byId)}`);
    const none = await run({ mode: 'perf', task: 'p', ...CMD },
      { ...plan, 'kernel_liaison:collect': () => ({ status: 'no_gain', speedup: 1.01, integrate_wp: null }) });
    check(none.result.status === 'no_gain' && none.result.stopped_at === 'Kernel', `no gain: ${none.result.status}`);
  }],
  ['kernel: back-end arguments are checked before anything starts', async () => {
    check(asks(await run({ mode: 'feature', task: 't', kernel_backend: 'triton-magic' }), 'kernel_backend'), 'unknown back end asked');
    check(asks(await run({ mode: 'feature', task: 't', kernel_backend: 'geak' }), 'kernel_backend_dir'), 'geak without its checkout asked');
    check(asks(await run({ mode: 'feature', task: 't', kernel_backend: 'command' }), 'kernel_backend_command'), 'command without a command asked');
  }],
  ['review: requested changes are implemented, verified, measured and reviewed again', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'reviewer:review': (ins, n) => (n === 1
        ? { verdict: 'changes_requested', findings: [{ severity: 'major', where: 'x.py:9', issue: 'breaks TP=8 with DP attention' },
          { severity: 'blocker', where: 'x.py:40', issue: 'fast path only for batch 64', hack: 'P7' }],
          work_packages: [WP('R1')], review_path: `${RUN}/review.md` }
        : { verdict: 'approve', findings: [], review_path: `${RUN}/review.md` }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases.slice(6), ['Performance', 'Review', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    check(r.result.done.map((d) => d.wp).includes('R1'), 'the review fix passed the test engineer');
    const second = r.calls.filter((c) => c.key === 'reviewer:review')[1];
    check(second && second.ins.round === 1, 'the second review knows its round');
  }],
  ['review: a blocked review, or an approval with a blocker, stops before the PR', async () => {
    const blocked = await run({ mode: 'ci', task: 'c' }, {
      'reviewer:review': () => ({ verdict: 'blocked', findings: [{ severity: 'blocker', issue: 'changes a shared CUDA path' }], review_path: `${RUN}/review.md` }),
    });
    check(blocked.result.status === 'blocked' && blocked.result.stopped_at === 'Review', `blocked: ${blocked.result.status} at ${blocked.result.stopped_at}`);
    check(!blocked.keys.includes('pr_engineer:pr'), 'no PR after a blocked review');
    const odd = await run({ mode: 'ci', task: 'c' }, {
      'reviewer:review': () => ({ verdict: 'approve', findings: [{ severity: 'blocker', issue: 'x' }], review_path: `${RUN}/review.md` }),
    });
    check(odd.result.status === 'blocked' && odd.result.stopped_at === 'Review', 'an approval with a blocker is not an approval');
  }],
  ['review: the review rounds are bounded', async () => {
    const r = await run({ mode: 'ci', task: 'c', max_review_rounds: 1 }, {
      'reviewer:review': (ins, n) => ({ verdict: 'changes_requested', findings: [{ severity: 'major', issue: 'again' }],
        work_packages: [WP(`R${n}`)], review_path: `${RUN}/review.md` }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Review', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'reviewer:review').length === 2, 'max_review_rounds + 1 reviews');
  }],
  ['review: review_model reaches the reviewer only', async () => {
    const r = await run({ mode: 'ci', task: 'c', review_model: 'another-model' });
    const reviews = r.calls.filter((c) => c.key === 'reviewer:review');
    check(reviews.length === 1 && reviews.every((c) => c.model === 'another-model'), `reviewer model: ${reviews.map((c) => c.model)}`);
    check(r.calls.filter((c) => c.key !== 'reviewer:review').every((c) => !c.model), 'the other roles keep the session model');
  }],
  ['perf: a workload and a model are required', async () => {
    check(asks(await run({ mode: 'perf', task: 'p', workload: '' }), 'workload'), 'perf without a workload asked');
    check(asks(await run({ mode: 'perf', task: 'p', model_path: '' }), 'model_path'), 'perf without a model asked');
    check(asks(await run({ mode: 'feature', task: 't', model_path: '' }), 'model_path'), 'feature without a model asked');
    check(asks(await run({ mode: 'feature', task: 't', server_script: '' }), 'server_script'), 'feature without a server script asked');
    check(asks(await run({ mode: 'perf', task: 'p', client_script: '' }), 'client_script'), 'perf without a client script asked');
    check(asks(await run({ mode: 'feature', task: 't', workload: '{"isl":8000}', client_script: '' }), 'client_script'),
      'a feature with a workload but no client script asked');
    check(asks(await run({ mode: 'feature', task: 't', client_script: '' }), 'client_script'),
      'and a feature without a workload: G5 measures every feature, so each names the client that measures');
    const plain = await run({ mode: 'feature', task: 't' });
    const setupIns = plain.calls.find((c) => c.key === 'director:setup');
    check(setupIns && setupIns.ins.server_script === '/s/server.sh' && setupIns.ins.client_script === '/s/client.sh',
      'the server and the client scripts reach the roles');
  }],
  ['feature: InferPilot\'s templates run only where the user took them, and its client never stands for the source\'s gain', async () => {
    const question = (r, field) => (r.result.questions || []).find((q) => q.field === field) || {};
    const none = await run({ mode: 'feature', task: 't', workload: '{"isl":8000}', server_script: '', client_script: '' });
    check(asks(none, 'server_script') && eqList(question(none, 'server_script').options, ['template'])
      && eqList(question(none, 'client_script').options, ['template'])
      && /never stands for the gain of the feature's source: on it G5 judges only that the feature is no slower/.test(question(none, 'client_script').question),
      `the questions offer the templates, the client's as no claim of the source's gain: ${JSON.stringify(none.result.questions)}`);
    // InferPilot's client runs the task's workload: without one G5 would judge the feature on lengths nobody chose.
    const bare = await run({ mode: 'feature', task: 't', client_script: 'template' });
    check(asks(bare, 'workload') && eqList((bare.result.questions || []).map((q) => q.field), ['workload']),
      `a feature that takes InferPilot's client without a workload is asked for the workload: ${JSON.stringify((bare.result.questions || []).map((q) => q.field))}`);
    const rendered = { server_script: `${RUN}/scripts/server.sh`, client_script: `${RUN}/scripts/perf.sh`,
      accuracy_script: `${RUN}/scripts/accuracy.sh` };
    const setup = (out) => ({ 'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', branch: 'inferpilot/task',
      worktree: `${RUN}/wt`, gpu_ids: '0,1', ...out }) });
    const whole = '{"isl":8000,"osl":1000,"conc":32}';
    const took = await run({ mode: 'feature', task: 't', workload: whole, server_script: 'template', client_script: 'template',
      harness: { CONTEXT_LENGTH: 8192 } }, setup(rendered));
    const setupIns = took.calls.find((c) => c.key === 'director:setup');
    check(setupIns && setupIns.ins.server_script === 'template' && setupIns.ins.client_script === 'template'
      && (typeof setupIns.ins.harness === 'string' ? JSON.parse(setupIns.ins.harness) : setupIns.ins.harness || {}).CONTEXT_LENGTH === 8192,
      `Setup renders what the user took, with the task's values: ${JSON.stringify(setupIns && setupIns.ins.harness)}`);
    const perf = took.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(took.result.status === 'ready_for_review' && perf && perf.ins.server_script === rendered.server_script
      && perf.ins.client_script === rendered.client_script && perf.ins.accuracy_script === rendered.accuracy_script
      && /^no gain is claimed: no slower than the baseline/.test(perf.ins.claim),
      `the rendered scripts reach the performance engineer, and on InferPilot's client G5 judges no slowdown, not the spec's gain: ${
        took.result.status}, claim ${perf && perf.ins.claim}`);
    const own = await run({ mode: 'feature', task: 't', accuracy_script: '/s/accuracy.sh' }, setup(rendered));
    const ownIns = own.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ownIns && ownIns.ins.server_script === '/s/server.sh' && ownIns.ins.client_script === '/s/client.sh'
      && ownIns.ins.accuracy_script === '/s/accuracy.sh', 'the task\'s own scripts win over rendered ones');
    const unrendered = await run({ mode: 'feature', task: 't', server_script: 'template' }, setup({}));
    check(unrendered.result.status === 'blocked' && unrendered.result.stopped_at === 'Setup'
      && !unrendered.keys.includes('director:spec'), `a template Setup did not render stops the run: ${unrendered.result.status}`);
    const noAccuracy = await run({ mode: 'feature', task: 't' }, setup({}));
    check(noAccuracy.result.status === 'blocked' && noAccuracy.result.stopped_at === 'Setup'
      && /no accuracy script/.test(JSON.stringify(noAccuracy.result.phases)), `no accuracy script stops the run at Setup: ${noAccuracy.result.status}`);
    // The whole of the load is the task's: what a workload leaves out is asked for, and what harness gives counts.
    const part = await run({ mode: 'feature', task: 't', client_script: 'template', workload: '{"isl":8000}' });
    const pq = (part.result.questions || []).find((q) => q.field === 'workload') || {};
    check(asks(part, 'workload') && /gives no output length and no concurrency for it/.test(pq.why || ''),
      `a workload that gives only its input length is asked for again, and the question says what it lacks: ${JSON.stringify(pq.why)}`);
    const again = (part.result.next || {}).rerun_with || {};
    check(again.workload === '' && 'workload' in (again.answers || {}),
      `the answer takes the place of the part the task gave, which would otherwise win over it: ${JSON.stringify(again)}`);
    const answered = await run({ mode: 'feature', task: 't', client_script: 'template', workload: '{"isl":8000}', ...again,
      answers: { workload: whole } }, setup(rendered));
    check(answered.result.status !== 'needs_input' && answered.result.task_def.workload === whole,
      `and with the answer the run starts on it: ${answered.result.status}, ${JSON.stringify(answered.result.task_def.workload)}`);
    const byHarness = await run({ mode: 'feature', task: 't', client_script: 'template', workload: '{"isl":8000}',
      harness: { OSL: 256, CONCURRENCY: 1 } }, setup(rendered));
    check(byHarness.result.status !== 'needs_input', `a length and a concurrency the task gives in harness count: ${byHarness.result.status}`);
    const words = await run({ mode: 'feature', task: 't', client_script: 'template', workload: '8k in, 1k out, c64' }, setup(rendered));
    check(words.result.status !== 'needs_input', `a workload in words is the director's to turn into the three: ${words.result.status}`);
    const ownClient = await run({ mode: 'feature', task: 't', workload: '{"isl":8000}' });
    check(ownClient.result.status !== 'needs_input', `the task's own client runs what it runs, whatever the workload names: ${ownClient.result.status}`);
    const gap = await run({ mode: 'feature', task: 't', workload: whole, client_script: 'template' }, {
      ...setup(rendered),
      'director:spec': () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true, perf_claim: '+30% output tok/s',
        workload_gap: "vLLM's PR measured 256-token answers at concurrency 1; the task's workload is 8k in." }),
    });
    check(gap.result.status === 'needs_input' && gap.result.stopped_at === 'Spec' && !gap.keys.includes('analyst:recon'),
      `InferPilot's client on the task's workload still asks for the source's scenarios: ${gap.result.status} at ${gap.result.stopped_at}`);
  }],
  ['perf: a perf task may take InferPilot\'s templates, and its numbers on them count', async () => {
    const question = (r, field) => (r.result.questions || []).find((q) => q.field === field) || {};
    const none = await run({ mode: 'perf', task: 'p', server_script: '', client_script: '' });
    check(asks(none, 'server_script') && eqList(question(none, 'server_script').options, ['template'])
      && eqList(question(none, 'client_script').options, ['template'])
      && !/never stands for/.test(question(none, 'client_script').question),
      `both questions offer the templates, the client's with no caveat: ${JSON.stringify(none.result.questions)}`);
    const r = await run({ mode: 'perf', task: 'p', server_script: 'template', client_script: 'template' }, {
      'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', branch: 'inferpilot/task', worktree: `${RUN}/wt`,
        gpu_ids: '0,1', server_script: `${RUN}/scripts/server.sh`, client_script: `${RUN}/scripts/perf.sh`,
        accuracy_script: `${RUN}/scripts/accuracy.sh` }),
    });
    const perf = r.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(r.result.status === 'ready_for_review' && perf && perf.ins.server_script === `${RUN}/scripts/server.sh`
      && perf.ins.client_script === `${RUN}/scripts/perf.sh` && perf.ins.claim,
      `on InferPilot's server and client, the planned gain is the claim: ${r.result.status}, ${JSON.stringify(perf && perf.ins.claim)}`);
    // InferPilot's client runs the whole of the task's workload: what it leaves out is asked for, never the template's own.
    const part = await run({ mode: 'perf', task: 'p', client_script: 'template', workload: '{"isl":8000,"osl":1000}' });
    const pq = (part.result.questions || []).find((q) => q.field === 'workload') || {};
    check(asks(part, 'workload') && eqList((part.result.questions || []).map((q) => q.field), ['workload'])
      && /gives no concurrency for it/.test(pq.why || ''),
    `a perf task that takes InferPilot's client with a part of its workload is asked for the rest: ${JSON.stringify(pq.why)}`);
    const own = await run({ mode: 'perf', task: 'p', workload: '{"isl":8000,"osl":1000}' });
    check(own.result.status !== 'needs_input', `with its own client the workload is as the task gives it: ${own.result.status}`);
  }],
  ['perf: a profile of the wrong path stops before any plan', async () => {
    const r = await run({ mode: 'perf', task: 'p' }, {
      'perf_engineer:profile': () => ({ analysis_path: `${RUN}/perf_analysis.md`, path_ok: false,
        path_evidence: 'intended aiter attention, the log shows the triton fallback', opportunities: [] }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Profile', `status ${r.result.status}`);
    check(!r.keys.includes('architect:perf_plan'), 'no plan from an untrusted profile');
  }],
  ['perf: a measurement off the fixed workload is not evidence and drops nothing', async () => {
    const r = await run({ mode: 'perf', task: 'p' }, {
      'perf_engineer:perf_eval': () => ({ claim_reproduced: false, held_out_ok: true, guards_ok: true, workload_unchanged: false, path_ok: true, summary: 'c64 dropped' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Performance', `status ${r.result.status}`);
    check(!r.keys.includes('implementer:rollback'), 'an untrusted measurement rolls nothing back');
  }],
  ['routes: the route each mode logs is what its straight path walks, and what both READMEs list', async () => {
    // A README's Modes table, the one whose header begins "| Mode |" ("| 模式 |"): per mode, each step of its
    // Phases column by name, and whether it runs only when it applies, "(Kernel)".
    const table = (file) => {
      const out = {};
      const lines = readFileSync(path.join(INFERPILOT, file), 'utf8').split('\n');
      for (let k = lines.findIndex((l) => /^\| (Mode|模式) \|/.test(l)) + 2; k > 1 && lines[k] && lines[k].startsWith('|'); k++) {
        const [, mode, , phases] = lines[k].split('|').map((c) => c.trim());
        out[mode.replace(/`/g, '')] = phases.split('→').map((t) => t.trim())
          .map((step) => ({ name: (/[A-Za-z]+/.exec(step) || [''])[0], optional: /^[(（]/.test(step) }));
      }
      return out;
    };
    const en = table('README.md');
    const zh = table('README.zh-CN.md');
    const always = (steps) => (steps || []).filter((s) => !s.optional).map((s) => s.name);
    const modes = JSON.parse((/^const MODES = (\[[^\]]*\]);/m.exec(readFileSync(SCRIPT, 'utf8')) || [, '[]'])[1].replace(/'/g, '"'));
    check(eqList(Object.keys(en), modes) && eqList(Object.keys(zh), modes), `MODES ${modes} | README.md ${Object.keys(en)} | README.zh-CN.md ${Object.keys(zh)}`);
    for (const mode of modes) {
      check(JSON.stringify(en[mode]) === JSON.stringify(zh[mode]), `${mode}: the two READMEs list other steps: ${JSON.stringify(en[mode])} | ${JSON.stringify(zh[mode])}`);
    }
    const straight = {
      feature: { mode: 'feature', task: 'port HiSparse V2 to DeepSeek-V4.1' },
      issue: { mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' },
      ci: { mode: 'ci', task: 'tracker cluster _check ImportError' },
      perf: { mode: 'perf', task: 'p', apply_prs: ['#46329'] },
      scan: { mode: 'scan', framework_dir: '' },
    };
    for (const mode of modes) check(straight[mode], `${mode}: the routes check has no straight path for it`);
    for (const [mode, args] of Object.entries(straight)) {
      const r = await run(args);
      check(r.route.length >= 3 && eqList(r.route, r.phases), `${mode}: route ${r.route} | walked ${r.phases}`);
      check(eqList(always(en[mode]), r.route), `${mode}: README.md lists ${always(en[mode])} | route ${r.route}`);
    }
  }],
  ['roles: every role and PHASE inferpilot.js calls is documented', async () => {
    const src = readFileSync(SCRIPT, 'utf8');
    const sites = [...src.matchAll(CALL_SITE)].map((m) => [m[1], m[2]]);
    check(sites.length >= 20, `found ${sites.length} call sites`);
    for (const [role, ph] of sites) {
      const f = path.join(INFERPILOT, 'roles', `${role}.md`);
      const doc = existsSync(f) ? readFileSync(f, 'utf8') : '';
      check(phaseSection(doc, ph), `roles/${role}.md documents PHASE=${ph}`);
      check(DEFAULTS[`${role}:${ph}`] !== undefined, `dry run scripts an answer for ${role}:${ph}`);
    }
  }],
  ['roles: every knowledge file, template and script a role names exists', async () => {
    const refs = new Set();
    for (const f of readdirSync(path.join(INFERPILOT, 'roles'))) {
      const text = readFileSync(path.join(INFERPILOT, 'roles', f), 'utf8');
      for (const m of text.matchAll(/`((?:knowledge|templates|scripts)\/[A-Za-z0-9_./-]+\.(?:md|py|sh))`/g)) refs.add(m[1]);
    }
    check(refs.size >= 20, `found ${refs.size} references`);
    for (const ref of refs) check(existsSync(path.join(INFERPILOT, ref)), `${ref} exists`);
  }],
  ['skills: every role in the framework-skills index reads it', async () => {
    const index = readFileSync(path.join(INFERPILOT, 'knowledge', 'frameworks', 'sglang', 'skills.md'), 'utf8');
    const roles = [...new Set([...index.matchAll(/^\| ([a-z_]+) \| [a-z_, ]+ \|/gm)].map((m) => m[1]))].filter((r) => r !== 'role');
    check(roles.length >= 10, `index rows for ${roles.length} roles`);
    for (const role of roles) {
      const f = path.join(INFERPILOT, 'roles', `${role}.md`);
      check(existsSync(f) && readFileSync(f, 'utf8').includes('frameworks/<framework>/skills.md'), `roles/${role}.md points to the index`);
    }
  }],
  ['schemas: a malformed answer is rejected and the run stops cleanly', async () => {
    const r = await run({ mode: 'issue', task: 'i' }, {
      'issue_triager:triage': () => ({ priority: 'P5', action: 'maybe', triage_path: `${RUN}/t.md` }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Triage', `status ${r.result.status}`);
  }],
  ['arguments: an invalid or incomplete definition comes back as questions', async () => {
    check(asks(await run({ mode: 'magic', task: 't' }), 'mode'), 'unknown mode asked');
    check(asks(await run({ mode: 'issue' }), 'task'), 'missing task asked');
    check(asks(await run({ mode: 'issue', task: 't', framework: 'vllm', framework_dir: '' }), 'framework_dir'), 'missing checkout asked');
    check(asks(await run({ mode: 'issue', task: 't', client_script: '/s/client.sh', stability: 'yes' }), 'stability'),
      'a gate field that is neither true nor false is asked about, not read as off');
    check(asks(await run({ mode: 'feature', task: 't', long_context: 1 }), 'long_context'), 'the same for G3');
    const perfClient = await run({ mode: 'perf', task: 'p', stability: 'true', client_script: '' });
    check((perfClient.result.questions || []).filter((q) => q.field === 'client_script').length === 1, 'G4 without the client it runs: asked once where the mode asks too');
    const g4 = await run({ mode: 'issue', task: 't', stability: 'True', long_context: false, server_script: '/s/server.sh', client_script: '/s/client.sh' });
    const g4verify = g4.calls.find((c) => c.key === 'verify_engineer:verify');
    check(g4.result.status !== 'needs_input' && g4verify && g4verify.ins.stability === true && g4verify.ins.long_context === false,
      `true and false as words or values start the run: ${g4.result.status}`);
    const image = await run({ mode: 'issue', task: 't', framework_dir: '' });
    const setupIns = image.calls.find((c) => c.key === 'director:setup').ins;
    check(setupIns.framework_dir === '/sgl-workspace/sglang', `sglang defaults to the image's checkout: ${setupIns.framework_dir}`);
    check(asks(await run({ mode: 'issue', task: 't', resume_from: 'implement', run_dir: RUN }), 'resume_from'), 'resume outside feature asked');
    check(asks(await run({ mode: 'scan', resume_from: 'verify', run_dir: RUN }), 'resume_from'), 'a scan has nothing to resume');
    check(asks(await run({ mode: 'issue', task: 't', resume_from: 'verify' }), 'run_dir'), 'resume_from verify without its run directory asked');
    let msg = '';
    try { await run({ mode: 'issue', task: 't', inferpilot_dir: '' }); } catch (e) { msg = e.message; }
    check(msg.includes('args.inferpilot_dir is required'), `no InferPilot checkout is still an error: ${msg}`);
  }],
  ['arguments: with stability on the task names the server and the client G4 keeps up, and no template is offered for either', async () => {
    const fields = (r) => (r.result.questions || []).map((q) => q.field);
    const offers = (r) => (r.result.questions || []).filter((q) => (q.options || []).includes('template') || /template/i.test(q.question));
    const none = await run({ mode: 'issue', task: 't', stability: true, client_script: '' });
    check(asks(none, 'server_script') && asks(none, 'client_script') && fields(none).length === 2,
      `an issue with neither is asked for both, and for nothing else: ${fields(none)}`);
    // An issue that stops before its fix has nothing for G5 to measure: the client is asked for on G4's account alone.
    const early = await run({ mode: 'issue', task: 't', stability: true, client_script: '', stop_after: 'Reproduce' });
    const eq = (early.result.questions || []).find((q) => q.field === 'client_script') || {};
    check(eqList(fields(early), ['server_script', 'client_script']) && /G4 keeps up for more than 48 hours/.test(eq.question || '')
      && !/G5/.test(`${eq.question} ${eq.why}`), `before the fix, on G4's account alone: ${JSON.stringify(eq)}`);
    check(offers(none).length === 0, `neither question offers a template: ${JSON.stringify(offers(none).map((q) => q.field))}`);
    const ci = await run({ mode: 'ci', task: 'c', stability: 'true', client_script: '/s/client.sh' });
    check(asks(ci, 'server_script') && eqList(fields(ci), ['server_script']), `a ci task is asked for the one it lacks: ${fields(ci)}`);
    // A feature is asked for both scripts by its mode, stability on or off (G5 measures every feature): G4 adds no question.
    const feature = await run({ mode: 'feature', task: 't', stability: true, client_script: '' });
    check(asks(feature, 'client_script') && eqList(fields(feature), ['client_script']) && offers(feature).length === 1,
      `a feature is asked for its client once, by its mode, as with stability off: ${fields(feature)}`);
    const noServer = await run({ mode: 'feature', task: 't', stability: true, server_script: '', client_script: '/s/client.sh' });
    check(fields(noServer).filter((f) => f === 'server_script').length === 1, `the server is asked once where the mode asks too: ${fields(noServer)}`);
    const off = await run({ mode: 'issue', task: 't', stability: false });
    check(off.result.status !== 'needs_input', `with stability off an issue names neither: ${off.result.status}`);
    // InferPilot's client, named by the task all the same, runs the task's workload: never lengths and a concurrency of its own.
    const tpl = await run({ mode: 'issue', task: 't', stability: true, server_script: '/s/server.sh', client_script: 'template' });
    check(asks(tpl, 'workload') && eqList(fields(tpl), ['workload']), `the template named without a workload: the workload is asked for: ${fields(tpl)}`);
    const tplFeature = await run({ mode: 'feature', task: 't', stability: true, client_script: 'Template' });
    check(asks(tplFeature, 'workload') && eqList(fields(tplFeature), ['workload']), `the same for a feature, asked once: ${fields(tplFeature)}`);
    const tplPart = await run({ mode: 'ci', task: 'c', stability: true, server_script: '/s/server.sh', client_script: 'template',
      workload: '{"conc":64}' });
    check(asks(tplPart, 'workload') && eqList(fields(tplPart), ['workload']),
      `and a workload that names only a part of the load is asked for again: ${fields(tplPart)}`);
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const load = await run({ mode: 'issue', task: 't', stability: true, server_script: '/s/server.sh', client_script: 'template',
      workload: '{"isl":8192,"osl":1024,"conc":64}' }, rendered);
    const ve = load.calls.find((c) => c.key === 'verify_engineer:verify');
    check(load.result.status !== 'needs_input' && ve && ve.ins.stability === true && ve.ins.client_script === `${RUN}/scripts/perf.sh`
      && ve.ins.server_script === '/s/server.sh',
      `with its workload the run starts, on the task's server and the rendered client: ${load.result.status} ${ve && ve.ins.client_script}`);
    const plain = await run({ mode: 'issue', task: 't', client_script: 'template', stop_after: 'Reproduce' }, rendered);
    check(plain.result.status !== 'needs_input',
      `without stability, and stopping before its fix, a task that names the template is asked nothing more: ${plain.result.status}`);
  }],
  ['arguments: an output_script that names no script is asked about before anything runs, so that the answer can take its place', async () => {
    const fields = (r) => (r.result.questions || []).map((q) => q.field);
    for (const mode of ['issue', 'ci']) {
      for (const value of ['template', '   ']) {
        const r = await run({ mode, task: 't', output_script: value });
        const q = (r.result.questions || []).find((x) => x.field === 'output_script') || {};
        check(asks(r, 'output_script') && eqList(fields(r), ['output_script']) && /names no script/.test(q.why || '')
          && /InferPilot has no template for it/.test(q.question || ''),
          `${mode}: ${JSON.stringify(value)} names no script, and InferPilot has no template for one: ${fields(r)} ${JSON.stringify(q.why)}`);
        // The value the run could not use is cleared for the next run: left there, it would win over the answer.
        const again = (r.result.next || {}).rerun_with || {};
        check(again.output_script === '' && 'output_script' in (again.answers || {}), `${mode}: and it is cleared for the next run: ${JSON.stringify(again)}`);
        const answered = await run({ mode, task: 't', output_script: value, ...again, answers: { output_script: '/s/outputs.sh' } });
        const ve = answered.calls.find((c) => c.key === 'verify_engineer:verify');
        check(answered.result.status === 'ready_for_review' && ve && ve.ins.output_script === '/s/outputs.sh',
          `${mode}: with the answer the run starts on the script the user named: ${answered.result.status}, ${ve && ve.ins.output_script}`);
        // A model that answers text questions needs none: the question left unanswered, the run starts.
        const left = await run({ mode, task: 't', output_script: value, ...again });
        check(left.result.status === 'ready_for_review', `${mode}: left unanswered, the run starts without one: ${left.result.status}`);
      }
      const named = await run({ mode, task: 't', output_script: '/s/outputs.sh' });
      check(named.result.status === 'ready_for_review', `${mode}: a task that names its script is asked nothing: ${named.result.status}`);
    }
    // A feature or a perf task measures gsm8k from its baseline on, and reads no such script: nothing is asked of it.
    const feature = await run({ mode: 'feature', task: 't', output_script: 'template' });
    check(feature.result.status !== 'needs_input', `a feature task is not asked about it: ${feature.result.status}`);
    const noText = await run({ mode: 'feature', task: 't' }, { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), text_model: false }) });
    check(noText.result.status === 'ready_for_review', `nor does what Setup says of the model stop a feature: ${noText.result.status}`);
  }],
  ['arguments: with long_context on the task names the test G3 runs, and nothing stands in for it', async () => {
    const fields = (r) => (r.result.questions || []).map((q) => q.field);
    for (const mode of ['feature', 'issue', 'ci', 'perf']) {
      const none = await run({ mode, task: 't', long_context: true });
      const q = (none.result.questions || []).find((x) => x.field === 'long_context_script') || {};
      check(asks(none, 'long_context_script') && eqList(fields(none), ['long_context_script']),
        `${mode}: with long_context on and no test of its own, the task is asked for it, and for nothing else: ${fields(none)}`);
      check(!(q.options || []).length && /InferPilot has no template for it/.test(q.question || ''),
        `${mode}: the question offers nothing in its place: ${JSON.stringify(q)}`);
    }
    const tpl = await run({ mode: 'feature', task: 't', long_context: 'true', long_context_script: 'template' });
    check(asks(tpl, 'long_context_script'), `"template" is no answer, since InferPilot has none: ${fields(tpl)}`);
    // The answer takes the place of the value the task gave, which would otherwise win over it and be asked about again.
    const again = (tpl.result.next || {}).rerun_with || {};
    check(again.long_context_script === '' && 'long_context_script' in (again.answers || {}),
      `the value the run could not use is cleared for the next run: ${JSON.stringify(again)}`);
    const answered = await run({ mode: 'feature', task: 't', long_context: 'true', long_context_script: 'template', ...again,
      answers: { long_context_script: '/s/niah.sh' } });
    const av = answered.calls.find((c) => c.key === 'verify_engineer:verify');
    check(answered.result.status !== 'needs_input' && av && av.ins.long_context_script === '/s/niah.sh',
      `and with the answer the run starts on the script the user named: ${answered.result.status}, ${av && av.ins.long_context_script}`);
    const blank = await run({ mode: 'feature', task: 't', long_context: true, long_context_script: '   ' });
    check(asks(blank, 'long_context_script'), `a path of blanks names no script: ${fields(blank)}`);
    const padded = await run({ mode: 'feature', task: 't', long_context: true, long_context_script: ' /s/niah.sh ' });
    const pv = padded.calls.find((c) => c.key === 'verify_engineer:verify');
    check(pv && pv.ins.long_context_script === '/s/niah.sh', `the roles read the path without the blanks around it: ${JSON.stringify(pv && pv.ins.long_context_script)}`);
    const off = await run({ mode: 'feature', task: 't', long_context: false });
    check(off.result.status !== 'needs_input', `with long_context off a task names no test: ${off.result.status}`);
    const unused = await run({ mode: 'feature', task: 't', long_context_script: '/s/niah.sh' });
    const uv = unused.calls.find((c) => c.key === 'verify_engineer:verify');
    check(unused.result.status !== 'needs_input' && uv && uv.ins.long_context === false,
      `a test the task names does not turn the gate on: ${unused.result.status}, long_context ${uv && uv.ins.long_context}`);
    const ready = await run({ mode: 'issue', task: 't', long_context: true, long_context_script: '/s/niah.sh' });
    const ve = ready.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ready.result.status !== 'needs_input' && ve && ve.ins.long_context === true && ve.ins.long_context_script === '/s/niah.sh',
      `with its test the run starts, and the verify engineer reads which one G3 runs: ${ready.result.status}, ${ve && ve.ins.long_context_script}`);
    check(ready.result.task_def.long_context_script === '/s/niah.sh', 'the definition the run records names it');
    // From a request in plain words the intake sets the gate and the script; the same check then applies.
    const said = await run({ request: 'port this PR and run the long-context test with /s/niah.sh', mode: 'feature', task: 't' }, {
      'intake:define': () => ({ status: 'ready', questions: [],
        task_def: { mode: 'feature', task: 't', long_context: true, long_context_script: '/s/niah.sh' } }),
    });
    const sv = said.calls.find((c) => c.key === 'verify_engineer:verify');
    check(sv && sv.ins.long_context === true && sv.ins.long_context_script === '/s/niah.sh', 'the intake turns the request into both fields');
    const half = await run({ request: 'port this PR and run the long-context gate', mode: 'feature', task: 't' }, {
      'intake:define': () => ({ status: 'ready', questions: [], task_def: { mode: 'feature', task: 't', long_context: true } }),
    });
    check(asks(half, 'long_context_script'), `a definition the intake calls ready, with the gate on and no test, is still asked: ${fields(half)}`);
  }],
  ['intake: a complete definition starts without it', async () => {
    const r = await run({ mode: 'issue', task: 'i' });
    check(!r.keys.includes('intake:define') && r.phases[0] === 'Setup', `phases ${r.phases}`);
    check(r.result.task_def.mode === 'issue' && r.result.task_def.task === 'i', `task_def ${JSON.stringify(r.result.task_def)}`);
  }],
  ['intake: a request in plain words becomes a definition and the task runs', async () => {
    const r = await run({ request: 'DeepSeek-V4.1 在 MI355X 上 decode 太慢，8k 输入 1k 输出 64 并发，目标提升 5%', framework_dir: '' }, {
      'intake:define': () => ({ status: 'ready', summary: 'perf task',
        task_def: { mode: 'perf', task: 'DeepSeek-V4.1 decode throughput on MI355X', goal: '+5% output tok/s at c64',
          framework_dir: '/tmp/sglang', model_path: '/models/dsv41', workload: { isl: 8192, osl: 1024, conc: 64 },
          server_script: '/s/server.sh', client_script: '/s/client.sh' },
        provenance: { mode: 'inferred', workload: 'inferred', gpu_ids: 'default' }, assumptions: ['Setup picks free GPUs'], questions: [] }),
    });
    check(r.result.status === 'ready_for_review' && r.phases[0] === 'Intake' && r.phases[1] === 'Setup', `${r.result.status}: ${r.phases}`);
    const setup = r.calls.find((c) => c.key === 'director:setup');
    check(setup && setup.ins.mode === 'perf' && setup.ins.goal === '+5% output tok/s at c64'
      && setup.ins.workload.conc === 64, `setup sees the definition: ${JSON.stringify(setup && setup.ins)}`);
    check(setup && setup.ins.assumptions.length === 1, 'assumptions reach the roles');
    const intakeCall = r.calls.find((c) => c.key === 'intake:define');
    check(intakeCall.prompt.includes('MODE=undecided') && intakeCall.prompt.includes('change nothing')
      && !intakeCall.prompt.includes('restore_owner'), 'the intake is told it has no run directory and changes nothing');
    check(setup.prompt.includes('MODE=perf') && setup.prompt.includes('restore_owner.sh'), 'later roles keep the ownership rule');
    check(r.result.task_def.mode === 'perf' && r.result.assumptions.length === 1, 'the run records the definition it used');
  }],
  ['intake: missing information comes back as questions, and answers continue the task', async () => {
    const intake = { 'intake:define': (ins) => (ins.answers && ins.answers.workload
      ? { status: 'ready', task_def: { mode: 'perf', task: 'p', framework_dir: '/tmp/sglang', model_path: '/models/m', workload: ins.answers.workload, server_script: '/s/server.sh', client_script: '/s/client.sh' }, questions: [] }
      : { status: 'needs_input', task_def: { mode: 'perf', task: 'p', framework_dir: '/tmp/sglang', model_path: '/models/m', server_script: '/s/server.sh', client_script: '/s/client.sh' },
        questions: [{ field: 'workload', question: 'Which workload?', why: 'fixed before profiling', default: '{"isl":8192}' }] }) };
    const first = await run({ request: 'make it faster' }, intake);
    const next = first.result.next || {};
    check(asks(first, 'workload') && (next.ask || []).some((q) => q.field === 'workload') && 'workload' in ((next.rerun_with || {}).answers || {}),
      `first: ${JSON.stringify(first.result.questions)} | next ${JSON.stringify(next)}`);
    check(first.result.run_dir === '' && !first.keys.includes('director:report'), 'nothing was created');
    const again = await run({ request: 'make it faster', answers: { workload: { isl: 8192, osl: 1024, conc: 64 } } }, intake);
    check(again.result.status === 'ready_for_review', `with the answer: ${again.result.status}`);
    const asText = await run({ request: 'make it faster', answers: '{"workload": {"isl": 8192, "osl": 1024, "conc": 64}}' }, intake);
    check(asText.result.status === 'ready_for_review', `answers as a JSON string: ${asText.result.status}`);
  }],
  ['intake: the code still checks a definition the intake calls ready', async () => {
    const r = await run({ request: 'speed up decode' }, {
      'intake:define': () => ({ status: 'ready', task_def: { mode: 'perf', task: 'p', framework_dir: '/tmp/sglang', model_path: '/models/m' }, questions: [] }),
    });
    check(asks(r, 'workload'), `a ready definition without a workload: ${r.result.status}`);
  }],
  ['intake: fields the caller set win over what the intake inferred', async () => {
    const r = await run({ request: 'look at 38849', mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' }, {
      'intake:define': () => ({ status: 'ready', task_def: { mode: 'feature', task: 'something else', framework_dir: '/tmp/sglang', model_path: '/m' }, questions: [] }),
    });
    check(r.result.mode === 'issue' && r.result.task_def.task.endsWith('/38849'), `mode ${r.result.mode}, task ${r.result.task_def.task}`);
  }],
  ['intake: a gate the request asks for is kept beside a goal the caller gave', async () => {
    const r = await run({ request: 'port this PR and run the 48-hour stability test', mode: 'feature', task: 't', goal: '20% faster',
      client_script: '/s/client.sh' }, {
      'intake:define': () => ({ status: 'ready', questions: [],
        task_def: { mode: 'feature', task: 't', goal: 'the port passes its gates', stability: true } }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const ve = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ve && ve.ins.goal === '20% faster' && ve.ins.stability === true && ve.ins.long_context === false,
      `the caller's goal, and the gate the request asked for: ${JSON.stringify(ve && [ve.ins.goal, ve.ins.stability, ve.ins.long_context])}`);
    check(r.result.task_def.goal === '20% faster' && r.result.task_def.stability === true, 'both are in the definition the run records');
    const off = await run({ request: 'port this PR and run the 48-hour stability test', mode: 'feature', task: 't', stability: false }, {
      'intake:define': () => ({ status: 'ready', questions: [], task_def: { mode: 'feature', task: 't', stability: true } }),
    });
    const ov = off.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ov && ov.ins.stability === false, 'a field the caller set still wins over the intake');
  }],
  ['intake: a gate field the intake fills with another word than true or false is asked for again, never read as off', async () => {
    const answers = [];
    const r = await run({ request: 'port this PR and run the stability test', mode: 'feature', task: 't', client_script: '/s/client.sh' }, {
      'intake:define': (ins, n) => {
        answers.push(n === 1 ? 'yes' : true);
        return { status: 'ready', questions: [], task_def: { mode: 'feature', task: 't', stability: answers[answers.length - 1] } };
      },
    }, { schemaRetries: 1 });
    check(answers.length === 2 && answers[0] === 'yes', `"yes" does not match the schema, and the intake answers again: ${JSON.stringify(answers)}`);
    const ve = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ve && ve.ins.stability === true, 'the gate the request asked for is on');
  }],
  ['intake: a request outside InferPilot is rejected with what to do instead', async () => {
    const r = await run({ request: 'push my branch and comment on the PR' }, {
      'intake:define': () => ({ status: 'rejected', task_def: {}, questions: [], reason: 'InferPilot never pushes or posts; run the push yourself' }),
    });
    check(r.result.status === 'rejected' && r.result.reason.includes('never pushes'), `status ${r.result.status}`);
    check(!r.keys.includes('director:setup'), 'nothing started');
  }],
  ['intake: an intake that returns nothing falls back to the given fields', async () => {
    const r = await run({ request: 'this issue', mode: 'issue', task: 'i' }, { 'intake:define': () => ({ status: 'maybe' }) });
    check(r.result.status === 'ready_for_review' && r.result.phases[0].summary.includes('returned nothing'), `status ${r.result.status}`);
  }],
  ['roles: every field an answer must have is named where its role documents the PHASE', async () => {
    const src = readFileSync(SCRIPT, 'utf8');
    const sites = new Set([...src.matchAll(CALL_SITE)].map((m) => `${m[1]}:${m[2]}`));
    if (!ONLY) for (const key of sites) check(CONTRACTS.has(key), `no scenario sends ${key} a prompt`);
    for (const [key, required] of CONTRACTS) {
      const [role, ph] = key.split(':');
      const section = phaseSection(readFileSync(path.join(INFERPILOT, 'roles', `${role}.md`), 'utf8'), ph);
      for (const field of required) {
        // As a key or in code ("culprit", `culprit`), not a word of the prose around it.
        check(new RegExp(`["\`]${field}["\`]`).test(section), `roles/${role}.md PHASE=${ph} does not name ${field}, which the answer must have`);
      }
    }
  }],
  ['phases: every phase a run entered is in meta.phases, which Claude Code shows; in a whole dry run, every listed one is entered', async () => {
    const src = readFileSync(SCRIPT, 'utf8');
    const meta = src.slice(0, src.indexOf('\n};') + 3);
    const listed = [...meta.matchAll(/\{ title: '([^']+)', detail:/g)].map((m) => m[1]);
    check(listed.length >= 15 && ENTERED.size > 0, `found ${listed.length} listed, ${ENTERED.size} entered`);
    for (const p of ENTERED) check(listed.includes(p), `a run entered ${p}, which meta.phases does not list`);
    if (!ONLY) for (const p of listed) check(ENTERED.has(p), `meta.phases lists ${p}, which no scenario enters`);
  }],
  ['groups: the runner of every mode a task group\'s scenarios ran is one of its entry points (tests/groups.json)', async () => {
    const groups = JSON.parse(readFileSync(path.join(INFERPILOT, 'tests', 'groups.json'), 'utf8'));
    const table = /^const RUNNERS = \{([^}]*)\}/m.exec(readFileSync(SCRIPT, 'utf8'));
    const runners = Object.fromEntries([...(table ? table[1] : '').matchAll(/(\w+): (\w+)/g)].map((m) => [m[1], m[2]]));
    check(Object.keys(runners).length >= 5, `found the runners ${JSON.stringify(runners)}`);
    for (const [g, spec] of Object.entries(groups)) {
      for (const prefix of g === 'basic' ? [] : spec.dry_run) {
        for (const mode of MODES_RUN.get(prefix) || []) {
          check(spec.code.includes(runners[mode]), `${prefix}: its scenarios ran ${mode}; add ${runners[mode]} to ${g}'s code`);
        }
      }
    }
  }],
  ['next: every result says what comes after it, asks and offers what fits how it ended, and both sessions\' instructions act on it', async () => {
    const seen = new Set();
    for (const { scenario, result: r } of RESULTS) {
      const n = r.next || {};
      const again = n.rerun_with;
      const where = `${scenario.split(':')[0]}, ${r.status}`;
      seen.add(r.status);
      check(typeof n.say === 'string' && n.say.length > 10, `${where}: next.say ${JSON.stringify(n.say)}`);
      if (r.status === 'needs_input') {
        check((n.ask || []).length > 0 && n.ask.every((q) => q.field in ((again || {}).answers || {})),
          `${where}: asks ${JSON.stringify(n.ask)}, runs again with ${JSON.stringify(again)}`);
      } else if (r.status === 'awaiting_design_review' || (r.status === 'stopped' && r.stopped_at === 'Design' && r.mode === 'feature')) {
        check(again && again.resume_from === 'implement' && again.run_dir === r.run_dir, `${where}: runs again with ${JSON.stringify(again)}`);
      } else if (r.status === 'blocked' && r.stopped_at === 'Verify'
        && r.phases.some((p) => p.phase === 'Verify' && p.summary === 'the verify engineer returned nothing')) {
        check(again && again.resume_from === 'verify' && again.run_dir === r.run_dir && ['', 'Verify', 'Performance', 'Review'].includes(again.stop_after),
          `${where}: a Verify without a result runs again from Verify, with no stop that is behind it: ${JSON.stringify(again)}`);
      } else if (r.status === 'stopped') {
        check(again && again.stop_after === '', `${where}: runs again with ${JSON.stringify(again)}`);
      } else if (r.status === 'busy') {
        check(again && Object.keys(again).length === 0, `${where}: runs again as it was: ${JSON.stringify(again)}`);
      } else {
        check(again === undefined, `${where}: nothing to run again, yet ${JSON.stringify(again)}`);
      }
      if (r.status === 'queued') {
        check((n.start || []).length > 0 && n.start.every((s) => s.args.mode && s.args.inferpilot_dir === INFERPILOT), `${where}: starts ${JSON.stringify(n.start)}`);
      }
      // A run that verified someone else's pull request has a comment to post, and nothing of its own to push.
      if (r.status === 'ready_for_review' && r.pr && r.pr.comment_for) {
        check((n.user || []).some((u) => /^post the verification comment on /.test(u)), `${where}: the comment is the user's to post: ${JSON.stringify(n.user)}`);
      } else if (r.status === 'ready_for_review') {
        check((n.user || []).some((u) => /\bpush\b/.test(u)), `${where}: the push is the user's: ${JSON.stringify(n.user)}`);
      }
    }
    if (!ONLY) {
      for (const s of ['needs_input', 'rejected', 'awaiting_design_review', 'stopped', 'busy', 'blocked', 'queued', 'ready_for_review',
        'handed_off', 'triaged', 'fixed_on_main', 'not_reproduced', 'no_gain', 'dropped']) check(seen.has(s), `no scenario ends ${s}`);
    }
    const src = readFileSync(SCRIPT, 'utf8');
    const whenToUse = (/whenToUse: '((?:[^'\\]|\\.)*)'/.exec(src) || [, ''])[1];
    const agents = readFileSync(path.join(INFERPILOT, 'AGENTS.md'), 'utf8');
    for (const k of new Set(RESULTS.flatMap(({ result }) => Object.keys(result.next || {})))) {
      check(whenToUse.includes(`next.${k}`), `inferpilot.js's whenToUse does not tell Claude Code what to do with next.${k}`);
      check(agents.includes(`next.${k}`), `AGENTS.md does not tell a Cursor agent what to do with next.${k}`);
    }
  }],
];

const PREFIXES = new Set(scenarios.map(([name]) => name.split(':')[0]));
if (ONLY && (!ONLY.size || [...ONLY].some((p) => !PREFIXES.has(p)))) {
  console.error(`--only: no scenarios named ${[...ONLY].filter((p) => !PREFIXES.has(p)).join(', ') || '(none given)'}; `
    + `the names begin ${[...PREFIXES].join(', ')}`);
  process.exit(2);
}
for (const [name, fn] of scenarios) {
  if (ONLY && !ONLY.has(name.split(':')[0])) continue;
  const before = failed;
  SCENARIO = name;
  try {
    await fn();
  } catch (e) {
    failed++;
    console.error(`  FAIL: threw ${e && e.stack ? e.stack : e}`);
  }
  console.log(`${failed === before ? 'ok  ' : 'FAIL'} ${name}`);
}
console.log(`\n${passed} checks passed, ${failed} failed.`);
process.exit(failed ? 1 : 0);
