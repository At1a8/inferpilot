export const meta = {
  name: 'inferpilot',
  description: 'InferPilot: the framework agent for open-source LLM inference frameworks on AMD GPUs (sglang first). It turns one task (an upstream feature to bring to AMD, an AMD user issue, a red AMD CI job, a system-level performance gap) into a verified, reviewable pull request: correct first (independent unit gates, G0 and G2 against a same-pattern baseline, G3 and G4 when the task asks, an investigation loop with controls), then fast (equal-budget G5), within the community rules of knowledge/guardrails.md. mode=scan writes the day\'s report on the framework (the AMD CI on main, user issues by priority, upstream changes AMD needs to follow) and ranks the tasks in it, changing no code. InferPilot never pushes and never posts upstream: it prepares the branch, the PR body and the reply drafts for a human; what it posts goes to its own repository, only with the user\'s GITHUB_TOKEN: the knowledge a run proved, as a pull request for a human to merge, and a scan\'s daily report, as an issue (when the token cannot write there, a local file for a human to share).',
  whenToUse: 'Pass args.inferpilot_dir (absolute path of the InferPilot checkout, required) and the task, either as args.request (the user\'s words, any language; the Intake phase turns it into a task definition) or as the task-definition fields of templates/task_definition.md: args.mode (feature | issue | ci | perf | scan), args.task (the upstream PR or design, issue URL, CI job or tracker cluster, or performance goal), args.framework_dir (the framework checkout; for sglang the image\'s /sgl-workspace/sglang by default), args.model_path (required for feature and perf, and for an issue or a ci task that can reach a fix: a fix that changes code the server runs is measured against the base, G2 on gsm8k and G5 on args.client_script; with stop_after at a phase before the fix it needs neither), args.workload (required for perf: the fixed benchmark every measurement uses), args.server_script (required for feature and perf: a script that starts the baseline server; InferPilot sets PORT, HIP_VISIBLE_DEVICES and PYTHONPATH, and EXTRA_ARGS carries the feature flags; or "template", InferPilot\'s templates/harness/server.sh after the model\'s sglang cookbook recipe, only when the user takes it) and args.client_script (required for feature and perf, where G5 measures every change, and for an issue or a ci task that can reach a fix: runs the workload against PORT and writes to OUT_DIR; or "template", InferPilot\'s templates/harness/perf.sh on the task\'s workload, only when the user takes it and gives the whole workload; on it a feature is judged only to be no slower than the baseline, never on its source\'s gain), args.accuracy_script (optional: the user\'s own, run against API_URL with TAG and RESULT_ROOT; without one InferPilot\'s templates/harness/accuracy.sh runs scripts/eval_accuracy.sh), args.harness (values for the templates Setup renders with scripts/harness.py, e.g. {"CONTEXT_LENGTH": 8192}: each parameter takes the task\'s value, else the one the tests of the feature\'s source use, else, for the server, the model\'s sglang cookbook recipe, else the template\'s default), args.goal (what done means), args.framework (default sglang), args.platform, args.gpu_ids, args.container, args.apply_prs (perf), args.sources, args.since, args.upstream_repos and args.rescan (scan: a report from the last 24 hours makes the run ask before scanning again), args.image (the image a task must run on; by default the newest daily one, and another stops Setup to ask) and args.image_ok, args.bisect_fixed (issue: when the reporter\'s code fails and main does not, find the commit that fixed it), args.confirm_leads (default false: leads, the problems roles met that are not the task\'s, are listed with how to reproduce them; true checks each on the base commit before the report and drafts an issue for those that show there), args.stop_after (a phase title), args.resume_from="implement" with args.run_dir (feature: continue after a human approved design.md), or "verify" with args.run_dir (every mode but scan: Verify and what follows again, on the commit Implement verified, in a run whose Verify ended without a result), args.auto_approve_design (feature, default true: a design the architect flags, or L3 work, goes on to Implement and the report says no human approved it; false makes it wait at Design for a human; stop_after still stops), args.held_out, args.guards and args.sweep (performance checks beyond the workload and goal of the task, each off by default: a held-out operating point the gain must also hold at, guard workloads InferPilot picks, and points beyond those of the workload), args.tune (feature, default false: the feature runs at the configuration of the target PR or design; true lets a work package choose its knobs by measurement), args.long_context and args.stability (correctness gates that run only when the task asks, each off by default: G3, args.long_context_script, the user\'s own needle-in-a-haystack test on long prompts, asked for when a task with long_context on names none since InferPilot has no template for it, and G4, args.client_script run back to back for more than 48 hours against one server, once in a run, which needs a client_script), args.kernel_backend (none | geak | command) with args.kernel_backend_dir (geak: the GEAK checkout, whose kernel lane runs as an outside program) or args.kernel_backend_command (command; for geak it replaces the default command), args.kernel_budget ({budget} in the command). A definition that lacks something its mode needs returns status=needs_input and starts nothing. Every result ends with next, what comes after the run: tell the user next.say; put each question of next.ask to the user, never answering one yourself, then run inferpilot.js again with the same args plus next.rerun_with, its answers filled in from the user\'s (they merge with the answers already given); offer each of next.start (a scan\'s top tasks) as a new run with its args, and start the one the user picks; next.user is what only the user does (the push, a reply to post, a daily report the director could not share). Operator settings: args.exp_root, args.review_model (the reviewer\'s model, where the runtime takes one per call), args.max_fix_rounds, args.max_investigate_rounds, args.max_review_rounds, args.max_work_packages, args.agent_retries, args.agent_timeout_ms.',
  phases: [
    { title: 'Intake', detail: 'intake: turn a request, or a definition with gaps, into a complete task definition; questions when something is missing' },
    { title: 'Setup', detail: 'director: run dir, the task\'s branch in the framework checkout, the server and client scripts, baseline (not in scan)' },
    { title: 'Spec', detail: 'director: feature_spec.md with measurable acceptance criteria [feature]' },
    { title: 'Recon', detail: 'analyst: code map and assumption table, gap type and level [feature]' },
    { title: 'Design', detail: 'architect: work packages, invariants, memory budget, startup rejections [feature]' },
    { title: 'Triage', detail: 'issue_triager: state checks, P0/P1/P2, action, reply draft [issue]' },
    { title: 'Reproduce', detail: 'issue_triager: reproduce on the reporter configuration [issue]' },
    { title: 'Diagnose', detail: 'investigator: root cause and fix work packages [issue]' },
    { title: 'Classify', detail: 'ci_guardian: code regression / dependency bump / test bug / flaky / infrastructure [ci]' },
    { title: 'Bisect', detail: 'ci_guardian: culprit commit, confirmation, fix work packages [ci]; the commit that fixed it, when the reporter\'s code fails and main does not [issue, bisect_fixed]' },
    { title: 'Profile', detail: 'perf_engineer: trace metrics and the opportunity list [perf]' },
    { title: 'Plan', detail: 'architect: performance work packages, kernel hand-offs, PRs to apply [perf]' },
    { title: 'Implement', detail: 'implementer per work package, independent test_engineer unit + feature-off gate, rollback on a measured failure' },
    { title: 'Kernel', detail: 'kernel_liaison: kernel hand-offs to args.kernel_backend (none records them; geak runs the kernel lane of a GEAK checkout as an outside program; command runs a kernel agent CLI); gains integrated as work packages' },
    { title: 'Verify', detail: 'verify_engineer: G0 and G2 against the same-pattern baseline, G3 and G4 when the task asks, plus mode gates; investigator loop on failure' },
    { title: 'Investigate', detail: 'investigator: controls first, hypothesis ledger, fix or baseline bug' },
    { title: 'Performance', detail: 'perf_engineer: G5 equal-budget A/B and guard workloads' },
    { title: 'Review', detail: 'reviewer: independent review of the diff and its evidence; requested changes come back as work packages' },
    { title: 'PR', detail: 'pr_engineer: hygiene, lint, upstream sync, pr_body.md, exact push command; never pushes' },
    { title: 'Collect', detail: 'scout: the AMD CI on main, user issues, upstream changes (platform gates, host syncs on request) -> candidates; asks first when a report from the last 24 hours exists [scan]' },
    { title: 'Dispatch', detail: 'dispatcher: type, level, priority, dependencies, effort, ranking, ecosystem-matrix changes -> the daily report [scan]' },
    { title: 'Spinoff', detail: 'issue_triager: for each problem the run found that is not its own (a failure the baseline shares, or a lead a role reported), the upstream issue that tracks it or an issue draft for a human to file, and the definition that starts it; leads are listed, and checked on the base commit only with confirm_leads [when there are any]' },
    { title: 'Reply', detail: 'issue_triager: the reply draft rewritten from how the run ended; the Triage draft is kept beside it; reviewer: the draft checked against the run, what it finds rewritten once [issue]' },
    { title: 'Report', detail: 'director: final report for every outcome of a run that has its run directory (for a scan, the daily report, shared as an issue in InferPilot\'s own repository, else by a human), then frees the machine; curator: knowledge updates, proposed as a pull request to InferPilot\'s own repository' },
  ],
};

