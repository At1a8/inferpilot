// The answers the dry run scripts for every role, shared with tests/backend_parity.mjs: what each role
// returns unless a scenario overrides it, and how a prompt names its role, phase and inputs.
export const RUN = '/tmp/inferpilot_dry_run';
// A kernel back end the kernel liaison runs; a hand-off whose reason is FAIL makes it fail.
export const CMD = { kernel_backend: 'command', kernel_backend_command: 'my-kernel-agent --task {task_dir} --out {out_dir} --gpus {gpu_ids}' };

export const WP = (id, extra) => ({ id, goal: `goal of ${id}`, kind: 'framework', files: ['x.py'], test: `test ${id}`, depends_on: [], ...(extra || {}) });

// Answers every role gives unless a scenario overrides them.
export const DEFAULTS = {
  'intake:define': (ins) => ({ status: 'ready', task_def: ins.given || {}, provenance: {}, assumptions: [], questions: [], summary: 'as given' }),
  'director:setup': () => ({ lock: 'acquired', run_dir: RUN, baseline_status: 'pass', baseline: '3/3 runs within spread', branch: 'inferpilot/task', worktree: `${RUN}/wt`, gpu_ids: '0,1',
    base_commit: 'base0f1e2d3c',
    server_script: `${RUN}/scripts/server.sh`, accuracy_script: `${RUN}/scripts/accuracy.sh` }),
  'director:spec': () => ({ spec_path: `${RUN}/feature_spec.md`, gates_measurable: true, perf_claim: '+20% output tok/s at equal HBM' }),
  'director:report': (ins) => ({ report_path: `${RUN}/run_report.md`, summary: 'done',
    ...('share_daily_report' in ins ? { daily_report_url: '', daily_report_action: '' } : {}) }),
  'analyst:recon': () => ({ design_path: `${RUN}/design.md`, subsystems: [{ name: 'scheduler', files: ['a.py'], hook: 'h' }], unmapped: [], gap_type: 'framework-only', level: 'L1' }),
  'architect:design': () => ({ design_path: `${RUN}/design.md`, work_packages: [WP('WP1'), WP('WP2', { depends_on: ['WP1'] })], gate_ok: true, needs_human_review: false, perf_claim: '+20% output tok/s at equal HBM' }),
  'architect:perf_plan': () => ({ work_packages: [WP('WP1')], handoffs: [{ id: 'O2', owner: 'kernel_workflow', spec_path: `${RUN}/op_spec.md` }], perf_claim: '-5% TPOT', gate_ok: true }),
  'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py', files_changed: ['x.py'], kernel_handoff: null }),
  'implementer:rollback': () => ({ wp: 'x', reset_to: 'base', clean: true }),
  'test_engineer:unit': (ins) => ({ wp: ins.work_package.id, unit_pass: true, feature_off_unchanged: true, fails_without_change: 'parent', incomplete: '', evidence: 'ok' }),
  'verify_engineer:gpus': () => ({ in_use: '' }),
  'verify_engineer:verify': () => ({ all_pass: true, gates: { G0: 'pass', G2: 'pass' }, failures: [], ledger_path: `${RUN}/experiment_ledger.md` }),
  'investigator:investigate': () => ({ verdict: 'fix_needed', work_packages: [WP('FIX1')], summary: 'race in hook' }),
  'investigator:diagnose': () => ({ verdict: 'fix_needed', root_cause: 'stale import', work_packages: [WP('WP1')] }),
  'perf_engineer:perf_eval': () => ({ claim_reproduced: true, held_out_ok: true, guards_ok: true, workload_unchanged: true, path_ok: true, results: [], summary: '+22%' }),
  'perf_engineer:profile': () => ({ analysis_path: `${RUN}/perf_analysis.md`, path_ok: true, path_evidence: 'aiter attention, graphs on', metrics: { gpu_busy: 0.71 }, opportunities: [
    { id: 'O1', kind: 'host_sync', where: 'aiter_backend.py', evidence: 'trace', upper_bound_pct: 4, owner: 'inferpilot' },
    { id: 'O2', kind: 'kernel', where: 'moe', evidence: 'trace', upper_bound_pct: 9, owner: 'kernel_workflow' }] }),
  'reviewer:review': () => ({ verdict: 'approve', findings: [{ severity: 'minor', where: 'x.py:1', issue: 'a name' }], review_path: `${RUN}/review.md`, summary: 'one minor' }),
  'reviewer:reply': () => ({ findings: [], check_path: `${RUN}/reply_check.md`, summary: 'says only what the run showed' }),
  'pr_engineer:comment': () => ({ comment_path: `${RUN}/pr_comment.md`, summary: 'applied to main, the reproduction still fails 3 of 3' }),
  'pr_engineer:pr': () => ({ branch: 'inferpilot/task', title: '[Fix] [AMD] scope: thing', commits: ['c_WP1'], lint_pass: true, feature_off_final: true, content_unchanged: true, merges_cleanly: true, leak_scan_findings: [], launch_checked: true, pr_body_path: `${RUN}/pr_body.md`, push_command: 'git push origin inferpilot/task' }),
  'curator:learn': () => ({ updates: [{ file: 'knowledge/known_issues.md', change: 'none' }], skill_status: 'draft' }),
  'issue_triager:triage': () => ({ priority: 'P0', category: 'crash', action: 'reproduce', reason: 'default config crash', existing_prs: [], triage_path: `${RUN}/issue_triage.md`, reply_draft_path: `${RUN}/reply.md` }),
  'issue_triager:reproduce': () => ({ reproduced: true, repro_cmd: 'bash repro.sh', evidence: 'segfault 3/3' }),
  'issue_triager:reply': () => ({ reply_draft_path: `${RUN}/reply_draft.md`, summary: 'fix prepared, PR to follow' }),
  'issue_triager:spinoff': (ins) => ({ spinoffs: [
    ...(ins.known_issues || []).map((k) => ({ id: k.id, source: 'known_issue', status: 'drafted',
      draft_path: `${RUN}/spinoff/${k.id}.md`, task_definition: 'mode=issue, task=<issue URL>' })),
    ...(ins.leads || []).map((l, i) => ({ id: `lead-${i + 1}`, source: 'lead', status: 'unconfirmed', note: l.title })),
  ] }),
  'ci_guardian:classify': () => ({ failure_class: 'code_regression', failing_test_cmd: 'pytest test_x.py', window: 'a..b', evidence: 'new regression since 10-21', triage_path: `${RUN}/ci_triage.md` }),
  'ci_guardian:bisect': () => ({ culprit: 'deadbeef', confirmed: true, work_packages: [WP('WP1')], evidence: 'fails at culprit, passes at parent' }),
  'ci_guardian:bisect_fix': () => ({ fix_commit: 'feedface', confirmed: true, evidence: 'fails at parent, passes at fix' }),
  'scout:collect': () => ({ lock: 'acquired', run_dir: RUN, candidates_path: `${RUN}/candidates.json`, counts: { upstream: 3, issues: 32, ci: 76, hardware: 5 } }),
  'dispatcher:dispatch': () => ({ queue_path: `${RUN}/task_queue.md`, top: [{ rank: 1, task: 'GLM-5.3 DCP crash', type: 'bug', level: 'L1', priority: 'P0', source: 'issues',
    task_def: { mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849', framework_dir: '/sgl-workspace/sglang' } }], matrix_changes: 2 }),
  'kernel_liaison:package': (ins) => ({ task_dir: `${RUN}/kernel/${ins.handoff.id}/task`, mode: 'optimize', target_language: 'hip',
    op_spec: { op_kind: 'swap_in' }, task: ins.handoff.reason === 'FAIL' ? 'FAIL on purpose' : 'decode, M <= 64' }),
  'kernel_liaison:run': (ins) => (ins.handoff.reason === 'FAIL'
    ? { exit_code: 1, result_path: '', error: 'the kernel agent failed' }
    : { exit_code: 0, result_path: `${RUN}/kernel/out/result.json` }),
  'kernel_liaison:collect': (ins) => ((ins.backend_result || {}).error
    ? { status: 'failed', notes: ins.backend_result.error }
    : { status: 'improved', speedup: 1.37, evidence: 'eval dir', integrate_wp: WP(`K-${ins.handoff.id}`, { kind: 'kernel' }) }),
};
// What every unit answer says besides its result (roles/test_engineer.md): what the gate could not stop, '' when
// it left nothing. The default is a gate that cleaned up; a scenario that writes its own answer says so itself.
const unitResult = DEFAULTS['test_engineer:unit'];
DEFAULTS['test_engineer:unit'] = (ins, n) => ({ ...unitResult(ins, n), left_running: '' });
// The check after a Verify round that did not say what it left running says what it could not stop, too.
const gpusBeforeARound = DEFAULTS['verify_engineer:gpus'];
DEFAULTS['verify_engineer:gpus'] = (ins, n) => (String(ins.round).startsWith('after')
  ? { in_use: '', in_use_gpus: [], left_running: '' } : gpusBeforeARound(ins, n));

// The implementer hands WP1's kernel over (reason FAIL makes the fake back end throw).
export const withKernelHandoff = (reason) => ({
  'implementer:implement': (ins) => ({ wp: ins.work_package.id, status: 'done', commit: `c_${ins.work_package.id}`, unit_test_cmd: 'pytest -q t.py',
    kernel_handoff: ins.work_package.id === 'WP1'
      ? { op_spec_path: `${RUN}/handoffs/K-WP1/op_spec.md`, reference_path: `${RUN}/handoffs/K-WP1/ref.py`, test_cmd: 'pytest k.py', plug_in: 'x.py:f', reason }
      : null }),
});

export const ROLE_RE = /You are the ([a-z_]+)\. PHASE=([a-z_]+)\./;

export function parseInputs(prompt) {
  const out = {};
  const block = prompt.split('## Inputs\n')[1] || '';
  for (const line of block.split('\n')) {
    const m = /^- ([a-z_]+): (.*)$/.exec(line);
    if (!m) continue;
    try { out[m[1]] = JSON.parse(m[2]); } catch { out[m[1]] = m[2]; }
  }
  return out;
}

// What a task of each mode needs besides its definition, so that the intake has nothing to ask.
export const MODE_DEFAULTS = {
  issue: { model_path: '/models/m', client_script: '/s/client.sh' },
  ci: { model_path: '/models/m', client_script: '/s/client.sh' },
  feature: { model_path: '/models/m', server_script: '/s/server.sh', client_script: '/s/client.sh' },
  perf: { model_path: '/models/m', workload: '{"isl":8192,"osl":1024,"conc":64}', server_script: '/s/server.sh', client_script: '/s/client.sh' },
};
