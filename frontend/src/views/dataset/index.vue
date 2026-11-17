<template>
  <section class="page" data-module="dataset">
    <header class="page-head">
      <div>
        <h2>数据集管理管理</h2>
        <p class="page-desc">维护分析数据集，围绕数据集编号、数据集名称、来源类型、字段数量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记分析数据集</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据集管理清单</button>
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
            <button
              class="link"
              type="button"
              :data-testid="`import-${row.id}`"
              @click="openImport(row)"
            >
              导入文件
            </button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无数据集管理数据，可先登记分析数据集</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记分析数据集</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
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
        <h3>导入文件（{{ importTarget?.数据集编号 }}）</h3>
        <label class="create-field">
          <span>文件名</span>
          <input v-model="importFileName" data-testid="import-file-name" placeholder="如 orders_v2.csv" required />
        </label>
        <label class="create-field">
          <span>来源连接</span>
          <input v-model="importSource" data-testid="import-source" placeholder="如 jdbc:crm / kafka:orders" required />
        </label>
        <label class="create-field">
          <span>字段签名（每行一个：字段名,字段类型）</span>
          <textarea v-model="importSignatures" data-testid="import-signatures" rows="6" required></textarea>
        </label>
        <p class="hint-text">导入后按声明来源规则编译，生成字段契约快照；导出或下载时会在快照上保留文件回执。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showImport = false">取消</button>
          <button class="btn primary" data-testid="submit-import" type="submit" :disabled="importing">
            {{ importing ? '导入中…' : '确认导入' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集管理记录</span>
      <span v-if="message" class="hint-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dataset'
const columns = ["数据集编号", "数据集名称", "来源类型", "字段数量", "行数估算", "刷新周期", "负责人", "数据集状态"]
const actions = ["完成接入", "启用数据集", "归档数据集"]
const statuses = ["待接入", "已接入", "使用中", "已归档"]
const metricNames = ["使用中数据集", "待接入数据集", "已归档数据集"]

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
const importFileName = ref('')
const importSource = ref('')
const importSignatures = ref('')
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
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
    link.download = 'dataset.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
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

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据集管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理操作失败'
  }
}

function openImport(row: Row) {
  importTarget.value = row
  importFileName.value = ''
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
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${importTarget.value.id}/import`, {
      method: 'POST',
      body: JSON.stringify({
        文件名: importFileName.value,
        来源连接: importSource.value,
        字段签名: parseSignatures(importSignatures.value),
      }),
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

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('分析数据集列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.hint-text { color: var(--muted); font-size: 12px; }
textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: monospace; }
</style>
