// Self-test of runtime/: the schema checks, the engine's globals, the command-line backends (on fake
// CLIs written to a temporary directory) and runtime/run.mjs end to end. No network, no model.
//
//   node tests/runtime_selftest.mjs
import path from 'node:path';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { extractJson, validate } from '../runtime/schema.mjs';
import { createRuntime, fmtTokens, scriptBody } from '../runtime/engine.mjs';
import { BACKENDS, cliBackend } from '../runtime/backends.mjs';
import { PID_NS, orphansIn, runnerAlive, stopInContainer } from '../runtime/procs.mjs';

const INFERPILOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = mkdtempSync(path.join(tmpdir(), 'inferpilot-runtime-'));
const quiet = () => {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failed = 0;
let passed = 0;
function check(cond, msg) {
  if (cond) passed++;
  else { failed++; console.error(`  FAIL: ${msg}`); }
}
async function rejects(p, re, msg) {
  try {
    await p;
    check(false, `${msg}: did not throw`);
  } catch (e) {
    check(re.test(String(e && e.message)), `${msg}: ${e && e.message}`);
  }
}
function fakeCli(name, body) {
  const file = path.join(TMP, name);
  writeFileSync(file, `#!/bin/bash\n${body}\n`);
  chmodSync(file, 0o755);
  return file;
}
const preset = (bin, promptVia, extra = {}) => ({ bin, args: [], promptVia, modelFlag: '--model', env: {}, ...extra });
const OK_JSON = 'echo "working"; printf \'```json\\n{"ok": true}\\n```\\n\'';
const SCHEMA = { type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'] };

const scenarios = [
  ['schema: the last JSON object of a reply, fenced or bare', async () => {
    check(extractJson('text\n```json\n{"a": 1}\n```\nmore\n```json\n{"a": 2}\n```').a === 2, 'the last fenced block wins');
    check(extractJson('```json\nnot json\n```\n```json\n{"a": 3}\n```').a === 3, 'a fenced block that fails to parse is skipped');
    check(extractJson('done: {"a": {"b": [1, 2]}} bye').a.b[1] === 2, 'a bare object with nesting');
    let threw = false;
    try {
      extractJson('no object here');
    } catch {
      threw = true;
    }
    check(threw, 'no object throws');
  }],
  ['schema: type, enum, required, properties and items are checked', async () => {
    const s = {
      type: 'object', required: ['status', 'list'],
      properties: { status: { type: 'string', enum: ['ok', 'no'] }, n: { type: 'number' }, x: { type: ['string', 'null'] },
        list: { type: 'array', items: { type: 'object', required: ['id'], properties: { id: { type: 'string' } } } } },
    };
    check(validate({ status: 'ok', n: 3, x: null, list: [{ id: 'a' }], extra: 1 }, s).length === 0, 'a valid value, extra fields allowed, integer as number');
    const errs = validate({ status: 'maybe', n: '3', list: [{}] }, s);
    check(errs.some((e) => e.startsWith('status:')) && errs.some((e) => e.startsWith('n:')) && errs.some((e) => e.includes('[0].id: missing')),
      `errors: ${errs.join('; ')}`);
    check(validate('x', { type: 'object' }).length === 1 && validate({}, { type: 'object', required: ['a'] })[0] === 'a: missing', 'top level');
  }],
  ['engine: a script gets the Workflow globals; a bad structured answer is asked for again', async () => {
    const script = path.join(TMP, 'wf.js');
    writeFileSync(script, `export const meta = { name: 'wf', description: 'test', phases: [] };
phase('One');
log('hello');
const a = await agent('first', { label: 'a', schema: ${JSON.stringify(SCHEMA)} });
const b = await agent('plain', { label: 'b', model: 'm2' });
const par = await parallel([() => agent('x', { label: 'p1' }), () => { throw new Error('boom'); }]);
let nested = '';
try { await workflow({ scriptPath: '/x.js' }, {}); } catch (e) { nested = e.message; }
return { a, b, par, nested, n: args.n, budget: budget.remaining() };`);
    const logs = [];
    const calls = [];
    const backend = {
      name: 'fake',
      async runAgent({ prompt, label, model }) {
        calls.push({ label, model, prompt });
        if (label === 'a') return { text: calls.filter((c) => c.label === 'a').length === 1 ? 'no json' : '```json\n{"ok": true}\n```' };
        return { text: `text-${label}` };
      },
    };
    const rt = createRuntime({ backend, log: (m) => logs.push(m) });
    const r = await rt.runScript(script, { n: 7 });
    check(r.a.ok === true && calls.filter((c) => c.label === 'a').length === 2, 'the schema answer, after one retry');
    const [asked, again] = calls.filter((c) => c.label === 'a').map((c) => c.prompt);
    check(!asked.includes('An earlier session') && again.startsWith(asked) && again.includes('could not be used (the reply holds no JSON object)')
      && again.includes('It may have left work behind') && again.includes('never start a second server'),
      'the session asked again is told why, and that the one before may have left work behind');
    check(calls[0].prompt.includes('## Output contract') && calls[0].prompt.includes('"required"'), 'the schema reaches the prompt');
    check(r.b === 'text-b' && calls.find((c) => c.label === 'b').model === 'm2', 'a plain call returns text; the model reaches the backend');
    check(r.par[0] === 'text-p1' && r.par[1] === null, 'parallel: a failing thunk is null');
    check(/not supported/.test(r.nested) && r.n === 7 && r.budget === Infinity, 'workflow() refuses; args and budget');
    check(logs.includes('=== PHASE: One ===') && logs.includes('hello'), `phase and log lines: ${logs.slice(0, 3)}`);
    check(scriptBody('export const meta = {};\n  export async function f() {}\nconst s = "export x";').startsWith('const meta'), 'export is stripped');
  }],
  ['engine: retries run out; concurrency is bounded', async () => {
    const rt = createRuntime({ backend: { name: 'fake', runAgent: async () => ({ text: 'never json' }) }, schemaRetries: 1, log: quiet });
    await rejects(rt.agent('q', { schema: SCHEMA }), /no JSON object/, 'an answer that never parses throws');
    let now = 0;
    let most = 0;
    const slow = { name: 'slow', async runAgent() {
      most = Math.max(most, ++now);
      await new Promise((r) => setTimeout(r, 40));
      now--;
      return { text: 'x' };
    } };
    const rt2 = createRuntime({ backend: slow, concurrency: 2, log: quiet });
    await Promise.all(Array.from({ length: 6 }, (_, i) => rt2.agent('p', { label: `c${i}` })));
    check(most === 2, `at most 2 at once: ${most}`);
  }],
  ['backends: the prompt on stdin, or as the last argument; the model flag', async () => {
    const stdinCli = fakeCli('stdin.sh', `prompt=$(cat); echo "args=$*"; echo "len=\${#prompt}"; ${OK_JSON}`);
    const out = await cliBackend('fake', { preset: preset(stdinCli, 'stdin'), model: 'm1' }).runAgent({ prompt: 'hello world', label: 'x' });
    check(out.text.includes('len=11') && out.text.includes('args=--model m1'), `stdin: ${out.text}`);
    const argCli = fakeCli('arg.sh', 'for a; do last=$a; done; timeout 5 cat >/dev/null && echo stdin-closed; echo "last=$last"');
    const b = cliBackend('fake', { preset: preset(argCli, 'arg') });
    const short = await b.runAgent({ prompt: 'do it', label: 'y' });
    check(short.text.includes('stdin-closed') && short.text.includes('last=do it'), `arg: ${short.text}`);
    // 128 KiB: one more byte than Linux takes in a single argument.
    const fileCli = fakeCli('file.sh', 'for a; do last=$a; done; f=${last#*instructions are in }; f=${f%%. Read*}; echo "file=$f size=$(wc -c < "$f")"');
    const long = await cliBackend('fake', { preset: preset(fileCli, 'arg') }).runAgent({ prompt: 'z'.repeat(128 * 1024), label: 'big/role' });
    const file = (/file=(\S+)/.exec(long.text) || [])[1];
    check(file && long.text.includes(`size=${128 * 1024}`), `a long prompt goes in a file the CLI reads: ${long.text.slice(0, 160)}`);
    check(file && !existsSync(file.replace(/\/[^/]+$/, '')), `and its directory is gone once the CLI is done: ${file}`);
    const rt = createRuntime({ backend: cliBackend('fake', { preset: preset(stdinCli, 'stdin') }), log: quiet });
    check((await rt.agent('q', { schema: SCHEMA })).ok === true, 'a structured answer through a real process');
  }],
  ['backends: a failure, a missing CLI and a timeout that stops the process group', async () => {
    const fail = cliBackend('fake', { preset: preset(fakeCli('fail.sh', 'echo oops >&2; exit 3'), 'stdin') });
    await rejects(fail.runAgent({ prompt: 'p', label: 'f' }), /exited 3: oops/, 'non-zero exit');
    const missing = cliBackend('fake', { preset: preset(path.join(TMP, 'no-such-cli'), 'stdin') });
    await rejects(missing.runAgent({ prompt: 'p' }), /cannot run/, 'missing CLI');
    const sleeper = cliBackend('fake', { preset: preset(fakeCli('sleep.sh', 'sleep 37.25 & wait'), 'arg') });
    await rejects(sleeper.runAgent({ prompt: 'p', label: 's', timeoutMs: 300 }), /timed out/, 'timeout');
    await new Promise((r) => setTimeout(r, 300));
    const left = spawnSync('pgrep', ['-f', 'sleep 37.25']).status === 0;
    check(!left, 'the agent\'s own child process is stopped too');
    check(BACKENDS.cursor.args.includes('--force') && BACKENDS.cursor.promptVia === 'arg' && BACKENDS.claude.promptVia === 'stdin',
      'the presets');
  }],
  ['backends: stream-json shows each tool call as it starts; the answer comes from the result event', async () => {
    const answer = JSON.stringify({ type: 'result', subtype: 'success', is_error: false, result: '```json\n{"ok": true}\n```' });
    const claudeCli = fakeCli('claude-stream.sh', `cat >/dev/null
cat <<'EOF'
{"type":"system","subtype":"init"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"python3  scripts/scan_ci.py\\n --out ci.json"}}]}}
EOF
sleep 0.4
cat <<'EOF'
{"type":"user","message":{"content":[]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/ci.md"}},{"type":"text","text":"almost"}]}}
${answer}
EOF`);
    const seen = [];
    const t0 = Date.now();
    const out = await cliBackend('fake', { preset: preset(claudeCli, 'stdin', { events: BACKENDS.claude.events }) })
      .runAgent({ prompt: 'p', label: 'c', onProgress: (m) => seen.push([m, Date.now() - t0]) });
    const done = Date.now() - t0;
    check(seen.map((s) => s[0]).join('|') === 'Bash: python3 scripts/scan_ci.py --out ci.json|Read: /x/ci.md', `claude tool calls: ${seen}`);
    check(seen[0][1] < done - 250, `the first tool call is shown while the agent still runs (${seen[0][1]} ms of ${done} ms)`);
    check(extractJson(out.text).ok === true, `claude answer: ${out.text}`);

    const cursorCli = fakeCli('cursor-stream.sh', `cat <<'EOF'
{"type":"system","subtype":"init","model":"x"}
{"type":"thinking","subtype":"delta","text":"hmm"}
{"type":"tool_call","subtype":"started","tool_call":{"shellToolCall":{"args":{"command":"echo hi"},"description":"Run echo hi"}}}
{"type":"tool_call","subtype":"completed","tool_call":{"shellToolCall":{"args":{"command":"echo hi"},"result":{}}}}
{"type":"tool_call","subtype":"started","tool_call":{"readToolCall":{"args":{"path":"/etc/hostname"}}}}
{"type":"tool_call","subtype":"started","tool_call":{"editToolCall":{"args":{"path":"/tmp/a.txt","streamContent":"hi"}}}}
${JSON.stringify({ type: 'result', subtype: 'success', is_error: false, result: 'x'.repeat(300000) })}
EOF`);
    const lines = [];
    const big = await cliBackend('fake', { preset: preset(cursorCli, 'arg', { events: BACKENDS.cursor.events }) })
      .runAgent({ prompt: 'p', label: 'k', onProgress: (m) => lines.push(m) });
    check(lines.join('|') === 'shell: echo hi|read: /etc/hostname|edit: /tmp/a.txt', `cursor tool calls: ${lines}`);
    check(big.text.length === 300000, `a result line longer than a pipe read: ${big.text.length}`);

    const plain = await cliBackend('fake', { preset: preset(fakeCli('plain.sh', OK_JSON), 'arg', { events: BACKENDS.cursor.events }) })
      .runAgent({ prompt: 'p', label: 'plain' });
    check(extractJson(plain.text).ok === true && plain.text.includes('working'), 'no result event: the plain text is the answer');
    const erred = fakeCli('erred.sh', `echo '${JSON.stringify({ type: 'result', subtype: 'error', is_error: true, result: 'quota exceeded' })}'`);
    await rejects(cliBackend('fake', { preset: preset(erred, 'arg', { events: BACKENDS.cursor.events }) }).runAgent({ prompt: 'p', label: 'e' }),
      /ended in an error: quota exceeded/, 'a result that is an error');
  }],
  ['backends: the tokens of a result event, under either CLI\'s names', async () => {
    const claudeUsage = { type: 'result', is_error: false, result: 'a',
      usage: { input_tokens: 10, output_tokens: 200, cache_read_input_tokens: 3000, cache_creation_input_tokens: 400 } };
    const cursorUsage = { type: 'result', is_error: false, result: 'b',
      usage: { inputTokens: 6, outputTokens: 295, cacheReadTokens: 21041, cacheWriteTokens: 4746 } };
    const c = await cliBackend('fake', { preset: preset(fakeCli('cu.sh', `echo '${JSON.stringify(claudeUsage)}'`), 'arg',
      { events: BACKENDS.claude.events }) }).runAgent({ prompt: 'p' });
    check(JSON.stringify(c.usage) === JSON.stringify({ input: 10, output: 200, cache: 3400, total: 3610 }), `claude usage ${JSON.stringify(c.usage)}`);
    const k = await cliBackend('fake', { preset: preset(fakeCli('ku.sh', `echo '${JSON.stringify(cursorUsage)}'`), 'arg',
      { events: BACKENDS.cursor.events }) }).runAgent({ prompt: 'p' });
    check(k.usage.total === 26088 && k.usage.output === 295, `cursor usage ${JSON.stringify(k.usage)}`);
    const none = await cliBackend('fake', { preset: preset(fakeCli('nu.sh', OK_JSON), 'arg', { events: BACKENDS.cursor.events }) })
      .runAgent({ prompt: 'p' });
    check(none.usage === null, 'plain text: no tokens to report');
  }],
  ['engine: phases numbered along the route the script logs, tokens per call and for the run', async () => {
    const logs = [];
    let n = 0;
    const backend = { name: 'fake', async runAgent() {
      n++;
      return { text: '```json\n{"ok": true}\n```', usage: { input: 0, output: 0, cache: 0, total: n === 1 ? 410000 : 1250000 } };
    } };
    const rt = createRuntime({ backend, quietMs: 0, log: (m) => logs.push(m) });
    rt.log('[inferpilot] Route: Collect → Dispatch → Report');
    rt.phase('Collect');
    await rt.agent('q', { label: 'scout:collect', schema: SCHEMA });
    rt.phase('Dispatch');
    await rt.agent('q', { label: 'dispatcher:dispatch', schema: SCHEMA });
    rt.phase('Bisect');
    check(logs.includes('=== PHASE 1/3 [#--]: Collect ==='), `the first phase: ${logs}`);
    check(logs.includes('  <- [Collect] scout:collect  done in 0 min, 410k tokens (run total 410k)'), `tokens of a call: ${logs}`);
    check(logs.includes('=== PHASE 2/3 [##-]: Dispatch ===  so far: 1 agent call, 410k tokens, 0 min'), `so far: ${logs}`);
    check(logs.includes('  <- [Dispatch] dispatcher:dispatch  done in 0 min, 1.3M tokens (run total 1.7M)'), `the run total: ${logs}`);
    check(logs.some((l) => l.startsWith('=== PHASE (off the route): Bisect ===')), `a phase off the route: ${logs}`);
    check(rt.summary() === '2/3 phases of the route, 2 agent calls, 1.7M tokens, 0 min', `summary: ${rt.summary()}`);
    const plain = createRuntime({ backend: { name: 'fake', runAgent: async () => ({ text: 'x' }) }, quietMs: 0, log: quiet });
    plain.phase('One');
    await plain.agent('q', { label: 'a' });
    check(plain.summary() === '1 phase, 1 agent call, 0 min', `no route, no tokens: ${plain.summary()}`);
    const loose = [];
    const rt3 = createRuntime({ backend, quietMs: 0, log: (m) => loose.push(m) });
    rt3.log('Route: A → B');
    rt3.log('[inferpilot] intake: ready — the user wrote "Route: X → Y" in the request');
    rt3.phase('A');
    check(loose.includes('=== PHASE 1/2 [#-]: A ==='), `only a whole Route line sets the route: ${loose}`);
    check([[999, '999'], [1000, '1k'], [1049, '1k'], [26088, '26.1k'], [999949, '999.9k'], [999950, '1M'], [1700000, '1.7M']]
      .every(([n, s]) => fmtTokens(n) === s), `token counts: ${[999, 1000, 999949, 999950].map(fmtTokens)}`);
  }],
  ['engine: the tool calls, a still-working line after a quiet spell, and the end of each call', async () => {
    const logs = [];
    const backend = { name: 'fake', async runAgent({ onProgress }) {
      onProgress('shell: python3 scripts/scan_ci.py');
      await new Promise((r) => setTimeout(r, 200));
      return { text: '```json\n{"ok": true}\n```' };
    } };
    const rt = createRuntime({ backend, quietMs: 50, log: (m) => logs.push(m) });
    rt.phase('Collect');
    check((await rt.agent('q', { label: 'scout:collect', schema: SCHEMA })).ok === true, 'the answer');
    check(logs.includes('     · [Collect] scout:collect  shell: python3 scripts/scan_ci.py'), `a tool call: ${logs}`);
    check(logs.some((l) => /^ {5}· \[Collect\] scout:collect {2}still working, 0 min$/.test(l)), `still working: ${logs}`);
    check(logs[logs.length - 1] === '  <- [Collect] scout:collect  done in 0 min', `the end: ${logs[logs.length - 1]}`);
    const quietLogs = [];
    const rt2 = createRuntime({ backend, quietMs: 0, log: (m) => quietLogs.push(m) });
    await rt2.agent('q', { label: 'x' });
    check(!quietLogs.some((l) => l.includes('still working')), 'quietMs 0: no still-working line');
  }],
  ['run.mjs: a workflow end to end on a CLI backend, the result on stdout and in a file', async () => {
    const script = path.join(TMP, 'e2e.js');
    writeFileSync(script, `export const meta = { name: 'e2e', description: 'test', phases: [] };
phase('Only');
const r = await agent('go', { label: 'only', schema: ${JSON.stringify(SCHEMA)} });
return { status: r.ok ? 'done' : 'no', n: args.n };`);
    const resultFile = path.join(TMP, 'result.json');
    const out = execFileSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args', '{"n": 5}', '--result-file', resultFile],
    { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('cursor.sh', OK_JSON) }, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    const r = JSON.parse(out);
    check(r.status === 'done' && r.n === 5 && JSON.parse(readFileSync(resultFile, 'utf8')).n === 5, `result ${out}`);
    const bad = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'nope'], { encoding: 'utf8' });
    check(bad.status === 2 && bad.stderr.includes('usage:'), 'an unknown backend prints the usage');
  }],
  ['backends: a log dir keeps each run\'s prompt, whole stream and stderr; onText hears what the agent writes', async () => {
    const dir = path.join(TMP, 'agent-logs');
    const events = [
      { type: 'assistant', message: { content: [{ type: 'text', text: 'Looking at the tree.' }] } },
      { type: 'tool_call', subtype: 'started', tool_call: { shellToolCall: { args: { command: 'ls' } } } },
      { type: 'result', subtype: 'success', is_error: false, result: 'done' },
    ];
    const cli = fakeCli('logged.sh', `echo to-stderr >&2\ncat <<'EOF'\n${events.map((e) => JSON.stringify(e)).join('\n')}\nEOF`);
    const b = cliBackend('fake', { preset: preset(cli, 'arg', { events: BACKENDS.cursor.events }), logDir: dir });
    const texts = [];
    const r = await b.runAgent({ prompt: 'the prompt', label: 'role x', onText: (t) => texts.push(t) });
    check(r.text === 'done' && texts.length === 1 && texts[0] === 'Looking at the tree.', `texts ${JSON.stringify(texts)}`);
    const stream = path.join(dir, '001-role_x.jsonl');
    for (let i = 0; i < 50 && !(existsSync(stream) && readFileSync(stream, 'utf8').split('\n').filter(Boolean).length === 3); i++) await sleep(20);
    check(readFileSync(path.join(dir, '001-role_x.prompt.md'), 'utf8') === 'the prompt', 'the prompt is kept');
    check(readFileSync(stream, 'utf8').split('\n').filter(Boolean).length === 3, 'every event is kept');
    check(readFileSync(path.join(dir, '001-role_x.stderr.log'), 'utf8').includes('to-stderr'), 'stderr is kept');
    const broken = path.join(TMP, 'agent-logs-broken');
    const b2 = cliBackend('fake', { preset: preset(cli, 'arg', { events: BACKENDS.cursor.events }), logDir: broken });
    mkdirSync(path.join(broken, '001-x.jsonl')); // after the backend numbered its runs: the stream cannot open it
    const r2 = await b2.runAgent({ prompt: 'p', label: 'x' });
    await sleep(50);
    check(r2.text === 'done', 'a log that cannot be written leaves the agent run alone');
    const again = cliBackend('fake', { preset: preset(cli, 'arg', { events: BACKENDS.cursor.events }), logDir: dir });
    await again.runAgent({ prompt: 'second runner', label: 'role x' });
    check(readFileSync(path.join(dir, '001-role_x.prompt.md'), 'utf8') === 'the prompt'
      && readFileSync(path.join(dir, '002-role_x.prompt.md'), 'utf8') === 'second runner',
    'a runner started again on the same directory goes on numbering and keeps the earlier logs');
    const shared = path.join(TMP, 'agent-logs-shared');
    const [one, two] = [1, 2].map(() => cliBackend('fake', { preset: preset(cli, 'arg', { events: BACKENDS.cursor.events }), logDir: shared }));
    await one.runAgent({ prompt: 'one', label: 'x' });
    await two.runAgent({ prompt: 'two', label: 'x' });
    check(readFileSync(path.join(shared, '001-x.prompt.md'), 'utf8') === 'one' && readFileSync(path.join(shared, '002-x.prompt.md'), 'utf8') === 'two'
      && existsSync(path.join(shared, '002-x.jsonl')), 'two runners at once on one directory: the second skips the number the first took');
    const zombie = spawn('bash', ['-c', 'sleep 0.1 & echo $!; exec sleep 3'], { stdio: ['ignore', 'pipe', 'ignore'] });
    const zpid = await new Promise((r) => zombie.stdout.once('data', (d) => r(Number(String(d).trim()))));
    let zstat = '';
    for (let i = 0; i < 50 && !/^Z /.test(zstat); i++) {
      await sleep(20);
      zstat = readFileSync(`/proc/${zpid}/stat`, 'utf8').split(') ').pop();
    }
    check(/^Z /.test(zstat) && !runnerAlive(`${zpid}-${zstat.split(' ')[19]}-${PID_NS}`),
      'a runner that exited and is not reaped yet is gone');
    zombie.kill('SIGKILL');
    const said = BACKENDS.claude.events({ type: 'assistant', message: { content: [{ type: 'text', text: 'hi' },
      { type: 'tool_use', name: 'Bash', input: { command: 'ls' } }] } });
    check(JSON.stringify(said.steps) === JSON.stringify([{ text: 'hi' }, { tool: 'Bash: ls' }]), `claude, in the message's order: ${JSON.stringify(said)}`);
  }],
  ['backends: the texts and tool calls of an assistant message are heard in its order; an event of a shape the parser does not know is skipped', async () => {
    const cli = fakeCli('ordered.sh', `cat >/dev/null
cat <<'EOF'
{"type":"assistant","message":{"content":"a string, not a list"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Let me run it."},{"type":"tool_use","name":"Bash","input":{"command":"python3 bench.py"}}]}}
{"type":"result","subtype":"success","is_error":false,"result":"done"}
EOF`);
    const heard = {};
    for (const which of ['claude', 'cursor']) {
      heard[which] = [];
      const r = await cliBackend('fake', { preset: preset(cli, 'stdin', { events: BACKENDS[which].events }) }).runAgent({ prompt: 'p',
        label: 'x', onProgress: (t) => heard[which].push(`tool ${t}`), onText: (t) => heard[which].push(`text ${t}`) });
      check(r.text === 'done', `${which}: the run goes on past the event it cannot read: ${r.text}`);
    }
    check(JSON.stringify(heard.claude) === JSON.stringify(['text Let me run it.', 'tool Bash: python3 bench.py']), `claude: ${JSON.stringify(heard.claude)}`);
    check(JSON.stringify(heard.cursor) === JSON.stringify(['text Let me run it.']), `cursor, whose tool calls come as tool_call events: ${JSON.stringify(heard.cursor)}`);
  }],
  ['backends: a log that falls too far behind its agent stops; the agent run goes on', async () => {
    // One libuv thread, which a pbkdf2 holds: no write of the log gets through, as on a file system that stalls.
    const cli = fakeCli('flood.sh', `yes '{"type":"user"}' | head -c 4000000\necho\n`
      + `echo '{"type":"result","subtype":"success","is_error":false,"result":"done"}'`);
    const probe = path.join(TMP, 'flood.mjs');
    writeFileSync(probe, `import { pbkdf2 } from 'node:crypto';
import { BACKENDS, cliBackend } from ${JSON.stringify(pathToFileURL(path.join(INFERPILOT, 'runtime', 'backends.mjs')).href)};
pbkdf2('x', 'y', 1e9, 64, 'sha512', () => {});
const b = cliBackend('fake', { preset: { bin: ${JSON.stringify(cli)}, args: [], promptVia: 'arg', modelFlag: '--model', env: {},
  events: BACKENDS.cursor.events }, logDir: ${JSON.stringify(path.join(TMP, 'agent-logs-flood'))}, logBacklog: 1048576 });
process.stdout.write((await b.runAgent({ prompt: 'p', label: 'flood' })).text);
process.kill(process.pid, 'SIGKILL'); // an exit would wait for the thread the pbkdf2 holds
`);
    const r = spawnSync(process.execPath, [probe], { env: { ...process.env, UV_THREADPOOL_SIZE: '1', UV_USE_IO_URING: '0' },
      encoding: 'utf8', timeout: 60000 });
    check(r.stdout === 'done', `the agent run goes on: ${r.stdout} ${r.stderr}`);
    check(r.stderr.includes('001-flood.jsonl stops here (1 MB not written yet)'), `the log stops: ${r.stderr}`);
  }],
  ['engine: showText adds what the agents write to the log; live() lists the calls under way, each with what it last did', async () => {
    // An agent that writes, starts a tool and waits on it: the log and live() during that, with and without showText.
    const watch = async (opts) => {
      const lines = [];
      const seen = [];
      let rt = null;
      const backend = {
        name: 'fake',
        async runAgent({ onProgress, onText }) {
          onText('  thinking\n about it  ');
          seen.push(rt.live().map((c) => ({ ...c })));
          onProgress('Bash: python3 bench.py');
          seen.push(rt.live().map((c) => ({ ...c })));
          await sleep(4 * opts.quietMs);
          seen.push(rt.live().map((c) => ({ ...c })));
          return { text: 'ok', usage: null };
        },
      };
      rt = createRuntime({ backend, log: (m) => lines.push(m), ...opts });
      rt.phase('P');
      await rt.agent('q', { label: 'speaker' });
      return { lines, seen, rt };
    };
    const loud = await watch({ showText: true, quietMs: 0 });
    check(loud.lines.some((l) => l.endsWith('[P] speaker  > thinking about it')), `lines ${JSON.stringify(loud.lines)}`);
    const [first] = loud.seen;
    check(first.length === 1 && first[0].label === 'speaker' && first[0].phase === 'P' && first[0].last === '> thinking about it',
      `live ${JSON.stringify(first)}`);
    check(loud.rt.live().length === 0, 'no call once it is done');
    const mute = await watch({ quietMs: 0 });
    check(!mute.lines.some((l) => l.includes('> thinking')), 'no text in the log without showText');
    check(mute.seen[0][0].last === '> thinking about it' && mute.seen[0][0].last_at >= mute.seen[0][0].started,
      `but live() has it, and when: ${JSON.stringify(mute.seen[0])}`);
    const slow = await watch({ quietMs: 50 });
    check(slow.lines.some((l) => l.endsWith('[P] speaker  still working, 0 min')), `still working: ${JSON.stringify(slow.lines)}`);
    check(slow.seen[2][0].last === 'Bash: python3 bench.py' && slow.seen[2][0].last_at === slow.seen[1][0].last_at,
      `the still-working lines leave the tool call, and its time, in live(): ${JSON.stringify(slow.seen.slice(1))}`);
  }],
  ['run.mjs: with --result-file, a status file and the agent streams next to it', async () => {
    const script = path.join(TMP, 'status.js');
    // Its result takes 50 ms to write out (file times are coarser than the gap between two writes): a status
    // written before it would be the older file.
    writeFileSync(script, `phase('Only');\nawait agent('go', { label: 'only' });\n`
      + `return { status: 'done', slow: { toJSON() { const t = Date.now() + 50; while (Date.now() < t); return 'slow'; } } };`);
    execFileSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor', '--args', '{}',
      '--result-file', path.join(TMP, 'st.result.json')], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('st.sh', 'echo done') },
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    const st = JSON.parse(readFileSync(path.join(TMP, 'st.status.json'), 'utf8'));
    check(st.state === 'ended' && st.agents.length === 0 && /^\d+-\d+-\d*$/.test(st.run_tag) && st.updated >= st.started, `status ${JSON.stringify(st)}`);
    check(!existsSync(path.join(TMP, 'st.status.json.tmp')), 'the status file is renamed into place, nothing left beside it');
    check(statSync(path.join(TMP, 'st.status.json')).mtimeMs >= statSync(path.join(TMP, 'st.result.json')).mtimeMs,
      'the status says ended once the result is written');
    check(existsSync(path.join(TMP, 'st.agents', '001-only.jsonl')), 'the agent stream is kept');
    const notDir = path.join(TMP, 'st-not-a-dir');
    writeFileSync(notDir, '');
    const r2 = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor', '--args', '{}',
      '--agent-log-dir', path.join(notDir, 'logs')], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('st.sh', 'echo done') }, encoding: 'utf8' });
    check(r2.status === 0 && r2.stderr.includes('no agent logs in') && !r2.stderr.includes('agent streams:'),
      `a log dir that cannot be made is not named as the streams' place: ${r2.stderr.slice(-400)}`);
  }],
  ['run.mjs: the runs the log\'s NEXT names keep --show-text and get agent logs of their own', async () => {
    const dir = path.join(TMP, 'next-runs');
    mkdirSync(dir, { recursive: true });
    const script = path.join(dir, 'next.js');
    writeFileSync(script, `phase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done', next: { say: 'two to start', `
      + `start: [{ rank: 1, task: 'a', args: {} }, { rank: 2, task: 'b', args: {} }] } };`);
    writeFileSync(path.join(dir, 'scan.json'), '{}');
    const r = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args-file', path.join(dir, 'scan.json'), '--result-file', path.join(dir, 'scan.result.json'), '--agent-log-dir', path.join(dir, 'logs'),
      '--show-text'], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('next.sh', 'echo done') }, encoding: 'utf8' });
    const starts = r.stderr.split('\n').filter((l) => l.includes('nohup') && l.includes('.start-'));
    check(r.status === 0 && starts.length === 2 && starts.every((l) => l.includes(' --show-text') && !l.includes('--agent-log-dir')),
      `the start commands: ${JSON.stringify(starts)}`);
  }],
  ['run.mjs: the control file next to --args-file is read when the script asks; one there before the run counts once it changes; the pid stays on the log\'s first line', async () => {
    const script = path.join(TMP, 'control.js');
    writeFileSync(script, `const before = control();\nawait agent('go', { label: 'writes' });\nreturn { before, after: control() };`);
    const argsFile = path.join(TMP, 'ctl.json');
    writeFileSync(argsFile, '{}');
    const controlFile = path.join(TMP, 'ctl.control.json');
    writeFileSync(controlFile, '{"stop_after": "now"}');
    const run = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args-file', argsFile], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('ctl.sh',
      `echo '{"stop_after": "Verify"}' > ${controlFile}; echo done`) }, encoding: 'utf8' });
    const r = JSON.parse(run.stdout);
    check(JSON.stringify(r.before) === '{}', `an earlier run's file is ignored: ${JSON.stringify(r.before)}`);
    check(r.after.stop_after === 'Verify', `one written during the run is read: ${JSON.stringify(r.after)}`);
    check(new RegExp(`^\\[[\\d:]+\\] runner pid ${run.pid}: kill ${run.pid} stops`).test(run.stderr)
      && run.stderr.includes(`control file: ${controlFile} (read at every phase boundary)`),
    `the log starts with the runner's pid, the control file after it: ${run.stderr.slice(0, 160)}`);
  }],
  ['run.mjs: a control file that holds no JSON object changes nothing; the log names it once for each state', async () => {
    const script = path.join(TMP, 'badctl.js');
    writeFileSync(script, `await agent('go', { label: 'one' });\nconst a = control();\nconst b = control();\n`
      + `await agent('go', { label: 'two' });\nreturn { a, b, c: control() };`);
    const argsFile = path.join(TMP, 'badctl.json');
    writeFileSync(argsFile, '{}');
    const controlFile = path.join(TMP, 'badctl.control.json');
    const first = path.join(TMP, 'badctl.first');
    const run = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args-file', argsFile], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('badctl.sh',
      `if [ -e ${first} ]; then echo '["now"]' > ${controlFile}; else touch ${first}; echo '{stop_after: "now"}' > ${controlFile}; fi; echo done`) },
    encoding: 'utf8' });
    const r = JSON.parse(run.stdout);
    check(JSON.stringify(r) === '{"a":{},"b":{},"c":{}}', `nothing read from it: ${JSON.stringify(r)}`);
    const named = run.stderr.split('\n').filter((l) => l.includes(`!! control file ${controlFile}: `));
    check(named.length === 2 && named[1].endsWith('not a JSON object; the stop stays as it was'),
      `named once for each state: ${named.join(' | ')}`);
  }],
  ['run.mjs: SIGTERM stops the agents and what they started with setsid, frees the lock, writes an interrupted result', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const script = path.join(TMP, 'stop.js');
    writeFileSync(script, `phase('Long');\nawait agent('go', { label: 'long' });\nreturn { status: 'done' };`);
    const lock = path.join(TMP, 'stop.lock');
    const runDir = path.join(TMP, 'stop-run');
    const cli = fakeCli('long.sh', `python3 ${path.join(INFERPILOT, 'scripts', 'run_lock.py')} acquire --run-dir ${runDir} >/dev/null
setsid sleep 41.25 > /dev/null 2>&1 < /dev/null &
sleep 42.25 & wait`);
    const resultFile = path.join(TMP, 'stop.result.json');
    const runner = spawn(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args', '{}', '--result-file', resultFile], { env: { ...process.env, INFERPILOT_CURSOR_BIN: cli, INFERPILOT_LOCK_FILE: lock },
      stdio: ['ignore', 'ignore', 'pipe'] });
    let log = '';
    runner.stderr.on('data', (d) => { log += d; });
    const running = (what) => spawnSync('pgrep', ['-f', what]).status === 0;
    for (let i = 0; i < 100 && !(running('sleep 41.25') && running('sleep 42.25') && existsSync(lock)); i++) await sleep(100);
    check(running('sleep 41.25') && running('sleep 42.25'), 'the agent and its setsid child started');
    runner.kill('SIGTERM');
    const code = await new Promise((r) => runner.on('exit', (c) => r(c)));
    check(code === 143, `exit ${code}`);
    check(!running('sleep 41.25'), 'the setsid child of the agent was stopped');
    check(!running('sleep 42.25'), 'the agent was stopped');
    const result = JSON.parse(readFileSync(resultFile, 'utf8'));
    check(result.status === 'interrupted' && result.signal === 'SIGTERM' && result.stopped_at === 'Long', `result ${JSON.stringify(result)}`);
    check(JSON.parse(readFileSync(path.join(TMP, 'stop.status.json'), 'utf8')).state === 'interrupted', 'the status file says interrupted');
    const st = JSON.parse(execFileSync('python3', [path.join(INFERPILOT, 'scripts', 'run_lock.py'), '--lock', lock, 'status'], { encoding: 'utf8' }));
    check(st.status === 'free', `the lock is ${st.status}`);
    check(/=== END \(interrupted by SIGTERM in Long\)/.test(log), 'the log ends with the interruption');
    check(new RegExp(`^\\[[\\d:]+\\] runner pid ${runner.pid}: kill ${runner.pid} stops`).test(log), `the log starts with the runner's pid: ${log.slice(0, 120)}`);
  }],
  ['run.mjs: a second SIGTERM kills at once an agent that ignores the first; the lock is freed and the result written all the same', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const script = path.join(TMP, 'deaf.js');
    writeFileSync(script, `phase('Long');\nawait agent('go', { label: 'deaf' });\nreturn { status: 'done' };`);
    const lock = path.join(TMP, 'deaf.lock');
    const cli = fakeCli('deaf.sh', `python3 ${path.join(INFERPILOT, 'scripts', 'run_lock.py')} acquire --run-dir ${path.join(TMP, 'deaf-run')} >/dev/null
trap '' TERM
sleep 46.25 & wait`);
    const resultFile = path.join(TMP, 'deaf.result.json');
    const runner = spawn(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args', '{}', '--result-file', resultFile], { env: { ...process.env, INFERPILOT_CURSOR_BIN: cli, INFERPILOT_LOCK_FILE: lock },
      stdio: ['ignore', 'ignore', 'pipe'] });
    let log = '';
    runner.stderr.on('data', (d) => { log += d; });
    const running = (what) => spawnSync('pgrep', ['-f', what]).status === 0;
    for (let i = 0; i < 100 && !(running('sleep 46.25') && existsSync(lock)); i++) await sleep(100);
    check(running('sleep 46.25'), 'the agent started');
    const t0 = Date.now();
    runner.kill('SIGTERM');
    for (let i = 0; i < 100 && !/=== SIGTERM: stopping/.test(log); i++) await sleep(20);
    await sleep(300);
    runner.kill('SIGTERM');
    const code = await new Promise((r) => runner.on('exit', (c) => r(c)));
    const took = Date.now() - t0;
    check(code === 143 && took < 8000, `exit ${code} after ${took} ms, not the first signal's 10 s of grace`);
    check(!running('sleep 46.25'), 'the agent that ignored SIGTERM was killed');
    check(existsSync(resultFile) && JSON.parse(readFileSync(resultFile, 'utf8')).status === 'interrupted', 'the interrupted result was written');
    const st = JSON.parse(execFileSync('python3', [path.join(INFERPILOT, 'scripts', 'run_lock.py'), '--lock', lock, 'status'], { encoding: 'utf8' }));
    check(st.status === 'free', `the lock is ${st.status}`);
    check(/=== SIGTERM again: killing them at once/.test(log), 'the log says the second signal killed them');
  }],
  ['procs: a task with a container has its tagged processes stopped there, by the container\'s root', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const bin = path.join(TMP, 'fakedocker');
    mkdirSync(bin, { recursive: true });
    const calls = path.join(TMP, 'docker_calls.txt');
    // docker exec <container> <command...>: here the command runs on this machine, as the container's root would.
    writeFileSync(path.join(bin, 'docker'), `#!/bin/bash\necho "$*" >> ${calls}\n[ "$1" = exec ] || exit 1\nshift 2\nexec "$@"\n`);
    chmodSync(path.join(bin, 'docker'), 0o755);
    const old = process.env.PATH;
    process.env.PATH = `${bin}:${old}`;
    try {
      const victim = spawn('sleep', ['48.25'], { env: { ...process.env, INFERPILOT_RUN_TAG: '424242-1-1' }, detached: true, stdio: 'ignore' });
      victim.unref();
      await sleep(200);
      // a worker that, told to stop, starts another tagged process on its way out
      const forker = spawn('bash', ['-c', 'trap "sleep 49.25 & exit 0" TERM; while :; do sleep 0.1; done'],
        { env: { ...process.env, INFERPILOT_RUN_TAG: '424242-1-1' }, detached: true, stdio: 'ignore' });
      forker.unref();
      await sleep(300);
      const got = await stopInContainer('box', '424242-1-1', 3);
      check(got && got.map(Number).includes(victim.pid) && got.map(Number).includes(forker.pid), `stopped ${JSON.stringify(got)}`);
      await sleep(200);
      check(spawnSync('pgrep', ['-f', 'sleep 48.25']).status !== 0, 'the tagged process in the container is gone');
      check(spawnSync('pgrep', ['-f', 'sleep 49.25']).status !== 0, 'and the one its worker started while stopping');
      const gone = spawn('sleep', ['50.25'], { env: { ...process.env, INFERPILOT_RUN_TAG: `999999999-1-${PID_NS}` }, detached: true, stdio: 'ignore' });
      gone.unref();
      const foreign = spawn('sleep', ['51.25'], { env: { ...process.env, INFERPILOT_RUN_TAG: '999999999-1-1' }, detached: true, stdio: 'ignore' });
      foreign.unref();
      await sleep(200);
      const left = await orphansIn('box');
      process.kill(gone.pid, 'SIGKILL');
      process.kill(foreign.pid, 'SIGKILL');
      check(left && left.some((p) => p.pid === gone.pid && p.tag === `999999999-1-${PID_NS}` && p.cmd === 'sleep 50.25')
        && !left.some((p) => p.pid === foreign.pid), `the container's root lists what a runner that is gone left there, and only that: ${JSON.stringify(left)}`);
      const script = path.join(TMP, 'boxed.js');
      writeFileSync(script, `phase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done' };`);
      const r = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
        '--args', '{"container": "box"}'], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('boxed.sh', 'echo done') }, encoding: 'utf8' });
      check(r.status === 0 && /^exec box sh -c [\s\S]* sh \d+-\d+-\d* 10$/m.test(readFileSync(calls, 'utf8')),
        `the runner looked in the task's container at its end: ${readFileSync(calls, 'utf8').slice(-200)}`);
      const named = path.join(TMP, 'named.js');
      writeFileSync(named, `log('[inferpilot] container=box2');\nphase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done' };`);
      const r2 = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), named, '--backend', 'cursor',
        '--args', '{}'], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('named.sh', 'echo done') }, encoding: 'utf8' });
      check(r2.status === 0 && /^exec box2 /m.test(readFileSync(calls, 'utf8')), 'a container the script resolved is cleaned up too');
    } finally {
      process.env.PATH = old;
    }
  }],
  ['run.mjs: what the agents left running is stopped at the end; orphans are listed, and stopped with --kill-orphans', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const script = path.join(TMP, 'tidy.js');
    writeFileSync(script, `phase('Only');\nreturn { status: (await agent('go', { label: 'only', schema: ${JSON.stringify(SCHEMA)} })).ok };`);
    const run = (...extra) => spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args', '{}', ...extra], { env: { ...process.env, INFERPILOT_CURSOR_BIN: fakeCli('tidy.sh',
      `setsid sleep 43.25 > /dev/null 2>&1 < /dev/null &\n${OK_JSON}`), INFERPILOT_LOCK_FILE: path.join(TMP, 'tidy.lock') }, encoding: 'utf8' });
    const running = (what) => spawnSync('pgrep', ['-f', what]).status === 0;
    const r = run();
    check(r.status === 0 && /stopped 1 process\(es\) the run's agents left running/.test(r.stderr), `end ${r.stderr.slice(-300)}`);
    check(!running('sleep 43.25'), 'the leftover was stopped');
    const orphan = spawn('sleep', ['44.25'], { env: { ...process.env, INFERPILOT_RUN_TAG: '999999999-1' }, detached: true, stdio: 'ignore' });
    orphan.unref();
    await sleep(200);
    const warned = run();
    check(/1 process\(es\) left by runs that are gone/.test(warned.stderr) && running('sleep 44.25'), 'an orphan is listed and left alone');
    const foreign = spawn('sleep', ['45.25'], { env: { ...process.env, INFERPILOT_RUN_TAG: '999999999-1-1' }, detached: true, stdio: 'ignore' });
    foreign.unref();
    await sleep(200);
    const killed = run('--kill-orphans');
    check(/stopped 1 process\(es\) left by runs that are gone/.test(killed.stderr) && !running('sleep 44.25'), 'and stopped on request');
    check(running('sleep 45.25'), 'a process of a runner in another pid namespace is not judged an orphan');
    process.kill(foreign.pid, 'SIGKILL');
  }],
  ['run.mjs: a signal after the run\'s end hurries what it stops; the result is still the run\'s', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const script = path.join(TMP, 'late.js');
    writeFileSync(script, `phase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done' };`);
    const resultFile = path.join(TMP, 'late.result.json');
    // The agent leaves a server that ignores SIGTERM: the run's end would wait out its 10 s of grace.
    const runner = spawn(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
      '--args', '{}', '--result-file', resultFile], { env: { ...process.env, INFERPILOT_LOCK_FILE: path.join(TMP, 'late.lock'),
      INFERPILOT_CURSOR_BIN: fakeCli('late.sh', `setsid bash -c 'trap "" TERM; exec sleep 55.25' > /dev/null 2>&1 < /dev/null &\necho done`) },
      stdio: ['ignore', 'ignore', 'pipe'] });
    let log = '';
    runner.stderr.on('data', (d) => { log += d; });
    const exited = new Promise((r) => runner.on('exit', (c) => r(c)));
    for (let i = 0; i < 200 && !/<- \[Only\] only\s+done/.test(log); i++) await sleep(25);
    await sleep(300);
    const sent = Date.now();
    runner.kill('SIGTERM');
    const code = await exited;
    check(code === 0 && Date.now() - sent < 3000, `exit ${code} ${Date.now() - sent} ms after the signal`);
    check(JSON.parse(readFileSync(resultFile, 'utf8')).status === 'done', 'the result is the run\'s, not interrupted');
    check(spawnSync('pgrep', ['-f', 'sleep 55.25']).status !== 0, 'what the agent left, deaf to SIGTERM, was killed');
    check(/=== SIGTERM after the run's end: killing what its agents left running at once/.test(log), 'the log says so');
  }],
  ['run.mjs: a second signal cuts short the wait in the task\'s container and kills there at once; a third exits at once', async () => {
    if (!existsSync('/proc/self/environ')) return;
    const bin = path.join(TMP, 'slowdocker');
    mkdirSync(bin, { recursive: true });
    const calls = path.join(TMP, 'slow_docker_calls.txt');
    // A container whose server ignores SIGTERM: a sweep waits out its grace (longer here); with SLOW_KILL, so does one that kills.
    writeFileSync(path.join(bin, 'docker'), `#!/bin/bash
[ "\${@: -1}" = sh ] && exit 0
echo "grace \${@: -1}" >> ${calls}
[ "\${@: -1}" = 0 ] && [ -z "$SLOW_KILL" ] && exit 0
exec sleep 53.25
`);
    chmodSync(path.join(bin, 'docker'), 0o755);
    const script = path.join(TMP, 'slow.js');
    writeFileSync(script, `phase('Long');\nawait agent('go', { label: 'long' });\nreturn { status: 'done' };`);
    const lock = path.join(TMP, 'slow.lock');
    const cli = fakeCli('slow.sh', `python3 ${path.join(INFERPILOT, 'scripts', 'run_lock.py')} acquire --run-dir ${path.join(TMP, 'slow-run')} >/dev/null
sleep 54.25 & wait`);
    const resultFile = path.join(TMP, 'slow.result.json');
    const has = (what) => existsSync(calls) && readFileSync(calls, 'utf8').includes(what);
    const until = async (ok) => {
      for (let i = 0; i < 200 && !ok(); i++) await sleep(25);
      return ok();
    };
    // Two signals: the first reaches the sweep of the container, the second comes while it waits.
    const twice = async (env = {}) => {
      writeFileSync(calls, '');
      const runner = spawn(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), script, '--backend', 'cursor',
        '--args', '{"container": "box"}', '--result-file', resultFile], { env: { ...process.env, PATH: `${bin}:${process.env.PATH}`,
        INFERPILOT_CURSOR_BIN: cli, INFERPILOT_LOCK_FILE: lock, ...env }, stdio: 'ignore' });
      const exited = new Promise((r) => runner.on('exit', (c) => r(c)));
      await until(() => spawnSync('pgrep', ['-f', 'sleep 54.25']).status === 0);
      runner.kill('SIGTERM');
      check(await until(() => has('grace 10')), 'the first signal swept the container, with a grace');
      runner.kill('SIGTERM');
      return { runner, exited, sent: Date.now() };
    };
    try {
      const fast = await twice();
      const code = await fast.exited;
      check(code === 143 && Date.now() - fast.sent < 3000, `exit ${code} ${Date.now() - fast.sent} ms after the second signal, not after the grace`);
      check(has('grace 0'), 'the second signal swept it again, killing at once');
      check(JSON.parse(readFileSync(resultFile, 'utf8')).status === 'interrupted', 'the interrupted result was written');
      const st = JSON.parse(execFileSync('python3', [path.join(INFERPILOT, 'scripts', 'run_lock.py'), '--lock', lock, 'status'], { encoding: 'utf8' }));
      check(st.status === 'free', `the lock is ${st.status}`);
      const slow = await twice({ SLOW_KILL: '1' });
      check(await until(() => has('grace 0')), 'the sweep that kills is under way');
      const third = Date.now();
      slow.runner.kill('SIGTERM');
      const code3 = await slow.exited;
      check(code3 === 143 && Date.now() - third < 1500, `a third signal exits at once: exit ${code3} after ${Date.now() - third} ms`);
    } finally {
      spawnSync('pkill', ['-f', 'sleep 53.25']);
    }
  }],
  ['run.mjs: what runs that are gone left in the task\'s container is listed before an agent starts, and stopped with --kill-orphans', async () => {
    const bin = path.join(TMP, 'boxdocker');
    mkdirSync(bin, { recursive: true });
    const calls = path.join(TMP, 'box_docker_calls.txt');
    const gone = `999999999-1-${PID_NS}`;
    // The container's root sees a server that a host runner now gone left there, and one of a runner in another
    // pid namespace; a sweep notes the tag it stops.
    writeFileSync(path.join(bin, 'docker'), `#!/bin/bash
if [ "\${@: -1}" = sh ]; then
  echo "look $2" >> ${calls}
  printf '4242 ${gone} python3 -m sglang.launch_server --port 30000\\n4343 999999999-1-1 sleep 60\\n'
  exit 0
fi
echo "stop $2 \${@: -2:1} \${@: -1}" >> ${calls}
`);
    chmodSync(path.join(bin, 'docker'), 0o755);
    const named = path.join(TMP, 'box_named.js');
    writeFileSync(named, `log('[inferpilot] container=box4');\nphase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done' };`);
    const given = path.join(TMP, 'box_given.js');
    writeFileSync(given, `phase('Only');\nawait agent('go', { label: 'only' });\nreturn { status: 'done' };`);
    const run = (file, a, ...extra) => {
      writeFileSync(calls, '');
      const r = spawnSync(process.execPath, [path.join(INFERPILOT, 'runtime', 'run.mjs'), file, '--backend', 'cursor', '--args', a, ...extra],
        { env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, INFERPILOT_CURSOR_BIN: fakeCli('box_agent.sh', `echo agent >> ${calls}; echo done`) },
          encoding: 'utf8' });
      return { ...r, calls: readFileSync(calls, 'utf8') };
    };
    const listed = run(named, '{}');
    check(listed.status === 0 && /!! 1 process\(es\) left in container box4 by runs that are gone[^\n]*\n\s+4242 python3 -m sglang\.launch_server/
      .test(listed.stderr), `listed: ${listed.stderr.slice(-400)}`);
    check(listed.calls.includes('look box4') && listed.calls.indexOf('look box4') < listed.calls.indexOf('agent'),
      `the container the script named was looked in before the agent started: ${JSON.stringify(listed.calls)}`);
    check(!listed.calls.includes(`stop box4 ${gone}`), 'and nothing was stopped without --kill-orphans');
    const killed = run(given, '{"container": "box5"}', '--kill-orphans');
    check(/stopped 1 process\(es\) left in container box5 by runs that are gone/.test(killed.stderr)
      && killed.calls.includes(`stop box5 ${gone} 10`), `stopped with --kill-orphans: ${JSON.stringify(killed.calls)}`);
    check(killed.calls.indexOf(`stop box5 ${gone}`) < killed.calls.indexOf('agent'), 'before the agent started');
    check(!killed.calls.includes('999999999-1-1'), 'a runner in another pid namespace is not judged');
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
process.exit(failed ? 1 : 0);
