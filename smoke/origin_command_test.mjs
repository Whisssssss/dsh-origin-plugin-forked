// smoke/origin_command_test.mjs
// ---------------------------------------------------------------------------
// 离线冒烟：验证 v2.8.0 的 /origin 斜杠指令（不依赖 Origin、不依赖 DSH 运行时）。
//
//   node smoke/origin_command_test.mjs
//
// 覆盖：命令注册元数据、handler 的 steer 消息组装、空参数行为、
//       缺少 commands 服务 / 缺少 agent.steer 时的降级、卸载 disposer。
// ---------------------------------------------------------------------------
import {
  buildOriginCommandPrompt,
  registerOriginCommand,
} from '../index.js'

let failures = 0
function check(name, ok, detail) {
  if (ok) {
    console.log('  PASS  ' + name)
  } else {
    failures += 1
    console.log('  FAIL  ' + name + (detail === undefined ? '' : '  → ' + detail))
  }
}

/** 最小假 ctx：只实现 registerOriginCommand / apply 用到的 effect + inject。 */
function makeCtx(services) {
  const state = { registered: [], effects: [] }
  const ctx = {
    effect(fn, label) {
      const disposer = fn()
      state.effects.push({ disposer, label })
      return disposer
    },
    inject(deps, callback) {
      if (deps.every((name) => services.includes(name))) callback(makeCtxView(services, state))
      return {}
    },
  }
  function makeCtxView(list, shared) {
    const view = {
      effect: ctx.effect,
      inject: ctx.inject,
    }
    if (list.includes('commands')) {
      view.commands = {
        register(definition) {
          shared.registered.push(definition)
          return () => { definition.__disposed = true }
        },
      }
    }
    return view
  }
  ctx.__state = state
  return ctx
}

console.log('/origin command smoke test')

// 1) 注册元数据
const ctx = makeCtx(['commands'])
registerOriginCommand(ctx)
const registered = ctx.__state.registered
check('registers exactly one command', registered.length === 1, 'got ' + registered.length)

const cmd = registered[0]
check('command name is origin', cmd && cmd.name === 'origin', cmd && cmd.name)
check('description is non-empty', Boolean(cmd && cmd.description))
check('input hint is provided', Boolean(cmd && cmd.input && cmd.input.hint))

// 2) 带参数：steer 一条 user 消息，内容包含用户需求与工作流
const steered = []
function invocation(rawInput, options) {
  const opts = options || {}
  return {
    commandId: 'cmd-1',
    agent: opts.noSteer ? {} : { steer: (message) => steered.push(message) },
    rawInput,
    attachments: [],
    signal: new AbortController().signal,
  }
}

const withRequest = cmd.handler(invocation('  画一张 UV-Vis 吸收谱，标注 280 nm 峰  '))
check('request returns success', withRequest.kind === 'success', withRequest.kind)
check('steers exactly one message', steered.length === 1, 'got ' + steered.length)

const message = steered[0] || {}
check('steered message is user role', message.role === 'user', message.role)
check('steered message has plugin source', Boolean(message.source && message.source.kind === 'plugin'), JSON.stringify(message.source))
check('steered message has a unique id', typeof message.id === 'string' && message.id.length > 0)
const text = message.content && message.content[0] && message.content[0].text
check('prompt carries the user request', typeof text === 'string' && text.includes('UV-Vis 吸收谱'))
check('prompt carries the workflow (origin_help)', typeof text === 'string' && text.includes('origin_help'))
check('prompt carries the fast path (origin_figure)', typeof text === 'string' && text.includes('origin_figure'))

// 3) 空参数：仍起一轮，但先反问
const empty = cmd.handler(invocation('   '))
check('empty input returns success', empty.kind === 'success', empty.kind)
const emptyText = steered[1] && steered[1].content[0].text
check('empty input asks the user first', typeof emptyText === 'string' && emptyText.includes('请先问清楚'))

// 4) 降级路径
const noSteer = cmd.handler(invocation('画个折线图', { noSteer: true }))
check('missing agent.steer returns error', noSteer.kind === 'error', noSteer.kind)

const silent = makeCtx([])
registerOriginCommand(silent)
check('no commands service does not throw or register', silent.__state.registered.length === 0)

// 5) 卸载
const disposer = ctx.__state.effects[0] && ctx.__state.effects[0].disposer
check('register returns a disposer via ctx.effect', typeof disposer === 'function')
if (typeof disposer === 'function') disposer()
check('disposer marks the command disposed', cmd.__disposed === true)

// 6) 提示词工具的边界
const prompt = buildOriginCommandPrompt('做一张 XRD 图')
check('buildOriginCommandPrompt embeds the request', prompt.includes('用户需求：做一张 XRD 图'))
check('buildOriginCommandPrompt warns about fake data', prompt.includes('不虚构'))

console.log(failures === 0 ? 'RESULT: PASS' : 'RESULT: FAIL (' + failures + ')')
process.exit(failures === 0 ? 0 : 1)