// ---------------------------------------------------------------------------
// Arguments. Control flow (phase order, retries, gates, budgets) lives here;
// agents are called only for judgement and for the work itself, and every agent
// returns structured JSON whose gate fields this script reads.
// ---------------------------------------------------------------------------
const RAW = args || {};
const MODES = ['feature', 'issue', 'ci', 'perf', 'scan'];
// The phases a run of each mode passes when nothing stops it early, logged as its route so that the log
// shows how far a run is (runtime/run.mjs numbers its phases from it). Intake, an issue's Bisect, Kernel,
// Investigate and Spinoff come on top when needed; a fix that changes no code the server runs logs its route
// again, without Performance.
const ROUTES = {
  feature: ['Setup', 'Spec', 'Recon', 'Design', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report'],
  issue: ['Setup', 'Triage', 'Reproduce', 'Diagnose', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Reply', 'Report'],
  ci: ['Setup', 'Classify', 'Bisect', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report'],
  perf: ['Setup', 'Profile', 'Plan', 'Implement', 'Verify', 'Performance', 'Review', 'PR', 'Report'],
  scan: ['Collect', 'Dispatch', 'Report'],
};
const logRoute = (route) => log(`[inferpilot] Route: ${route.join(' → ')}`);
// Kernel back ends: where kernel-level work goes (tuning a kernel InferPilot wrote for a feature, or a
// kernel-level performance opportunity). knowledge/kernel_backends.md describes the contract.
//   none     record the hand-off package in the report and run nothing (default)
//   geak     an interface to GEAK: the kernel lane of the GEAK checkout args.kernel_backend_dir, run by the
//            kernel liaison as an outside program through GEAK's own workflow CLI; InferPilot ships
//            nothing of GEAK
//   command  a kernel agent's CLI, run by the kernel liaison from args.kernel_backend_command
const KERNEL_BACKENDS = ['none', 'geak', 'command'];
// The geak back end's command; args.kernel_backend_command replaces it when GEAK's entry points move.
const GEAK_COMMAND = 'node {kernel_backend_dir}/interface/runtime/engine/run_workflow.mjs '
  + '{kernel_backend_dir}/kernel_workflow/kernel_lane.js --args-file {task_dir}/lane_args.json --result-file {out_dir}/result.json';
const INFERPILOT = String(RAW.inferpilot_dir || '').replace(/\/+$/, '');
if (!INFERPILOT) {
  throw new Error('args.inferpilot_dir is required: absolute path of the InferPilot checkout ' +
    '(the directory containing inferpilot.js, roles/, knowledge/ and templates/)');
}

const int = (v, d) => {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : d;
};
const AGENT_RETRIES = Math.max(1, int(RAW.agent_retries, 3));
const TIMEOUT_GIVEN = RAW.agent_timeout_ms != null && String(RAW.agent_timeout_ms).trim() !== '';
const AGENT_TIMEOUT_MS = int(RAW.agent_timeout_ms, 3 * 3600 * 1000);
// Roles that run whole suites or benchmarks, and the issue reproduction (soaks, one arm per commit or
// configuration), get twice the default, unless a timeout is given.
const LONG_ROLES = new Set(['verify_engineer', 'perf_engineer', 'investigator']);
const LONG_CALLS = new Set(['issue_triager:reproduce']);
// A longer limit InferPilot gave a call that showed it needs one (callWithTime); never past a given timeout.
const GRANTED_MS = {};
const timeoutFor = (role, sub) => GRANTED_MS[`${role}:${sub}`] || GRANTED_MS[role]
  || (!TIMEOUT_GIVEN && (LONG_ROLES.has(role) || LONG_CALLS.has(`${role}:${sub}`)) ? 2 * AGENT_TIMEOUT_MS : AGENT_TIMEOUT_MS);
// What a role is told of its limit: 0 only when there is none.
const minutes = (ms) => (ms > 0 ? Math.max(1, Math.floor(ms / 60000)) : 0);
// The same to a hundredth of a minute, for a role that keeps a part of its limit in reserve: a limit
// rounded up can put the reserve's start before the call's.
const exactMinutes = (ms) => (ms > 0 ? Math.max(0.01, Math.round(ms / 600) / 100) : 0);
// A Verify round is not asked again by the engine, under what is left of the same limit: an answer of
// its that cannot be used goes to call()'s next attempt, which has a limit of its own and is told to
// check first what the one before left. A unit gate goes to the call that finishes it (UNIT_ONCE).
const ONE_SESSION = { schemaRetries: 0 };
// More time for a call (callWithTime) is at most this many times its limit.
const MAX_GRANT = 2;
// One task per machine (scripts/run_lock.py): a run that wrote nothing in its run directory for twice
// its longest agent call, one given the most time it can get, has died, and its lock may be taken over.
const MAX_CALL_MS = TIMEOUT_GIVEN ? AGENT_TIMEOUT_MS : MAX_GRANT * 2 * AGENT_TIMEOUT_MS;
const LOCK_IDLE_HOURS = MAX_CALL_MS > 0 ? Math.ceil((2 * MAX_CALL_MS) / 3600000) : 24;
const ATTENTION = []; // state a human must clean up before anything else runs there (R.attention)
const TIMED_OUT = { timedOut: true };
const TIMED_OUT_CALLS = new Set(); // labels of calls that timed out: their agents may still be running
let PHASE = '';
let curatorSilent = false; // the curator gave no answer: what it did to knowledge/ is not known
const cfg = (o) => Object.entries(o)
  .filter(([, v]) => v !== '' && v != null && !(Array.isArray(v) && v.length === 0))
  .map(([k, v]) => `- ${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
  .join('\n');

// ---------------------------------------------------------------------------
// Structured outputs. The role files describe every field; these schemas keep
// the fields this script branches on mandatory.
// ---------------------------------------------------------------------------
const S = (props, required) => ({ type: 'object', properties: props, required: required || [] });
const str = { type: 'string' };
const bool = { type: 'boolean' };
const num = { type: 'number' };
const obj = { type: 'object' };
const arr = (items) => (items ? { type: 'array', items } : { type: 'array' });
const strs = arr(str);
// A word for a POSIX shell, in a command the user is given to paste.
const shq = (s) => (/^[\w@%+=:,./-]+$/.test(String(s)) ? String(s) : `'${String(s).replace(/'/g, "'\\''")}'`);

// ---------------------------------------------------------------------------
// The task definition (templates/task_definition.md): the fields that define a task, named as the
// arguments are. A request in plain words, or a definition with gaps, becomes a complete one in the
// Intake phase before anything runs; operator settings (inferpilot_dir, exp_root, budgets, review_model)
// stay outside it.
// ---------------------------------------------------------------------------
const TASK_KEYS = ['mode', 'task', 'goal', 'framework', 'framework_dir', 'model_path', 'platform', 'gpu_ids',
  'container', 'workload', 'server_script', 'client_script', 'accuracy_script', 'harness', 'apply_prs', 'sources', 'since',
  'stop_after', 'resume_from', 'run_dir',
  'auto_approve_design', 'held_out', 'guards', 'sweep', 'tune', 'long_context', 'stability', 'long_context_script',
  'kernel_backend', 'kernel_backend_dir', 'kernel_backend_command', 'kernel_budget', 'rescan', 'upstream_repos',
  'image', 'image_ok', 'bisect_fixed', 'confirm_leads'];
// '' is accepted for the enums so an undecided field costs no schema retry; pick() drops it.
const TASK_DEF = S({
  mode: { type: 'string', enum: [...MODES, ''] },
  task: str,
  goal: str,
  framework: str,
  framework_dir: str,
  model_path: str,
  platform: str,
  gpu_ids: str,
  container: str,
  workload: { type: ['object', 'string'] },
  server_script: str,
  client_script: str,
  accuracy_script: str,
  harness: { type: ['object', 'string'] },
  apply_prs: strs,
  sources: strs,
  since: str,
  stop_after: str,
  resume_from: str,
  run_dir: str,
  kernel_backend: { type: 'string', enum: [...KERNEL_BACKENDS, ''] },
  kernel_backend_dir: str,
  kernel_backend_command: str,
  // set only when the request asks for the gate: true or false, as a word too; any other value ("yes", 1)
  // is asked for again, since on() would read it as off and the run would leave the gate out
  long_context: { enum: [true, false, 'true', 'false', ''] },
  stability: { enum: [true, false, 'true', 'false', ''] },
  long_context_script: str,
});
const INTAKE = S({
  status: { type: 'string', enum: ['ready', 'needs_input', 'rejected'] },
  task_def: TASK_DEF,
  provenance: obj,
  assumptions: strs,
  questions: arr(S({ field: str, question: str, why: str, options: arr(), default: {} }, ['field', 'question', 'why'])),
  reason: str,
  summary: str,
}, ['status', 'task_def', 'questions']);

const pick = (o, keys) => Object.fromEntries(keys.filter((k) => o && o[k] != null && o[k] !== '').map((k) => [k, o[k]]));
const asObject = (v) => {
  if (v && typeof v === 'object') return v;
  try {
    const o = JSON.parse(String(v || ''));
    return o && typeof o === 'object' ? o : {};
  } catch (e) {
    return {};
  }
};
const TASK_PROMPTS = {
  feature: 'Which upstream PR, design or paper should be brought to AMD (URL)?',
  issue: 'Which issue (URL or <repo>#<n>)?',
  ci: 'Which CI job or Failure Tracker cluster (URL, or tracker issue and cluster)?',
  perf: 'Which performance goal (model, metric, target)?',
};

// The phases each mode can stop after; a stop_after outside them would be ignored and the task would
// run to its end.
const STOP_PHASES = {
  feature: ['Setup', 'Spec', 'Recon', 'Design', 'Implement', 'Kernel', 'Verify', 'Performance', 'Review'],
  issue: ['Setup', 'Triage', 'Reproduce', 'Diagnose', 'Implement', 'Kernel', 'Verify', 'Performance', 'Review'],
  ci: ['Setup', 'Classify', 'Bisect', 'Implement', 'Kernel', 'Verify', 'Performance', 'Review'],
  perf: ['Setup', 'Profile', 'Plan', 'Implement', 'Kernel', 'Verify', 'Performance', 'Review'],
  scan: ['Collect'],
};
// The phases of an issue or a ci task that come before its fix: a run that stops after one of them changes no
// code, and has no fix for G2 or G5 to measure.
const BEFORE_A_FIX = {
  issue: ['setup', 'triage', 'reproduce', 'diagnose'],
  ci: ['setup', 'classify', 'bisect'],
};

// The answer that takes InferPilot's template (templates/harness/) for a script the user does not have.
const TEMPLATE = 'template';
const isTemplate = (v) => String(v || '').trim().toLowerCase() === TEMPLATE;

// What InferPilot's client runs is the task's to give: two lengths and a concurrency, in workload (isl, osl, conc)
// or in harness (ISL, OSL, CONCURRENCY), as scripts/harness.py takes them. One the task leaves out would come
// from the feature's source or from the template: a load nobody chose. A workload given in words is the
// director's to turn into the three, and cannot be read here.
const TEMPLATE_LOAD = { isl: ['ISL', 'input length'], osl: ['OSL', 'output length'], conc: ['CONCURRENCY', 'concurrency'] };
function templateLoadGaps(a) {
  const workload = asObject(a.workload);
  if (typeof a.workload === 'string' && a.workload.trim() && !Object.keys(workload).length) return [];
  const harness = asObject(a.harness);
  const given = (v) => v != null && String(v).trim() !== '';
  return Object.keys(TEMPLATE_LOAD).filter((k) => !given(workload[k]) && !given(harness[TEMPLATE_LOAD[k][0]]));
}
// The same in words, for a question: what the task says of the load InferPilot's client runs.
const loadGapWords = (gaps) => (gaps.length < Object.keys(TEMPLATE_LOAD).length
  ? `gives no ${gaps.map((k) => TEMPLATE_LOAD[k][1]).join(' and no ')} for it` : 'names none');

// What a definition still lacks before its mode can start; every gap is a question for the user.
function missingFields(a) {
  const qs = [];
  const ask = (field, question, why, options) => qs.push({ field, question, why, ...(options ? { options } : {}) });
  const mode = String(a.mode || '').trim().toLowerCase();
  if (!MODES.includes(mode)) {
    ask('mode', 'Which kind of task is this?', 'every phase after Intake depends on the mode', MODES);
    return qs;
  }
  if (mode !== 'scan' && !a.task) ask('task', TASK_PROMPTS[mode], 'it is the source of truth the run works from');
  if (mode !== 'scan' && !a.framework_dir) {
    ask('framework_dir', 'Which framework checkout should InferPilot take the code from (absolute path)?',
      'Setup clones the task\'s own working copy from it');
  }
  if ((mode === 'feature' || mode === 'perf') && !a.model_path) {
    ask('model_path', 'Which model should the baseline and the gates run (local path)?', 'Setup runs the baseline on it');
  }
  if (mode === 'perf' && !a.workload) {
    ask('workload', 'Which fixed workload should every measurement use (input/output lengths, concurrency, dataset)?',
      'performance numbers count only on a workload fixed before any profile is read');
  }
  if ((mode === 'feature' || mode === 'perf') && !a.server_script) {
    ask('server_script', 'Which script starts the server for the baseline (absolute path)? It must pass --port $PORT and $EXTRA_ARGS '
      + `to the server. Or ${TEMPLATE}: InferPilot's, after the model's sglang cookbook recipe.`,
      'every arm, baseline and feature, is launched with it: the feature arm adds its flags through EXTRA_ARGS', [TEMPLATE]);
  }
  // G5 measures every feature against the baseline, whether it claims a gain or not (validation_doctrine.md, G5 for
  // a feature), and every perf task: both name the client that measures. A feature is measured on its source's
  // scenarios, which InferPilot's client need not reproduce; a perf task's workload is the user's own, which it runs.
  if ((mode === 'perf' || mode === 'feature') && !a.client_script) {
    const feature = mode === 'feature';
    const onTemplate = feature ? " (give the workload with it), which never stands for the gain of the feature's source: on it G5 judges "
      + 'only that the feature is no slower than the baseline' : '';
    ask('client_script', `Which script runs the ${feature ? "feature's scenarios" : 'workload'} against the server (absolute path)? `
      + `It reads PORT and writes its logs to OUT_DIR. Or ${TEMPLATE}: InferPilot's sglang serving benchmark on the workload${onTemplate}.`,
      feature ? 'G5 measures every feature against the baseline with it, unchanged: the gain it claims, or no slowdown when it claims none'
        : 'it is the benchmark: every performance measurement runs it unchanged', [TEMPLATE]);
  }
  // InferPilot's client runs the task's workload: a task that takes it gives the whole of it. What it leaves out
  // the template would fill with lengths or a concurrency of its own, and G5 would judge the change on them.
  // A perf task without any workload is asked for one above.
  const gaps = isTemplate(a.client_script) ? templateLoadGaps(a) : [];
  const lacks = loadGapWords(gaps);
  if ((mode === 'feature' || (mode === 'perf' && a.workload)) && gaps.length) {
    ask('workload', `Which workload should InferPilot's client run${mode === 'feature' ? ' for the feature' : ''} (input and output `
      + 'lengths, concurrency)?',
      `client_script is ${TEMPLATE}: InferPilot's serving benchmark runs the task's workload, and the task ${lacks}`);
  }
  // A gate's field the caller set to anything but true or false would leave the gate out without a word.
  const asked = (v) => String(v).trim().toLowerCase();
  for (const [field, gate] of [['long_context', 'G3, the long-context gate'], ['stability', 'G4, the stability run of more than 48 hours']]) {
    if (a[field] != null && a[field] !== '' && !['true', 'false'].includes(asked(a[field]))) {
      ask(field, `Should the run include ${gate}?`,
        `${field} takes true or false, and ${JSON.stringify(a[field])} is neither: the run would leave the gate out`, ['true', 'false']);
    }
  }
  // G3 runs the task's own long-context test, as G4 keeps up the task's own client: the user asked for the gate
  // and says what it runs. InferPilot has no template for such a test, and no role writes one in its place. A
  // path of blanks names no script.
  const g3script = String(a.long_context_script || '').trim();
  if (mode !== 'scan' && asked(a.long_context) === 'true' && (!g3script || isTemplate(g3script))) {
    ask('long_context_script', 'Which script runs the long-context test for G3 (absolute path)? It reads PORT, runs its prompts '
      + "against the server and writes each request's result to OUT_DIR. InferPilot has no template for it.",
      'long_context is on: G3 runs this test unchanged on the baseline and on the change');
  }
  // A fix that changes code the server runs is measured against the base as a feature is: G2 on gsm8k, G5 on the
  // task's client (validation_doctrine.md, G2 for a fix, G5 for a fix). An issue or a ci task that can reach a fix
  // names the model and the client for it. One that stops before the fix (stop_after) needs neither.
  const stop = String(a.stop_after || '').trim().toLowerCase();
  const fixes = !!BEFORE_A_FIX[mode] && !BEFORE_A_FIX[mode].includes(stop);
  // G4 keeps the task's performance test up for more than 48 hours, and the user asked for it: the task names the
  // server that test runs against and the client that runs it. A feature and a perf task are asked for both above;
  // an issue or a ci task is asked here: for the server on G4's account, for the client on G4's or on G5's. No
  // template is offered for either.
  const g4 = (mode === 'issue' || mode === 'ci') && asked(a.stability) === 'true';
  if (g4 && !a.server_script) {
    ask('server_script', 'Which script starts the server that G4 keeps up for more than 48 hours (absolute path)? It must pass '
      + '--port $PORT and $EXTRA_ARGS to the server.',
      'stability is on: G4 runs its client back to back against one server, which this script starts');
  }
  if ((g4 || fixes) && !a.client_script) {
    const forG4 = 'stability is on: G4 runs this client back to back against one server';
    ask('client_script', `Which script runs the workload that ${fixes ? 'G5 measures the fix on' : 'G4 keeps up for more than 48 hours'} `
      + '(absolute path)? It reads PORT and writes its logs to OUT_DIR.',
      fixes ? 'a fix that changes code the server runs is measured against the base with it: G5 shows that the fix is no slower; with '
        + `stop_after set to a phase before the fix, no client is needed${g4 ? `; ${forG4}` : ''}` : forG4);
  }
  // A task that names InferPilot's client all the same gives the workload it runs: without one the template would
  // run lengths and a concurrency of its own, which nobody asked for: for G5's judgement, or for G4's two days.
  if ((g4 || fixes) && gaps.length) {
    ask('workload', `Which workload should InferPilot's client ${fixes ? 'run for the fix' : 'keep up for G4'} (input and output `
      + 'lengths, concurrency)?',
      `client_script is ${TEMPLATE}: InferPilot's serving benchmark runs the task's workload, and the task ${lacks}`);
  }
  const kb = String(a.kernel_backend || 'none').trim().toLowerCase();
  if (!KERNEL_BACKENDS.includes(kb)) {
    ask('kernel_backend', 'Where should kernel hand-offs go?', 'the Kernel phase runs the chosen back end', KERNEL_BACKENDS);
  }
  if (kb === 'geak' && !a.kernel_backend_dir) {
    ask('kernel_backend_dir', 'Where is the GEAK checkout (absolute path)?',
      'the geak back end runs the kernel lane of that checkout as an outside program');
  }
  if (kb === 'command' && !a.kernel_backend_command) {
    ask('kernel_backend_command', 'Which command runs a kernel task (with {task_dir}, {out_dir} and {gpu_ids})?',
      'the command back end runs it for every kernel hand-off');
  }
  // implement: a feature run that stopped at Design; verify: a run whose Verify ended without a result.
  const resume = String(a.resume_from || '').trim().toLowerCase();
  const resumable = mode === 'feature' ? ['implement', 'verify'] : mode === 'scan' ? [] : ['verify'];
  if (resume && !resumable.includes(resume)) {
    ask('resume_from', mode === 'scan' ? 'A scan cannot be resumed: drop resume_from?'
      : `resume_from in mode=${mode} supports ${resumable.map((r) => `"${r}"`).join(' and ')}: which, or drop it?`,
    'implement continues an approved feature design; verify runs Verify again on the commit a run verified in Implement', resumable);
  }
  if (resume && !a.run_dir) {
    ask('run_dir', resume === 'verify' ? 'Which run should Verify run again in (its run directory)?'
      : 'Which run stopped at Design (its run directory)?', 'the resumed run continues in it');
  }
  if (stop && !STOP_PHASES[mode].some((p) => p.toLowerCase() === stop)) {
    ask('stop_after', `After which phase of mode=${mode} should the run stop?`,
      `"${a.stop_after}" is not a phase of this mode; the run would ignore it and go on to the end`, STOP_PHASES[mode]);
  }
  if (fixes && !a.model_path) {
    ask('model_path', `Which model should G2 measure the fix on (local path)? ${mode === 'issue' ? "The reporter's model" : 'The model the failing test serves'}, `
      + 'when it is on this machine.',
      'a fix that changes code the server runs is measured on gsm8k against the base; with stop_after set to a phase before the fix, no model is needed');
  }
  return qs;
}

// Fields the caller set win over answers, answers over what the intake inferred; the same completeness
// check then applies to every definition, from a request or not. Nothing runs before it passes.
async function intakeStage(raw) {
  const request = String(raw.request || '').trim();
  const given = pick(raw, TASK_KEYS);
  const answers = pick(asObject(raw.answers), TASK_KEYS);
  if (!request && missingFields({ ...answers, ...given }).length === 0) return { args: { ...raw, ...answers, ...given } };
  enter('Intake');
  const res = await call('intake', 'define', { request, given, answers, inferpilot_dir: INFERPILOT }, INTAKE, 'intake');
  const merged = { ...(res && res.task_def ? pick(res.task_def, TASK_KEYS) : {}), ...answers, ...given };
  const asked = res && res.status === 'needs_input' ? (res.questions || []) : [];
  const questions = [...asked, ...missingFields(merged).filter((q) => !asked.some((a) => a.field === q.field))];
  const provenance = (res && res.provenance) || {};
  const assumptions = (res && res.assumptions) || [];
  const stopped = (status, summary, extra) => {
    log(`[inferpilot] intake: ${status} — ${summary}`);
    return { record: { mode: String(merged.mode || ''), status, stopped_at: 'Intake', run_dir: '', report_path: '',
      summary, task_def: merged, provenance, assumptions,
      phases: [{ phase: 'Intake', verdict: status, summary }], ...extra } };
  };
  if (res && res.status === 'rejected') {
    const why = res.reason || res.summary || 'outside what InferPilot does';
    return stopped('rejected', why, { reason: why, next: { say: `Outside what InferPilot does: ${why}` } });
  }
  if (questions.length) {
    // A field the caller set wins over an answer. One that is asked about all the same (a value the run cannot
    // use, or only a part of what it needs) is cleared for the next run, so that the answer takes its place.
    const rejected = Object.fromEntries(questions.filter((q) => q.field in given).map((q) => [q.field, '']));
    return stopped('needs_input', `missing: ${questions.map((q) => q.field).join(', ')}`, {
      questions,
      // null until the user answers: a run that still has it asks again rather than taking a placeholder
      next: { say: 'InferPilot needs these answers before it starts; nothing ran.', ask: questions,
        rerun_with: { ...rejected, answers: Object.fromEntries(questions.map((q) => [q.field, null])) } },
    });
  }
  return {
    args: { ...raw, ...merged },
    note: { phase: 'Intake', verdict: 'ready', summary: res ? (res.summary || 'task defined') : 'the intake returned nothing; started from the given fields' },
    provenance,
    assumptions,
  };
}

// sglang images carry a full-history checkout of sglang and install sglang from it in editable mode; each
// task works in it on its own branch, so what runs is what is checked out.
const IMAGE_SGLANG = '/sgl-workspace/sglang';
const START = await intakeStage(!RAW.framework_dir && String(RAW.framework || 'sglang').trim().toLowerCase() === 'sglang'
  ? { ...RAW, framework_dir: IMAGE_SGLANG } : RAW);
if (START.record) return START.record;
const A = START.args;
const MODE = String(A.mode).trim().toLowerCase();
const MAX_FIX_ROUNDS = int(A.max_fix_rounds, 2);          // extra implement rounds per work package
const MAX_INVESTIGATE = int(A.max_investigate_rounds, 3); // verify -> investigate -> verify cycles
const MAX_REVIEW = int(A.max_review_rounds, 2);           // review -> fix -> verify -> review cycles
const REVIEW_MODEL = String(A.review_model || '').trim();
const MAX_WPS = Math.max(1, int(A.max_work_packages, 12));
const STOP_AFTER = String(A.stop_after || '').trim().toLowerCase();
const IMAGE_WANTED = String(A.image || '').trim();  // the image the task asks for; empty: the newest daily one
const IMAGE_OK = A.image_ok === true || String(A.image_ok).toLowerCase() === 'true';
const AUTO_APPROVE_DESIGN = !(A.auto_approve_design === false || String(A.auto_approve_design).toLowerCase() === 'false');
// Performance checks beyond the task's own workload and goal, each off unless the task asks for it.
const on = (v) => v === true || String(v).toLowerCase() === 'true';
const HELD_OUT = on(A.held_out);
const GUARDS = on(A.guards);
const SWEEP = on(A.sweep);
const TUNE = on(A.tune);
const APPLY_PRS = (Array.isArray(A.apply_prs) ? A.apply_prs : (A.apply_prs ? [A.apply_prs] : [])).map(String);
const BISECT_FIXED = on(A.bisect_fixed);
// Correctness gates that take hours or days, each off unless the task asks for it: G3 (long context)
// and G4 (stability: one throughput test of more than 48 hours, validation_doctrine.md).
const LONG_CONTEXT = on(A.long_context);
const STABILITY = on(A.stability);
// leads cost GPU time only when asked: by default Spinoff lists them and runs nothing
const CONFIRM_LEADS = on(A.confirm_leads);
const RESUME = String(A.resume_from || '').trim().toLowerCase();
// The route this run logs: one resumed from Verify passes Setup, then Verify and what follows.
const routeOf = () => (RESUME === 'verify' ? ['Setup', ...ROUTES[MODE].slice(ROUTES[MODE].indexOf('Verify'))] : ROUTES[MODE]);
const EXP_ROOT = String(A.exp_root || `${INFERPILOT}/exp`).replace(/\/+$/, '');
const KERNEL_BACKEND = String(A.kernel_backend || 'none').trim().toLowerCase();
const KERNEL_BACKEND_DIR = String(A.kernel_backend_dir || '').replace(/\/+$/, '');
// Context handed to every agent; artifact paths are added as phases produce them.
const C = {
  mode: MODE,
  task: A.task ? (typeof A.task === 'string' ? A.task : JSON.stringify(A.task)) : '',
  inferpilot_dir: INFERPILOT,
  exp_root: EXP_ROOT,
  framework: String(A.framework || 'sglang'),
  framework_dir: String(A.framework_dir || ''),
  model_path: String(A.model_path || ''),
  platform: String(A.platform || ''),
  gpu_ids: String(A.gpu_ids || ''),
  container: String(A.container || ''),
  workload: A.workload ? (typeof A.workload === 'string' ? A.workload : JSON.stringify(A.workload)) : '',
  server_script: String(A.server_script || ''),
  client_script: String(A.client_script || ''),
  accuracy_script: String(A.accuracy_script || ''),
  harness: A.harness ? (typeof A.harness === 'string' ? A.harness : JSON.stringify(A.harness)) : '',
  apply_prs: APPLY_PRS,
  goal: String(A.goal || ''),
  held_out: HELD_OUT,
  guards: GUARDS,
  sweep: SWEEP,
  tune: TUNE,
  long_context: LONG_CONTEXT,
  stability: STABILITY,
  long_context_script: String(A.long_context_script || '').trim(),
  confirm_leads: CONFIRM_LEADS,
  assumptions: START.assumptions || [],
  kernel_backend: KERNEL_BACKEND,
  kernel_backend_dir: KERNEL_BACKEND_DIR,
  kernel_backend_command: String(A.kernel_backend_command
    || (KERNEL_BACKEND === 'geak' ? GEAK_COMMAND.split('{kernel_backend_dir}').join(KERNEL_BACKEND_DIR) : '')),
  kernel_budget: String(Math.max(1, int(A.kernel_budget, 6))),
  lock_idle_hours: String(LOCK_IDLE_HOURS),
  run_dir: '',
  resume_run_dir: String(A.run_dir || ''),
  resume_from: RESUME,
  branch: String(A.branch || ''),
  worktree: String(A.worktree || ''),
};

// Run record returned to the caller.
const R = {
  mode: MODE,
  status: 'running',
  stopped_at: '',
  run_dir: '',
  report_path: '',
  summary: '',
  task_def: pick(A, TASK_KEYS), // the definition this run started from
  provenance: START.provenance || {}, // per field: given | user | inferred | default
  assumptions: START.assumptions || [],
  phases: START.note ? [START.note] : [], // {phase, verdict, summary}
  done: [],            // verified work packages {wp, commit, evidence}
  handoffs: [],        // {id, owner: kernel_workflow | hyperloom, source, status, ...}
  known_issues: [],    // pre-existing failures proven by a same-pattern baseline control
  leads: [],           // problems a role met that are not this task's, not yet checked on the base commit
  spinoffs: [],        // per known issue or lead: tracked upstream, drafted as an issue, or unconfirmed
  knowledge_updates: [],
  attention: ATTENTION, // state a human must clean up before anything else runs there
};

function note(phaseTitle, verdict, summary) {
  R.phases.push({ phase: phaseTitle, verdict, summary: String(summary || '').slice(0, 600) });
}

// A stop the control file asks for "now" takes effect at the next phase boundary, wherever the route
// goes from there (Verify into Investigate as well); the closing phases always run.
class StopRequested extends Error {}
const CLOSING = new Set(['Spinoff', 'Reply', 'Report']);
function enter(title) {
  if (PHASE && !CLOSING.has(title) && liveStopAfter() === 'now') throw new StopRequested(PHASE);
  PHASE = title;
  phase(title);
}
// Under runtime/run.mjs, control() returns the run's control file, read at every phase boundary: its
// stop_after replaces the argument's while the run goes on, and "now" stops after the phase that is ending.
// The Workflow tool has no control global.
let stopAfterNow = STOP_AFTER;
let stopAfterSeen = STOP_AFTER;
// The stops the route has checked: one named there holds only if the run checks it again (a repair loop
// may; Spec never comes back). `at` is the stop being checked now, which is not behind the run.
const STOPS_PASSED = new Set();
function liveStopAfter(at = '') {
  if (typeof control !== 'function') return STOP_AFTER;
  let c = null;
  try {
    c = control();
  } catch { /* an unreadable file changes nothing */ }
  const s = c && typeof c.stop_after === 'string' ? c.stop_after.trim().toLowerCase() : stopAfterNow;
  if (s !== stopAfterSeen) {
    stopAfterSeen = s;
    const phaseName = (STOP_PHASES[MODE] || []).find((p) => p.toLowerCase() === s);
    const known = s === '' || s === 'now' || !!phaseName;
    const why = !known ? ` is not a phase of mode=${MODE}; the stop stays ${stopAfterNow || '(none)'}`
      : phaseName && s !== at && STOPS_PASSED.has(s) ? `; the run is past ${phaseName} and stops there only if it comes back to it` : '';
    log(`[inferpilot] control: stop_after=${s || '(none)'}${why}`);
    if (known) stopAfterNow = s;
  }
  return stopAfterNow;
}
const stopHere = (title) => {
  const t = title.toLowerCase();
  const s = liveStopAfter(t);
  STOPS_PASSED.add(t);
  return s !== '' && (s === 'now' || s === t);
};
// The same check where the verify, review and PR repair loops end a phase again: a stop named while the
// run is in such a loop ends the run there, as the route's own checks do on its first pass.
const stopAt = (title) => (stopHere(title) ? { ok: false, stopped: true, at: title } : null);
const ended = (r, at) => finish(r.stopped ? 'stopped' : 'blocked', r.at || at);

// Inputs every role sees: the context plus what earlier phases produced.
const inputs = (extra) => ({
  ...C,
  known_issues: R.known_issues,
  verified_work_packages: R.done,
  ...(extra || {}),
});

function rolePrompt(role, sub, ins, attempt, takesLeads) {
  const writes = role === 'intake'
    ? 'No RUN_DIR exists yet: change nothing; every check you run is read-only. Never push, never post on GitHub.'
    : `Write artifacts only under RUN_DIR (code changes only in the
task worktree named in the inputs; a baseline goes to the baseline cache only through scripts/baseline_cache.py).
Never push, never post on GitHub. The two exceptions go to InferPilot's own repository: the curator's
knowledge pull request (scripts/propose_knowledge.py) and the director's daily report issue
(scripts/share_daily_report.py).
Framework code runs from the tree under test, the task worktree (or a worktree at the base commit,
RUN_DIR/base or RUN_DIR/spinoff/base, for a run there): put it first on PYTHONPATH (sglang:
<tree>/python) for every server, test and benchmark.
Use only the GPUs in gpu_ids: export HIP_VISIBLE_DEVICES=<gpu_ids> for every server, test and
benchmark. Touch only the processes, servers and containers this run started (knowledge/guardrails.md).
As root in the container, give back what you write on host mounts: after every git write and before you
return, run ${INFERPILOT}/scripts/restore_owner.sh on RUN_DIR and on the worktree's .git
(knowledge/env_hygiene.md).`;
  const retry = attempt > 1 ? `
This is attempt ${attempt}: an earlier attempt of this call failed and may have left work behind —
commits, files in RUN_DIR, servers or processes it started. Check first (git status and the last
commits of the worktree, RUN_DIR, the run's process records), then continue from that state or clean it
up; never commit the same change twice or start a second server.` : '';
  const lead = takesLeads ? `
Something wrong you meet that is not this task's (a bug in code you read, a failure on a path the task
does not touch, a test or image that is broken for another reason): do not chase it. Return it in
\`leads\` and go on (knowledge/dev_methodology.md, Leads).` : '';
  return `You are the ${role}. PHASE=${sub}. MODE=${ins.mode || 'undecided'}.
First Read ${INFERPILOT}/roles/${role}.md and follow its instructions for PHASE=${sub}.
Read ${INFERPILOT}/knowledge/guardrails.md; its rules override anything else. Read every other knowledge
file the role names under ${INFERPILOT}/knowledge/ and fill the templates it names under ${INFERPILOT}/templates/.
Do all filesystem and shell work yourself. ${writes}${retry}${lead}

## Inputs
${cfg(ins)}

Return ONLY the structured JSON the role file specifies for PHASE=${sub} (a StructuredOutput tool is forced).`;
}

function withTimeout(p, ms) {
  if (typeof setTimeout !== 'function' || !(ms > 0)) return p;
  let to;
  const guard = new Promise((resolve) => {
    to = setTimeout(() => resolve(TIMED_OUT), ms);
  });
  const done = (v) => {
    if (typeof clearTimeout === 'function') clearTimeout(to);
    return v;
  };
  return Promise.race([p.then(done, (e) => { done(null); throw e; }), guard]);
}

// One agent call; resolves null (never throws) so every caller decides what a missing answer means
// for its gate. A failed attempt is retried and the retry is told to check what it left behind, unless
// the caller takes over (opts.attempts; opts.schemaRetries for the engine's asking again with the same
// prompt). A timeout is not retried: the timed-out agent cannot be cancelled and may still be running,
// so a retry would run the role twice at once.
// Every role that works on the task may hand on what it meets that is not the task's; Spinoff checks it.
const LEAD = S({ title: str, observed: str, where: str, evidence: str, reproduce: str }, ['title', 'observed']);
// a function, not a const: the Intake calls in before MODE and the consts below exist. Spinoff and what
// runs after it take none: nothing would hand their leads on.
function takesLeads(role, sub) {
  if (['intake', 'scout', 'dispatcher', 'curator'].includes(role)) return false;
  return MODE !== 'scan' && !(role === 'director' && sub === 'report')
    && !(role === 'issue_triager' && (sub === 'spinoff' || sub === 'reply')) && !(role === 'reviewer' && sub === 'reply');
}
const withLeads = (schema) => (schema && schema.type === 'object'
  ? { ...schema, properties: { ...schema.properties, leads: arr(LEAD) } } : schema);

async function call(role, sub, ins, schema, label, opts) {
  const lbl = label || `${role}:${sub}`;
  const ms = timeoutFor(role, sub);
  const leads = takesLeads(role, sub);
  const { attempts = AGENT_RETRIES, ...agentOpts } = opts || {};
  for (let attempt = 1; attempt <= attempts; attempt++) {
    // Each attempt has a timeout of its own, and a role told its limit gets the moment it ends.
    const told = ins.time_limit_min > 0 && ms > 0
      ? { ...ins, deadline_utc: new Date(Date.now() + ms).toISOString() } : ins;
    try {
      const r = await withTimeout(agent(rolePrompt(role, sub, told, attempt, leads),
        { label: lbl, phase: PHASE, schema: leads ? withLeads(schema) : schema, ...agentOpts }), ms);
      if (r === TIMED_OUT) {
        const min = Math.round(ms / 60000);
        log(`  [inferpilot] ${lbl} timed out after ${min} min; not retried`);
        TIMED_OUT_CALLS.add(lbl);
        ATTENTION.push({ call: lbl, issue: `timed out after ${min} min and may still be running: check its servers and GPUs before reusing them` });
        return null;
      }
      if (r) {
        if (leads && Array.isArray(r.leads)) {
          for (const l of r.leads) if (l && l.title) R.leads.push({ ...l, from: lbl });
        }
        return r;
      }
      log(`  [inferpilot] ${lbl} returned nothing (attempt ${attempt}/${attempts})`);
    } catch (e) {
      log(`  [inferpilot] ${lbl} failed (attempt ${attempt}/${attempts}): ` +
        String(e && e.message ? e.message : e).slice(0, 200));
    }
  }
  return null;
}

// A call that stopped before work that would not fit runs again, once, with the limit it asked for, up
// to MAX_GRANT times its own (needs: its needs_min, 0 when it ended for any other reason); nobody is
// asked to choose a time. A timeout the user gave stays the limit. ins(limit, earlier) builds the
// inputs, earlier the answer of the call before more time; schemaAgain, when given, checks that call's
// answer. Returns the answer, the earlier one, and with needs set, what it still needs.
async function callWithTime({ role, sub, grantKey, what, label, schema, schemaAgain, ins, needs: needsOf, opts }) {
  let earlier = null;
  for (let again = false; ; again = true) {
    const limit = minutes(timeoutFor(role, sub));
    const r = await call(role, sub, ins(limit, earlier), again && schemaAgain ? schemaAgain : schema,
      `${label}${again ? '+time' : ''}`, opts);
    const needs = limit && r ? Math.ceil(Number(needsOf(r)) || 0) : 0;
    if (!needs) return { r, earlier };
    if (again || TIMEOUT_GIVEN) return { r, earlier, needs, limit, short: again ? 'the limit it asked for' : 'agent_timeout_ms' };
    // past it, the run lock's idle window (MAX_CALL_MS) no longer covers the call: a human gives a timeout
    if (needs > MAX_GRANT * limit) return { r, earlier, needs, limit, short: `more than ${MAX_GRANT} times its limit` };
    const grant = Math.max(needs, limit);
    GRANTED_MS[grantKey] = grant * 60000;
    note(PHASE, 'more_time', `${what} needs ${needs} min and had ${limit}: run again with a ${grant}-min limit`);
    earlier = r;
  }
}

// Schemas of the phases after Intake (the helpers are defined above).
const WP = S({
  id: str,
  goal: str,
  kind: { type: 'string', enum: ['framework', 'kernel', 'test', 'docs', 'apply_pr'] },
  files: strs,
  test: str,
  depends_on: strs,
}, ['id', 'goal', 'test']);
const WPS = arr(WP);

const SETUP = S({
  lock: { type: 'string', enum: ['acquired', 'busy', 'skipped'] }, // skipped: stopped at the image check, nothing created
  image: obj, // {current, wanted, verdict} when the image check stops the run
  run_dir: str,
  baseline_status: { type: 'string', enum: ['pass', 'fail', 'skip'] },
  baseline: str,
  branch: str,
  worktree: str,
  gpu_ids: str, // the GPU set every later role uses
  // The scripts every later measurement runs: the task's own, or rendered from templates/harness/
  server_script: str,
  client_script: str,
  accuracy_script: str,
  notes: str,
}, ['lock', 'run_dir', 'baseline_status', 'gpu_ids']);
// workload_gap: the task's client does not run the scenarios the source measured its gain on; the run asks.
// perf_cost: perf_claim is a cost the user stated (a bound), not a gain: it is the claim on any client.
const SPEC = S({ spec_path: str, gates_measurable: bool, perf_claim: str, perf_cost: bool, skill: str, notes: str,
  workload_gap: str, fallback_client: str }, ['spec_path', 'gates_measurable']);
const RECON = S({
  design_path: str,
  subsystems: arr(S({ name: str, files: strs, hook: str }, ['name'])),
  unmapped: strs,
  gap_type: str,
  level: { type: 'string', enum: ['L1', 'L2', 'L3'] },
  needs_kernel: bool,
  summary: str,
}, ['design_path', 'unmapped', 'level']);
const DESIGN = S({
  design_path: str,
  work_packages: WPS,
  gate_ok: bool,
  needs_human_review: bool,
  perf_claim: str, // required, '' for none: a resumed run measures exactly this claim
  perf_cost: bool, // the claim is a cost the user stated, as the spec has it
  summary: str,
}, ['work_packages', 'gate_ok', 'perf_claim']);
const TRIAGE = S({
  priority: { type: 'string', enum: ['P0', 'P1', 'P2'] },
  category: {
    type: 'string',
    enum: ['wrong result', 'crash', 'startup or build failure', 'performance', 'feature request', 'CI', 'tool'],
  },
  action: { type: 'string', enum: ['reproduce', 'push_existing_pr', 'close_fixed', 'needs_info', 'not_amd', 'plan'] },
  reason: str,
  existing_prs: strs,
  triage_path: str,
  reply_draft_path: str,
}, ['priority', 'category', 'action', 'triage_path']);
// reproduced: on current main, the code a fix would change; reproduced_on_reporter: on the reporter's
// image or commit, left out when that could not run here.
const REPRO = S({ reproduced: bool, reproduced_on_reporter: bool, repro_cmd: str, perf_claim: str, evidence: str,
  needs_min: num, // the arms left would not fit: the limit the call that runs them needs, from its start
}, ['reproduced', 'repro_cmd', 'evidence']);
// The call with more time: what it leaves out is the earlier call's.
const REPRO_MORE = S(REPRO.properties, ['reproduced']);
const REPLY = S({ reply_draft_path: str, summary: str }, ['reply_draft_path']);
// The reviewer's check of the reply draft: each claim the run does not bear out, what the run shows
// instead and where; none when the draft says only what the run showed.
const REPLY_CHECK = S({ findings: arr(S({ claim: str, issue: str, evidence: str }, ['claim', 'issue'])),
  check_path: str, summary: str }, ['findings', 'check_path']);
// tracked: upstream names the issue or PR that already tracks it; drafted: draft_path, an issue for a
// human to file; unconfirmed: a lead the base commit did not show, note says what would.
const SPINOFF = S({
  spinoffs: arr(S({
    id: str,
    source: { type: 'string', enum: ['known_issue', 'lead'] },
    status: { type: 'string', enum: ['tracked', 'drafted', 'unconfirmed'] },
    upstream: str,
    draft_path: str,
    task_definition: str,
    known_issue: obj, // a lead the base commit confirmed, in the known_issues.md format
    base_import: str, // with known_issue: sglang.__file__ from that base run's first log
    note: str,
  }, ['id', 'source', 'status'])),
  summary: str,
}, ['spinoffs']);
const DIAG = S({
  verdict: { type: 'string', enum: ['fix_needed', 'baseline_bug', 'not_amd_side', 'inconclusive'] },
  root_cause: str,
  work_packages: WPS,
  known_issue: obj,
  summary: str,
}, ['verdict', 'root_cause']);
const CLASSIFY = S({
  failure_class: { type: 'string', enum: ['code_regression', 'dependency_bump', 'test_bug', 'flaky', 'infrastructure'] },
  failing_test_cmd: str,
  window: str,
  // the test fails on a number it measures (a throughput, a latency, a speed threshold), not on an error:
  // perf_claim is the number the fix must restore, as an issue's reproduction states it
  performance: bool,
  perf_claim: str,
  evidence: str,
  triage_path: str,
}, ['failure_class', 'failing_test_cmd', 'evidence', 'triage_path']);
const BISECT = S({ culprit: str, confirmed: bool, work_packages: WPS, evidence: str }, ['culprit', 'confirmed']);
const BISECT_FIX = S({ fix_commit: str, confirmed: bool, evidence: str }, ['fix_commit', 'confirmed']);
const PROFILE = S({
  analysis_path: str,
  path_ok: bool,
  path_evidence: str,
  not_run: str, // another job held a GPU of the machine: who held which; no profile was taken
  metrics: obj,
  opportunities: arr(S({
    id: str,
    kind: str,
    where: str,
    evidence: str,
    upper_bound_pct: num,
    owner: { type: 'string', enum: ['inferpilot', 'kernel_workflow', 'hyperloom'] },
  }, ['id', 'kind', 'owner'])),
}, ['analysis_path', 'path_ok', 'opportunities']);
const PERF_PLAN = S({
  work_packages: WPS,
  handoffs: arr(S({
    id: str,
    owner: { type: 'string', enum: ['kernel_workflow', 'hyperloom'] },
    spec_path: str,
  }, ['id', 'owner'])),
  perf_claim: str,
  gate_ok: bool,
}, ['work_packages', 'gate_ok']);
const IMPL = S({
  wp: str,
  status: { type: 'string', enum: ['done', 'blocked'] },
  commit: str,
  unit_test_cmd: str,
  files_changed: strs,
  kernel_handoff: { type: ['object', 'null'] },
  notes: str,
}, ['wp', 'status']);
// incomplete: the checks that have no result yet, none of those that ran having failed, and where the
// runs still going write; '' when every check has a result. A gate without a result is not a failed one.
// null: a check not run yet, which only a gate without a result has.
const UNIT = S({
  wp: str,
  unit_pass: bool,
  feature_off_unchanged: { type: ['boolean', 'null'] },
  fails_without_change: { type: ['string', 'null'], enum: ['parent', 'mutant', 'no', 'n/a', null] },
  incomplete: str,
  failure_summary: str,
  evidence: str,
}, ['wp', 'unit_pass', 'feature_off_unchanged', 'fails_without_change', 'incomplete']);
const ROLLBACK = S({ wp: str, reset_to: str, clean: bool }, ['reset_to', 'clean']);
const VERIFY = S({
  all_pass: bool,
  gates: obj,
  // blocking false: a report-only item of the acceptance matrix, or a G4 failure the investigator found on the
  // baseline too (a known issue): reported, and not investigated (again)
  failures: arr(S({ gate: str, failure_class: str, counts: str, evidence: str, blocking: bool }, ['gate'])),
  needs_min: num, // the round stopped before a suite that would not fit: the limit it needs, from its start
  // a fix (issue, ci): false when every file it changes is a test, a documentation file or a CI file of the
  // repository, which G2 and G5 do not measure; left out, the fix counts as one that changes code the server runs
  server_code_changed: bool,
  ledger_path: str,
  summary: str,
}, ['all_pass', 'failures']);
// in_use: '' when the pinned GPUs are idle; otherwise each one another job uses, its VRAM%, GPU% and pids
const GPU_CHECK = S({ in_use: str }, ['in_use']);
const INVESTIGATE = S({
  verdict: { type: 'string', enum: ['fix_needed', 'baseline_bug', 'kernel_regression', 'inconclusive'] },
  work_packages: WPS,
  known_issue: obj,
  kernel_handoffs: strs, // kernel_regression: the integrated hand-offs whose kernels cause the failure
  counterexample: str,   // kernel_regression: the failing case, given to the kernel back end as a test
  summary: str,
}, ['verdict']);
const PERF = S({
  claim_reproduced: bool,
  held_out_ok: bool,
  guards_ok: bool,
  workload_unchanged: bool,
  path_ok: bool,
  not_run: str, // another job held a GPU of the machine: who held which; no number was taken
  // a fix whose base cannot serve the configuration, in a run that knows no commit that served it: why the
  // fix's numbers have nothing to be judged against. They are reported.
  no_baseline: str,
  results: arr(),
  summary: str,
}, ['claim_reproduced', 'held_out_ok', 'guards_ok', 'workload_unchanged', 'path_ok']);
const REVIEW = S({
  verdict: { type: 'string', enum: ['approve', 'changes_requested', 'blocked'] },
  findings: arr(S({
    severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
    where: str,
    issue: str,
    evidence: str,
    hack: str, // knowledge/reward_hacks.md id, or "new"
  }, ['severity', 'issue'])),
  work_packages: WPS,
  review_path: str,
  summary: str,
}, ['verdict', 'findings', 'review_path']);
const PR = S({
  branch: str,
  title: str,
  commits: strs,
  lint_pass: bool,
  feature_off_final: bool,
  content_unchanged: bool, // the branch's tree is the one Verify and Review passed
  merges_cleanly: bool,
  leak_scan_findings: strs, // what scripts/leak_scan.py still finds in the diff, the commits or the body; empty when clean
  diff_stat: str, // files changed, lines added and removed
  launch_checked: bool, // the description's command (launch, failing test or reproduction) ran once on the final commit and held, the local weights for its model id
  pr_body_path: str,
  push_command: str,
  work_packages: WPS, // what the branch still lacks; implemented, verified and reviewed like a finding
}, ['branch', 'lint_pass', 'feature_off_final', 'content_unchanged', 'pr_body_path', 'leak_scan_findings', 'launch_checked']);
const K_PACKAGE = S({
  task_dir: str,
  mode: { type: 'string', enum: ['optimize', 'author'] },
  target_language: str,
  op_spec: obj,
  workload_spec_path: str,
  task: str,
  notes: str,
}, ['task_dir', 'mode']);
const K_RUN = S({ exit_code: num, result_path: str, notes: str }, ['exit_code']);
const K_COLLECT = S({
  status: { type: 'string', enum: ['improved', 'no_gain', 'failed'] },
  speedup: num,
  evidence: str,
  integrate_wp: { type: ['object', 'null'], properties: WP.properties, required: WP.required },
  notes: str,
}, ['status']);
// resume_from=verify: what the resumed run had, restored from its run directory. head_ok: the branch head
// is the commit its last Verify round ran on; the mode's gate command and the claim are the ones its Verify
// and Performance were given, perf_required whether its Performance was to judge a claim the run stated
// (perf_cost: a feature's that is a cost the user stated, which the answer always says: a claim not said to be
// one is a gain, never claimed on InferPilot's client; a feature that states none, and a fix of a problem
// that is not one of performance, are judged as no slower, which is no claim to restore);
// known_issues and handoffs as its report wrote them (Verify judges a failure the baseline shares by the
// first, and reverts an integrated kernel by the second), null when the run directory lost them;
// task_definition as its run.json recorded it.
const RESUMED = S({
  head_ok: bool,
  head: str,
  // [] when the run's report says none was left (a kernel-only plan whose integration was reverted), null
  // when the run directory does not record them
  verified_work_packages: { type: ['array', 'null'], items: S({ wp: str, commit: str, evidence: str }, ['wp', 'commit']) },
  known_issues: { type: ['array', 'null'], items: obj },
  handoffs: { type: ['array', 'null'], items: obj },
  task_definition: { type: ['object', 'null'] },
  repro_cmd: str,        // issue: the issue_repro gate
  failing_test_cmd: str, // ci: the failing_test gate
  perf_required: bool,
  perf_claim: str,       // '' for none
  perf_cost: bool,       // feature: the claim is a cost the user stated, as its spec has it
  design_auto_approved: bool, // feature: its Design went on to Implement with no human's approval
  triage_path: str,
  spec_path: str,
  design_path: str,
  analysis_path: str,
  notes: str,
}, ['head_ok', 'verified_work_packages', 'known_issues', 'handoffs', 'task_definition', 'perf_required', 'perf_claim',
  'perf_cost', 'design_auto_approved']);
// What a resumed run must measure as the run it resumes did, the gates its task asked for (G3, G4) and the
// test G3 runs among them; the machine, the task's wording and the operator settings may differ. GIVEN holds
// them as Setup is given them and records them in run.json: Setup's rendered scripts replace a template's
// in C later.
const MEASURED = ['mode', 'model_path', 'workload', 'server_script', 'client_script', 'accuracy_script', 'harness', 'apply_prs',
  'held_out', 'guards', 'sweep', 'tune', 'long_context', 'stability', 'long_context_script'];
const GIVEN = Object.fromEntries(MEASURED.map((k) => [k, k in C ? C[k] : A[k]]));
// One form for a setting however it was written down: JSON as an object or in a string, a flag unset or
// false, a list of one as that one. Compared as JSON, which keeps the boundaries of every value: a comma
// or a colon in a path is not a second setting.
function plain(v) {
  if (typeof v === 'string') {
    const t = v.trim();
    if (/^[[{]/.test(t)) {
      try {
        return plain(JSON.parse(t));
      } catch (e) {
        return t;
      }
    }
    return t === 'false' ? '' : t;
  }
  if (v === undefined || v === null || v === false) return '';
  if (Array.isArray(v)) return v.length < 2 ? plain(v[0]) : v.map(plain);
  if (typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, plain(v[k])]));
  return String(v);
}
// `harness` holds parameter lines, read as scripts/harness.py renders them (value_of): a flag is 1 or 0,
// so false is a value and not an unset line; a list is its items; a line set to nothing is a setting too
// (REASONING_EFFORT empty sends none, unset its default), and a space is part of a value. What only a line's
// type decides (a word for a flag, the spaces of a list) stays as written: written another way it stops the
// resume, which then measures nothing.
function harnessLines(v) {
  let h = v;
  if (typeof h === 'string' && /^\s*\{/.test(h)) {
    try {
      h = JSON.parse(h);
    } catch (e) {
      return plain(h);
    }
  }
  if (!h || typeof h !== 'object' || Array.isArray(h)) return plain(h);
  const line = (x) => (typeof x === 'boolean' ? (x ? '1' : '0')
    : Array.isArray(x) ? x.map(String).join(' ') : x === null || x === undefined ? '' : String(x));
  const lines = Object.keys(h).sort().map((k) => [k, line(h[k])]);
  return lines.length ? lines : '';
}
const canon = (v, key = '') => JSON.stringify(key === 'harness' ? harnessLines(v) : plain(v));
const REPORT = S({ report_path: str, summary: str }, ['report_path']);
// A scan's report always says how its daily report went out: empty strings when it did not.
const SCAN_REPORT = S({ ...REPORT.properties, daily_report_url: str, daily_report_action: str },
  ['report_path', 'daily_report_url', 'daily_report_action']);
const LEARN = S({ updates: arr(S({ file: str, change: str }, ['file'])), skill_status: str, pr_url: str, patch_path: str,
  notes: str }, ['updates']);
const COLLECT = S({ lock: { type: 'string', enum: ['acquired', 'busy', 'skipped'] }, run_dir: str, candidates_path: str,
  counts: obj, recent: obj, notes: str }, ['lock', 'run_dir', 'candidates_path']);
const DISPATCH = S({
  queue_path: str,
  top: arr(S({ rank: num, task: str, type: str, level: str, priority: str, source: str, task_def: obj }, ['task'])),
  matrix_changes: num,
}, ['queue_path', 'top']);

// ---------------------------------------------------------------------------
// Shared stages
// ---------------------------------------------------------------------------
// A lead counts as confirmed only when its base run imported the framework from a base worktree of this run.
// A path with '..' could start with a base worktree and still leave it.
function checkedOnBase(s) {
  if (s.source !== 'lead' || !s.known_issue) return s;
  const imp = String(s.base_import || '');
  const inBase = [`${C.run_dir}/base/`, `${C.run_dir}/spinoff/base/`].some((t) => imp.startsWith(t));
  if (inBase && !/(^|\/)\.\.(\/|$)/.test(imp)) return s;
  const { known_issue, draft_path, ...rest } = s;
  return { ...rest, status: 'unconfirmed', ...(draft_path ? { withdrawn_draft: draft_path } : {}),
    note: `${s.note ? `${s.note} ` : ''}Not counted as confirmed: its base run imported the framework from `
      + `${imp || 'a path it did not record'}, not from a base worktree of this run.` };
}

// The triager rewrites reply_draft.md in place: a call that returned nothing may have left it half
// rewritten, and one that timed out may be writing it still.
const halfDone = (label, what) => ({ claim: 'the whole draft',
  issue: `not checked: ${what} returned nothing, so the draft is an earlier one or one rewritten halfway`
    + (TIMED_OUT_CALLS.has(label) ? `; ${label} timed out and may still be writing it: stop it before reading or posting the draft` : '') });

// The reply draft goes to the issue's author and maintainers: the reviewer checks it against the run, and
// what it finds goes back to the triager, once; what is still left, or a draft nobody checked, the user
// hears of before posting it.
async function checkReply(replyIns) {
  const opts = REVIEW_MODEL ? { model: REVIEW_MODEL } : undefined;
  for (let round = 0; ; round++) {
    const chk = await call('reviewer', 'reply', inputs({ ...replyIns, reply_draft_path: C.reply_draft_path, round }),
      REPLY_CHECK, `reviewer:reply#${round}`, opts);
    if (!chk) {
      // The record of the round before, when there was one, is of the draft before its rewrite.
      C.reply_check_path = '';
      C.reply_findings = [{ claim: 'the whole draft',
        issue: `not checked${round ? ' since it was rewritten' : ''}: the reviewer returned nothing` }];
      note('Reply', 'unchecked', `the reviewer returned nothing: the ${round ? 'rewritten ' : ''}reply draft is not checked against the run`);
      return;
    }
    // A finding is one whatever it leaves out: an entry without a claim, or without anything, is not a clean
    // check. A check that wrote no record of itself, or names another file as its record, is none.
    const said = (v) => String(v || '').trim();
    const findings = (chk.findings || []).map((f) => (f && typeof f === 'object' ? f : {})).map((f) => ({ ...f,
      claim: said(f.claim) || 'a claim the reviewer did not quote',
      issue: said(f.issue) || `the reviewer did not say what the run shows${said(f.evidence) ? ` (its evidence: ${said(f.evidence)})` : ''}` }));
    const named = said(chk.check_path).replace(/\/{2,}/g, '/');
    C.reply_check_path = named === `${C.run_dir}/reply_check.md` ? named : '';
    if (!C.reply_check_path) {
      C.reply_findings = [...findings, { claim: 'the whole draft', issue: named
        ? `not checked: the reviewer gave ${named} as its record, which is not this run's reply_check.md`
        : 'not checked: the reviewer wrote no reply_check.md' }];
      note('Reply', 'unchecked', `the reviewer named ${named ? 'another file as its' : 'no'} reply_check.md: the reply draft is not checked against the run`);
      return;
    }
    if (!findings.length) {
      C.reply_findings = [];
      note('Reply', 'checked', `${round ? 'rewritten from the reviewer\'s findings; ' : ''}it says only what the run showed`);
      return;
    }
    C.reply_findings = findings;
    if (round >= 1) {
      note('Reply', 'findings', `${findings.length} claim(s) of the rewritten draft the run does not bear out: ${C.reply_check_path || ''}`);
      return;
    }
    const again = await call('issue_triager', 'reply', inputs({ ...replyIns, reply_findings: findings }), REPLY,
      'issue_triager:reply#fix');
    if (!again || !String(again.reply_draft_path || '').trim()) {
      C.reply_findings = [...findings, halfDone('issue_triager:reply#fix', 'the rewrite from these findings')];
      note('Reply', 'findings', `${findings.length} claim(s) the run does not bear out, and the rewrite returned nothing`);
      return;
    }
    C.reply_draft_path = again.reply_draft_path;
  }
}

async function finish(status, at) {
  R.status = status;
  R.stopped_at = at || '';
  // A stop the control file asked for is the report's to explain: stop_after did not ask.
  if (status === 'stopped' && stopAfterNow !== STOP_AFTER) note(at, 'stopped', `the control file asked: stop_after=${stopAfterNow}`);
  if (C.run_dir) {
    // What the run found that is not its own to fix is someone's: find it upstream or draft the issue.
    // Leads are run on the base commit only with confirm_leads; the GPUs are the task's.
    if (R.known_issues.length || R.leads.length) {
      enter('Spinoff');
      // A base run that fails for another reason would pass for a confirmed lead: a timed-out agent may still
      // hold the GPUs, and a run that ended at Setup never showed the environment works.
      const skipped = !C.confirm_leads ? [] : [...TIMED_OUT_CALLS].map((c) => `${c} timed out`)
        .concat(leftRunning)
        .concat(at === 'Setup' ? ['the run stopped at Setup'] : [])
        .concat(liveStopAfter() === 'now' ? ['the control file asked the run to stop now'] : []);
      const sp = await call('issue_triager', 'spinoff', inputs({ status, stopped_at: at || '', phases: R.phases,
        leads: R.leads, attention: R.attention, confirm_leads: C.confirm_leads && !skipped.length, confirm_skipped: skipped }), SPINOFF);
      if (sp && Array.isArray(sp.spinoffs)) {
        R.spinoffs = sp.spinoffs.filter(Boolean).map(checkedOnBase);
        for (const s of R.spinoffs) if (s.source === 'lead' && s.known_issue) R.known_issues.push(s.known_issue);
      }
    }
    // The Triage draft predates every result: by now it promises steps that were done, or changed.
    if (MODE === 'issue' && C.triage_path && at !== 'Triage') {
      enter('Reply');
      const replyIns = { status, stopped_at: at || '', phases: R.phases, spinoffs: R.spinoffs };
      const rp = await call('issue_triager', 'reply', inputs(replyIns), REPLY);
      if (rp && String(rp.reply_draft_path || '').trim()) {
        C.reply_draft_path = rp.reply_draft_path;
        await checkReply(replyIns);
      } else if (C.reply_draft_path) C.reply_findings = [halfDone('issue_triager:reply', 'the rewrite from how the run ended')];
    }
    if (MODE === 'issue') {
      R.reply_draft_path = C.reply_draft_path || '';
      // Only a draft the reviewer checked clean is the user's to post as it is.
      if (R.reply_draft_path && !C.reply_findings) {
        C.reply_findings = [{ claim: 'the whole draft', issue: at === 'Triage'
          ? 'not checked by the reviewer: the run ended at Triage, before the check'
          : 'not checked: no draft was rewritten from how the run ended, so this is an earlier draft' }];
      }
      if (C.reply_findings) R.reply_findings = C.reply_findings;
    }
    enter('Report');
    // A Dispatch that failed or timed out may have left a partial daily report: only a queued scan shares one.
    const rep = await call('director', 'report',
      inputs({ status, stopped_at: at || '', phases: R.phases, handoffs: R.handoffs, attention: R.attention,
        spinoffs: R.spinoffs, ...(MODE === 'scan' ? { share_daily_report: status === 'queued' } : {}) }),
      MODE === 'scan' ? SCAN_REPORT : REPORT);
    if (rep) {
      R.report_path = rep.report_path;
      R.summary = rep.summary || '';
    }
    if (MODE === 'scan') {
      R.daily_report_url = (rep && rep.daily_report_url) || '';
      R.daily_report_action = (R.daily_report_url && rep.daily_report_action) || '';
    }
    const learn = await call('curator', 'learn',
      inputs({ status, report_path: R.report_path, phases: R.phases }), LEARN);
    if (learn) {
      R.knowledge_updates = learn.updates || [];
      R.knowledge_pr = learn.pr_url || '';
      R.knowledge_notes = learn.notes || '';
      R.knowledge_patch = R.knowledge_pr ? '' : String(learn.patch_path || '').trim();
    } else {
      // No answer says nothing of what it did: it may have edited knowledge/ and saved a patch all the same.
      R.knowledge_notes = TIMED_OUT_CALLS.has('curator:learn') ? 'the curator timed out' : 'the curator returned no answer';
      curatorSilent = true;
    }
  }
  R.run_dir = C.run_dir;
  R.next = nextSteps(status, at);
  log(`[inferpilot] mode=${MODE} status=${status}${at ? ` at ${at}` : ''}` +
    `${R.report_path ? ` report=${R.report_path}` : ''}${R.attention.length ? ` attention=${R.attention.length}` : ''}`);
  return R;
}

// What comes after a run, the same for whoever runs InferPilot for the user (Claude Code, a Cursor agent,
// the runner's log): next.say tells the user; next.ask holds questions only the user answers;
// next.rerun_with sets arguments for running inferpilot.js again with the others unchanged (answers merge
// with those already given); next.start offers new runs, each with its arguments; next.user is what only
// the user does.
const ENDED = { blocked: 'Blocked', handed_off: 'Handed over', triaged: 'Triaged', fixed_on_main: 'Fixed on main already',
  not_reproduced: 'Not reproduced', diagnosed: 'Diagnosed, with no fix to make here', no_gain: 'No measured gain to keep',
  dropped: 'Dropped, with no measured gain' };
function nextSteps(status, at) {
  const report = R.report_path ? ` The report: ${R.report_path}.` : '';
  // Where the run ended, not what a closing phase (the reply check) recorded after it.
  const last = ([...R.phases].reverse().find((p) => !CLOSING.has(p.phase)) || {}).summary || '';
  const out = { say: `${ENDED[status] || `Ended ${status}`} at ${at}${last ? `: ${last}` : ''}.${report}` };
  if (status === 'blocked') out.say += ' Fix what it names, or change the task, then run it again.';
  if (status === 'ready_for_review') {
    out.say = `A pull request is ready on ${(R.pr && R.pr.branch) || C.branch}: read every changed line, re-run its tests `
      + `and measurements yourself as the report's Before pushing section gives them, then push it.${report}`;
  } else if (status === 'queued') {
    const shared = !R.daily_report_url ? ''
      : R.daily_report_action === 'exists'
        ? ` That day's daily report was shared before this scan: ${R.daily_report_url}; this scan's stays at ${C.run_dir}/daily_report.md.`
        : ` The daily report is shared: ${R.daily_report_url}.`;
    out.say = `${R.queue.length} ${R.queue.length === 1 ? 'task is' : 'tasks are'} ranked in ${C.queue_path}; each starts as a run of its own.${shared}${report}`;
    out.start = R.queue.filter((t) => t.task_def && t.task_def.mode).slice(0, 5)
      .map((t) => ({ rank: t.rank, task: t.task, args: { inferpilot_dir: INFERPILOT, ...t.task_def } }));
  } else if (status === 'awaiting_design_review' || (status === 'stopped' && at === 'Design' && MODE === 'feature')) {
    out.say = `The design waits for you: ${C.design_path || `${C.run_dir}/design.md`}. To go on with it, run again from `
      + `Implement in this run directory; to change it, say how and run the task again.${report}`;
    out.rerun_with = { resume_from: 'implement', run_dir: C.run_dir, stop_after: '' };
  } else if (status === 'stopped') {
    out.say = `Stopped after ${at}, as ${stopAfterNow !== STOP_AFTER ? 'the control file' : 'stop_after'} asked. To go on, run again `
      + `without stop_after: the task starts over.${report}`;
    out.rerun_with = { stop_after: '' };
  } else if (status === 'blocked' && at === 'Verify' && verifyUnfinished) {
    out.say = `Verify ended without a result: ${last}. Nothing was measured against the change; to verify the same commit `
      + `again, run again from Verify in this run directory, once nothing the round started still runs (Setup checks the GPUs are idle).${report}`;
    // A stop the run was to make at Verify or after it is still ahead of the run that goes on, which keeps it;
    // one before Verify is behind it.
    const stop = liveStopAfter();
    const ahead = STOP_PHASES[MODE].slice(STOP_PHASES[MODE].indexOf('Verify')).find((p) => p.toLowerCase() === stop);
    out.rerun_with = { resume_from: 'verify', run_dir: C.run_dir, stop_after: ahead || '' };
  } else if (status === 'busy') {
    out.say = `${last || 'Another InferPilot task holds this machine'}. Run again when it is free: python3 scripts/run_lock.py status `
      + 'names an InferPilot task that holds it, python3 scripts/gpu_status.py the processes holding its GPUs.';
    out.rerun_with = {};
  } else if (status === 'needs_input' && at === 'Spec') {
    const fallback = (((R.questions || [])[0] || {}).options || [])[0];
    const own = fallback ? `, or take InferPilot's own (${fallback}): it need not reproduce the source's numbers, so the source's `
      + 'gain is not claimed on it, and G5 judges there only that the feature is no slower than the baseline' : '';
    out.say = `The run stopped at Spec: ${last}. Give a client that runs the source's scenarios${own}. `
      + "To measure the task's own workload anyway, or to go on without a claimed gain, say so in goal and run the task again: "
      + `G5 then judges on the task's client that the feature is no slower than the baseline.${report}`;
    out.ask = R.questions;
    // The answer replaces the client the task gave, which would otherwise win over it.
    out.rerun_with = { client_script: '', answers: { client_script: null } };
  } else if (status === 'needs_input' && (R.questions || []).some((q) => FIX_NEEDS[q.field])) {
    const what = R.questions.map((q) => FIX_NEEDS[q.field]).filter(Boolean).join(' and ');
    out.say = `The run stopped after ${at}, before any code changed: ${last}. Give ${what}, and run the task again: it starts over.${report}`;
    out.ask = R.questions;
    // A workload the task gave in part would win over the answer: it is cleared, so that the answer takes its place.
    const partial = R.questions.some((q) => q.field === 'workload') && A.workload ? { workload: '' } : {};
    out.rerun_with = { ...partial, answers: Object.fromEntries(R.questions.map((q) => [q.field, null])) };
  }
  const user = [];
  if (status === 'ready_for_review' && R.pr) user.push(R.pr.push_command || `push ${R.pr.branch}: the report has the command`);
  if (status === 'queued' && !R.daily_report_url) {
    // A director that timed out may still be running, and may still share it.
    user.push(TIMED_OUT_CALLS.has('director:report')
      ? `the director timed out and may still share ${C.run_dir}/daily_report.md: once it has stopped, share it as a new issue in InferPilot's repository unless that day's is there already (README step 5)`
      : `share ${C.run_dir}/daily_report.md as a new issue in InferPilot's repository, unless that day's is there already (README step 5)`);
  }
  if (R.reply_draft_path) {
    const left = R.reply_findings || [];
    user.push(left.length
      ? `read the reply draft against what the run showed before posting it: ${left.map((f) => `${f.claim}: ${f.issue}`).join('; ')}`
        + `${C.reply_check_path ? ` (${C.reply_check_path})` : ''}; the draft: ${R.reply_draft_path}`
      : `post the reply draft on the issue: ${R.reply_draft_path}`);
  }
  for (const s of R.spinoffs) if (s && s.status === 'drafted' && s.draft_path) user.push(`file the issue draft upstream: ${s.draft_path}`);
  if (R.knowledge_pr) user.push(`review the curator's pull request to InferPilot: ${R.knowledge_pr}`);
  else if (R.knowledge_patch) {
    // The run directory is the task container's own unless exp_root is a host mount, which is not known here.
    // The command comes last, on a line of its own (indented as the runner's log has its commands), with nothing
    // after it: it is pasted as it stands.
    user.push(`propose the curator's knowledge changes, which the run did not (${R.knowledge_notes || 'see the curator\'s notes'}),`
      + ` from an InferPilot checkout, with the command below. Unless exp_root is on a host mount, ${C.run_dir} goes with the`
      + ' task\'s container: copy it out first, under the same name, which the branch takes, and give the command\'s three'
      + ' paths as they are in the copy:\n'
      + `    python3 scripts/propose_knowledge.py --run-dir ${shq(C.run_dir)}`
      + ` --body ${shq(`${C.run_dir}/knowledge_pr.md`)} --patch ${shq(R.knowledge_patch)}`);
  } else if ((R.knowledge_updates || []).some((u) => u && /(^|\/)knowledge\//.test(String(u.file || '')))) {
    user.push(`the curator's knowledge changes were not proposed and no patch of them was saved (${R.knowledge_notes || 'the curator gave no reason'}):`
      + ` they are only in ${INFERPILOT}/knowledge (git -C ${shq(INFERPILOT)} status -- knowledge), which goes with the task's container:`
      + ' copy the files out, or clear what the script refused and run it again');
  } else if (curatorSilent) {
    // The timeout's own entry (R.attention) speaks of servers and GPUs only.
    user.push(`${R.knowledge_notes}, so what it changed under knowledge/ is not known`
      + `${TIMED_OUT_CALLS.has('curator:learn') ? ': it may still be editing or proposing, so stop it first. Then' : ':'}`
      + ` look at ${INFERPILOT}/knowledge (git -C ${shq(INFERPILOT)} status -- knowledge) and for ${C.run_dir}/knowledge.patch`
      + ` and ${C.run_dir}/knowledge_pr.md. Unless on a host mount, the checkout and the run directory go with the task's`
      + ' container: copy what is there out, and propose it from an InferPilot checkout with scripts/propose_knowledge.py'
      + ' (--patch for a saved patch)');
  }
  for (const a of R.attention) user.push(`clean up before anything else runs there: ${a.issue}${a.worktree ? ` (${a.worktree})` : ''}`);
  if (user.length) out.user = user;
  return out;
}

// A rollback that did not leave the worktree clean is recorded for a human; returns the note suffix.
function rolledBack(rb, wpId) {
  const clean = !!rb && rb.clean === true;
  if (!clean) {
    R.attention.push({ wp: wpId, worktree: C.worktree,
      issue: rb ? `rollback to ${rb.reset_to} left the worktree unclean` : 'the rollback returned nothing' });
  }
  return rb ? ` (reset to ${rb.reset_to}${clean ? '' : ', worktree not clean'})` : ' (rollback returned nothing)';
}

// Setup, then the stop_after check: a finished run record when the run ends here, else null.
async function start(requireBaseline) {
  const s = await setup(requireBaseline);
  if (s && s.ask) return askImage(s.ask);
  if (s === 'busy') return finish('busy', 'Setup');
  if (!s) return finish('blocked', 'Setup');
  if (stopHere('Setup')) return finish('stopped', 'Setup');
  return null;
}

// Tasks run on the newest daily image unless the user names one: another image stops the run before it
// creates anything, with the question whether to run on it anyway.
function askImage(img) {
  const cur = img.current || "this container's image";
  const want = img.wanted || 'the newest daily image';
  const why = {
    older: `this container runs ${cur}, older than ${want}, the newest daily image`,
    mismatch: `this container runs ${cur}, not ${want}, the image the task asks for`,
    resumed_elsewhere: `this run was set up on ${want}, and this container runs ${cur}`,
  }[img.verdict] || `this container runs ${cur}, not ${want}`;
  note('Setup', 'needs_input', why);
  const questions = [{ field: 'image_ok', question: `${why}. Run on ${cur} anyway?`,
    why: 'tasks test the newest sglang unless you name an image; a run on another image tests something else',
    options: [true, false] }];
  Object.assign(R, { status: 'needs_input', stopped_at: 'Setup', image: img, questions,
    next: { say: `${why}. To run here anyway, answer image_ok; to run on ${want}, start a container from it (README step 2) and run there.`,
      ask: questions, rerun_with: { answers: { image_ok: true } } } });
  log(`[inferpilot] mode=${MODE} status=needs_input: ${why}`);
  return R;
}

// A port is measured on its source's scenarios with the user's own server and client: when the task's
// client does not run them, the run stops at Spec and asks, before anything is built on a workload nobody gave.
function askWorkload(spec) {
  const gap = String(spec.workload_gap).trim();
  const fallback = String(spec.fallback_client || '').trim();
  note('Spec', 'needs_input', gap);
  R.questions = [{ field: 'client_script', question: `${gap} Which client runs the source's scenarios against the server (absolute path)?`,
    why: "a port is measured on its source's scenarios with the user's own server and client", ...(fallback ? { options: [fallback] } : {}) }];
  return finish('needs_input', 'Spec');
}

// A fix that changes code the server runs is measured against the base: G2 on gsm8k, G5 on the task's client. So
// a fix needs a model and a client, and of a task that names InferPilot's client, the whole workload it runs.
// Intake asks all three of a task that can reach a fix. An issue or a ci run can get this far without them: it
// named a stop before the fix that its route skipped (an existing fix PR has no Diagnose) or that the control
// file lifted. It stops here and asks, before any code changes; null when it lacks nothing.
const FIX_NEEDS = { model_path: 'the model', client_script: 'the client', workload: "the whole workload of InferPilot's client" };
function askForFix(at) {
  const lacks = ['model_path', 'client_script'].filter((k) => !C[k]);
  // Setup rendered the template the task named with the workload the task had: what that leaves out the template
  // filled with lengths or a concurrency of its own, and G5 would judge the fix on a load nobody chose.
  const gaps = isTemplate(A.client_script) ? templateLoadGaps(A) : [];
  if (gaps.length) lacks.push('workload');
  if (!lacks.length) return null;
  const without = { model_path: 'a model', client_script: 'a client', workload: FIX_NEEDS.workload };
  const gate = { model_path: 'G2 on gsm8k', client_script: "G5 on the task's client",
    workload: `G5 on InferPilot's serving benchmark, which runs the task's workload, and the task ${loadGapWords(gaps)}` };
  const why = `the run reached a fix without ${lacks.map((k) => without[k]).join(' and ')}, and a fix that changes code the server runs `
    + `is measured against the base: ${lacks.map((k) => gate[k]).join(', ')}`;
  note(at, 'needs_input', why);
  const ask = {
    model_path: `Which model should G2 measure the fix on (local path)? ${MODE === 'issue' ? "The reporter's model"
      : 'The model the failing test serves'}, when it is on this machine.`,
    client_script: 'Which script runs the workload that G5 measures the fix on (absolute path)? It reads PORT and writes its logs to OUT_DIR.',
    workload: "Which workload should InferPilot's client run for the fix (input and output lengths, concurrency)?",
  };
  R.questions = lacks.map((k) => ({ field: k, question: ask[k], why }));
  return finish('needs_input', at);
}

// true, false, 'busy' when the machine is not free (another InferPilot task holds it, or, but for a scan,
// another job holds one of its GPUs), or {ask} when the image check stopped
// the run (nothing was created in either case).
async function setup(requireBaseline) {
  enter('Setup');
  const s = await call('director', 'setup', inputs({ require_baseline: requireBaseline, image: IMAGE_WANTED,
    image_ok: IMAGE_OK }), SETUP);
  if (s && s.lock === 'skipped') return { ask: s.image || {} };
  if (s && s.lock === 'busy') {
    note('Setup', 'busy', `the machine is not free${s.notes ? `: ${s.notes}` : ''}`);
    return 'busy';
  }
  if (!s || !s.run_dir) {
    // a check that could not run (gpu_status.py exit 2) stops here with its reason: waiting would not fix it
    note('Setup', 'fail', (s && s.baseline_status === 'fail' && s.baseline) || 'the director returned no run directory');
    return false;
  }
  // GPUs and scripts the user gave win; otherwise every later role uses the ones Setup pinned or rendered.
  const own = (v) => (isTemplate(v) ? '' : String(v || ''));
  Object.assign(C, { run_dir: s.run_dir, branch: s.branch || C.branch, worktree: s.worktree || C.worktree,
    gpu_ids: String(C.gpu_ids || s.gpu_ids || '').trim(),
    server_script: own(C.server_script) || own(s.server_script),
    client_script: own(C.client_script) || own(s.client_script),
    accuracy_script: own(C.accuracy_script) || own(s.accuracy_script) });
  if (!C.gpu_ids) {
    note('Setup', 'fail', 'no GPU set pinned (gpu_ids)');
    return false;
  }
  const unrendered = ['server_script', 'client_script'].filter((k) => isTemplate(A[k]) && !C[k]);
  if (unrendered.length) {
    note('Setup', 'fail', `${unrendered.join(' and ')}: the user took InferPilot's template, and Setup rendered none`);
    return false;
  }
  // G2 runs the same accuracy script for every arm: from the baseline on for a feature or a perf task, in
  // Verify for the fix of an issue or a ci task, which has a model whenever it can reach one.
  const fix = (MODE === 'issue' || MODE === 'ci') && !!C.model_path;
  if ((MODE === 'feature' || MODE === 'perf' || fix) && !C.accuracy_script) {
    note('Setup', 'fail', "no accuracy script: Setup returned neither the task's nor InferPilot's accuracy.sh");
    return false;
  }
  // G2 serves the fix and its baseline with the run's server script: the launch script Setup wrote from the
  // reporter's or the job's own command, or InferPilot's template where they start no server.
  if (fix && !C.server_script) {
    note('Setup', 'fail', 'no server script: Setup returned neither a launch script written from the report or the job, nor '
      + "InferPilot's server template, and G2 has nothing to serve the fix with");
    return false;
  }
  const ok = s.baseline_status === 'pass' || (!requireBaseline && s.baseline_status === 'skip');
  note('Setup', ok ? 'pass' : 'fail',
    `baseline ${s.baseline_status}${s.baseline ? `: ${s.baseline}` : ''}${s.notes ? `; ${s.notes}` : ''}`);
  return ok;
}

// One work package at a time: implement, then an independent unit + feature-off
// gate; failed rounds feed the test engineer's findings back; after the last round
// the package is rolled back to the last verified commit and the task stops. A gate
// that has no result is no failure: it costs no round and rolls nothing back.
// A call that timed out may still be running in the worktree and on the GPUs: no other agent starts on
// its work package, not another round and not a rollback; it is on the attention list for a human.
// Both it and a gate without a result are `held`: the commit stays where it is, and the run stops for a human.
function stalled(wp, label, commit = '') {
  note('Implement', 'fail', `${wp.id}: ${label} timed out; no further round or rollback, since it may still be running`);
  return { ok: false, held: true, failed_wp: wp.id, commit, reason: `${label} timed out` };
}
// What a unit answer leaves without a result: what it says in `incomplete`, or, when it says nothing
// there and no check failed, the checks it gives as null. A check that ran and failed is a failure,
// whatever else did not run.
function leftOpen(ut) {
  const said = String((ut && ut.incomplete) || '').trim();
  if (!ut || said) return said;
  if (ut.unit_pass === false || ut.feature_off_unchanged === false || ut.fails_without_change === 'no') return '';
  const open = [ut.feature_off_unchanged === null && 'the feature-off check',
    ut.fails_without_change === null && 'the check that the test can fail'].filter(Boolean);
  return open.length ? `${open.join(' and ')} did not run (null in the answer, which named nothing in incomplete)` : '';
}
// A unit gate with checks that have no result, none of those that ran having failed.
const unfinished = (ut) => leftOpen(ut) !== '';
// A unit call is never asked again blind: a retry without what the first one left going could start the
// same tests on the same GPUs. An answer it cannot use, or none, goes to the call that finishes the gate.
const UNIT_ONCE = { attempts: 1, schemaRetries: 0 };
// A unit call that timed out may still be running: the human who cleans up gets the commit and what the
// gate had left going.
function gateTimedOut(wp, label, commit, left = '') {
  R.attention.push({ wp: wp.id, worktree: C.worktree, issue: `${label} timed out: commit ${commit} stays in the worktree, `
    + `unverified${left ? `; what the gate had left without a result before it: ${left}` : ''}` });
  return stalled(wp, label, commit);
}
// Gates and rounds that ended without a result and may have left detached runs on the GPUs.
const leftRunning = [];
// Still without a result after the call that was to finish it: nothing measured went against the change,
// so it is not rolled back; the package stops with its commit, on the attention list for a human.
function noResult(wp, commit, what) {
  const missing = String(what || '').trim() || 'the test engineer returned nothing';
  R.attention.push({ wp: wp.id, worktree: C.worktree, issue: `the unit gate of ${wp.id} has no result (${missing}): `
    + `commit ${commit} stays in the worktree, unverified; stop what the gate left running before reusing its GPUs` });
  note('Implement', 'incomplete', `${wp.id}: the unit gate has no result after the call that was to finish it `
    + `(${missing}); not rolled back`);
  leftRunning.push(`the unit gate of ${wp.id} may have left runs going`);
  return { ok: false, held: true, failed_wp: wp.id, commit, reason: `the unit gate of ${wp.id} has no result` };
}

async function implementAll(wps) {
  enter('Implement');
  const list = Array.isArray(wps) ? wps : [];
  if (list.length === 0) {
    note('Implement', 'fail', 'no work packages');
    return { ok: false, reason: 'no work packages' };
  }
  if (R.done.length + list.length > MAX_WPS) {
    note('Implement', 'fail', `${R.done.length + list.length} work packages exceed args.max_work_packages=${MAX_WPS}`);
    return { ok: false, reason: 'work-package budget exceeded' };
  }
  for (const wp of list) {
    const known = new Set(R.done.map((d) => d.wp));
    const missing = (wp.depends_on || []).filter((d) => !known.has(d));
    if (missing.length) log(`  [inferpilot] ${wp.id} depends on unverified ${missing.join(', ')}; order follows the design`);
    let feedback = '';
    let passed = false;
    for (let round = 0; round <= MAX_FIX_ROUNDS && !passed; round++) {
      const imLabel = `implementer:${wp.id}#${round}`;
      const im = await call('implementer', 'implement', inputs({ work_package: wp, round, feedback }), IMPL, imLabel);
      if (!im && TIMED_OUT_CALLS.has(imLabel)) return stalled(wp, imLabel);
      if (!im) {
        feedback = 'the implementer returned nothing';
        continue;
      }
      if (im.status === 'blocked') {
        feedback = im.notes || 'the implementer reported the work package blocked';
        break;
      }
      if (!String(im.commit || '').trim()) {
        feedback = 'the implementer reported done without a commit';
        continue;
      }
      if (im.kernel_handoff && !R.handoffs.some((h) => h.id === `K-${wp.id}`)) {
        R.handoffs.push({ id: `K-${wp.id}`, wp: wp.id, owner: 'kernel_workflow', source: 'implement', ...im.kernel_handoff });
      }
      const utLabel = `test_engineer:${wp.id}#${round}`;
      const unitIns = { work_package: wp, commit: im.commit, unit_test_cmd: im.unit_test_cmd || wp.test,
        time_limit_min: exactMinutes(timeoutFor('test_engineer')) };
      let ut = await call('test_engineer', 'unit', inputs(unitIns), UNIT, utLabel, UNIT_ONCE);
      if (!ut && TIMED_OUT_CALLS.has(utLabel)) return gateTimedOut(wp, utLabel, im.commit);
      // A gate without its result, checks still to give one or no answer at all, is finished by one more
      // call, which spends no fix round and reads the runs the first one left going.
      if (!ut || unfinished(ut)) {
        const left = ut ? leftOpen(ut) : 'every check: the test engineer returned no answer';
        const finLabel = `${utLabel}+finish`;
        const fin = await call('test_engineer', 'unit', inputs({ ...unitIns, incomplete: left,
          earlier_evidence: (ut && ut.evidence) || '' }), UNIT, finLabel, UNIT_ONCE);
        if (!fin && TIMED_OUT_CALLS.has(finLabel)) return gateTimedOut(wp, finLabel, im.commit, left);
        if (!fin || unfinished(fin)) {
          return noResult(wp, im.commit, fin ? leftOpen(fin) : `the call to finish it returned no answer; before it: ${left}`);
        }
        ut = fin;
      }
      // A unit test proves something only if it can fail: on the parent commit when the package changes
      // behaviour, on a copy of the change broken in one way when it keeps behaviour (a performance
      // change, a kernel swap, a refactor). Only a docs package has nothing to fail.
      const bites = ['parent', 'mutant'].includes(ut.fails_without_change)
        || (ut.fails_without_change === 'n/a' && wp.kind === 'docs');
      if (ut.unit_pass === true && ut.feature_off_unchanged === true && bites) {
        passed = true;
        R.done.push({ wp: wp.id, commit: im.commit, evidence: ut.evidence || '' });
      } else if (ut.unit_pass === true && ut.feature_off_unchanged === true) {
        feedback = `the unit test cannot fail (fails_without_change=${ut.fails_without_change}); make it fail on the parent `
          + `commit, or on a copy of the change broken in one way when the change keeps behaviour`
          + `${ut.failure_summary ? `: ${ut.failure_summary}` : ''}`;
      } else {
        feedback = ut.failure_summary || 'the unit or feature-off gate failed';
      }
    }
    if (!passed) {
      const rb = await call('implementer', 'rollback', inputs({ work_package: wp, reason: feedback }),
        ROLLBACK, `implementer:rollback:${wp.id}`);
      note('Implement', 'fail', `${wp.id}: ${feedback}${rolledBack(rb, wp.id)}`);
      return { ok: false, failed_wp: wp.id, reason: feedback };
    }
  }
  note('Implement', 'pass', `verified: ${R.done.map((d) => d.wp).join(', ')}`);
  return { ok: true };
}

// Verify against the same-pattern baseline; a failure goes to the investigator,
// whose verdict decides: fix work packages (implemented, then re-verified), a
// baseline bug (recorded; the gate is re-judged on what remains) or a stop.
// A Verify that ended without a result (verifyUnfinished) can run again on the same commit (resume_from=verify).
let verifyUnfinished = false;
async function verifyLoop(extraGates) {
  for (let round = 0; ; round++) {
    enter('Verify');
    // Another job on a pinned GPU does not stop a correctness round, but the user hears of it before it starts.
    const gpus = await call('verify_engineer', 'gpus', inputs({ round }), GPU_CHECK, `verify_engineer:gpus#${round}`);
    const inUse = gpus ? String(gpus.in_use || '').trim() : '';
    if (inUse) {
      note('Verify', 'warning', `another job uses a pinned GPU: ${inUse}`);
      log(`  [inferpilot] warning: another job uses a pinned GPU before Verify round ${round}: ${inUse}`);
    } else if (!gpus) {
      log(`  [inferpilot] the pinned GPUs were not checked before Verify round ${round}: the check returned nothing`);
    }
    // A round with more time runs whole again: its suites are judged together.
    const t = await callWithTime({
      role: 'verify_engineer', sub: 'verify', grantKey: 'verify_engineer', what: 'the round', label: `verify_engineer#${round}`,
      schema: VERIFY, opts: ONE_SESSION, ins: (limit) => inputs({ extra_gates: extraGates || [], round, time_limit_min: limit }),
      needs: (r) => (r.all_pass !== true && !(r.failures || []).some((f) => f.blocking !== false) ? r.needs_min : 0),
    });
    if (t.needs) {
      note('Verify', 'fail', `the round needs ${t.needs} min; its limit is ${t.limit} min (${t.short})`);
      leftRunning.push(`Verify round ${round} stopped for time and may have left its servers running`);
      return { ok: false, reason: `out of time: the round needs ${t.needs} min` };
    }
    const v = t.r;
    if (!v) {
      note('Verify', 'fail', 'the verify engineer returned nothing');
      leftRunning.push(`Verify round ${round} may have left its servers running`);
      ATTENTION.push({ call: `verify_engineer#${round}`, issue: `Verify round ${round} returned no answer and may have left its `
        + 'servers running: stop them before reusing its GPUs' });
      verifyUnfinished = true;
      return { ok: false, reason: 'no verification result' };
    }
    if (v.ledger_path) C.ledger_path = v.ledger_path;
    // What this round's diff says of a fix, for G5 and for the roles after it. A round that does not say counts
    // as one whose fix changes code the server runs, whatever an earlier round said: its fix is measured.
    if (BEFORE_A_FIX[MODE]) C.server_code_changed = v.server_code_changed !== false;
    const failures = v.failures || [];
    const blocking = failures.filter((f) => f.blocking !== false);
    if (v.all_pass === true || (failures.length && !blocking.length)) {
      const missed = failures.filter((f) => f.blocking === false).map((f) => f.gate).join(', ');
      note('Verify', 'pass', `${v.summary || 'all blocking gates pass'}${missed ? `; missed without blocking: ${missed}` : ''}`);
      return { ok: true };
    }
    const failing = blocking.map((f) => `${f.gate}${f.failure_class ? `:${f.failure_class}` : ''}`).join(', ');
    note('Verify', 'fail', failing || v.summary || 'gates failed');
    if (round >= MAX_INVESTIGATE) return { ok: false, reason: `investigation budget exhausted (${failing})` };

    enter('Investigate');
    const integratedKernels = R.handoffs.filter((h) => h.status === 'integrated')
      .map((h) => ({ handoff: h.id, wp: h.integrated_wp, commit: h.commit }));
    const inv = await call('investigator', 'investigate',
      inputs({ failures: blocking, round, integrated_kernels: integratedKernels }),
      INVESTIGATE, `investigator#${round}`);
    if (!inv) {
      note('Investigate', 'fail', 'the investigator returned nothing');
      return { ok: false, reason: 'no investigation result' };
    }
    note('Investigate', inv.verdict, inv.summary || '');
    if (inv.verdict === 'baseline_bug') {
      if (inv.known_issue) R.known_issues.push(inv.known_issue);
      continue;
    }
    if (inv.verdict === 'kernel_regression') {
      const back = await rejectKernels(inv.kernel_handoffs || [], inv.counterexample || inv.summary || '');
      if (!back.ok) return { ok: false, at: back.at, reason: back.reason };
      continue;
    }
    if (inv.verdict === 'fix_needed' && Array.isArray(inv.work_packages) && inv.work_packages.length) {
      const fix = await implementAll(inv.work_packages);
      if (!fix.ok) return { ok: false, reason: `fix ${fix.failed_wp || ''} failed: ${fix.reason}` };
      const afterFix = stopAt('Implement');
      if (afterFix) return afterFix;
      const ks = await kernelStage(); // hand-offs a fix raised
      if (ks.held) return { ok: false, at: 'Kernel', reason: ks.held };
      const afterKernel = stopAt('Kernel');
      if (afterKernel) return afterKernel;
      continue;
    }
    return { ok: false, reason: 'investigation inconclusive' };
  }
}

// An integrated kernel that fails InferPilot's gates goes back to the functional kernel (its commit is
// reverted). With a back end it is handed back once more, the failing case added to its tests, and
// integrated again only if the back end passes it; otherwise it ends `rejected`.
async function rejectKernels(ids, counterexample) {
  const hs = R.handoffs.filter((h) => ids.includes(h.id) && h.status === 'integrated');
  if (!hs.length) {
    note('Investigate', 'fail', `kernel_regression names no integrated kernel (${ids.join(', ') || 'none'})`);
    return { ok: false, reason: 'kernel_regression without an integrated kernel' };
  }
  for (const h of hs) {
    const rb = await call('implementer', 'rollback', inputs({
      work_package: { id: h.integrated_wp, goal: `back to the functional kernel of ${h.id}`, test: '' },
      revert: h.commit, reason: `the integrated kernel fails InferPilot's gates: ${counterexample}`,
    }), ROLLBACK, `implementer:revert:${h.id}`);
    const suffix = rolledBack(rb, h.integrated_wp);
    if (!rb || rb.clean !== true) {
      note('Investigate', 'fail', `${h.id}: could not go back to the functional kernel${suffix}`);
      return { ok: false, reason: `revert of ${h.id} failed` };
    }
    R.done = R.done.filter((d) => d.wp !== h.integrated_wp);
    h.counterexamples = [...(h.counterexamples || []), counterexample];
    const retry = (h.retries || 0) < 1;
    h.status = retry ? undefined : 'rejected'; // undefined: pending again for the Kernel stage
    if (retry) h.retries = (h.retries || 0) + 1;
    note('Investigate', 'kernel_rejected', `${h.id}: back to the functional kernel${suffix}`
      + `${retry ? '; handed back once with the failing case' : '; rejected after its retry'}`);
  }
  const ks = await kernelStage();
  return ks.held ? { ok: false, at: 'Kernel', reason: ks.held } : { ok: true };
}

// What G5 judges where no gain is claimed (validation_doctrine.md, G5 for a feature, G5 for a fix): the change
// is measured all the same, and the measurement shows that it costs no speed.
const NO_SLOWER = "no gain is claimed: no slower than the baseline beyond the noise band, on the run's client";

// G5 for a fix (validation_doctrine.md). `when` says when a mode measures: true always (a feature, a perf task,
// a performance problem, whose number must come back whatever its fix changes), 'fix' when the fix changes code
// the server runs. The verify engineer reads that from the fix's diff, as it does for G2: a fix to tests,
// documentation or CI files alone is not measured. A fix whose last Verify round did not say so is measured.
const NOT_MEASURED = 'not run: no code the server runs changed';
const unmeasured = (when) => when === 'fix' && C.server_code_changed === false;
// The claim the reviewer reads: none where G5 did not run.
const claimNow = (claim, when) => (unmeasured(when) ? '' : claim || '');
let routeSkipsPerf = false;

// Performance where the task measures: what performance() returns, or {ok, skipped} for a fix G5 does not
// measure, which the run record says once. The route is logged again whenever that changes: a change the review
// asks for can add code the server runs to a fix that had none, and take it away again.
async function measure(claim, when) {
  const skip = unmeasured(when);
  if (skip !== routeSkipsPerf) {
    routeSkipsPerf = skip;
    logRoute(routeOf().filter((t) => !skip || t !== 'Performance'));
  }
  if (!skip) return performance(claim);
  if (!R.phases.some((p) => p.phase === 'Performance' && p.verdict === 'not_run')) note('Performance', 'not_run', NOT_MEASURED);
  log(`  [inferpilot] G5 ${NOT_MEASURED}: the fix is not measured for speed`);
  return { ok: true, skipped: true };
}

async function performance(claim) {
  enter('Performance');
  const p = await call('perf_engineer', 'perf_eval', inputs({ claim: claim || '' }), PERF);
  // A measurement counts only on the fixed workload and on the path the task targets; with held_out, a
  // gain counts only if it also holds at the held-out operating point nobody tuned on. One not run (another
  // job on the machine) is no evidence either way: nothing is judged or dropped on it.
  const notRun = !!p && String(p.not_run || '').trim();
  const trusted = !!p && !notRun && p.workload_unchanged === true && p.path_ok === true;
  const reproduced = trusted && p.claim_reproduced === true && (!HELD_OUT || p.held_out_ok === true);
  // A fix whose base cannot serve the configuration is compared with the last commit that served it. Where the
  // run knows none, "no slower" has nothing to be judged against: the fix's numbers are reported, as G2's score
  // is. A number that must come back (a performance problem) is its own measure, and is always judged.
  const unjudged = trusted && BEFORE_A_FIX[MODE] && claim === NO_SLOWER ? String(p.no_baseline || '').trim() : '';
  const ok = trusted && p.guards_ok === true && (!claim || reproduced || !!unjudged);
  const why = !p ? 'the performance engineer returned nothing'
    : notRun ? `not run: ${notRun}`
    : !trusted ? `not trusted: ${p.workload_unchanged !== true ? 'workload changed' : 'the run left the intended path'}; ${p.summary || ''}`
      : claim && p.claim_reproduced === true && HELD_OUT && p.held_out_ok !== true
        ? `the claim does not hold at the held-out operating point; ${p.summary || ''}`
        : unjudged ? `reported, not judged: no baseline (${unjudged}); ${p.summary || ''}`
          : (p.summary || '');
  note('Performance', ok ? 'pass' : 'fail', why);
  return { ok, trusted, claimed: !!claim, reproduced };
}

// An independent review of the diff and its evidence. Requested changes come back as work packages:
// implemented, verified again (and measured again when the task measures), then reviewed again.
async function reviewLoop(extraGates, perfClaim, runPerf) {
  for (let round = 0; ; round++) {
    enter('Review');
    const rv = await call('reviewer', 'review', inputs({ round, extra_gates: extraGates || [], claim: claimNow(perfClaim, runPerf) }),
      REVIEW, `reviewer#${round}`, REVIEW_MODEL ? { model: REVIEW_MODEL } : undefined);
    if (!rv) {
      note('Review', 'fail', 'the reviewer returned nothing');
      return { ok: false, at: 'Review' };
    }
    if (rv.review_path) C.review_path = rv.review_path;
    const findings = rv.findings || [];
    const count = (s) => findings.filter((f) => f.severity === s).length;
    note('Review', rv.verdict, `${count('blocker')} blocker, ${count('major')} major, ${count('minor')} minor` +
      `${rv.summary ? `; ${rv.summary}` : ''}`);
    if (rv.verdict === 'approve') {
      if (count('blocker') === 0) return { ok: true };
      note('Review', 'fail', 'an approval with a blocker finding is not an approval');
      return { ok: false, at: 'Review' };
    }
    if (rv.verdict === 'blocked') return { ok: false, at: 'Review' };
    const wps = rv.work_packages || [];
    if (!wps.length || round >= MAX_REVIEW) {
      note('Review', 'fail', wps.length ? `review budget exhausted (max_review_rounds=${MAX_REVIEW})`
        : 'changes requested without work packages');
      return { ok: false, at: 'Review' };
    }
    const fix = await implementAll(wps);
    if (!fix.ok) return { ok: false, at: 'Implement' };
    const stop = await repaired(extraGates, perfClaim, runPerf);
    if (stop) return stop;
  }
}

// After the fixes of a review or PR round: Kernel, Verify and Performance again, each ending with the
// stop check; what failed or stopped, or null.
async function repaired(extraGates, perfClaim, runPerf) {
  const afterFix = stopAt('Implement');
  if (afterFix) return afterFix;
  const ks = await kernelStage(); // hand-offs a requested change raised
  if (ks.held) return { ok: false, at: 'Kernel' };
  const afterKernel = stopAt('Kernel');
  if (afterKernel) return afterKernel;
  const ver = await verifyLoop(extraGates);
  if (!ver.ok) return ver.stopped ? ver : { ok: false, at: ver.at || 'Verify' };
  const afterVerify = stopAt('Verify');
  if (afterVerify) return afterVerify;
  if (runPerf) {
    const perf = await measure(perfClaim, runPerf);
    if (!perf.ok) return { ok: false, at: 'Performance' };
    const afterPerf = stopAt('Performance');
    if (afterPerf) return perf.skipped ? stoppedUnmeasured() : afterPerf;
  }
  return null;
}

// A stop named for Performance still ends a run whose fix G5 does not measure: after Verify, the last phase it ran.
function stoppedUnmeasured() {
  note('Verify', 'stopped', `stop_after=Performance: G5 is ${NOT_MEASURED}, so the run stops after Verify`);
  return { ok: false, stopped: true, at: 'Verify' };
}

// The PR engineer changes no code: the branch it hands over must be the tree Verify and Review passed.
// What it finds missing comes back as work packages.
async function preparePR(round) {
  enter('PR');
  const pr = await call('pr_engineer', 'pr', inputs({ round }), PR, `pr_engineer#${round}`);
  const leaks = (pr && pr.leak_scan_findings) || [];
  const wps = (pr && pr.work_packages) || [];
  const ok = !!pr && pr.lint_pass === true && pr.feature_off_final === true && pr.content_unchanged === true
    && !!pr.pr_body_path && Array.isArray(pr.leak_scan_findings) && leaks.length === 0 && pr.launch_checked === true
    && wps.length === 0;
  if (pr) {
    if (pr.branch) C.branch = pr.branch;
    C.pr_body_path = pr.pr_body_path || '';
    C.leak_scan_findings = leaks;
    C.pr_work_packages = wps;
    R.pr = { title: pr.title || '', branch: pr.branch, pr_body_path: pr.pr_body_path, push_command: pr.push_command || '',
      merges_cleanly: pr.merges_cleanly !== false, leak_scan_findings: leaks, work_packages: wps };
  }
  note('PR', ok ? 'pass' : 'fail', !pr ? 'the PR engineer returned nothing'
    : `${pr.title || pr.branch}${pr.diff_stat ? ` (${pr.diff_stat})` : ''}`
      + `${pr.content_unchanged !== true ? '; the branch no longer matches what was verified' : ''}`
      + `${pr.merges_cleanly === false ? '; conflicts with main, reported in the PR body' : ''}`
      + `${leaks.length ? `; would publish ${leaks.length} local path(s), host name(s) or identities` : ''}`
      + `${pr.launch_checked === false ? '; the command the description gives failed' : ''}`
      + `${wps.length ? `; the branch still lacks ${wps.length} work package(s)` : ''}`);
  return { ok, wps: !pr || ok || pr.content_unchanged !== true ? [] : wps };
}

// Kernel hand-offs go to the selected back end. A gain comes back as a work package that the
// implementer integrates and the test engineer re-verifies; the later Verify and Performance gates
// judge it with everything else. A back end that fails or finds no gain never blocks the task:
// the functional kernel stays. Runs again after every fix loop, for hand-offs raised there and for a
// kernel handed back after it failed Verify (rejectKernels).
async function kernelStage() {
  const pending = R.handoffs.filter((h) => h.owner === 'kernel_workflow' && !h.status);
  if (!pending.length) return { integrated: 0 };
  if (KERNEL_BACKEND === 'none') {
    for (const h of pending) h.status = 'recorded';
    note('Kernel', 'recorded', `${pending.length} kernel hand-off(s) recorded (kernel_backend=none)`);
    return { integrated: 0 };
  }
  let integrated = 0;
  for (const h of pending) {
    if (PHASE !== 'Kernel') enter('Kernel');
    const pkg = await call('kernel_liaison', 'package', inputs({ handoff: h }), K_PACKAGE, `kernel_liaison:package:${h.id}`);
    if (!pkg) {
      h.status = 'failed';
      h.reason = 'no task package';
      continue;
    }
    const result = await call('kernel_liaison', 'run', inputs({ handoff: h, task_dir: pkg.task_dir }), K_RUN,
      `kernel_liaison:run:${h.id}`) || { error: 'the command back end did not run' };
    const res = await call('kernel_liaison', 'collect', inputs({ handoff: h, task_dir: pkg.task_dir, backend_result: result }),
      K_COLLECT, `kernel_liaison:collect:${h.id}`);
    h.status = res ? res.status : 'failed';
    h.result = res ? { speedup: res.speedup, evidence: res.evidence || '' } : result;
    if (res && res.status === 'improved' && res.integrate_wp) {
      h.status = 'not_integrated'; // what stays when a stop asked for now ends the run before Implement
      const r = await implementAll([res.integrate_wp]);
      // Not rolled back (a gate without a result, a call that timed out): the commit stays, so the run stops.
      if (r.held) {
        h.status = 'integration_held';
        h.commit = r.commit || '';
        // The hand-offs it had not reached are recorded, as with no back end: a later run takes them. Read
        // now, not from `pending`: the integration's own implementer can have raised one.
        const rest = R.handoffs.filter((p) => p.owner === 'kernel_workflow' && !p.status);
        for (const p of rest) p.status = 'recorded';
        note('Kernel', 'held', `${h.id}: ${r.reason}; its integration commit stays for a human, and the run stops`
          + `${rest.length ? `; ${rest.map((p) => p.id).join(', ')} recorded, not run` : ''}`);
        return { integrated, held: r.reason };
      }
      h.status = r.ok ? 'integrated' : 'integration_failed';
      if (r.ok) {
        integrated += 1;
        h.integrated_wp = res.integrate_wp.id;
        h.commit = (R.done.find((d) => d.wp === res.integrate_wp.id) || {}).commit || '';
      }
    }
  }
  note('Kernel', integrated ? 'integrated' : 'no_gain', pending.map((h) => `${h.id}:${h.status}`).join(', '));
  return { integrated };
}

// Implement -> Kernel -> Verify -> Performance -> PR, shared by every code-changing mode. `runPerf` says when
// Performance runs (measure()). `wps` may be empty only in perf mode when the plan is all kernel hand-offs.
async function deliver(wps, extraGates, perfClaim, runPerf) {
  if (wps.length) {
    const impl = await implementAll(wps);
    if (!impl.ok) return finish('blocked', 'Implement');
  }
  if (stopHere('Implement')) return finish('stopped', 'Implement');
  const ks = await kernelStage();
  if (ks.held) return finish('blocked', 'Kernel');
  if (!wps.length && ks.integrated === 0) return finish('no_gain', 'Kernel');
  if (stopHere('Kernel')) return finish('stopped', 'Kernel');
  return fromVerify(extraGates, perfClaim, runPerf);
}

// Verify -> Performance -> Review -> PR: the rest of deliver(), where a run resumed from Verify starts.
async function fromVerify(extraGates, perfClaim, runPerf) {
  const ver = await verifyLoop(extraGates);
  if (!ver.ok) return ended(ver, 'Verify');
  if (stopHere('Verify')) return finish('stopped', 'Verify');

  if (runPerf) {
    const perf = await measure(perfClaim, runPerf);
    if (!perf.ok) {
      if (perf.trusted && perf.claimed && !perf.reproduced && MODE === 'perf') {
        // A performance change without a measured win is dropped (guardrails.md).
        const rb = await call('implementer', 'rollback', inputs({ work_package: { id: 'ALL', goal: 'drop the change', test: '' },
          reason: 'no measured win' }), ROLLBACK, 'implementer:rollback:ALL');
        note('Performance', 'dropped', `no measured win${rolledBack(rb, 'ALL')}`);
        return finish('dropped', 'Performance');
      }
      return finish('blocked', 'Performance');
    }
    if (stopHere('Performance')) return perf.skipped ? ended(stoppedUnmeasured()) : finish('stopped', 'Performance');
  }

  const rev = await reviewLoop(extraGates, perfClaim, runPerf);
  if (!rev.ok) return ended(rev, rev.at);
  if (stopHere('Review')) return finish('stopped', 'Review');

  // What the PR engineer finds missing goes back through Implement, Verify and Review, once.
  for (let round = 0; ; round++) {
    const pr = await preparePR(round);
    if (pr.ok) return finish('ready_for_review', 'PR');
    if (!pr.wps.length || round >= 1) return finish('blocked', 'PR');
    const fix = await implementAll(pr.wps);
    if (!fix.ok) return finish('blocked', 'Implement');
    const stop = await repaired(extraGates, perfClaim, runPerf);
    if (stop) return ended(stop, stop.at);
    const reviewed = await reviewLoop(extraGates, perfClaim, runPerf);
    if (!reviewed.ok) return ended(reviewed, reviewed.at);
    if (stopHere('Review')) return finish('stopped', 'Review');
  }
}

// resume_from=verify: Verify, and the route after it, again on a run directory whose Verify ended without
// a result. The director restores what the run had; a branch that moved since its last Verify round
// started, a task that would measure otherwise, or anything of the run's that its directory no longer
// records stops the run before anything is measured.
async function resumeVerify(claimOf) {
  // The run this one resumes passed every stop before Verify: one named for them now never holds.
  for (const p of STOP_PHASES[MODE].slice(0, STOP_PHASES[MODE].indexOf('Verify'))) STOPS_PASSED.add(p.toLowerCase());
  const rs = await call('director', 'resume', inputs({ task_def: R.task_def }), RESUMED);
  const cmd = !rs ? '' : String((MODE === 'issue' ? rs.repro_cmd : MODE === 'ci' ? rs.failing_test_cmd : 'none') || '').trim();
  const recorded = !!rs && Array.isArray(rs.verified_work_packages);
  const done = (recorded ? rs.verified_work_packages : []).filter((d) => d && d.wp && d.commit);
  // None left is a state only a plan of kernel hand-offs reaches, its integration reverted.
  const noneLeft = recorded && !rs.verified_work_packages.length && MODE === 'perf'
    && Array.isArray(rs.handoffs) && rs.handoffs.some((h) => h && h.owner === 'kernel_workflow');
  const saved = rs && rs.task_definition && typeof rs.task_definition === 'object' ? rs.task_definition : null;
  const changed = saved ? MEASURED.filter((k) => canon(saved[k], k) !== canon(GIVEN[k], k)) : [];
  const restored = rs ? String(rs.perf_claim || '').trim() : '';
  const lost = rs ? ['known_issues', 'handoffs'].filter((k) => !Array.isArray(rs[k])) : [];
  const why = !rs ? ['the director restored nothing from the run directory'] : [
    rs.head_ok !== true && `the branch is no longer the commit the run's last Verify round ran on${rs.head ? ` (its head: ${rs.head})` : ''}; `
      + `a changed branch is verified in a run of its own${rs.notes ? `; ${rs.notes}` : ''}`,
    !saved && 'the run directory records no task definition (run.json)',
    changed.length && `this run would measure otherwise than the one it resumes (${changed.join(', ')} not as its run.json has them); `
      + 'a changed task is verified in a run of its own',
    !done.length && !noneLeft && 'the run directory records no verified work package',
    !cmd && `the run directory records no ${MODE === 'issue' ? 'reproduction command (issue_repro)' : 'failing test command (failing_test)'}`,
    MODE === 'ci' && !String(rs.triage_path || '').trim() && 'the run directory records no ci_triage.md, which names the lanes the test failed on',
    MODE === 'issue' && !String(rs.triage_path || '').trim()
      && 'the run directory records no issue_triage.md, which holds the issue\'s classification and what Reply answers',
    ...(MODE === 'feature' ? [['spec_path', 'feature_spec.md, whose acceptance matrix Verify measures'],
      ['design_path', 'design.md, whose test plan Verify runs']] : [])
      .map(([k, what]) => !String(rs[k] || '').trim() && `the run directory records no ${what}`),
    rs.perf_required === true && !restored && 'the run was to measure a claim its run directory does not record',
    lost.length && `the run directory has no readable ${lost.map((k) => `${k}.json`).join(' or ')}: what Verify judges by `
      + 'and what a kernel regression reverts would be lost',
  ].filter(Boolean);
  if (why.length) {
    note('Setup', 'fail', `resume_from verify: ${why.join('; ')}`);
    return finish('blocked', 'Setup');
  }
  R.done = done;
  R.known_issues.push(...rs.known_issues.filter((k) => k && typeof k === 'object'));
  R.handoffs.push(...rs.handoffs.filter((h) => h && h.id));
  // The reply draft the run wrote stays for the triager; it is not this run's to offer until Reply rewrites it.
  Object.assign(C, pick(rs, ['triage_path', 'repro_cmd', 'failing_test_cmd', 'spec_path', 'design_path', 'analysis_path']));
  // The task and the goal are the run's own: the goal names data sets and guard workloads its Verify and
  // Performance ran, and the task the issue or PR its triage, reproduction and reply are about. A goal
  // run.json does not have is the run's too: it had none, and this run's would add what it never measured.
  const own = ['task', 'goal'].filter((k) => (k === 'goal' || k in saved) && canon(saved[k]) !== canon(C[k]));
  for (const k of own) C[k] = typeof saved[k] === 'string' ? saved[k] : saved[k] ? JSON.stringify(saved[k]) : '';
  note('Setup', 'resumed', `Verify again on ${rs.head || 'the verified commit'}: `
    + `${done.map((d) => d.wp).join(', ') || 'no work package left, its kernel integration reverted'}`
    + `${own.length ? `; the ${own.join(' and the ')} it was given ${own.length > 1 ? 'are' : 'is'} not the run's, which `
      + `${own.length > 1 ? 'stay' : 'stays'} as run.json has ${own.length > 1 ? 'them' : 'it'}`
      + `${own.includes('goal') && !C.goal ? ' (no goal)' : ''}` : ''}`);
  if (MODE === 'feature' && rs.design_auto_approved === true) {
    note('Design', 'auto_approved', 'restored from the run this one resumes: no human approved its design');
  }
  // The claim is read as the first run read it. A feature's as its Spec did: a cost the user stated stays the
  // claim on InferPilot's client. A fix's (issue, ci) is the number a performance problem must bring back, which
  // G5 measures whatever the fix changes; any other fix is no slower than the base, and is measured when it
  // changes code the server runs, which this run's Verify says again (validation_doctrine.md, G5 for a fix).
  const claim = claimOf(restored, rs.perf_cost);
  const when = BEFORE_A_FIX[MODE] && !restored ? 'fix' : true;
  logRoute(routeOf());
  if (MODE === 'issue') return fromVerify([{ gate: 'issue_repro', cmd, expect: 'fixed' }], claim, when);
  if (MODE === 'ci') return fromVerify([{ gate: 'failing_test', cmd, expect: 'pass on every lane it failed on' }], claim, when);
  return fromVerify([], claim, true);
}

// ---------------------------------------------------------------------------
// Modes
// ---------------------------------------------------------------------------
async function runFeature() {
  const early = await start(true);
  if (early) return early;
  // Spec's fallback client, which the user took for the source's scenarios.
  const fallback = C.client_script.startsWith(`${EXP_ROOT}/`) && /\/scripts\/fallback_client\.\w+$/.test(C.client_script);
  // InferPilot's client, that fallback or its template taken at Intake for the task's workload, need not
  // reproduce the source's numbers: the source's gain is never the claim on it. A cost the user stated (cost) is
  // no number of the source's, and stays the claim there. Every feature is judged all the same
  // (validation_doctrine.md, G5 for a feature): where neither is claimed, G5 shows that it is no slower.
  const ours = fallback || isTemplate(A.client_script);
  const claimOf = (c, cost) => (ours && cost !== true ? '' : String(c || '').trim()) || NO_SLOWER;
  if (RESUME === 'verify') return resumeVerify(claimOf);

  // Resume after a human approved design.md (a run that stopped at Design).
  if (RESUME === 'implement') {
    C.spec_path = String(A.spec_path || `${C.run_dir}/feature_spec.md`);
    C.design_path = String(A.design_path || `${C.run_dir}/design.md`);
    // The run this one resumes passed these stops: one named for them now never holds.
    for (const p of ['spec', 'recon', 'design']) STOPS_PASSED.add(p);
    enter('Design');
    const approved = await call('architect', 'design', inputs({ approved_design: true }), DESIGN);
    if (!approved || approved.gate_ok !== true || !(approved.work_packages || []).length) {
      note('Design', 'fail', approved ? (approved.summary || 'approved design has no usable work packages') : 'no design');
      return finish('blocked', 'Design');
    }
    note('Design', 'approved', `${approved.work_packages.length} work package(s) from the approved design`);
    return deliver(approved.work_packages, [], claimOf(approved.perf_claim, approved.perf_cost), true);
  }

  enter('Spec');
  const spec = await call('director', 'spec', inputs(), SPEC);
  if (!spec || spec.gates_measurable !== true) {
    note('Spec', 'fail', spec ? (spec.notes || 'acceptance criteria not measurable') : 'no spec');
    return finish('blocked', 'Spec');
  }
  C.spec_path = spec.spec_path;
  // Only the fallback runs the source's scenarios; the template taken at Intake runs the task's workload.
  if (!fallback && String(spec.workload_gap || '').trim()) return askWorkload(spec);
  note('Spec', 'pass', `${spec.spec_path}${spec.skill ? `; skill ${spec.skill}` : ''}`);
  if (stopHere('Spec')) return finish('stopped', 'Spec');

  enter('Recon');
  const recon = await call('analyst', 'recon', inputs(), RECON);
  if (!recon || (recon.unmapped || []).length > 0) {
    note('Recon', 'fail', recon ? `unmapped: ${(recon.unmapped || []).join(', ')}` : 'no recon');
    return finish('blocked', 'Recon');
  }
  C.design_path = recon.design_path;
  C.level = recon.level;
  C.gap_type = recon.gap_type || '';
  note('Recon', 'pass', `${recon.gap_type || ''} ${recon.level}; ${recon.summary || ''}`);
  if (stopHere('Recon')) return finish('stopped', 'Recon');

  enter('Design');
  const design = await call('architect', 'design', inputs(), DESIGN);
  if (!design || design.gate_ok !== true || !(design.work_packages || []).length) {
    note('Design', 'fail', design ? (design.summary || 'design gate not met') : 'no design');
    return finish('blocked', 'Design');
  }
  if (design.design_path) C.design_path = design.design_path;
  note('Design', 'pass', `${design.work_packages.length} work package(s)${design.summary ? `; ${design.summary}` : ''}`);
  // L3 work, or a design the architect flags, goes on as approved and the report says no human did;
  // auto_approve_design=false makes it wait for a human. stop_after "Design" stops either way.
  const review = design.needs_human_review === true || recon.level === 'L3';
  if (stopHere('Design') || (review && !AUTO_APPROVE_DESIGN)) {
    return finish(stopHere('Design') ? 'stopped' : 'awaiting_design_review', 'Design');
  }
  if (review) {
    note('Design', 'auto_approved', `auto_approve_design: ${recon.level === 'L3' ? 'L3 work' : 'the architect asked for a human review'}`
      + ` that no human gave; ${C.design_path} goes to the PR's reviewer${design.summary ? `; ${design.summary}` : ''}`);
  }

  return deliver(design.work_packages, [], claimOf(spec.perf_claim, spec.perf_cost), true);
}

async function runIssue() {
  const early = await start(false);
  if (early) return early;
  if (RESUME === 'verify') return resumeVerify((c) => c || NO_SLOWER);

  enter('Triage');
  const tri = await call('issue_triager', 'triage', inputs(), TRIAGE);
  if (!tri) {
    note('Triage', 'fail', 'no triage');
    return finish('blocked', 'Triage');
  }
  Object.assign(C, { triage_path: tri.triage_path, reply_draft_path: tri.reply_draft_path || '', priority: tri.priority });
  R.priority = tri.priority;
  note('Triage', tri.action, `${tri.priority} ${tri.category || ''}: ${tri.reason || ''}`);
  if (['needs_info', 'not_amd', 'plan'].includes(tri.action)) return finish('triaged', 'Triage');
  if (stopHere('Triage')) return finish('stopped', 'Triage');

  // Every path that changes code first reproduces on main: the reproduction is the issue_repro gate,
  // also for an existing fix PR.
  enter('Reproduce');
  const expectFixed = tri.action === 'close_fixed';
  // A soak or an arm that would not fit returns early with needs_min; the call with more time gets that
  // answer and goes on from the ledger. Once it has reproduced, nothing is left to need time for.
  const t = await callWithTime({
    role: 'issue_triager', sub: 'reproduce', grantKey: 'issue_triager:reproduce', what: 'the reproduction',
    label: 'issue_triager:reproduce', schema: REPRO, schemaAgain: REPRO_MORE,
    ins: (limit, earlier) => inputs({ expect_fixed: expectFixed, time_limit_min: limit, ...(earlier ? { earlier_answer: earlier } : {}) }),
    needs: (r) => (r.reproduced === true ? 0 : r.needs_min),
  });
  // Each answer is a phase entry of its own (a summary keeps 600 characters), so the arms a call ran stay
  // in the result whatever the next one returns, also when the run stops here.
  const arms = (a) => note('Reproduce', a.reproduced_on_reporter === true ? 'reproduced_on_reporter' : 'partial', a.evidence);
  if (t.needs || !t.r) {
    const partial = [t.earlier, t.r].filter(Boolean);
    C.repro_cmd = partial.map((a) => String(a.repro_cmd || '').trim()).reverse().find(Boolean) || '';
    for (const a of partial) arms(a);
    note('Reproduce', 'fail', t.needs ? `the reproduction needs ${t.needs} min; its limit is ${t.limit} min (${t.short})`
      : t.earlier ? 'no reproduction result from the call with more time' : 'no reproduction result');
    return finish('blocked', 'Reproduce');
  }
  if (t.earlier) arms(t.earlier);
  // The answer is for the whole reproduction: what the call with more time leaves out (the reporter's arm
  // it did not run again) is the earlier call's. An intermittent bug the reporter's code showed once has
  // shown, even if a later arm there passed.
  const onReporter = t.earlier ? [t.earlier.reproduced_on_reporter, t.r.reproduced_on_reporter] : [];
  const rep = t.earlier ? {
    ...t.r,
    reproduced_on_reporter: onReporter.includes(true) ? true : onReporter.find((v) => typeof v === 'boolean'),
    repro_cmd: String(t.r.repro_cmd || '').trim() || t.earlier.repro_cmd,
    perf_claim: String(t.r.perf_claim || '').trim() || t.earlier.perf_claim,
  } : t.r;
  const reproCmd = String(rep.repro_cmd || '').trim();
  if (rep.reproduced && !reproCmd) {
    note('Reproduce', 'fail', 'reproduced without a repro_cmd; the issue_repro gate needs one');
    return finish('blocked', 'Reproduce');
  }
  C.repro_cmd = reproCmd;
  // The reporter's code fails where main passes: fixed in between, which "not reproduced" would hide.
  const onReporterOnly = !rep.reproduced && rep.reproduced_on_reporter === true;
  note('Reproduce', rep.reproduced ? 'reproduced' : onReporterOnly ? 'reproduced_on_reporter' : 'not_reproduced',
    rep.evidence);
  if (!rep.reproduced) {
    if (!onReporterOnly || !BISECT_FIXED || stopHere('Reproduce')) {
      return finish(expectFixed || onReporterOnly ? 'fixed_on_main' : 'not_reproduced', 'Reproduce');
    }
    enter('Bisect');
    const bf = reproCmd ? await call('ci_guardian', 'bisect_fix', inputs(), BISECT_FIX) : null;
    const fix = bf && bf.confirmed === true ? String(bf.fix_commit || '').trim() : '';
    if (fix) {
      C.fixed_by = R.fixed_by = fix;
      note('Bisect', 'pass', `fixed by ${fix}${bf.evidence ? `: ${bf.evidence}` : ''}`);
    } else {
      note('Bisect', 'fail', !reproCmd ? 'no repro_cmd to bisect with'
        : bf ? `fix ${bf.fix_commit || '?'} not confirmed` : 'no bisect result');
    }
    return finish('fixed_on_main', 'Bisect');
  }
  // A performance issue is fixed when the reported number comes back; the reproduction states it, and G5
  // measures it whatever the fix changes. Any other fix is measured when it changes code the server runs, and
  // is no slower than the base (validation_doctrine.md, G5 for a fix).
  const perfIssue = tri.category === 'performance';
  const issueClaim = perfIssue ? String(rep.perf_claim || '').trim() : NO_SLOWER;
  const runPerf = perfIssue ? true : 'fix';
  if (perfIssue && !issueClaim) {
    note('Reproduce', 'fail', 'a performance issue reproduced without a perf_claim: the number the fix must restore');
    return finish('blocked', 'Reproduce');
  }
  if (stopHere('Reproduce')) return finish('stopped', 'Reproduce');

  const gates = [{ gate: 'issue_repro', cmd: reproCmd, expect: 'fixed' }];
  if (tri.action === 'push_existing_pr') {
    // An existing fix has no Diagnose phase: a stop named for it still ends the run before the fix is applied.
    if (stopHere('Diagnose')) {
      note('Reproduce', 'stopped', 'stop_after=Diagnose: an existing fix PR is applied without a Diagnose phase, so the run stops before applying it');
      return finish('stopped', 'Reproduce');
    }
    const lacking = askForFix('Reproduce');
    if (lacking) return lacking;
    const prs = (tri.existing_prs || []).join(', ');
    const wp = { id: 'WP1', kind: 'apply_pr', goal: `apply the existing fix ${prs} and complete what verification needs`,
      test: 'the issue reproduction no longer fails; the PR\'s own tests pass', files: [], depends_on: [] };
    return deliver([wp], gates, issueClaim, runPerf);
  }

  enter('Diagnose');
  const dg = await call('investigator', 'diagnose', inputs(), DIAG);
  if (!dg) {
    note('Diagnose', 'fail', 'no diagnosis');
    return finish('blocked', 'Diagnose');
  }
  note('Diagnose', dg.verdict, dg.root_cause);
  // The baseline's bug is what the issue reported: the reply explains it as the cause, not as something else seen.
  if (dg.verdict === 'baseline_bug' && dg.known_issue) R.known_issues.push({ ...dg.known_issue, cause_of_this_issue: true });
  if (dg.verdict !== 'fix_needed' || !(dg.work_packages || []).length) return finish('diagnosed', 'Diagnose');
  if (stopHere('Diagnose')) return finish('stopped', 'Diagnose');
  const lacking = askForFix('Diagnose');
  if (lacking) return lacking;

  return deliver(dg.work_packages, gates, issueClaim, runPerf);
}

async function runCi() {
  const early = await start(false);
  if (early) return early;
  if (RESUME === 'verify') return resumeVerify((c) => c || NO_SLOWER);

  enter('Classify');
  const cl = await call('ci_guardian', 'classify', inputs(), CLASSIFY);
  if (!cl) {
    note('Classify', 'fail', 'no classification');
    return finish('blocked', 'Classify');
  }
  const failingCmd = String(cl.failing_test_cmd || '').trim();
  const handOver = cl.failure_class === 'infrastructure' || cl.failure_class === 'flaky';
  Object.assign(C, { triage_path: cl.triage_path, failing_test_cmd: failingCmd, failure_class: cl.failure_class });
  if (!handOver && !failingCmd) {
    note('Classify', 'fail', `${cl.failure_class} without a failing_test_cmd; the failing_test gate needs one`);
    return finish('blocked', 'Classify');
  }
  // A failure on a number the test measures is a performance problem: fixed when that number comes back, which
  // G5 measures whatever the fix changes, a moved threshold included. Any other fix is measured when it changes
  // code the server runs, and is no slower than the base (validation_doctrine.md, G5 for a fix).
  const perfFailure = cl.performance === true;
  const ciClaim = perfFailure ? String(cl.perf_claim || '').trim() : NO_SLOWER;
  if (!handOver && perfFailure && !ciClaim) {
    note('Classify', 'fail', 'a performance failure classified without a perf_claim: the number the fix must restore');
    return finish('blocked', 'Classify');
  }
  note('Classify', cl.failure_class, `${perfFailure ? 'performance: ' : ''}${cl.evidence}`);
  if (handOver) return finish('handed_off', 'Classify');
  if (stopHere('Classify')) return finish('stopped', 'Classify');

  enter('Bisect');
  const bi = await call('ci_guardian', 'bisect', inputs(), BISECT);
  if (!bi || bi.confirmed !== true || !(bi.work_packages || []).length) {
    note('Bisect', 'fail', bi ? `culprit ${bi.culprit} not confirmed or no fix planned` : 'no bisect result');
    return finish('blocked', 'Bisect');
  }
  C.culprit = bi.culprit;
  note('Bisect', 'pass', `culprit ${bi.culprit}`);
  if (stopHere('Bisect')) return finish('stopped', 'Bisect');
  const lacking = askForFix('Bisect');
  if (lacking) return lacking;

  return deliver(bi.work_packages, [{ gate: 'failing_test', cmd: failingCmd, expect: 'pass on every lane it failed on' }],
    ciClaim, perfFailure ? true : 'fix');
}

async function runPerf() {
  const early = await start(true);
  if (early) return early;
  if (RESUME === 'verify') return resumeVerify((c) => c || 'the planned gain');

  enter('Profile');
  const pf = await call('perf_engineer', 'profile', inputs(), PROFILE);
  if (!pf) {
    note('Profile', 'fail', 'no profile');
    return finish('blocked', 'Profile');
  }
  if (String(pf.not_run || '').trim()) {
    note('Profile', 'fail', `not run: ${String(pf.not_run).trim()}`);
    return finish('blocked', 'Profile');
  }
  if (pf.path_ok !== true) {
    note('Profile', 'fail', `the profiled run did not take the intended path: ${pf.path_evidence || 'no evidence given'}`);
    return finish('blocked', 'Profile');
  }
  C.analysis_path = pf.analysis_path;
  const opps = pf.opportunities || [];
  note('Profile', 'pass', `${opps.length} opportunit${opps.length === 1 ? 'y' : 'ies'}: ` +
    opps.map((o) => `${o.id}:${o.kind}(${o.owner})`).join(', '));
  if (stopHere('Profile')) return finish('stopped', 'Profile');

  enter('Plan');
  const plan = await call('architect', 'perf_plan', inputs({ opportunities: opps, apply_prs: APPLY_PRS }), PERF_PLAN);
  if (!plan || plan.gate_ok !== true) {
    note('Plan', 'fail', 'plan gate not met');
    return finish('blocked', 'Plan');
  }
  for (const h of plan.handoffs || []) {
    // Kernel hand-offs wait for the Kernel stage; other owners (Hyperloom) are recorded.
    R.handoffs.push(h.owner === 'kernel_workflow' ? { ...h, source: 'plan' } : { ...h, source: 'plan', status: 'recorded' });
  }
  const kernelHandoffs = R.handoffs.filter((h) => h.owner === 'kernel_workflow').length;
  if (!(plan.work_packages || []).length) {
    if (kernelHandoffs && KERNEL_BACKEND !== 'none') {
      note('Plan', 'kernel_only', `${kernelHandoffs} kernel hand-off(s) for kernel_backend=${KERNEL_BACKEND}`);
      return deliver([], [], plan.perf_claim || 'the planned gain', true);
    }
    note('Plan', 'no_work', `${(plan.handoffs || []).length} hand-off(s), nothing for InferPilot`);
    for (const h of R.handoffs) if (!h.status) h.status = 'recorded';
    return finish('handed_off', 'Plan');
  }
  note('Plan', 'pass', `${plan.work_packages.length} work package(s); claim: ${plan.perf_claim || 'none'}`);
  if (stopHere('Plan')) return finish('stopped', 'Plan');

  return deliver(plan.work_packages, [], plan.perf_claim || 'the planned gain', true);
}

async function runScan() {
  enter('Collect');
  const sources = Array.isArray(A.sources) && A.sources.length ? A.sources : ['ci', 'issues', 'upstream'];
  const rescan = A.rescan === true || String(A.rescan).toLowerCase() === 'true';
  const col = await call('scout', 'collect', inputs({ sources, since: String(A.since || ''), rescan,
    upstream_repos: Array.isArray(A.upstream_repos) ? A.upstream_repos : [] }), COLLECT);
  // One report a day: a scan from the last 24 hours makes the run ask before it starts another.
  if (col && col.lock === 'skipped' && !rescan) {
    const r = col.recent || {};
    const when = r.at || 'the last 24 hours';
    const where = r.url || r.run_dir || 'this container';
    note('Collect', 'needs_input', `a scan from ${when} exists: ${where}`);
    const questions = [{ field: 'rescan', question: `A daily report from ${when} exists (${where}). Scan again anyway?`,
      why: 'one report a day is enough: another scan repeats the same API calls and agent work', options: [true, false] }];
    Object.assign(R, { status: 'needs_input', stopped_at: 'Collect', recent: r, questions,
      next: { say: `A daily report from ${when} exists (${where}); scan again only if a fresh one is needed.`,
        ask: questions, rerun_with: { answers: { rescan: true } } } });
    log(`[inferpilot] mode=scan status=needs_input: a scan from ${when} exists (${where})`);
    return R;
  }
  if (col && col.lock === 'busy') {
    note('Collect', 'busy', `another InferPilot task holds this machine${col.notes ? `: ${col.notes}` : ''}`);
    return finish('busy', 'Collect');
  }
  if (!col || !col.run_dir) {
    note('Collect', 'fail', 'no candidates');
    return finish('blocked', 'Collect');
  }
  C.run_dir = col.run_dir;
  C.candidates_path = col.candidates_path;
  note('Collect', 'pass', JSON.stringify(col.counts || {}));
  if (stopHere('Collect')) return finish('stopped', 'Collect');

  enter('Dispatch');
  const dp = await call('dispatcher', 'dispatch', inputs(), DISPATCH);
  if (!dp) {
    note('Dispatch', 'fail', 'no ranking');
    return finish('blocked', 'Dispatch');
  }
  C.queue_path = dp.queue_path;
  R.queue = (dp.top || []).slice(0, 20);
  note('Dispatch', 'pass', `${(dp.top || []).length} ranked; ${dp.matrix_changes || 0} matrix change(s)`);
  return finish('queued', 'Dispatch');
}

const RUNNERS = { feature: runFeature, issue: runIssue, ci: runCi, perf: runPerf, scan: runScan };
log(`[inferpilot] mode=${MODE} inferpilot_dir=${INFERPILOT} framework=${C.framework}${C.framework_dir ? ` (${C.framework_dir})` : ''}`);
if (ROUTES[MODE]) logRoute(ROUTES[MODE]);
if (C.container) log(`[inferpilot] container=${C.container}`); // the runner stops what the run started there
try {
  return await RUNNERS[MODE]();
} catch (e) {
  if (e instanceof StopRequested) return finish('stopped', e.message);
  throw e;
}
