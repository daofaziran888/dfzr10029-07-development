<template>
  <section class="page" data-module="field_contract">
    <header class="page-head">
      <div>
        <h2>字段契约接入</h2>
        <p class="page-desc">把来源 schema 变更包按声明来源规则编译为可审计的字段契约，查看字段签名差异、接受或拒绝变更。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记字段契约</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出字段契约清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">契约详情</button>
            <button class="link" type="button" :data-testid="`import-${row.id}`" @click="openImport(row)">导入变更包</button>
            <button class="link" type="button" :data-testid="`compile-${row.id}`" @click="compileContract(row)">编译契约</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无字段契约数据，可先登记字段契约</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记字段契约</h3>
        <label v-for="field in createFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" :required="field !== '来源规则'" />
        </label>
        <p class="hint-text">来源规则用 &gt; 分隔来源连接，越靠前优先级越高，同名字段冲突时按此裁决。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showImport" class="modal-backdrop" data-testid="import-modal">
      <form class="modal" @submit.prevent="submitImport">
        <h3>导入来源 schema 变更包（{{ importTarget?.契约编号 }}）</h3>
        <label class="create-field">
          <span>来源连接</span>
          <input v-model="importSource" data-testid="import-source" placeholder="如 jdbc:crm / kafka:orders" required />
        </label>
        <label class="create-field">
          <span>字段签名（每行一个：字段名,字段类型）</span>
          <textarea v-model="importSignatures" data-testid="import-signatures" rows="6" required></textarea>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showImport = false">取消</button>
          <button class="btn primary" data-testid="submit-import" type="submit" :disabled="importing">
            {{ importing ? '导入中…' : '确认导入' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detail" class="modal-backdrop" data-testid="contract-detail">
      <div class="modal wide-modal">
        <h3>契约详情 {{ detail.契约编号 }} · 版本 {{ detail.契约版本 }} · {{ detail.status }}</h3>

        <h4>字段签名（来源规则：{{ detail.来源规则 }}）</h4>
        <table class="data-table">
          <thead><tr><th>字段名</th><th>字段类型</th><th>来源连接</th></tr></thead>
          <tbody>
            <tr v-for="sig in detail.字段签名" :key="sig.字段名">
              <td>{{ sig.字段名 }}</td><td>{{ sig.字段类型 }}</td><td>{{ sig.来源连接 }}</td>
            </tr>
          </tbody>
        </table>

        <h4>来源 schema 变更包</h4>
        <table class="data-table">
          <thead><tr><th>包编号</th><th>来源连接</th><th>到达时间</th><th>字段数</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="pkg in packages" :key="pkg.包编号">
              <td>{{ pkg.包编号 }}</td><td>{{ pkg.来源连接 }}</td><td>{{ pkg.到达时间 }}</td>
              <td>{{ pkg.字段签名.length }}</td><td>{{ pkg.status }}</td>
            </tr>
            <tr v-if="!packages.length"><td colspan="5" class="empty-state">暂无变更包</td></tr>
          </tbody>
        </table>

        <h4>字段契约快照</h4>
        <table class="data-table">
          <thead><tr><th>快照编号</th><th>契约版本</th><th>生成时间</th><th>文件回执</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="snap in snapshots" :key="snap.快照编号" :data-testid="`snapshot-${snap.id}`">
              <td>{{ snap.快照编号 }}</td><td>{{ snap.契约版本 }}</td><td>{{ snap.生成时间 }}</td>
              <td>{{ snap.文件回执 || '—' }}</td><td>{{ snap.status }}</td>
              <td class="row-actions">
                <button class="link" type="button" :data-testid="`diff-${snap.id}`" @click="loadDiff(snap)">查看差异</button>
                <button class="link" type="button" :data-testid="`source-${snap.id}`" @click="loadSnapshotSource(snap)">关联来源记录</button>
              </td>
            </tr>
            <tr v-if="!snapshots.length"><td colspan="6" class="empty-state">暂无快照</td></tr>
          </tbody>
        </table>

        <div v-if="diffResult" data-testid="diff-result">
          <h4>快照 {{ diffResult.快照编号 }} 与主版本逐项核对（差异 {{ diffResult.差异字段数 }} 处）</h4>
          <table class="data-table">
            <thead><tr><th>字段名</th><th>快照类型</th><th>当前类型</th><th>快照来源</th><th>当前来源</th><th>差异</th></tr></thead>
            <tbody>
              <tr v-for="item in diffResult.items" :key="item.字段名" :data-diff="item.差异">
                <td>{{ item.字段名 }}</td><td>{{ item.快照类型 || '—' }}</td><td>{{ item.当前类型 || '—' }}</td>
                <td>{{ item.快照来源 || '—' }}</td><td>{{ item.当前来源 || '—' }}</td><td>{{ item.差异 }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="snapshotSource" data-testid="snapshot-source">
          <h4>快照 {{ snapshotSource.快照编号 }} 的关联来源记录</h4>
          <p class="hint-text">业务输入：{{ JSON.stringify(snapshotSource.业务输入) }}</p>
          <table class="data-table">
            <thead><tr><th>包编号</th><th>来源连接</th><th>到达时间</th><th>字段签名</th></tr></thead>
            <tbody>
              <tr v-for="pkg in snapshotSource.关联来源记录明细" :key="pkg.包编号">
                <td>{{ pkg.包编号 }}</td><td>{{ pkg.来源连接 }}</td><td>{{ pkg.到达时间 }}</td>
                <td>{{ pkg.字段签名.map((s: FieldSignature) => `${s.字段名}:${s.字段类型}`).join('，') }}</td>
              </tr>
              <tr v-if="!snapshotSource.关联来源记录明细.length"><td colspan="4" class="empty-state">该快照无关联变更包</td></tr>
            </tbody>
          </table>
        </div>

        <h4>受影响查询模板（引用未发布或已变化字段）</h4>
        <table class="data-table">
          <thead><tr><th>模板编号</th><th>模板名称</th><th>模板状态</th><th>问题字段</th></tr></thead>
          <tbody>
            <tr v-for="tpl in affectedTemplates" :key="tpl.模板编号">
              <td>{{ tpl.模板编号 }}</td><td>{{ tpl.模板名称 }}</td><td>{{ tpl.模板状态 }}</td>
              <td>{{ tpl.问题字段.map((p: TemplateProblem) => `${p.字段名}（${p.问题}）`).join('；') }}</td>
            </tr>
            <tr v-if="!affectedTemplates.length"><td colspan="4" class="empty-state">没有需要停用的查询模板</td></tr>
          </tbody>
        </table>

        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-detail" @click="closeDetail">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条字段契约记录</span>
      <span v-if="message" class="hint-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type FieldSignature = { 字段名: string; 字段类型: string; 来源连接?: string }
type TemplateProblem = { 字段名: string; 问题: string }

const ENDPOINT = '/api/field_contract'
const columns = ["契约编号", "数据集编号", "数据集名称", "契约版本", "来源规则", "字段数量", "负责人", "契约状态"]
const createFields = ["契约编号", "数据集编号", "数据集名称", "来源规则", "负责人"]
const actions = ["接受变更", "拒绝变更"]
const statuses = ["草稿", "待审核", "已发布", "已拒绝"]

const rows = ref<Row[]>([])
const total = ref(0)
const message = ref('')
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const showImport = ref(false)
const importing = ref(false)
const importTarget = ref<Row | null>(null)
const importSource = ref('')
const importSignatures = ref('')
const detail = ref<Row | null>(null)
const packages = ref<Row[]>([])
const snapshots = ref<Row[]>([])
const diffResult = ref<Row | null>(null)
const snapshotSource = ref<Row | null>(null)
const affectedTemplates = ref<Row[]>([])
const stats = computed(() => [
  { label: "字段契约总数", value: total.value },
  { label: "待审核契约", value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: "已发布契约", value: rows.value.filter((row) => row.status === statuses[2]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'field_contract.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(createFields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

function openImport(row: Row) {
  importTarget.value = row
  importSource.value = ''
  importSignatures.value = ''
  errorMessage.value = ''
  showImport.value = true
}

function parseSignatures(text: string) {
  return text.split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, type] = line.split(/[,，\s]+/)
      return { 字段名: name, 字段类型: type }
    })
    .filter((item) => item.字段名 && item.字段类型)
}

async function submitImport() {
  if (!importTarget.value) return
  importing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${importTarget.value.id}/packages`, {
      method: 'POST',
      body: JSON.stringify({ 来源连接: importSource.value, 字段签名: parseSignatures(importSignatures.value) }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '导入失败')
    message.value = payload.message
    showImport.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入失败'
  } finally {
    importing.value = false
  }
}

async function compileContract(row: Row) {
  errorMessage.value = ''
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/compile`, {
      method: 'POST',
      body: JSON.stringify({ 基准版本: row.契约版本 }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '编译失败')
    message.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '编译失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '字段契约动作未生效')
    message.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段契约操作失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  diffResult.value = null
  snapshotSource.value = null
  try {
    const [detailResp, packagesResp, snapshotsResp, affectedResp] = await Promise.all([
      request(`${ENDPOINT}/${row.id}`),
      request(`${ENDPOINT}/${row.id}/packages`),
      request(`${ENDPOINT}/${row.id}/snapshots`),
      request(`${ENDPOINT}/${row.id}/affected_templates`),
    ])
    if (!detailResp.ok) throw new Error('契约详情读取失败')
    detail.value = await detailResp.json()
    packages.value = (await packagesResp.json()).items ?? []
    snapshots.value = (await snapshotsResp.json()).items ?? []
    affectedTemplates.value = (await affectedResp.json()).items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '契约详情读取失败'
  }
}

async function loadDiff(snap: Row) {
  if (!detail.value) return
  errorMessage.value = ''
  snapshotSource.value = null
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/diff?snapshot_id=${snap.id}`)
    if (!response.ok) throw new Error('快照差异读取失败')
    diffResult.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照差异读取失败'
  }
}

async function loadSnapshotSource(snap: Row) {
  if (!detail.value) return
  errorMessage.value = ''
  diffResult.value = null
  try {
    const response = await request(`${ENDPOINT}/${detail.value.id}/snapshots/${snap.id}`)
    if (!response.ok) throw new Error('关联来源记录读取失败')
    snapshotSource.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '关联来源记录读取失败'
  }
}

function closeDetail() {
  detail.value = null
  diffResult.value = null
  snapshotSource.value = null
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('字段契约列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段契约列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.wide-modal { width: min(880px, 100%); }
.hint-text { color: var(--muted); font-size: 12px; }
textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: monospace; }
h4 { margin: 14px 0 6px; font-size: 13px; }
</style>
