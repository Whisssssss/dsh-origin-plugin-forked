/**
 * /origin 集成冒烟：用 DSH 真实的 `@deepseek-ai/dsh-commands` 注册并执行一次
 * `/origin`，验证「命令 → handler → agent.steer → user 消息」整条链路。
 *
 * 与 smoke/origin_command_test.mjs 的分工：
 *   - origin_command_test.mjs       纯离线（假 ctx），CI 可跑；
 *   - origin_command_integration.mjs 需要本机装了 DSH（真实 cordis + 真实命令注册表）。
 *
 * 运行:
 *   node smoke/origin_command_integration.mjs
 * 可用 DSH_NODE_MODULES 指定 DSH 的 node_modules（内含 @deepseek-ai/cordis 与 dsh-commands）。
 */
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { pathToFileURL } from 'node:url'

const here = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const repoRoot = join(here, '..')

function findNodeModules() {
  if (process.env.DSH_NODE_MODULES) return process.env.DSH_NODE_MODULES
  const localAppData = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
  const appData = process.env.APPDATA || join(homedir(), 'AppData', 'Roaming')
  const candidates = [
    join(appData, 'npm', 'node_modules', '@deepseek-ai', 'dsh', 'node_modules'),
    join(localAppData, 'DSH Desktop', 'dsh-desktop', 'node_modules'),
    join(localAppData, 'DSH Desktop', 'resources', 'app', 'node_modules'),
  ]
  for (const c of candidates) if (existsSync(join(c, '@deepseek-ai', 'dsh-commands', 'lib', 'index.js'))) return c
  throw new Error('找不到 DSH 的 node_modules，请用 DSH_NODE_MODULES 指定（内含 @deepseek-ai/dsh-commands）')
}

const nm = findNodeModules()
const load = (rel) => import(pathToFileURL(join(nm, ...rel.split('/'))).href)

const { Context } = await load('@deepseek-ai/cordis/lib/index.js')
const Commands = (await load('@deepseek-ai/dsh-commands/lib/index.js')).default
const plugin = (await import(pathToFileURL(join(repoRoot, 'index.js')).href)).default

const ctx = new Context()
ctx.plugin(Commands)
ctx.plugin(plugin)
await new Promise((resolve) => setTimeout(resolve, 400))

let failures = 0
function check(name, ok, detail) {
  console.log((ok ? '  PASS  ' : '  FAIL  ') + name + (ok || detail === undefined ? '' : '  → ' + detail))
  if (!ok) failures += 1
}

console.log('/origin integration smoke (real dsh-commands)')
check('plugin exports apply()', Boolean(plugin && typeof plugin.apply === 'function'))

const descriptors = ctx.commands.list({})
check('origin command is registered globally', descriptors.some((d) => d.name === 'origin'), JSON.stringify(descriptors.map((d) => d.name)))
const definition = ctx.commands.find({}, 'origin')
check('command advertises attachment input', definition?.input?.attachments === true, JSON.stringify(definition?.input))

const steered = []
const lifecycle = []
const agent = {
  session: { append: (type, data) => { lifecycle.push({ type, data }); return lifecycle.length } },
  steer: (message) => steered.push(message),
}

const execution = await ctx.commands.execute(
  agent,
  '/origin 画一张 UV-Vis 吸收谱，标注 280 nm 峰',
  [],
  new AbortController().signal,
)

check('execute returns success', execution?.result?.kind === 'success', execution?.result?.kind)
check('command lifecycle logged (run/done)', lifecycle.map((e) => e.type).join(',') === 'command/run,command/done', lifecycle.map((e) => e.type).join(','))
check('steers exactly one message', steered.length === 1, 'got ' + steered.length)
check('steer target is user role', steered[0]?.role === 'user', steered[0]?.role)
check('steer source is plugin', steered[0]?.source?.kind === 'plugin', JSON.stringify(steered[0]?.source))
const text = steered[0]?.content?.[0]?.text
check('prompt carries the request', typeof text === 'string' && text.includes('UV-Vis 吸收谱'))
check('prompt carries the workflow', typeof text === 'string' && text.includes('origin_help'))

console.log(failures === 0 ? 'RESULT: PASS' : 'RESULT: FAIL (' + failures + ')')
process.exit(failures === 0 ? 0 : 1)
