// dsh-origin-plugin — bundle entry artifact
// ===========================================
// This is a *Python* MCP plugin: the actual server is origin_mcp_server.py,
// launched by DSH through the mcp-origin loader entry (cordis.patch.yml →
// @deepseek-ai/dsh-mcp-client, stdio).
//
// Why an index.js at all?  The DSH plugin market refuses packages that look
// "source-only" — a carrier bundle (patch mounts a client, ships no main/
// exports/index.js of its own) is indistinguishable from a checkout that still
// needs a build step, so it gets rejected with "nothing installable / 没有可
// 安装的内容". Providing a real entry artifact here makes the market recognize
// a prebuilt bundle (entryArtifactExists) and lets install complete WITHOUT
// needing build approval (allowBuilds).
//
// This module carries a no-op `apply` so the cordis loader creates a fiber
// for dsh-origin-plugin itself. Without it, the bundle has no fiber of its
// own (only the mcp-origin insert does), and the plugin market's installed-
// state check (verify.js → loaderLive) reads it as perpetually "restart to
// apply" even after a restart. The apply body is empty: this bundle's
// runtime value is in cordis.patch.yml (insert mcp-origin), not in JS code.

import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const pkg = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
)

const pluginRoot = path.dirname(fileURLToPath(import.meta.url))

/** Static descriptor of this DSH bundle (kept in sync with package.json). */
export const plugin = {
  name: pkg.name,
  version: pkg.version,
  kind: 'python-mcp-bundle',
  server: 'origin_mcp_server.py',
  loader: 'mcp-origin (@deepseek-ai/dsh-mcp-client, stdio)',
  layout: '74 MCP tools · styled plots · inline preview · stats batch · /origin command',
  note: 'Wired into DSH by cordis.patch.yml; main/exports exist so the plugin ' +
    'market recognizes a prebuilt artifact instead of a build-required checkout.',
}

export function describe() {
  return plugin
}

// ---------------------------------------------------------------------------
// /origin 斜杠指令（DSH 命令面板，v2.8.0+）
// ---------------------------------------------------------------------------
// DSH 的斜杠命令是"人类侧"命令：handler 直接执行，命令行本身不会发给模型
// （dsh-commands 的类型注释写明 "without sending the command to the model"）。
// 因此这里用 agent.steer() 把"用户要用 Origin 作图"的意图 + 工作流纪律作为
// 一条 user-role 消息交给模型：空闲时直接起一轮，运行中在下一步边界消费。

const ORIGIN_COMMAND_NAME = 'origin'
const ORIGIN_COMMAND_DESCRIPTION =
  '用 Origin MCP 插件（mcp__origin__* 工具）完成本次作图/分析请求'
const ORIGIN_COMMAND_INPUT_HINT = '描述你要画的图或分析；可附 CSV/XLSX 数据文件'

/**
 * 组装交给模型的 /origin 提示词（导出以便冒烟测试）。
 * @param {string} rawInput 命令后面的自然语言需求
 * @param {{files?: string[], images?: number}} [attachments] 附件摘要
 */
export function buildOriginCommandPrompt(rawInput, attachments) {
  const request = String(rawInput || '').trim()
  const files = Array.isArray(attachments && attachments.files)
    ? attachments.files.filter(Boolean)
    : []
  const imageCount = Number((attachments && attachments.images) || 0)
  const preamble = [
    '【Origin 作图模式】用户通过 /origin 指令要求使用 Origin MCP 插件（mcp__origin__* 工具）完成作图或数据分析。',
    '工作流程：',
    '1) 先调 origin_help 了解工具与模板；数据含义不确定时先 origin_plot_plan，返回的 questions 非空时先向用户确认。',
    '2) 主路径用 origin_figure（写数→画图→验证→导出一次完成）；复用已有文件时用 origin_load_file + origin_plot + origin_export。',
    '3) 交付前用 origin_verify_graph / origin_view_graph 自检；需要可编辑工程时 origin_save_project。',
    '约束：只使用真实数据，不虚构或补造数值；首次连接 Origin 可能耗时 5~45 秒。',
  ].join('\n')
  const extra = []
  if (files.length > 0) {
    extra.push('已随消息附带 ' + files.length + ' 个数据文件：' + files.join('、') + '。')
    extra.push('请用 origin_load_file 导入消息里"只读副本路径"指向的文件后再作图；不要根据文件名臆测内容或补造数据。')
  }
  if (imageCount > 0) {
    extra.push('另附 ' + imageCount + ' 张图片，仅作参考；若图片是数据表格，请要求用户提供 CSV/XLSX 源文件，不要凭图估算数值。')
  }
  const tail = extra.length > 0 ? '\n\n' + extra.join('\n') : ''
  if (!request) return preamble + tail + '\n\n用户尚未给出具体需求，请先问清楚要画什么数据/图型。'
  return preamble + '\n\n用户需求：' + request + tail
}

