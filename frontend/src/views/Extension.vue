<template>
  <div>
    <!-- 分析概览卡片：随数据入库实时刷新 -->
    <el-row :gutter="12" class="stat-row">
      <el-col :span="4" v-for="c in cards" :key="c.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value" :style="c.color ? { color: c.color } : null">{{ c.value }}</div>
          <div class="stat-sub" v-if="c.sub">{{ c.sub }}</div>
          <el-progress v-if="c.progress !== undefined" :percentage="Math.min(c.progress, 130)"
                       :color="rateColor(c.progress)" :stroke-width="8" :show-text="false" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
        <el-select v-model="district" style="width:140px">
          <el-option v-for="d in districtOptions" :key="d" :label="d === '全部' ? '全部区县' : d" :value="d" />
        </el-select>
        <el-radio-group v-model="tab" @change="load">
          <el-radio-button value="rank">区县排名</el-radio-button>
          <el-radio-button value="personal">个人明细</el-radio-button>
          <el-radio-button value="team">班组汇总</el-radio-button>
        </el-radio-group>
        <span class="target-tip" :class="{ editable: isAdmin }" @click="isAdmin && openTargetDlg()"
              title="管理员可点击修改">
          人均任务目标：{{ target }} 元/月<el-icon v-if="isAdmin" style="vertical-align:-2px"><Edit /></el-icon>
        </span>
        <span v-if="latest" class="target-tip">数据截止：{{ latest.slice(5, 16) }}</span>
        <el-button v-if="isAdmin" type="primary" style="margin-left:auto" :loading="uploading" @click="openDlg">
          <el-icon><Upload /></el-icon>&nbsp;上传通报表
        </el-button>
      </div>

      <el-table v-if="tab === 'rank'" :data="filteredRanking" size="small" max-height="520">
        <el-table-column prop="rank" label="#" width="50" />
        <el-table-column prop="district" label="区县" min-width="100" />
        <el-table-column prop="members" label="人数" min-width="60" />
        <el-table-column label="参与" min-width="70">
          <template #default="{ row }">{{ row.active ?? 0 }}/{{ row.members }}</template>
        </el-table-column>
        <el-table-column prop="target" label="任务目标(元)" min-width="110" sortable />
        <el-table-column prop="income" label="完成收入(元)" min-width="110" sortable />
        <el-table-column label="欠产(元)" min-width="110">
          <template #default="{ row }">
            <span :style="{ color: row.gap >= 0 ? '#67c23a' : '#f56c6c' }">{{ row.gap.toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="完成占比" min-width="150">
          <template #default="{ row }">
            <el-progress :percentage="Math.min(row.ratio, 130)" :color="rateColor(row.ratio)" :stroke-width="12"
                         :format="() => row.ratio + '%'" />
          </template>
        </el-table-column>
      </el-table>

      <el-table v-else-if="tab === 'personal'" :data="filteredPersonal" size="small" max-height="520">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="handler" label="人员" min-width="90" />
        <el-table-column prop="district" label="区县" min-width="90" />
        <el-table-column prop="grid" label="网格" min-width="100" show-overflow-tooltip />
        <el-table-column prop="orders" label="单数" min-width="70" sortable />
        <el-table-column prop="income" label="收入(元)" min-width="100" sortable />
        <el-table-column label="完成占比" min-width="160">
          <template #default="{ row }">
            <el-progress :percentage="Math.min(row.ratio, 130)" :color="rateColor(row.ratio)" :stroke-width="12"
                         :format="() => row.ratio + '%'" />
          </template>
        </el-table-column>
      </el-table>

      <el-table v-else :data="filteredTeam" size="small" max-height="520">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="district" label="区县" min-width="90" />
        <el-table-column prop="position" label="网格" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.group_by === 'grid'" size="small" type="success" effect="plain">{{ row.position }}</el-tag>
            <span v-else style="color:#909399">{{ row.position }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="people" label="人数" min-width="70" />
        <el-table-column prop="orders" label="单数" min-width="70" />
        <el-table-column prop="income" label="收入(元)" min-width="100" sortable />
      </el-table>
    </el-card>

    <el-dialog v-model="dlg" title="上传延伸服务数据通报表" width="680px" @closed="parsed = null">
      <el-alert type="info" :closable="false" show-icon
                title="自动解析「当月明细」「人员明细」两个 sheet（其余 sheet 跳过）；month 由受理时间推导；通报为当月累计快照，入库按月替换" />
      <el-upload drag :show-file-list="false" accept=".xlsx,.xlsm" :auto-upload="false"
                 :disabled="uploading || importing" :on-change="onFileChange" style="margin-top:10px">
        <div class="el-upload__text" style="padding:10px 0">
          拖拽文件到此处或 <em>点击选择 .xlsx 通报表</em>（如：延伸服务数据通报10.13.xlsx）
        </div>
      </el-upload>

      <div v-if="parsed" style="margin-top:10px">
        <div style="font-size:13px">
          {{ parsed.filename }}：解析订单 <b>{{ parsed.orders }}</b> 行、有效人员 <b>{{ parsed.members }}</b> 人，
          月份 <b>{{ (parsed.months || []).join('、') || '-' }}</b>，预览前 8 行：
        </div>
        <el-table :data="parsed.preview.slice(0, 8)" size="small" max-height="220" style="margin-top:8px">
          <el-table-column prop="order_no" label="订单号" min-width="150" show-overflow-tooltip />
          <el-table-column prop="district" label="区县" min-width="90" />
          <el-table-column prop="handler" label="受理人" min-width="90" />
          <el-table-column prop="accept_time" label="受理时间" min-width="140" />
          <el-table-column prop="service_item" label="服务项目" min-width="140" show-overflow-tooltip />
          <el-table-column prop="income" label="收入(元)" min-width="80" />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :disabled="!parsed" :loading="importing" @click="doImport">确认入库</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="targetDlg" title="修改人均任务目标" width="380px">
      <el-form label-width="90px">
        <el-form-item label="月份"><el-input :model-value="month" disabled /></el-form-item>
        <el-form-item label="目标(元)">
          <el-input-number v-model="targetForm" :min="1" :max="100000" :step="5" style="width:100%" />
        </el-form-item>
      </el-form>
      <div style="color:#909399;font-size:12px">目标 = 有效人数 × 人均任务；修改后排名、卡片、占比实时刷新</div>
      <template #footer>
        <el-button @click="targetDlg = false">取消</el-button>
        <el-button type="primary" :loading="savingTarget" @click="saveTarget">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const TARGET = 160                                      // 默认人均任务（手册 7.6），每月可在页面修改
const month = ref(new Date().toISOString().slice(0, 7))
const target = ref(TARGET)                              // 当月人均任务（后端 settings 读取）
const latest = ref('')                                  // 当月明细最晚受理时间（数据截止）
const district = ref('全部')                            // 区县筛选，默认全部区县
const tab = ref('rank')
const ranking = ref([])
const personal = ref([])
const team = ref([])

// 区县筛选联动：卡片、排名、个人、班组全部随选择过滤
const districtOptions = computed(() => ['全部', ...new Set(ranking.value.map(r => r.district))])
const matchDistrict = d => district.value === '全部' || d === district.value
const filteredRanking = computed(() => ranking.value.filter(r => matchDistrict(r.district)))
const filteredPersonal = computed(() => personal.value.filter(r => matchDistrict(r.district)))
const filteredTeam = computed(() => team.value.filter(r => matchDistrict(r.district)))

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)
const dlg = ref(false)
const uploading = ref(false)
const importing = ref(false)
const parsed = ref(null)
const targetDlg = ref(false)
const targetForm = ref(TARGET)
const savingTarget = ref(false)

const rateColor = r => (r >= 100 ? '#67c23a' : r >= 60 ? '#e6a23c' : '#f56c6c')
const fmt = n => Math.round(n || 0).toLocaleString()

// 概览分析：由（筛选后的）区县排名聚合（目标=人数×160，收入/欠产/占比/参与率实时计算）
const overview = computed(() => {
  const sum = k => filteredRanking.value.reduce((s, r) => s + (Number(r[k]) || 0), 0)
  const income = sum('income'), target = sum('target')
  const members = sum('members'), active = sum('active')
  return {
    income, target, members, active, orders: sum('orders'),
    gap: income - target,
    ratio: target ? +(income / target * 100).toFixed(1) : 0,
    rate: members ? +(active / members * 100).toFixed(1) : 0,
  }
})

const cards = computed(() => {
  const o = overview.value
  return [
    { label: '完成收入(元)', value: fmt(o.income), sub: `共 ${o.orders} 单` },
    { label: '任务目标(元)', value: fmt(o.target), sub: `${o.members} 人 × ${target.value} 元` },
    { label: '完成占比', value: o.ratio + '%', progress: o.ratio, color: rateColor(o.ratio) },
    { label: '欠产(元)', value: fmt(o.gap), color: o.gap >= 0 ? '#67c23a' : '#f56c6c' },
    { label: '参与人数', value: `${o.active}/${o.members}`, sub: `参与率 ${o.rate}%`,
      color: o.rate >= 80 ? '#67c23a' : '#e6a23c' },
    { label: '人均产出(元)', value: o.active ? fmt(o.income / o.active) : '0', sub: '按参与人数' },
  ]
})

async function load() {
  const params = { month: month.value }
  const [r, p, t, tg] = await Promise.all([
    api.get('/metrics/extension/district-ranking', { params }),
    api.get('/metrics/extension/personal', { params }),
    api.get('/metrics/extension/team', { params }),
    api.get('/metrics/extension/target', { params }),
  ])
  ranking.value = r
  personal.value = p
  team.value = t
  target.value = tg.target
  latest.value = tg.latest || ''
}

function openTargetDlg() {
  targetForm.value = target.value
  targetDlg.value = true
}

async function saveTarget() {
  savingTarget.value = true
  try {
    const r = await api.put(`/metrics/extension/target?month=${month.value}&target=${targetForm.value}`)
    target.value = r.target
    ElMessage.success(`已设置 ${month.value} 人均任务 ${r.target} 元/月，指标已实时刷新`)
    targetDlg.value = false
    load()
  } catch (e) { /* 拦截器已提示 */ }
  finally { savingTarget.value = false }
}

function openDlg() {
  parsed.value = null
  dlg.value = true
}

async function onFileChange(file) {
  uploading.value = true
  parsed.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    parsed.value = await api.post('/sync/upload/extension', fd)   // 铁律 1：只解析预览，不入库
    ElMessage.success(`解析成功：订单 ${parsed.value.orders} 行、人员 ${parsed.value.members} 人，请核对后入库`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { uploading.value = false }
}

async function doImport() {
  importing.value = true
  try {
    const r = await api.post(`/sync/confirm/${parsed.value.batch_id}`)
    ElMessage.success(r.msg)
    dlg.value = false
    parsed.value = null
    load()                                                        // 入库后指标实时生效，自动刷新卡片
  } catch (e) { /* 拦截器已提示 */ }
  finally { importing.value = false }
}

onMounted(load)
</script>

<style scoped>
.stat-row { margin-bottom: 12px; }
.stat-card { text-align: center; }
.stat-label { font-size: 12px; color: #909399; }
.stat-value { font-size: 22px; font-weight: 600; margin: 4px 0 2px; }
.stat-sub { font-size: 12px; color: #909399; margin-bottom: 4px; }
.target-tip { color: #909399; font-size: 13px; margin-left: 8px; }
.target-tip.editable { cursor: pointer; color: #1a6fdb; }
</style>
