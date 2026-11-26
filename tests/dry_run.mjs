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
        ? { wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', failure_summary: 'case 7 mismatches the reference' }
        : { wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const second = r.calls.filter((c) => c.key === 'implementer:implement')[1];
    check(second && second.ins.feedback === 'case 7 mismatches the reference', 'feedback passed to round 1');
  }],
  ['implement: a unit test that passes without the change is a failed round', async () => {
    const r = await run({ mode: 'feature', task: 't' }, {
      'test_engineer:unit': (ins, n) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true,
        fails_without_change: n === 1 ? 'no' : 'mutant', failure_summary: n === 1 ? 'passes on the parent: it never calls the new path' : '' }),
    });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    const rounds = r.calls.filter((c) => c.key === 'implementer:implement');
    check(rounds.length === 3 && rounds[1].ins.feedback.includes('cannot fail')
      && rounds[1].ins.feedback.includes('never calls the new path'), `feedback: ${rounds[1] && rounds[1].ins.feedback}`);
  }],
  ['implement: only a docs package may have nothing to fail', async () => {
    const asNa = (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'n/a' });
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
  }],
  ['feature: a gate\'s answer InferPilot cannot use goes to a new attempt with a limit of its own, told to check what the first left', async () => {
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
      check(first && again && !first.prompt.includes('This is attempt') && again.prompt.includes('This is attempt 2: an earlier attempt '
        + 'of this call failed and may have left work behind') && !again.prompt.includes('An earlier session of this same request'),
        `${key}: not asked again by the engine but by the call's next attempt, which checks first what the one before left`);
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
      && (unit.result.next.user || []).some((u) => u.includes('returned no answer: commit c_WP1 stays in the worktree')),
      `no lead is checked on its GPUs, and a human is told to stop what it left: ${JSON.stringify(usp && usp.ins.confirm_skipped)}`);
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
      && unitDoc.includes('the gate fails, and `failure_summary` names the check, how far it got and its log'),
      'the reserve fits a short limit, and a check still running when it starts is named, not waited for');
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
    check(taken.result.status === 'ready_for_review' && perf && !perf.ins.claim,
      `InferPilot's own client, once taken, is measured and reported, never claimed: ${taken.result.status}, claim ${perf && perf.ins.claim}`);
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
          : () => ({ unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent' })),
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
    check(String(setup.ins.lock_idle_hours) === '12', `idle hours for the lock: ${setup.ins.lock_idle_hours}`);
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
    check(eqList(afterPr, ['PR', 'Implement', 'Verify', 'Review', 'PR', 'Report']), `phases after the first PR: ${afterPr}`);
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
    check(asks(await run({ mode: 'ci', task: 't', stop_after: 'Performance' }), 'stop_after'), 'ci never runs Performance');
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
    const bad = await run({ mode: 'ci', task: 'c', stop_after: 'Verify' }, undefined, { control: () => ({ stop_after: 'Performance' }) });
    check(bad.lines.some((l) => /stop_after=performance is not a phase of mode=ci; the stop stays verify/.test(l))
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
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', failure_summary: 'still wrong' }),
    });
    check(r.result.status === 'blocked' && r.result.stopped_at === 'Implement', `status ${r.result.status}`);
    check(r.keys.filter((k) => k === 'implementer:implement').length === 2, 'max_fix_rounds + 1 attempts');
    check(r.keys.includes('implementer:rollback'), 'rollback called');
    check(!r.keys.includes('verify_engineer:verify'), 'no verification of a failed package');
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
  ['issue and ci: a task that can reach a fix names the model G2 measures it on; one that stops before the fix needs none', async () => {
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
  ['issue and ci: no fix starts without a model, also when the stop before it was on a phase the route skips or the control file lifted it', async () => {
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
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', failure_summary: 'wrong' }),
      'implementer:rollback': () => ({ wp: 'WP1', reset_to: 'base', clean: false }),
    });
    check(r.result.status === 'blocked' && r.result.attention.length === 1 && r.result.attention[0].wp === 'WP1',
      `attention ${JSON.stringify(r.result.attention)}`);
    const rep = r.calls.find((c) => c.key === 'director:report');
    check(rep && Array.isArray(rep.ins.attention) && rep.ins.attention.length === 1, 'the report sees what a human must clean up');
    const clean = await run({ mode: 'feature', task: 't', max_fix_rounds: 0 }, {
      'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: false, feature_off_unchanged: true, fails_without_change: 'parent', failure_summary: 'wrong' }),
    });
    check(clean.result.attention.length === 0, 'a clean rollback needs no attention');
  }],
  ['issue: reproduce, diagnose, fix, verify with the reproduction as a gate', async () => {
    const r = await run({ mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Triage', 'Reproduce', 'Diagnose', 'Implement', 'Verify', 'Review', 'PR', 'Reply', 'Report']), `phases ${r.phases}`);
    check(r.heads[8].startsWith('=== PHASE 9/10 [#########-]: Reply ===') && r.heads[9].startsWith('=== PHASE 10/10 [##########]: Report ==='),
      `the issue route numbers Reply and Report: ${r.heads[8]} | ${r.heads[9]}`);
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && JSON.stringify(v.ins.extra_gates).includes('issue_repro'), 'issue_repro gate passed to verify');
    check(r.result.priority === 'P0', 'priority recorded');
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
    check(eqList(r.phases, ['Setup', 'Triage', 'Reproduce', 'Implement', 'Verify', 'Review', 'PR', 'Reply', 'Report']), `phases ${r.phases}`);
    const im = r.calls.find((c) => c.key === 'implementer:implement');
    check(im && im.ins.work_package.kind === 'apply_pr' && im.ins.work_package.goal.includes('#40872'), 'apply_pr work package');
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && v.ins.extra_gates[0].cmd === 'bash repro.sh', `issue_repro gate carries the reproduction: ${JSON.stringify(v && v.ins.extra_gates)}`);
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
  ['ci: code regression to a fix PR', async () => {
    const r = await run({ mode: 'ci', task: 'tracker cluster _check ImportError' });
    check(r.result.status === 'ready_for_review', `status ${r.result.status}`);
    check(eqList(r.phases, ['Setup', 'Classify', 'Bisect', 'Implement', 'Verify', 'Review', 'PR', 'Report']), `phases ${r.phases}`);
    const v = r.calls.find((c) => c.key === 'verify_engineer:verify');
    check(v && JSON.stringify(v.ins.extra_gates).includes('failing_test'), 'failing_test gate passed to verify');
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
      `no claim, only guards: ${noClaim.result.status}`);
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
    const asked = await run({ mode: 'feature', task: 't', long_context: true, stability: 'true', client_script: '/s/client.sh' });
    const sp = asked.calls.find((c) => c.key === 'director:spec');
    const ve = asked.calls.find((c) => c.key === 'verify_engineer:verify');
    check(sp && sp.ins.long_context === true && sp.ins.stability === true, 'on for the spec when the task sets them');
    check(ve && ve.ins.long_context === true && ve.ins.stability === true, 'on for the verify engineer, which runs G3 and G4 only then');
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
    check(asks(await run({ mode: 'feature', task: 't', workload: '{"isl":8000}' }), 'client_script'), 'a feature with a workload but no client script asked');
    const plain = await run({ mode: 'feature', task: 't' });
    check(!asks(plain, 'client_script'), 'a feature without a workload needs no client script');
    const setupIns = plain.calls.find((c) => c.key === 'director:setup');
    check(setupIns && setupIns.ins.server_script === '/s/server.sh', 'the server script reaches the roles');
  }],
  ['feature: InferPilot\'s templates run only where the user took them, and its client\'s numbers are reported, never claimed', async () => {
    const question = (r, field) => (r.result.questions || []).find((q) => q.field === field) || {};
    const none = await run({ mode: 'feature', task: 't', workload: '{"isl":8000}', server_script: '', client_script: '' });
    check(asks(none, 'server_script') && eqList(question(none, 'server_script').options, ['template'])
      && eqList(question(none, 'client_script').options, ['template']) && /never a claim/.test(question(none, 'client_script').question),
      `the questions offer the templates, the client's as reported only: ${JSON.stringify(none.result.questions)}`);
    const rendered = { server_script: `${RUN}/scripts/server.sh`, client_script: `${RUN}/scripts/perf.sh`,
      accuracy_script: `${RUN}/scripts/accuracy.sh` };
    const setup = (out) => ({ 'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', branch: 'inferpilot/task',
      worktree: `${RUN}/wt`, gpu_ids: '0,1', ...out }) });
    const took = await run({ mode: 'feature', task: 't', workload: '{"isl":8000}', server_script: 'template', client_script: 'template',
      harness: { CONTEXT_LENGTH: 8192 } }, setup(rendered));
    const setupIns = took.calls.find((c) => c.key === 'director:setup');
    check(setupIns && setupIns.ins.server_script === 'template' && setupIns.ins.client_script === 'template'
      && (typeof setupIns.ins.harness === 'string' ? JSON.parse(setupIns.ins.harness) : setupIns.ins.harness || {}).CONTEXT_LENGTH === 8192,
      `Setup renders what the user took, with the task's values: ${JSON.stringify(setupIns && setupIns.ins.harness)}`);
    const perf = took.calls.find((c) => c.key === 'perf_engineer:perf_eval');
    check(took.result.status === 'ready_for_review' && perf && perf.ins.server_script === rendered.server_script
      && perf.ins.client_script === rendered.client_script && perf.ins.accuracy_script === rendered.accuracy_script && !perf.ins.claim,
      `the rendered scripts reach the performance engineer, and InferPilot's client claims nothing: ${took.result.status}, claim ${perf && perf.ins.claim}`);
    const own = await run({ mode: 'feature', task: 't', accuracy_script: '/s/accuracy.sh' }, setup(rendered));
    const ownIns = own.calls.find((c) => c.key === 'verify_engineer:verify');
    check(ownIns && ownIns.ins.server_script === '/s/server.sh' && ownIns.ins.accuracy_script === '/s/accuracy.sh',
      'the task\'s own scripts win over rendered ones');
    const unrendered = await run({ mode: 'feature', task: 't', server_script: 'template' }, setup({}));
    check(unrendered.result.status === 'blocked' && unrendered.result.stopped_at === 'Setup'
      && !unrendered.keys.includes('director:spec'), `a template Setup did not render stops the run: ${unrendered.result.status}`);
    const noAccuracy = await run({ mode: 'feature', task: 't' }, setup({}));
    check(noAccuracy.result.status === 'blocked' && noAccuracy.result.stopped_at === 'Setup'
      && /no accuracy script/.test(JSON.stringify(noAccuracy.result.phases)), `no accuracy script stops the run at Setup: ${noAccuracy.result.status}`);
    const gap = await run({ mode: 'feature', task: 't', workload: '{"isl":8000}', client_script: 'template' }, {
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
      && !/never a claim/.test(question(none, 'client_script').question),
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
    let msg = '';
    try { await run({ mode: 'issue', task: 't', inferpilot_dir: '' }); } catch (e) { msg = e.message; }
    check(msg.includes('args.inferpilot_dir is required'), `no InferPilot checkout is still an error: ${msg}`);
  }],
  ['arguments: with stability on the task names the server and the client G4 keeps up, and no template is offered for either', async () => {
    const fields = (r) => (r.result.questions || []).map((q) => q.field);
    const offers = (r) => (r.result.questions || []).filter((q) => (q.options || []).includes('template') || /template/i.test(q.question));
    const none = await run({ mode: 'issue', task: 't', stability: true });
    check(asks(none, 'server_script') && asks(none, 'client_script') && fields(none).length === 2,
      `an issue with neither is asked for both, and for nothing else: ${fields(none)}`);
    check(offers(none).length === 0, `neither question offers a template: ${JSON.stringify(offers(none).map((q) => q.field))}`);
    const ci = await run({ mode: 'ci', task: 'c', stability: 'true', client_script: '/s/client.sh' });
    check(asks(ci, 'server_script') && eqList(fields(ci), ['server_script']), `a ci task is asked for the one it lacks: ${fields(ci)}`);
    const feature = await run({ mode: 'feature', task: 't', stability: true });
    check(asks(feature, 'client_script') && eqList(fields(feature), ['client_script']) && offers(feature).length === 0,
      `a feature without a workload, whose server the mode asked for, is asked for its client, with no template: ${fields(feature)}`);
    const noServer = await run({ mode: 'feature', task: 't', stability: true, server_script: '', client_script: '/s/client.sh' });
    check(fields(noServer).filter((f) => f === 'server_script').length === 1, `the server is asked once where the mode asks too: ${fields(noServer)}`);
    const off = await run({ mode: 'issue', task: 't', stability: false });
    check(off.result.status !== 'needs_input', `with stability off an issue names neither: ${off.result.status}`);
    // InferPilot's client, named by the task all the same, runs the task's workload: never lengths and a concurrency of its own.
    const tpl = await run({ mode: 'issue', task: 't', stability: true, server_script: '/s/server.sh', client_script: 'template' });
    check(asks(tpl, 'workload') && eqList(fields(tpl), ['workload']), `the template named without a workload: the workload is asked for: ${fields(tpl)}`);
    const tplFeature = await run({ mode: 'feature', task: 't', stability: true, client_script: 'Template' });
    check(asks(tplFeature, 'workload'), 'the same for a feature');
    const rendered = { 'director:setup': () => ({ ...DEFAULTS['director:setup'](), client_script: `${RUN}/scripts/perf.sh` }) };
    const load = await run({ mode: 'issue', task: 't', stability: true, server_script: '/s/server.sh', client_script: 'template',
      workload: '{"isl":8192,"osl":1024,"conc":64}' }, rendered);
    const ve = load.calls.find((c) => c.key === 'verify_engineer:verify');
    check(load.result.status !== 'needs_input' && ve && ve.ins.stability === true && ve.ins.client_script === `${RUN}/scripts/perf.sh`
      && ve.ins.server_script === '/s/server.sh',
      `with its workload the run starts, on the task's server and the rendered client: ${load.result.status} ${ve && ve.ins.client_script}`);
    const plain = await run({ mode: 'issue', task: 't', client_script: 'template' }, rendered);
    check(plain.result.status !== 'needs_input', `without stability nothing more is asked of a task that names the template: ${plain.result.status}`);
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
      if (r.status === 'ready_for_review') check((n.user || []).some((u) => /\bpush\b/.test(u)), `${where}: the push is the user's: ${JSON.stringify(n.user)}`);
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