/** 在 ctx 上注册 /origin（可选依赖 commands 服务）；无 commands 时静默跳过。 */
export function registerOriginCommand(ctx) {
  if (!ctx || typeof ctx.inject !== 'function') return
  ctx.inject(['commands'], (cctx) => {
    if (!cctx.commands || typeof cctx.commands.register !== 'function') return
    cctx.effect(() => cctx.commands.register({
      name: ORIGIN_COMMAND_NAME,
      description: ORIGIN_COMMAND_DESCRIPTION,
      input: { hint: ORIGIN_COMMAND_INPUT_HINT, attachments: true },
      async handler(invocation) {
        const agent = invocation && invocation.agent
        if (!agent || typeof agent.steer !== 'function') {
          return { kind: 'error', text: '当前 DSH 版本不支持 /origin（缺少 agent.steer）。' }
        }
        const request = String((invocation && invocation.rawInput) || '').trim()
        const admitted = Array.isArray(invocation && invocation.attachments)
          ? invocation.attachments
          : []
        const blocks = admitted.filter((block) => block
          && (block.type === 'file' || block.type === 'image')
          && block.attachment)
        const files = blocks
          .filter((block) => block.type === 'file')
          .map((block) => block.attachment.name)
          .filter(Boolean)
        const imageCount = blocks.filter((block) => block.type === 'image').length
        try {
          agent.steer({
            id: 'origin-command-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
            role: 'user',
            // 附件块原样随消息发送：文件块会被 DSH 请求装配投影成"只读副本路径"文本，
            // 模型据此调用 origin_load_file；图片块则可直接被模型看到。
            content: [
              { type: 'text', text: buildOriginCommandPrompt(request, { files, images: imageCount }) },
              ...blocks,
            ],
            // 插件来源（DSH 的 MessageSource 为可合并扩展；dsh-imagegen 用同款写法）
            source: { kind: 'plugin', plugin: 'dsh-origin-plugin' },
          })
        } catch (error) {
          const reason = error && error.message ? error.message : String(error)
          return { kind: 'error', text: '发送 Origin 作图指令失败：' + reason }
        }
        if (request) {
          const suffix = files.length > 0 ? '（含 ' + files.length + ' 个数据文件）' : ''
          return { kind: 'success', text: '已把 Origin 作图请求交给助手' + suffix + '（Origin 工具：mcp__origin__*）。' }
        }
        if (files.length > 0) {
          return { kind: 'success', text: '已进入 Origin 作图模式，收到 ' + files.length + ' 个数据文件；请说明要画什么图。' }
        }
        return { kind: 'success', text: '已进入 Origin 作图模式，请描述要画什么数据/图型。' }
      },
    }), 'dsh-origin-plugin: /origin command')
  })
}

/** Cordis plugin entry: a no-op-ish apply so the loader creates a fiber for
 *  dsh-origin-plugin, which lets the plugin market read it as "live/active"
 *  (verify.js L149: if (loaderLive) → state='live') instead of perpetually
 *  "restart to apply" (L162: if (inBundles && !loaderLive) → state='restart').
 *  The real tools are mounted by the mcp-origin insert in cordis.patch.yml.
 *
 *  v2.3.0: register a ctx.effect so unloading the plugin releases engine-side
 *  resources (drains the Python COM task queue and stops its worker thread)
 *  instead of leaving them to the daemon reaper. We never kill Origin64.exe
 *  here — the user may still be using Origin by hand; residual-process cleanup
 *  is governed by DSH_ORIGIN_AUTOKILL / manual taskkill. */
export default {
  ...plugin,
  apply(ctx) {
    if (ctx && typeof ctx.effect === 'function') {
      ctx.effect(() => {
        // Fail-open best effort: ask the Python engine to shut down gracefully.
        // Any failure (no python on PATH, engine already gone) is non-fatal.
        try {
          const py = process.env.DSH_ORIGIN_PYTHON || 'python'
          spawnSync(py, ['-c',
            'import sys; sys.path.insert(0, r\'' + pluginRoot + '\'); '
            + 'import origin_engine as e; print(e.shutdown())'],
            { stdio: 'ignore', timeout: 8000 })
        } catch { /* unload must never throw */ }
      })
    }
    // v2.8.0：注册 /origin 斜杠指令（全局命令；旧运行时没有 commands 服务时自动跳过）。
    registerOriginCommand(ctx)
    // 必须**不返回任何值**（返回值 undefined）。
    // cordis 会把 apply() 的返回值当 effect 处理：返回普通对象（如插件描述符）
    // 会抛 `TypeError: Invalid effect`，导致 DSH 启动直接崩溃（issue #1 实证，
    // DSH ≥ 0.1.5 必现）。插件描述已由 describe() 单独导出，这里无需再返回。
    return void 0
  },
}
