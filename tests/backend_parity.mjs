// Parity of the two agent backends: the same task, run end to end by runtime/run.mjs on `--backend claude`
// and on `--backend cursor`. Stand-in CLIs answer as the dry run scripts (tests/fixtures/answers.mjs),
// each reading its prompt the way the real CLI gets it and printing that CLI's own stream-json. Both
// backends must hand the roles the same prompts in the same order and end with the same result; a role
// that fails, a prompt too long for one argument, a timeout and the models must come out alike too, and
// each CLI gets its own flags and environment. Offline, no model.
//
//   node tests/backend_parity.mjs
import path from 'node:path';
import { spawn } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { MODE_DEFAULTS } from './fixtures/answers.mjs';

const INFERPILOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = mkdtempSync(path.join(tmpdir(), 'inferpilot-parity-'));
const FIXTURES = pathToFileURL(path.join(INFERPILOT, 'tests', 'fixtures', 'answers.mjs')).href;

const STANDIN = path.join(TMP, 'standin.mjs');
writeFileSync(STANDIN, `
import { appendFileSync, readFileSync } from 'node:fs';
import { DEFAULTS, ROLE_RE, parseInputs } from ${JSON.stringify(FIXTURES)};
const as = process.env.STANDIN_AS;
let prompt = as === 'claude' ? readFileSync(0, 'utf8') : process.argv[process.argv.length - 1];
const pointer = /^Your complete instructions are in (\\S+)\\. Read the whole file first/.exec(prompt);
if (pointer) prompt = readFileSync(pointer[1], 'utf8');
const m = ROLE_RE.exec(prompt);
const key = m ? m[1] + ':' + m[2] : 'none';
appendFileSync(process.env.STANDIN_LOG, JSON.stringify({ key, prompt, args: process.argv.slice(2, as === 'claude' ? undefined : -1),
  via: as === 'claude' ? 'stdin' : pointer ? 'file' : 'argument', sandbox: process.env.IS_SANDBOX || '' }) + '\\n');
if (process.env.STANDIN_SLEEP_MS) await new Promise((r) => setTimeout(r, Number(process.env.STANDIN_SLEEP_MS)));
if (key === process.env.STANDIN_FAIL) {
  process.stderr.write('Error: ' + key + ' failed on purpose\\n');
  process.exit(1);
}
const f = DEFAULTS[key];
const answer = f ? JSON.stringify(await f(parseInputs(prompt), 1)) : 'no scripted answer for ' + key;
const events = as === 'claude' ? [
  { type: 'system', subtype: 'init' },
  { type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash', input: { command: 'ls' } }] } },
  { type: 'user', message: { content: [] } },
  { type: 'result', subtype: 'success', is_error: false, result: answer,
    usage: { input_tokens: 1, output_tokens: 2, cache_read_input_tokens: 3, cache_creation_input_tokens: 4 } },
] : [
  { type: 'system', subtype: 'init' },
  { type: 'thinking', subtype: 'delta', text: 'hm' },
  { type: 'tool_call', subtype: 'started', tool_call: { shellToolCall: { args: { command: 'ls' } } } },
  { type: 'tool_call', subtype: 'completed', tool_call: { shellToolCall: { args: { command: 'ls' }, result: {} } } },
  { type: 'result', subtype: 'success', is_error: false, result: answer,
    usage: { inputTokens: 1, outputTokens: 2, cacheReadTokens: 3, cacheWriteTokens: 4 } },
];
process.stdout.write(events.map((e) => JSON.stringify(e)).join('\\n') + '\\n');
`);
const BIN = {};
for (const as of ['claude', 'cursor']) {
  BIN[as] = path.join(TMP, `${as}-standin`);
  writeFileSync(BIN[as], `#!/bin/sh\nSTANDIN_AS=${as} exec "${process.execPath}" "${STANDIN}" "$@"\n`);
  chmodSync(BIN[as], 0o755);
}

