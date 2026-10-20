// smoke/origin_command_test.mjs
// ---------------------------------------------------------------------------
// 离线冒烟：验证 v2.8.0 的 /origin 斜杠指令（不依赖 Origin、不依赖 DSH 运行时）。
//
//   node smoke/origin_command_test.mjs
//
// 覆盖：命令注册元数据（含 attachments 声明）、handler 的 steer 消息组装、
//       附件（文件/图片）透传、空参数行为、缺少 commands 服务 / 缺少
//       agent.steer 时的降级、卸载 disposer。
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

const steered = []
function invocation(rawInput, options) {
  const opts = options || {}
  return {
    commandId: 'cmd-1',
    agent: opts.noSteer ? {} : { steer: (message) => steered.push(message) },
    rawInput,
    attachments: opts.attachments || [],
    signal: new AbortController().signal,
  }
}

const FILE_BLOCK = {
  type: 'file',
  attachment: { attachmentId: 'sha256:abc123', name: 'uvvis.csv', bytes: 128 },
}
const IMAGE_BLOCK = {
  type: 'image',
  attachment: { attachmentId: 'sha256:def456', name: 'table.png', bytes: 256, mediaType: 'image/png', width: 10, height: 10 },
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
check('input advertises attachments', Boolean(cmd && cmd.input && cmd.input.attachments === true))

// 2) 带参数：steer 一条 user 消息，内容包含用户需求与工作流
const withRequest = await cmd.handler(invocation('  画一张 UV-Vis 吸收谱，标注 280 nm 峰  '))
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
check('no attachments means text-only content', Array.isArray(message.content) && message.content.length === 1)

// 3) 附件透传：文件块 + 图片块随消息发送，提示词点名文件
const withFiles = await cmd.handler(invocation('把这份数据画成折线图', { attachments: [FILE_BLOCK, IMAGE_BLOCK] }))
check('attachment request returns success', withFiles.kind === 'success', withFiles.kind)
check('result mentions the attached data file', typeof withFiles.text === 'string' && withFiles.text.includes('1 个数据文件'), withFiles.text)
const attachedMessage = steered[1] || {}
check('attachments are forwarded as content blocks', Array.isArray(attachedMessage.content) && attachedMessage.content.length === 3, 'got ' + (attachedMessage.content || []).length)
check('file block is preserved', attachedMessage.content && attachedMessage.content[1] === FILE_BLOCK)
check('image block is preserved', attachedMessage.content && attachedMessage.content[2] === IMAGE_BLOCK)
const attachedText = attachedMessage.content && attachedMessage.content[0] && attachedMessage.content[0].text
check('prompt names the attached file', typeof attachedText === 'string' && attachedText.includes('uvvis.csv'))
check('prompt tells the model to use origin_load_file', typeof attachedText === 'string' && attachedText.includes('origin_load_file'))
check('prompt warns about estimating from images', typeof attachedText === 'string' && attachedText.includes('不要凭图估算'))

// 4) 只有附件、没有文字需求：仍起一轮并提示说明要画什么
const filesOnly = await cmd.handler(invocation('   ', { attachments: [FILE_BLOCK] }))
check('files-only returns success', filesOnly.kind === 'success', filesOnly.kind)
check('files-only result asks what to plot', typeof filesOnly.text === 'string' && filesOnly.text.includes('请说明要画什么'), filesOnly.text)

// 5) 空参数：仍起一轮，但先反问
const empty = await cmd.handler(invocation('   '))
check('empty input returns success', empty.kind === 'success', empty.kind)
const emptyText = steered[3] && steered[3].content[0].text
check('empty input asks the user first', typeof emptyText === 'string' && emptyText.includes('请先问清楚'))

// 6) 降级路径
const noSteer = await cmd.handler(invocation('画个折线图', { noSteer: true }))
check('missing agent.steer returns error', noSteer.kind === 'error', noSteer.kind)

const silent = makeCtx([])
registerOriginCommand(silent)
check('no commands service does not throw or register', silent.__state.registered.length === 0)

// 7) 卸载
const disposer = ctx.__state.effects[0] && ctx.__state.effects[0].disposer
check('register returns a disposer via ctx.effect', typeof disposer === 'function')
if (typeof disposer === 'function') disposer()
check('disposer marks the command disposed', cmd.__disposed === true)

// 8) 提示词工具的边界
const prompt = buildOriginCommandPrompt('做一张 XRD 图')
check('buildOriginCommandPrompt embeds the request', prompt.includes('用户需求：做一张 XRD 图'))
check('buildOriginCommandPrompt warns about fake data', prompt.includes('不虚构'))
const promptWithFiles = buildOriginCommandPrompt('画图', { files: ['a.csv', 'b.xlsx'], images: 1 })
check('buildOriginCommandPrompt lists files', promptWithFiles.includes('a.csv、b.xlsx'))
check('buildOriginCommandPrompt mentions images', promptWithFiles.includes('1 张图片'))

console.log(failures === 0 ? 'RESULT: PASS' : 'RESULT: FAIL (' + failures + ')')
process.exit(failures === 0 ? 0 : 1)
