<template>
  <div class="terr-page">
    <el-card class="page-card">
      <!-- 工具栏：同步入口 + 同步时间 -->
      <div class="toolbar">
        <el-button type="primary" :loading="syncing" @click="doSync">
          <el-icon><Refresh /></el-icon>&nbsp;同步责任田在线表
        </el-button>
        <el-button :loading="importing" @click="openUpload">
          <el-icon><Upload /></el-icon>&nbsp;导入表格
        </el-button>
        <el-button text @click="settingsVisible = true">同步设置</el-button>
        <el-button text @click="openLogs">同步记录</el-button>

        <!-- 同步时间：一眼看到数据新不新 -->
        <div class="sync-stamp">
          <el-icon><Clock /></el-icon>
          <span>上次同步</span>
          <b>{{ lastSync.time || '从未同步' }}</b>
          <el-tag v-if="lastSync.status" size="small" effect="plain"
                  :type="lastSync.status === 'success' ? 'success' : 'danger'">
            {{ lastSync.status === 'success' ? '成功' : '失败' }}
          </el-tag>
          <span v-if="lastSync.total" class="muted">（{{ lastSync.total }} 行 · {{ lastSync.operator }}）</span>
        </div>
        <span class="tip">
          口径：以<b>小区编码</b>为唯一键对齐在线表《实际责任田》；责任人、责任人工号/电话、
          装机人员、投诉责任人 全部取<b>该行责任人本人</b>；
          工单/质检/竣工率按 <b>工号 → 姓名 → 责任田</b> 三级优先匹配责任人
        </span>
        <el-tooltip v-if="summary.grid_empty"
                    content="在线表「市场网格名称」为空，班组统计会落到「未填网格」，建议回表补齐">
          <el-tag size="small" effect="plain" type="warning">
            网格待补 {{ summary.grid_empty }} 条
          </el-tag>
        </el-tooltip>
      </div>

      <!-- 汇总卡片 -->
      <el-row :gutter="12" class="stat-row">
        <el-col v-for="c in cards" :key="c.label" :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <div class="stat-label">{{ c.label }}</div>
            <div class="stat-value" :style="{ color: c.color || '' }">{{ c.value
              }}<span v-if="c.unit" class="unit">{{ c.unit }}</span></div>
            <div class="stat-sub">{{ c.sub }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-tabs v-model="tab" @tab-change="onTabChange">
        <!-- ================= 明细 ================= -->
        <el-tab-pane label="责任田明细" name="detail">
          <div class="filter-row">
            <el-select v-model="q.grid" placeholder="标准网格" clearable filterable
                       style="width:170px" @change="loadList">
              <el-option v-for="g in opts.grids" :key="g" :label="g" :value="g" />
            </el-select>
            <el-select v-model="q.team" placeholder="班组" clearable filterable
                       style="width:160px" @change="loadList">
              <el-option v-for="t in opts.teams" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select v-model="q.person" placeholder="责任人" clearable filterable
                       style="width:150px" @change="loadList">
              <el-option v-for="p in opts.persons" :key="p" :label="p" :value="p" />
            </el-select>
            <el-input v-model="q.keyword" placeholder="责任田 / 小区 / 姓名 / 工号" clearable
                      style="width:230px" @keyup.enter="loadList" />
            <el-button @click="loadList">查询</el-button>
            <el-button text @click="resetQuery">重置</el-button>
            <span class="count-hint">共 <b>{{ total }}</b> 条</span>
          </div>

          <el-table :data="rows" size="small" border max-height="520" v-loading="loading">
            <template #empty>
              <el-empty description="暂无责任田数据" :image-size="90">
                <el-button type="primary" size="small" :loading="syncing" @click="doSync">
                  同步责任田在线表
                </el-button>
                <el-button size="small" @click="openUpload">导入表格</el-button>
              </el-empty>
            </template>
            <el-table-column prop="seq" label="#" width="60" align="center" />
            <el-table-column prop="territory" label="实际责任田（小区）" min-width="210" show-overflow-tooltip>
              <template #default="{ row }">
                <span :class="{ 'gp-red': !row.territory }">{{ row.territory || '（未填）' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="territory_code" label="小区编码" min-width="150"
                             show-overflow-tooltip>
              <template #default="{ row }">
                <span class="muted">{{ row.territory_code || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="user_count" label="用户数" width="90" align="right" sortable>
              <template #default="{ row }">{{ row.user_count || 0 }}</template>
            </el-table-column>
            <el-table-column prop="grid" label="市场网格（班组）" min-width="130" show-overflow-tooltip />
            <el-table-column prop="person_name" label="责任人" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.person_name ? 'primary' : 'danger'">
                  {{ row.person_name || '未配' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="person_phone" label="责任人电话" width="130">
              <template #default="{ row }">
                <span :class="{ muted: !row.person_phone }">{{ row.person_phone || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="装机人员" width="130" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tooltip v-if="row.installer_name" placement="top"
                            :content="`装机人工号 ${row.installer_no || '—'} / 电话 ${row.installer_phone || '—'}` + instNote(row)">
                  <span class="inst">
                    {{ row.installer_name }}
                    <el-tag v-if="row.installer_source === 'owner'" size="small"
                            effect="plain" type="info" class="inst-tag">本人</el-tag>
                    <el-tag v-else-if="row.installer_source === 'owner_forced'" size="small"
                            effect="plain" type="warning" class="inst-tag">口径</el-tag>
                  </span>
                </el-tooltip>
                <span v-else class="muted">未找到</span>
              </template>
            </el-table-column>
            <el-table-column prop="complaint_person" label="投诉责任人" width="110">
              <template #default="{ row }">
                <el-tooltip placement="top" content="与责任人同一口径，取该小区编码在《实际责任田》对应行的责任人">
                  <span :class="{ muted: !row.complaint_person }">{{ row.complaint_person || '—' }}</span>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column prop="compliance" label="达标" width="110" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.compliance" size="small" effect="plain"
                        :type="row.compliance.includes('不达标') ? 'danger' : 'success'">
                  {{ row.compliance }}
                </el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="remark" label="备注" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.remark || '—' }}</template>
            </el-table-column>
            <el-table-column prop="person_no" label="责任人编码" width="110">
              <template #default="{ row }">
                <span :class="{ muted: !row.person_no }">{{ row.person_no || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70" fixed="right" align="center">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination style="margin-top:12px" layout="total, sizes, prev, pager, next"
                         :total="total" :page-sizes="[50, 100, 200, 500]"
                         v-model:current-page="q.page" v-model:page-size="q.page_size"
                         @change="loadList" />
        </el-tab-pane>

        <!-- ================= 按责任人 ================= -->
        <el-tab-pane label="按责任人" name="person">
          <el-table :data="personRows" size="small" border max-height="580">
            <el-table-column type="index" label="#" width="60" align="center" />
            <el-table-column prop="person_name" label="责任人" width="120" />
            <el-table-column prop="person_no" label="工号" width="100">
              <template #default="{ row }">{{ row.person_no || '—' }}</template>
            </el-table-column>
            <el-table-column prop="phone" label="联系电话" width="130">
              <template #default="{ row }">{{ row.phone || '—' }}</template>
            </el-table-column>
            <el-table-column prop="team" label="班组" min-width="140">
              <template #default="{ row }">{{ row.team || '—' }}</template>
            </el-table-column>
            <el-table-column prop="territories" label="责任田数" width="110" align="right" sortable />
            <el-table-column prop="grids" label="覆盖网格数" width="120" align="right" sortable />
            <el-table-column prop="users_sum" label="责任田用户数" width="130" align="right" sortable />
          </el-table>
        </el-tab-pane>

        <!-- ================= 按网格/班组 ================= -->
        <el-tab-pane label="按网格/班组" name="grid">
          <el-table :data="gridRows" size="small" border max-height="580">
            <el-table-column type="index" label="#" width="60" align="center" />
            <el-table-column prop="grid" label="标准网格" min-width="160" />
            <el-table-column prop="team" label="班组" min-width="150" />
            <el-table-column prop="count" label="责任田条数" width="120" align="right" sortable />
            <el-table-column prop="territories" label="不同责任田" width="120" align="right" sortable />
            <el-table-column prop="persons" label="责任人数" width="110" align="right" sortable />
            <el-table-column prop="users_sum" label="用户数" width="110" align="right" sortable />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 上传兜底 -->
    <el-dialog v-model="uploadVisible" title="导入责任田表格" width="520px">
      <div class="sync-hint">
        在线表需要登录、同步报 <b>NEED_AUTH</b> 时用这里：把金山文档
        「另存为 / 导出」成 <b>.xlsx</b> 后上传，解析口径与在线同步完全一致
        （自动识别表头，责任田 / 责任人 / 网格 / 班组 / 工号 / 电话 / 备注）。
      </div>
      <el-upload drag :auto-upload="false" :limit="1" accept=".xlsx,.xls"
                 :on-change="onFileChange" :on-remove="() => (file = null)">
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖入文件或<em>点击选择</em></div>
      </el-upload>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" :disabled="!file" @click="doUpload">
          开始导入
        </el-button>
      </template>
    </el-dialog>

    <!-- 同步设置 -->
    <el-dialog v-model="settingsVisible" title="同步设置" width="620px">
      <el-form label-width="110px">
        <el-form-item label="在线表地址">
          <el-input v-model="form.source_url" placeholder="https://www.kdocs.cn/l/xxxx" />
        </el-form-item>
        <el-form-item label="WPS Cookie">
          <el-input v-model="form.cookie" type="textarea" :rows="3"
                    :placeholder="hasCookie ? '已配置（留空表示不修改）' : '可选。链接需登录时，填浏览器里的 kdocs.cn Cookie'" />
        </el-form-item>
      </el-form>
      <div class="sync-hint">
        金山文档的 <b>/l/xxxx</b> 短链默认要求登录。两种解法：<br />
        ① 分享设置改为「<b>互联网获得链接的人可查看</b>」，并在高级设置里允许下载；<br />
        ② 在浏览器登录金山文档后按 F12 → Network → 任一 kdocs.cn 请求 → 复制
        <b>Cookie</b> 整串贴到上面（Cookie 过期需重新粘贴）。
      </div>
      <template #footer>
        <el-button @click="settingsVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSettings">保存</el-button>
      </template>
    </el-dialog>

    <!-- 同步记录 -->
    <el-drawer v-model="logsVisible" title="同步记录" size="640px">
      <el-table :data="logRows" size="small" border>
        <el-table-column prop="time" label="时间" width="150" />
        <el-table-column prop="status" label="结果" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : 'danger'">
              {{ row.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total" label="总行数" width="80" align="right" />
        <el-table-column label="新增/更新/下线" width="130" align="center">
          <template #default="{ row }">{{ row.inserted }}/{{ row.updated }}/{{ row.deactivated }}</template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="90" />
        <el-table-column prop="message" label="说明" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-drawer>

    <!-- 单条编辑 -->
    <el-dialog v-model="editVisible" title="编辑责任田对应关系" width="520px">
      <el-form label-width="100px">
        <el-form-item label="区县"><el-input v-model="edit.district" /></el-form-item>
        <el-form-item label="实际责任田"><el-input v-model="edit.territory" /></el-form-item>
        <el-form-item label="小区编码"><el-input v-model="edit.territory_code" /></el-form-item>
        <el-form-item label="市场网格"><el-input v-model="edit.grid" /></el-form-item>
        <el-form-item label="班组"><el-input v-model="edit.team" /></el-form-item>
        <el-form-item label="责任人"><el-input v-model="edit.person_name" /></el-form-item>
        <el-form-item label="工号"><el-input v-model="edit.person_no" /></el-form-item>
        <el-form-item label="联系电话"><el-input v-model="edit.person_phone" /></el-form-item>
        <el-form-item label="装机人员"><el-input v-model="edit.installer_name" /></el-form-item>
        <el-form-item label="装机人工号"><el-input v-model="edit.installer_no" /></el-form-item>
        <el-form-item label="装机人电话"><el-input v-model="edit.installer_phone" /></el-form-item>
        <el-form-item label="用户数"><el-input-number v-model="edit.user_count" :min="0" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="edit.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Upload, UploadFilled, Clock } from '@element-plus/icons-vue'
import api from '../api'

const syncing = ref(false)
const importing = ref(false)
const loading = ref(false)
const tab = ref('detail')

const summary = ref({ total: 0, territories: 0, persons: 0, grids: 0, teams: 0,
                      districts: 0, users_sum: 0, unbound_persons: 0 })
const lastSync = ref({})
const hasCookie = ref(false)

const rows = ref([])
const total = ref(0)
const q = reactive({ keyword: '', grid: '', team: '', person: '', page: 1, page_size: 50 })
const opts = reactive({ grids: [], teams: [], persons: [] })

const personRows = ref([])
const gridRows = ref([])

const cards = computed(() => {
  const s = summary.value
  return [
    { label: '责任田总数', value: s.total, unit: ' 条', sub: '当前有效对应关系' },
    { label: '不同责任田', value: s.territories, unit: ' 块', sub: '按小区去重' },
    { label: '责任人', value: s.persons, unit: ' 人', sub: '已绑定责任田', color: '#0f766e' },
    { label: '覆盖网格', value: s.grids, unit: ' 个', sub: '市场网格/班组' },
    { label: '责任田用户数', value: s.users_sum, unit: ' 户', sub: '在网用户合计',
      color: '#1d4ed8' },
    { label: s.has_compliance ? '不达标责任田' : '未配责任田',
      value: s.has_compliance ? s.non_ok : s.unbound_persons, unit: ' 条',
      sub: s.has_compliance ? '按在线表「是否达标」' : (s.unbound_persons > 0 ? '需补表' : '全部已配'),
      color: (s.has_compliance ? s.non_ok : s.unbound_persons) > 0 ? '#be123c' : '#0f766e' },
  ]
})

async function loadSummary() {
  const d = await api.get('/territory/summary')
  summary.value = d
  lastSync.value = d.last_sync || {}
  hasCookie.value = !!d.has_cookie
  if (d.source_url) form.source_url = d.source_url
}

// 装机人员列的口径说明：源表清单可能是别人（施工班组），已统一为责任人本人
function instNote(row) {
  if (row.installer_source === 'owner') return '（源表写「未找到」，按责任人本人回填）'
  if (row.installer_source === 'owner_forced') {
    return '（源表装机人员清单填的是他人，已按「责任人本人」口径统一；原串见原始数据）'
  }
  return ''
}

async function loadOptions() {
  const d = await api.get('/territory/grids')
  opts.grids = d.grids || []
  opts.teams = d.teams || []
  opts.persons = d.persons || []
}

async function loadList() {
  // 从 Excel/微信粘贴的小区名常带前导 Tab 或全角空格，不洗掉就一条都搜不到
  if (q.keyword) q.keyword = q.keyword.replace(/[\s\u3000]/g, '')
  loading.value = true
  try {
    const d = await api.get('/territory/list', { params: q })
    rows.value = d.items || []
    total.value = d.total || 0
  } finally {
    loading.value = false
  }
}

async function loadPerson() {
  personRows.value = await api.get('/territory/by-person')
}

async function loadGrid() {
  gridRows.value = await api.get('/territory/by-grid')
}

function onTabChange(name) {
  if (name === 'person' && !personRows.value.length) loadPerson()
  if (name === 'grid' && !gridRows.value.length) loadGrid()
}

function resetQuery() {
  Object.assign(q, { keyword: '', grid: '', team: '', person: '', page: 1 })
  loadList()
}

// ---------- 同步 ----------
async function doSync() {
  syncing.value = true
  try {
    const r = await api.post('/territory/sync', {}, { timeout: 120000 })
    ElMessage.success(`同步完成：新增 ${r.inserted} · 更新 ${r.updated} · 下线 ${r.deactivated}（共 ${r.total} 行）`)
    await refreshAll()
  } catch (e) {
    // 后端把「需要登录 / 无导出权限」的原因写在 detail 里，这里原样弹出，避免只报“请求失败”
    const msg = e?.response?.data?.detail
    if (msg) {
      ElMessageBox.alert(String(msg).replace(/\n/g, '<br>'), '同步未完成', {
        dangerouslyUseHTMLString: true, type: 'warning',
      })
    }
    await loadSummary()
  } finally {
    syncing.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadSummary(), loadOptions(), loadList()])
  if (personRows.value.length) loadPerson()
  if (gridRows.value.length) loadGrid()
}

// ---------- 上传 ----------
const uploadVisible = ref(false)
const file = ref(null)

function openUpload() {
  file.value = null
  uploadVisible.value = true
}
function onFileChange(f) { file.value = f.raw }

async function doUpload() {
  if (!file.value) return
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    const r = await api.post('/territory/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000,
    })
    ElMessage.success(`导入完成：新增 ${r.inserted} · 更新 ${r.updated}（共 ${r.total} 行）`)
    uploadVisible.value = false
    await refreshAll()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}

// ---------- 设置 ----------
const settingsVisible = ref(false)
const form = reactive({ source_url: '', cookie: '' })

async function saveSettings() {
  await api.post('/territory/settings', {
    source_url: form.source_url, cookie: form.cookie,
  })
  ElMessage.success('已保存')
  settingsVisible.value = false
  form.cookie = ''
  loadSummary()
}

// ---------- 日志 ----------
const logsVisible = ref(false)
const logRows = ref([])
async function openLogs() {
  logsVisible.value = true
  logRows.value = await api.get('/territory/logs')
}

// ---------- 编辑 ----------
const editVisible = ref(false)
const edit = reactive({})

function openEdit(row) {
  Object.keys(edit).forEach(k => delete edit[k])
  Object.assign(edit, row)
  editVisible.value = true
}

async function saveEdit() {
  await api.put(`/territory/${edit.id}`, edit)
  ElMessage.success('已保存')
  editVisible.value = false
  loadList()
  loadSummary()
}

onMounted(async () => {
  await loadSummary()
  await Promise.all([loadOptions(), loadList()])
})
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
.sync-stamp {
  display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #475569;
  background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 4px 10px;
}
.sync-stamp b { color: #0f172a; font-variant-numeric: tabular-nums; }
.muted { color: #94a3b8; }
.inst { color: #0f172a; display: inline-flex; align-items: center; white-space: nowrap; }
.inst-tag { margin-left: 4px; transform: scale(0.85); }
.tip { font-size: 12px; color: #94a3b8; margin-left: auto; }
.stat-row { margin-top: 12px; }
.stat-card { border-radius: 8px; }
.stat-label { font-size: 12px; color: #64748b; }
.stat-value { font-size: 24px; font-weight: 700; line-height: 1.5; font-variant-numeric: tabular-nums; }
.stat-value .unit { font-size: 12px; font-weight: 400; color: #94a3b8; margin-left: 2px; }
.stat-sub { font-size: 12px; color: #94a3b8; }
.filter-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 4px 0 12px; }
.count-hint { font-size: 13px; color: #64748b; margin-left: auto; }
.count-hint b { color: #0f172a; }
.gp-red { color: #be123c; font-weight: 600; }
.sync-hint { font-size: 12px; line-height: 1.9; color: #475569; background: #f8fafc;
  border-left: 3px solid #cbd5e1; border-radius: 4px; padding: 8px 10px; margin-bottom: 12px; }
.sync-hint b { color: #0f172a; }
</style>