let n = 0;
// One run of a workflow script on one backend: its exit code, result, phases, and what the CLI was given.
function runOn(backend, script, args, { env = {}, flags = [] } = {}) {
  const log = path.join(TMP, `calls-${++n}-${backend}.jsonl`);
  // IS_SANDBOX only from the backend (a container's shell may export it); a long prompt's file in TMP.
  const { IS_SANDBOX, ...inherited } = process.env;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', backend,
      '--concurrency', '1', '--args', JSON.stringify(args), ...flags], {
      env: { ...inherited, TMPDIR: TMP, INFERPILOT_CLAUDE_BIN: BIN.claude, INFERPILOT_CURSOR_BIN: BIN.cursor, STANDIN_LOG: log, ...env },
    });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('close', (code) => {
      let result = null;
      try {
        result = JSON.parse(out);
      } catch { /* no result: compared as null */ }
      const calls = existsSync(log) ? readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
      const phases = [...err.matchAll(/=== PHASE(?: \d+\/\d+ \[[#-]+\]| \(off the route\))?: (.*?) ===/g)].map((m) => m[1]);
      resolve({ code, result, calls, phases, err });
    });
  });
}

let failed = 0;
let passed = 0;
function check(cond, msg) {
  if (cond) passed++;
  else { failed++; console.error(`  FAIL: ${msg}`); }
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
// A role told its limit gets the moment it ends, which no two runs share.
const timeless = (prompt) => prompt.replace(/^- deadline_utc: .*$/gm, '- deadline_utc: <its call\'s start plus the limit>');
const INFERPILOT_JS = path.join(INFERPILOT, 'inferpilot.js');
const taskArgs = (args) => ({ inferpilot_dir: INFERPILOT, framework_dir: '/tmp/sglang', agent_retries: 1, ...MODE_DEFAULTS[args.mode], ...args });

// Both backends ran the same task alike: what each role was given, in order, and how the run ended.
function alike(name, c, k) {
  check(c.code === k.code, `${name}: exit code claude ${c.code}, cursor ${k.code}`);
  check(same(c.result, k.result), `${name}: the results differ: ${JSON.stringify(c.result).slice(0, 200)} | ${JSON.stringify(k.result).slice(0, 200)}`);
  check(same(c.phases, k.phases), `${name}: phases claude ${c.phases} | cursor ${k.phases}`);
  check(same(c.calls.map((x) => x.key), k.calls.map((x) => x.key)), `${name}: role calls claude ${c.calls.map((x) => x.key)} | cursor ${k.calls.map((x) => x.key)}`);
  const differ = c.calls.findIndex((x, i) => !k.calls[i] || timeless(x.prompt) !== timeless(k.calls[i].prompt));
  check(differ < 0, `${name}: the prompt of call ${differ} (${(c.calls[differ] || {}).key}) differs between the backends`);
}

const scenarios = [
  ['every mode: the same prompts, phases and result on both backends', async () => {
    const tasks = [
      { mode: 'feature', task: 'port HiSparse V2 to DeepSeek-V4.1' },
      { mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' },
      { mode: 'ci', task: 'tracker cluster _check ImportError' },
      { mode: 'perf', task: 'p', apply_prs: ['#46329'] },
      { mode: 'scan', framework_dir: '' },
    ];
    const runs = await Promise.all(tasks.flatMap((t) => ['claude', 'cursor'].map((b) => runOn(b, INFERPILOT_JS, taskArgs(t)))));
    tasks.forEach((t, i) => {
      const [c, k] = [runs[2 * i], runs[2 * i + 1]];
      check(c.code === 0 && c.calls.length >= 3 && c.result && c.result.status, `${t.mode}: a whole run on claude (${c.code}, ${c.calls.length} calls): ${c.err.slice(-300)}`);
      alike(t.mode, c, k);
      console.log(`     ${t.mode}: ${c.calls.length} role calls, ${c.phases.length} phases, status ${c.result && c.result.status}`);
    });
    const [c, k] = [runs[0], runs[1]];
    const unit = (r) => (r.calls.find((x) => x.key === 'test_engineer:unit') || { prompt: '' }).prompt;
    check([c, k].every((r) => /^- deadline_utc: \d{4}-\d\d-\d\dT[\d:.]+Z$/m.test(unit(r))), 'both tell the unit gate when its limit ends');
    check(c.calls.every((x) => x.via === 'stdin') && k.calls.every((x) => x.via === 'argument'), 'claude reads stdin, the Cursor CLI an argument');
    const flags = (x) => x.args.join(' ');
    check(c.calls.every((x) => flags(x) === '-p --dangerously-skip-permissions --output-format stream-json --verbose'),
      `claude's flags: ${flags(c.calls[0])}`);
    check(k.calls.every((x) => flags(x) === '-p --force --trust --sandbox disabled --output-format stream-json'),
      `the Cursor CLI's flags: ${flags(k.calls[0])}`);
    check(/done in 0 min, 10 tokens/.test(c.err) && /done in 0 min, 10 tokens/.test(k.err), 'both count the tokens of their result events');
  }],
  ['a role that fails: the same retries, stop and result', async () => {
    const t = taskArgs({ mode: 'issue', task: 'https://github.com/sgl-project/sglang/issues/38849' });
    const env = { STANDIN_FAIL: 'issue_triager:reproduce' };
    const [c, k] = await Promise.all(['claude', 'cursor'].map((b) => runOn(b, INFERPILOT_JS, t, { env })));
    check(c.result && c.result.status !== 'ready_for_review', `the failure stops the run: ${c.result && c.result.status}`);
    alike('failing role', c, k);
  }],
  ['a prompt too long for one argument reaches both CLIs whole', async () => {
    // 128 KiB: past the most Linux takes in a single argument.
    const script = path.join(TMP, 'long.js');
    writeFileSync(script, `export const meta = { name: 'long', description: 'a long prompt', phases: [] };
phase('Collect');
const r = await agent('You are the scout. PHASE=collect.\\n' + 'x'.repeat(128 * 1024), { label: 'long' });
return { status: r.includes('candidates') ? 'done' : 'no' };`);
    const [c, k] = await Promise.all(['claude', 'cursor'].map((b) => runOn(b, script, {})));
    check(k.calls[0] && k.calls[0].via === 'file', `the Cursor CLI gets it through a file: ${k.calls[0] && k.calls[0].via}`);
    check(c.calls[0] && c.calls[0].prompt.length > 128 * 1024, 'claude gets it on stdin, whole');
    alike('long prompt', c, k);
    const left = readdirSync(TMP).filter((f) => f.startsWith('inferpilot-prompt-'));
    check(left.length === 0, `the prompt's file is removed once the CLI is done: ${left}`);
  }],
  ['a model and a review model reach both CLIs as --model; Claude Code runs with IS_SANDBOX', async () => {
    const t = taskArgs({ mode: 'ci', task: 'tracker cluster _check ImportError', review_model: 'm-review' });
    const [c, k] = await Promise.all(['claude', 'cursor'].map((b) => runOn(b, INFERPILOT_JS, t, { flags: ['--model', 'm-main'] })));
    alike('models', c, k);
    const model = (x) => (x.args.indexOf('--model') < 0 ? '' : x.args[x.args.indexOf('--model') + 1]);
    for (const [b, r] of [['claude', c], ['cursor', k]]) {
      const reviews = r.calls.filter((x) => x.key === 'reviewer:review');
      check(reviews.length > 0 && reviews.every((x) => model(x) === 'm-review'), `${b}: the reviewer's model: ${reviews.map(model)}`);
      check(r.calls.every((x) => x.key === 'reviewer:review' || model(x) === 'm-main'), `${b}: the other roles' models: ${[...new Set(r.calls.map(model))]}`);
    }
    check(c.calls.length > 0 && c.calls.every((x) => x.sandbox === '1'), `Claude Code runs with IS_SANDBOX=1: ${[...new Set(c.calls.map((x) => x.sandbox))]}`);
  }],
  ['a timeout ends both runs alike', async () => {
    const script = path.join(TMP, 'slow.js');
    writeFileSync(script, `export const meta = { name: 'slow', description: 'a slow agent', phases: [] };
phase('Collect');
await agent('You are the scout. PHASE=collect.', { label: 'slow' });
return { status: 'done' };`);
    const opts = { env: { STANDIN_SLEEP_MS: '5000' }, flags: ['--agent-timeout-ms', '400'] };
    const [c, k] = await Promise.all(['claude', 'cursor'].map((b) => runOn(b, script, {}, opts)));
    check(c.code === 1 && k.code === 1, `both runs fail: ${c.code} ${k.code}`);
    const why = (r) => (/(?:claude|cursor) \[slow\] (timed out after \d+ s)/.exec(r.err) || [])[1];
    check(why(c) && why(c) === why(k), `the same reason: ${why(c)} | ${why(k)}`);
  }],
  ['the script gets the runner\'s --agent-timeout-ms as its agent_timeout_ms, unless it gave a shorter one', async () => {
    const script = path.join(TMP, 'limit.js');
    writeFileSync(script, `export const meta = { name: 'limit', description: 'the limit a script gets', phases: [] };
return { got: args.agent_timeout_ms === undefined ? null : args.agent_timeout_ms };`);
    const got = async (args, flags) => ((await runOn('claude', script, args, { flags })).result || {}).got;
    check(await got({}, []) === null, 'without the flag the script keeps its own');
    for (const [args, want] of [[{}, 400], [{ agent_timeout_ms: 100 }, 100], [{ agent_timeout_ms: 1000 }, 400], [{ agent_timeout_ms: 0 }, 400]]) {
      const g = await got(args, ['--agent-timeout-ms', '400']);
      check(g === want, `${JSON.stringify(args)} under --agent-timeout-ms 400: ${g}, not ${want}`);
    }
  }],
  ['the log ends with what comes next, alike on both backends, and each run it names gets its arguments file', async () => {
    const cases = [['scan', taskArgs({ mode: 'scan', framework_dir: '' }), 'start-1'], ['perf', taskArgs({ mode: 'perf', task: 'p', workload: '' }), 'again']];
    for (const [name, args, tag] of cases) {
      const runs = await Promise.all(['claude', 'cursor'].map((b) => {
        const file = path.join(TMP, `${name}-${b}.json`);
        writeFileSync(file, JSON.stringify(args));
        return runOn(b, INFERPILOT_JS, args, { flags: ['--args-file', file] }).then((r) => ({ ...r, b, file }));
      }));
      const next = (r) => r.err.slice(r.err.indexOf('=== NEXT: ')).replace(/^\[[\d:]+\] /gm, '').replaceAll(`${name}-${r.b}`, `${name}-B`).replaceAll(r.b, 'B');
      check(runs.every((r) => r.err.includes('=== NEXT: ')) && next(runs[0]) === next(runs[1]), `${name}: the NEXT lines differ: ${next(runs[0])} | ${next(runs[1])}`);
      for (const r of runs) {
        const written = r.file.replace(/\.json$/, `.${tag}.json`);
        const a = existsSync(written) ? JSON.parse(readFileSync(written, 'utf8')) : {};
        const ok = tag === 'again' ? 'workload' in (a.answers || {}) && a.mode === 'perf' : a.mode === 'issue' && a.inferpilot_dir === INFERPILOT;
        check(ok, `${name} on ${r.b}: ${written}: ${JSON.stringify(a)}`);
        check(r.err.includes(`--backend ${r.b} `) && r.err.includes(`--args-file ${written} --result-file`), `${name} on ${r.b}: the command for it: ${next(r)}`);
        check(JSON.stringify(JSON.parse(readFileSync(r.file, 'utf8'))) === JSON.stringify(args), `${name} on ${r.b}: its own arguments file is untouched`);
      }
    }
  }],
  ['a flag the runner does not take stops it before any agent runs, on both backends', async () => {
    const t = taskArgs({ mode: 'scan', framework_dir: '' });
    const [c, k] = await Promise.all(['claude', 'cursor'].map((b) => runOn(b, INFERPILOT_JS, t, { flags: ['--agent-timeout', '60000'] })));
    for (const [b, r] of [['claude', c], ['cursor', k]]) {
      check(r.code === 2 && r.calls.length === 0, `${b}: exit ${r.code} after ${r.calls.length} agent calls`);
      check(/unknown --agent-timeout\n/.test(r.err) && /usage: node runtime\/run\.mjs/.test(r.err), `${b}: says which flag: ${r.err.slice(0, 200)}`);
    }
  }],
];

for (const [name, fn] of scenarios) {
  const before = failed;
  try {
    await fn();
  } catch (e) {
    failed++;
    console.error(`  FAIL: threw ${e && e.stack ? e.stack : e}`);
  }
  console.log(`${failed === before ? 'ok  ' : 'FAIL'} ${name}`);
}
console.log(`\n${passed} checks passed, ${failed} failed.`);
if (failed) console.error(`the stand-in CLIs and what each was given: ${TMP}`);
else rmSync(TMP, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
