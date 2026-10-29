<template>
  <div class="cr-page">
    <!-- ============ 顶部筛选栏 ============ -->
    <div class="toolbar">
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false"
                      style="width:130px" @change="load" />
      <el-select v-model="district" style="width:130px" @change="load">
        <el-option label="全部区县" value="__all__" />
        <el-option v-for="d in districtOptions" :key="d" :label="d" :value="d" />
      </el-select>
      <el-radio-group v-model="viewMode" size="small">
        <el-radio-button value="grid">按网格</el-radio-button>
        <el-radio-button value="dim">按维度</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <span class="data-tip">
        <el-icon><InfoFilled /></el-icon>
        口径：来单=受理非空；竣工=已施工；有效可施工=来单−撤单−无状态
      </span>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-skeleton v-if="loading && !data.has_data" :rows="10" animated />

    <!-- ============ 空态 ============ -->
    <el-empty v-else-if="!data.has_data" :image-size="120"
              :description="`${month} 暂无装机宽表数据`">
      <el-button type="primary" @click="goSync">去「数据同步」上传装机宽表</el-button>
    </el-empty>

    <template v-else>
      <!-- ============ 全区总览 ============ -->
      <div class="section-title">
        <span class="bar" />全区总览
        <el-tag size="small" type="info" effect="plain">{{ data.district }} · {{ month }}</el-tag>
        <el-tag v-if="data.total.grid_count" size="small" effect="plain">
          {{ data.total.grid_count }} 个标准网格
        </el-tag>
      </div>

      <div class="hero">
        <!-- 主指标：有效竣工率做进度环主色 -->
        <div class="hero-main">
          <div class="hero-label">
            有效竣工率
            <el-tooltip content="已施工 ÷ 有效可施工（剔除撤单与无状态）" placement="top">
              <el-icon class="q"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="hero-ring">
            <el-progress type="dashboard" :percentage="data.total.rate_eff" :width="130"
                         :color="rateColor(data.total.rate_eff)" :stroke-width="10">
              <template #default>
                <div class="ring-num" :style="{ color: rateColor(data.total.rate_eff) }">
                  {{ data.total.rate_eff }}<span class="ring-pct">%</span>
                </div>
                <div class="ring-sub">{{ data.total.done }} / {{ data.total.valid }}</div>
              </template>
            </el-progress>
          </div>
          <div class="hero-nom">
            名义竣工率
            <b :style="{ color: rateColor(data.total.rate_nom) }">{{ data.total.rate_nom }}%</b>
            <span class="muted">（{{ data.total.done }} / {{ data.total.come }} 全部来单）</span>
          </div>
        </div>

        <!-- 六个关键数字，都可点 -->
        <div class="hero-stats">
          <div v-for="s in heroStats" :key="s.key" class="hstat" :class="{ click: s.click }"
               @click="s.click && openDrill('', s.key, s.label)">
            <div class="hstat-label">{{ s.label }}</div>
            <div class="hstat-value" :style="{ color: s.color }">{{ s.value }}</div>
            <div class="hstat-sub">{{ s.sub }}</div>
          </div>
        </div>
      </div>

      <!-- ============ 按网格视图 ============ -->
      <template v-if="viewMode === 'grid'">
        <div class="section-title">
          <span class="bar" />标准网格对比
          <span class="muted-sm">点击任一网格卡片，查看该网格 21 个维度明细</span>
        </div>

        <div class="grid-cards">
          <div v-for="g in data.grids" :key="g.grid" class="gcard"
               :class="{ unknown: g.is_unknown }" @click="openGridDetail(g)">
            <div class="gcard-top">
              <span class="gcard-name">{{ shortGrid(g.grid) }}</span>
              <el-tag v-if="g.is_unknown" size="small" type="info" effect="plain">未映射</el-tag>
              <span v-else class="gcard-rank" :class="rankClass(g)">{{ rankOf(g) }}</span>
            </div>

            <!-- 未映射组样本太小（通常 <20 单），画圆环会出现 100% 像最优网格的误导，
                 改成就地展示「已施工/来单」，不给百分比排名 -->
            <div v-if="g.is_unknown" class="gcard-ring">
              <div class="unknown-box">
                <div class="ub-num">{{ g.done }}<span class="ub-sep">/</span>{{ g.come }}</div>
                <div class="ub-lb">已施工 / 来单</div>
                <div class="ub-note">样本过小，不参与排名</div>
              </div>
            </div>
            <div v-else class="gcard-ring">
              <el-progress type="dashboard" :percentage="g.rate_eff" :width="96"
                           :color="rateColor(g.rate_eff)" :stroke-width="8">
                <template #default>
                  <div class="g-ring-num" :style="{ color: rateColor(g.rate_eff) }">{{ g.rate_eff }}%</div>
                  <div class="g-ring-lb">有效</div>
                </template>
              </el-progress>
            </div>

            <div class="gcard-kv">
              <div class="kv"><span class="k">来单</span><span class="v">{{ g.come }}</span></div>
              <div class="kv"><span class="k">已施工</span><span class="v ok">{{ g.done }}</span></div>
              <div v-if="!g.is_unknown" class="kv"><span class="k">名义率</span><span class="v">{{ g.rate_nom }}%</span></div>
              <div v-else class="kv"><span class="k">无状态</span><span class="v">{{ g.nostate }}</span></div>
              <div class="kv"><span class="k">在途</span><span class="v warn">{{ g.pending }}</span></div>
              <div class="kv"><span class="k">退撤</span><span class="v danger">{{ g.cancel + g.refund }}</span></div>
              <div class="kv"><span class="k">24h及时</span><span class="v">{{ g.rate_24h }}%</span></div>
            </div>

            <div class="gcard-foot">
              <span class="pair" @click.stop="openDrill(g.grid, 'done', '已施工')">
                已施工 {{ g.done }}
              </span>
              <span class="pair" @click.stop="openDrill(g.grid, 'pending', '处理中')">
                在途 {{ g.pending }}
              </span>
              <span class="pair" @click.stop="openDrill(g.grid, 'cancel', '已撤单')">
                撤单 {{ g.cancel }}
              </span>
            </div>
          </div>
        </div>

        <!-- ============ 完整对照表 ============ -->
        <div class="section-title">
          <span class="bar" />21 维度对照表
          <el-tag size="small" effect="plain">与《工单多维分析》表格列一致</el-tag>
        </div>
        <el-table :data="data.grids" size="small" border class="dim-table"
                  :header-cell-style="{ background: '#f5f7fa', color: '#606266' }"
                  @row-click="openGridDetail">
          <el-table-column prop="grid" label="标准网格" min-width="120" fixed>
            <template #default="{ row }">
              <span class="grid-cell" :class="{ unknown: row.is_unknown }">{{ shortGrid(row.grid) }}</span>
            </template>
          </el-table-column>
          <el-table-column v-for="c in countCols" :key="c.key" :label="c.label" width="82" align="center">
            <template #header>
              <el-tooltip :content="c.hint" placement="top">
                <span class="th">{{ c.label }}</span>
              </el-tooltip>
            </template>
            <template #default="{ row }">
              <span class="num-cell" @click.stop="openDrill(row.grid, c.key, c.label)">{{ row[c.key] }}</span>
            </template>
          </el-table-column>
          <el-table-column v-for="c in rateCols" :key="c.key" :label="c.label" width="96" align="center">
            <template #header>
              <el-tooltip :content="c.hint" placement="top">
                <span class="th">{{ c.label }}</span>
              </el-tooltip>
            </template>
            <template #default="{ row }">
              <span v-if="row.is_unknown" class="muted-cell">—</span>
              <span v-else class="rate-cell" :style="{ color: rateColor(row[c.key]) }"
                    @click.stop="openDrill(row.grid, c.key, c.label)">
                {{ row[c.key] }}%
              </span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- ============ 按维度视图 ============ -->
      <template v-else>
        <div class="section-title">
          <span class="bar" />维度总览
          <span class="muted-sm">每个数字都可点击查看明细</span>
        </div>

        <div class="dim-group" v-for="grp in dimGroups" :key="grp.title">
          <div class="dim-group-title">
            {{ grp.title }}
            <span class="muted-sm">{{ grp.desc }}</span>
          </div>
          <div class="dim-cards">
            <div v-for="c in grp.items" :key="c.key" class="dcard" @click="openDrill('', c.key, c.label)">
              <div class="dcard-label">
                {{ c.label }}
                <el-tooltip :content="c.hint" placement="top">
                  <el-icon class="q"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
              <div class="dcard-value" :style="{ color: c.type === 'rate' ? rateColor(data.total[c.key]) : '' }">
                {{ c.type === 'rate' ? data.total[c.key] + '%' : data.total[c.key] }}
              </div>
              <div class="dcard-sub">{{ c.sub }}</div>
              <div v-if="c.type === 'rate'" class="dcard-bar">
                <div class="dcard-bar-in" :style="{ width: Math.min(data.total[c.key], 100) + '%',
                                                    background: rateColor(data.total[c.key]) }" />
              </div>
            </div>
          </div>
        </div>

        <!-- 分网格明细矩阵 -->
        <div class="section-title">
          <span class="bar" />分网格分布
          <span class="muted-sm">同一维度在 7 个网格上的对比</span>
        </div>
        <div class="dim-cards">
          <div v-for="c in data.cols" :key="c.key" class="mgcard">
            <div class="mgcard-title">{{ c.label }}</div>
            <div v-for="g in data.grids" :key="g.grid" class="mgrow"
                 @click="openDrill(g.grid, c.key, c.label)">
              <span class="mgname">{{ shortGrid(g.grid) }}</span>
              <div class="mgbar">
                <div class="mgbar-in" :style="{ width: barWidth(g, c) + '%',
                                                background: c.type === 'rate' ? rateColor(g[c.key]) : '#409eff' }" />
              </div>
              <span class="mgval" :style="{ color: c.type === 'rate' ? rateColor(g[c.key]) : '#303133' }">
                {{ c.type === 'rate' ? g[c.key] + '%' : g[c.key] }}
              </span>
            </div>
          </div>
        </div>
      </template>

      <!-- ============ 投诉区块（占位） ============ -->
      <div class="section-title">
        <span class="bar" />投诉工单维度
        <el-tag size="small" type="warning" effect="plain">数据源待补</el-tag>
      </div>
      <div class="placeholder">
        <el-icon class="ph-icon"><WarningFilled /></el-icon>
        <div class="ph-body">
          <div class="ph-title">投诉宽表缺少网格与及时性字段，暂无法按标准网格统计</div>
          <div class="ph-text">
            当前投诉宽表 <code>grid</code>、<code>is_8h</code>、<code>is_24h</code>、
            <code>is_solved</code> 四列全表为空（{{ complaintCount }} 条记录均无值），
            因此「投诉数 / 已消单 / 8h及时 / 24h及时 / 调度转派」按网格算不出来。
          </div>
          <div class="ph-text">
            <b>需要省公司在投诉宽表导出时带上：</b>
            ① 网格（grid）② 8小时及时标识 ③ 24小时及时标识
            ④ 是否解决 / 归档满意度 / 是否高品质 ⑤ 调度转派次数。
            补齐后本区块可直接接入，无需改代码。
          </div>
        </div>
      </div>
    </template>

    <!-- ============ 钻取明细抽屉 ============ -->
    <el-drawer v-model="drill.show" size="72%" :destroy-on-close="false">
      <template #header>
        <div class="drill-head">
          <span class="drill-title">
            {{ drill.grid ? shortGrid(drill.grid) : '全区' }} ·
            <b>{{ drill.label }}</b>
          </span>
          <el-tag size="small" type="primary" effect="plain">共 {{ drill.total }} 单</el-tag>
        </div>
      </template>

      <div class="drill-toolbar">
        <el-input v-model="drill.keyword" placeholder="搜工单号 / 处理人 / 小区 / 号码" clearable
                  style="width:300px" :prefix-icon="Search" @keyup.enter="loadDrill" @clear="loadDrill" />
        <el-button size="small" @click="loadDrill">查询</el-button>
        <div class="spacer" />
        <span class="muted-sm">按受理时间倒序</span>
      </div>

      <el-table :data="drill.rows" size="small" border height="calc(100vh - 260px)"
                v-loading="drill.loading" :row-class-name="drillRowClass">
        <el-table-column type="index" label="#" width="50" :index="i => (drill.page - 1) * drill.pageSize + i + 1" />
        <el-table-column prop="no" label="工单号" width="205" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)" effect="light">{{ row.status || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="grid" label="网格" width="110">
          <template #default="{ row }">{{ shortGrid(row.grid) }}</template>
        </el-table-column>
        <el-table-column prop="handler" label="处理人" width="90" show-overflow-tooltip />
        <el-table-column prop="community" label="小区" min-width="150" show-overflow-tooltip />
        <el-table-column prop="number" label="服务号码" width="115" />
        <el-table-column prop="theme" label="业务主题" min-width="130" show-overflow-tooltip />
        <el-table-column prop="accept_time" label="受理时间" width="130" />
        <el-table-column prop="completion_time" label="完工时间" width="130" />
      </el-table>

      <div class="drill-pager">
        <el-pagination background layout="total, sizes, prev, pager, next, jumper"
                       :total="drill.total" :current-page="drill.page" :page-size="drill.pageSize"
                       :page-sizes="[50, 100, 200]" @current-change="onPage" @size-change="onSize" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Search, InfoFilled, QuestionFilled, WarningFilled } from '@element-plus/icons-vue'
import api from '../api'

const router = useRouter()

const month = ref(new Date().toISOString().slice(0, 7))
const district = ref('洪湖市')          // 默认洪湖市（本项目主战场），可切「全部区县」
const viewMode = ref('grid')
const loading = ref(false)
const data = ref({ has_data: false, cols: [], grids: [], total: {}, unmapped: {} })

const districtOptions = ['洪湖市', '监利市', '公安县', '松滋市', '石首市', '江陵县', '荆州区', '沙市区', '荆州开发区']

// axios 拦截器会过滤掉空串参数（见 api.js），所以「全部区县」用哨兵值 __all__，
// 发请求前再转成 ''，否则后端收到的是缺省空值、行为不明确。
const distParam = () => (district.value === '__all__' ? '' : district.value)

const countCols = computed(() => data.value.cols.filter(c => c.type === 'count'))
const rateCols = computed(() => data.value.cols.filter(c => c.type === 'rate'))

// 比率色阶：>=90 深绿 / >=70 绿 / >=50 橙 / <50 红
const rateColor = r => {
  const v = Number(r) || 0
  if (v >= 92) return '#3f9c35'
  if (v >= 80) return '#67c23a'
  if (v >= 65) return '#e6a23c'
  return '#f56c6c'
}
const statusType = s => {
  if (!s) return 'info'
  if (s.includes('施工') || s.includes('竣工') || s.includes('完工')) return 'success'
  if (s.includes('撤') || s.includes('退')) return 'danger'
  if (s.includes('处理') || s.includes('待')) return 'warning'
  return 'info'
}
// 「洪湖新堤网格」→「新堤」
const shortGrid = g => {
  if (!g) return '—'
  if (g.startsWith('未知')) return '未映射'
  return g.replace(/^洪湖/, '').replace(/网格$/, '')
}

// ---- 全区六个关键数字 ----
const heroStats = computed(() => {
  const t = data.value.total
  if (!t.come) return []
  return [
    { key: 'come', label: '来单', value: t.come, sub: '全部受理', color: '#303133', click: true },
    { key: 'done', label: '已施工', value: t.done, sub: `名义 ${t.rate_nom}%`, color: '#67c23a', click: true },
    { key: 'pending', label: '在途压单', value: t.pending, sub: `压单率 ${t.rate_pend}%`, color: '#e6a23c', click: true },
    { key: 'rate_re', label: '退撤单', value: t.cancel + t.refund, sub: `退撤率 ${t.rate_re}%`, color: '#f56c6c', click: true },
    { key: 'rate_24h', label: '24h及时率', value: t.rate_24h + '%', sub: `${t.h24} / ${t.done} 单`, color: rateColor(t.rate_24h), click: true },
    { key: 'rate_perf', label: '履约率', value: t.rate_perf + '%', sub: `${t.perform} / ${t.done} 单`, color: rateColor(t.rate_perf), click: true },
  ]
})

// ---- 按维度分组 ----
const dimGroups = computed(() => {
  const t = data.value.total
  if (!t.come) return []
  const pick = k => data.value.cols.find(c => c.key === k) || {}
  const mk = (k, sub) => {
    const c = pick(k)
    return { ...c, sub }
  }
  return [
    {
      title: '工单量',
      desc: '这些数字是分母，先看总量再看比率',
      items: [
        mk('come', '受理时间非空'),
        mk('done', '裁决后已施工'),
        mk('cancel', '客户/系统撤单'),
        mk('refund', '退回重派'),
        mk('pending', '待预约/待施工等'),
        mk('nostate', '未进入流程'),
      ],
    },
    {
      title: '及时性与履约',
      desc: '分母为「已施工」，只在完工单内计算',
      items: [mk('h8', '8 小时内完成'), mk('h24', '24 小时内完成'), mk('perform', '履约(新)判定为是')],
    },
    {
      title: '竣工率',
      desc: '名义率看全量，有效率看可施工',
      items: [mk('rate_nom', '已施工 ÷ 来单'), mk('rate_eff', '已施工 ÷ 有效可施工')],
    },
    {
      title: '风险与消单',
      desc: '压单、退撤、消单三个口径',
      items: [
        mk('rate_pend', '处理中 ÷ 有效可施工'),
        mk('rate_re', '(退+撤) ÷ 来单'),
        mk('rate_can', '撤单 ÷ 来单'),
        mk('rate_clear', '(施工+撤+退) ÷ 来单'),
      ],
    },
    {
      title: '及时率与履约率',
      desc: '三项均以「已施工」为分母',
      items: [
        mk('rate_8h', '8h及时 ÷ 已施工'),
        mk('rate_24h', '24h及时 ÷ 已施工'),
        mk('rate_perf', '履约 ÷ 已施工'),
      ],
    },
  ]
})

const complaintCount = ref(0)

// ---- 网格排名 ----
const rankOf = g => {
  const list = data.value.grids.filter(x => !x.is_unknown).slice().sort((a, b) => b.rate_eff - a.rate_eff)
  return list.findIndex(x => x.grid === g.grid) + 1
}
const rankClass = g => {
  const r = rankOf(g)
  if (r === 1) return 'r1'
  if (r <= 3) return 'r3'
  return ''
}

// 条形宽度：计数型按该维度最大值归一，比率型直接用作宽度
const barWidth = (g, c) => {
  if (c.type === 'rate') return Math.min(g[c.key], 100)
  const max = Math.max(...data.value.grids.map(x => Number(x[c.key]) || 0), 1)
  return Math.round((Number(g[c.key]) || 0) / max * 100)
}

// ---- 加载 ----
async function load() {
  loading.value = true
  try {
    const res = await api.post('/metrics/completion/multidim', { month: month.value, district: distParam() })
    data.value = res || { has_data: false, cols: [], grids: [], total: {} }
    if (data.value.has_data) loadComplaintCount()
  } catch (e) {
    if (e?.name !== 'CanceledError') ElMessage.error('加载多维分析失败：' + (e?.response?.data?.detail || e.message))
    data.value = { has_data: false, cols: [], grids: [], total: {} }
  } finally {
    loading.value = false
  }
}

// 投诉条数：只用来在占位说明里写「N 条记录均无值」，取失败不影响主流程
async function loadComplaintCount() {
  try {
    const r = await api.get('/metrics/wide/summary', { params: { month: month.value, district: distParam() } })
    complaintCount.value = r?.complaint?.total || r?.complaint_count || 0
  } catch (e) { complaintCount.value = 0 }
}

function goSync() {
  router.push('/sync')          // 路由表里挂的是 'sync'（见 router.js:24），不是 data-sync
}

// ---- 钻取 ----
const drill = reactive({
  show: false, grid: '', label: '', metric: 'come', keyword: '',
  rows: [], total: 0, page: 1, pageSize: 50, loading: false,
})

function openDrill(grid, metric, label) {
  drill.grid = grid || ''
  drill.metric = metric || 'come'
  drill.label = label || '来单'
  drill.keyword = ''
  drill.page = 1
  drill.show = true
  loadDrill()
}

function openGridDetail(g) {
  openDrill(g.grid, 'come', '全部来单')
}

async function loadDrill() {
  drill.loading = true
  try {
    const r = await api.post('/metrics/completion/drill', {
      month: month.value,
      district: distParam(),
      grid: drill.grid,
      metric: drill.metric,
      keyword: drill.keyword,
      page: drill.page,
      page_size: drill.pageSize,
    })
    drill.rows = r.rows || []
    drill.total = r.total || 0
  } catch (e) {
    if (e?.name !== 'CanceledError') ElMessage.error('加载明细失败：' + (e?.response?.data?.detail || e.message))
    drill.rows = []
    drill.total = 0
  } finally {
    drill.loading = false
  }
}

function onPage(p) { drill.page = p; loadDrill() }
function onSize(s) { drill.pageSize = s; drill.page = 1; loadDrill() }

// 冲突单高亮：跨源状态不一致时给行加底色，方便一眼看出哪条要人工核
const drillRowClass = ({ row }) => (row.status_conflict ? 'row-conflict' : '')

onMounted(load)
</script>

<style scoped>
.cr-page { padding: 4px; }

/* ---- 工具栏 ---- */
.toolbar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: #fff; border-radius: 10px; padding: 12px 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .06); margin-bottom: 16px;
}
.spacer { flex: 1; }
.data-tip {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; color: #909399;
}
.muted { color: #909399; font-size: 12px; }
.muted-sm { color: #a8abb2; font-size: 12px; font-weight: 400; margin-left: 6px; }

/* ---- 区块标题 ---- */
.section-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: #303133;
  margin: 18px 0 12px;
}
.section-title .bar {
  width: 3px; height: 15px; border-radius: 2px;
  background: linear-gradient(180deg, #409eff, #79bbff);
}

/* ---- 全区总览 ---- */
.hero {
  display: flex; gap: 16px; flex-wrap: wrap;
  background: #fff; border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .06);
}
.hero-main {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-width: 210px; padding-right: 20px; border-right: 1px dashed #ebeef5;
}
.hero-label { font-size: 13px; color: #606266; display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
.q { font-size: 13px; color: #c0c4cc; cursor: help; }
.ring-num { font-size: 26px; font-weight: 700; line-height: 1.1; }
.ring-pct { font-size: 13px; font-weight: 500; margin-left: 1px; }
.ring-sub { font-size: 11px; color: #909399; margin-top: 2px; }
.hero-nom { font-size: 12px; color: #606266; margin-top: 6px; text-align: center; }
.hero-nom b { font-size: 15px; margin: 0 3px; }

.hero-stats {
  flex: 1; display: grid; gap: 10px; min-width: 420px;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
}
.hstat {
  padding: 10px 12px; border-radius: 8px; background: #fafbfc;
  border: 1px solid #f0f2f5; transition: all .18s;
}
.hstat.click { cursor: pointer; }
.hstat.click:hover {
  background: #ecf5ff; border-color: #b3d8ff; transform: translateY(-1px);
  box-shadow: 0 3px 8px rgba(64, 158, 255, .12);
}
.hstat-label { font-size: 12px; color: #909399; }
.hstat-value { font-size: 22px; font-weight: 700; line-height: 1.25; }
.hstat-sub { font-size: 11px; color: #a8abb2; margin-top: 1px; }

/* ---- 网格卡片 ---- */
.grid-cards {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(238px, 1fr));
}
.gcard {
  background: #fff; border-radius: 12px; padding: 14px 16px 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .06);
  border: 1px solid #f0f2f5; cursor: pointer;
  transition: all .2s; position: relative; overflow: hidden;
}
.gcard::before {
  content: ''; position: absolute; left: 0; top: 0; width: 3px; height: 100%;
  background: linear-gradient(180deg, #409eff, #79bbff);
}
.gcard:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(64, 158, 255, .15); border-color: #b3d8ff; }
.gcard.unknown { background: #fafafa; }
.gcard.unknown::before { background: #dcdfe6; }

.gcard-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.gcard-name { font-size: 14px; font-weight: 600; color: #303133; }
.gcard-rank {
  font-size: 11px; color: #909399; background: #f4f4f5;
  border-radius: 9px; padding: 1px 7px;
}
.gcard-rank.r1 { background: #fdf6ec; color: #e6a23c; font-weight: 600; }
.gcard-rank.r3 { background: #f0f9eb; color: #67c23a; }

.gcard-ring { display: flex; justify-content: center; margin: 2px 0 6px; }
.g-ring-num { font-size: 18px; font-weight: 700; }
.unknown-box { text-align: center; padding: 20px 0 12px; }
.ub-num { font-size: 24px; font-weight: 700; color: #909399; }
.ub-sep { margin: 0 3px; color: #dcdfe6; font-weight: 400; }
.ub-lb { font-size: 11px; color: #a8abb2; }
.ub-note { font-size: 10px; color: #c0c4cc; margin-top: 3px; }
.g-ring-lb { font-size: 10px; color: #909399; }

.gcard-kv {
  display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px;
  font-size: 12px; padding-top: 6px; border-top: 1px solid #f5f7fa;
}
.kv { display: flex; justify-content: space-between; }
.kv .k { color: #909399; }
.kv .v { color: #303133; font-weight: 500; }
.kv .v.ok { color: #67c23a; }
.kv .v.warn { color: #e6a23c; }
.kv .v.danger { color: #f56c6c; }

.gcard-foot {
  display: flex; gap: 6px; margin-top: 8px; padding-top: 8px;
  border-top: 1px dashed #f0f2f5;
}
.pair {
  flex: 1; text-align: center; font-size: 11px; color: #606266;
  background: #fafbfc; border-radius: 5px; padding: 3px 0;
  transition: all .15s;
}
.pair:hover { background: #ecf5ff; color: #409eff; }

/* ---- 对照表 ---- */
.dim-table :deep(.el-table__row) { cursor: pointer; }
.grid-cell { font-weight: 600; color: #303133; }
.grid-cell.unknown { color: #a8abb2; font-weight: 400; }
.th { border-bottom: 1px dashed #c0c4cc; cursor: help; }
.num-cell { display: inline-block; min-width: 30px; cursor: pointer; }
.num-cell:hover { color: #409eff; text-decoration: underline; }
.rate-cell { font-weight: 600; cursor: pointer; }
.muted-cell { color: #c0c4cc; }
.rate-cell:hover { text-decoration: underline; }

/* ---- 维度卡片 ---- */
.dim-group { margin-bottom: 18px; }
.dim-group-title { font-size: 13px; font-weight: 600; color: #606266; margin-bottom: 8px; }
.dim-cards {
  display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
}
.dcard {
  background: #fff; border-radius: 10px; padding: 12px 14px;
  border: 1px solid #f0f2f5; cursor: pointer; transition: all .18s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .04);
}
.dcard:hover { border-color: #b3d8ff; box-shadow: 0 4px 12px rgba(64, 158, 255, .14); transform: translateY(-1px); }
.dcard-label { font-size: 12px; color: #909399; display: flex; align-items: center; gap: 3px; }
.dcard-value { font-size: 24px; font-weight: 700; color: #303133; line-height: 1.3; margin: 2px 0; }
.dcard-sub { font-size: 11px; color: #a8abb2; }
.dcard-bar { height: 3px; background: #f0f2f5; border-radius: 2px; margin-top: 6px; overflow: hidden; }
.dcard-bar-in { height: 100%; border-radius: 2px; transition: width .4s; }

/* ---- 分网格矩阵 ---- */
.mgcard {
  background: #fff; border-radius: 10px; padding: 12px 14px;
  border: 1px solid #f0f2f5; box-shadow: 0 1px 2px rgba(0, 0, 0, .04);
}
.mgcard-title { font-size: 12px; font-weight: 600; color: #606266; margin-bottom: 8px; }
.mgrow {
  display: flex; align-items: center; gap: 8px; padding: 3px 0;
  cursor: pointer; border-radius: 4px;
}
.mgrow:hover { background: #f5f9ff; }
.mgname { width: 54px; font-size: 11px; color: #909399; flex-shrink: 0; }
.mgbar { flex: 1; height: 8px; background: #f5f7fa; border-radius: 4px; overflow: hidden; }
.mgbar-in { height: 100%; border-radius: 4px; transition: width .4s; }
.mgval { width: 52px; text-align: right; font-size: 12px; font-weight: 600; flex-shrink: 0; }

/* ---- 投诉占位 ---- */
.placeholder {
  display: flex; gap: 14px; align-items: flex-start;
  background: #fffaf3; border: 1px solid #f8e3c0; border-radius: 10px; padding: 16px 18px;
}
.ph-icon { font-size: 22px; color: #e6a23c; flex-shrink: 0; margin-top: 2px; }
.ph-title { font-size: 14px; font-weight: 600; color: #b88230; margin-bottom: 6px; }
.ph-text { font-size: 12px; color: #8a6d3b; line-height: 1.85; }
.ph-text code {
  background: #fff2e0; padding: 1px 4px; border-radius: 3px;
  font-family: Consolas, Monaco, monospace; font-size: 11px;
}

/* ---- 抽屉 ---- */
.drill-head { display: flex; align-items: center; gap: 10px; }
.drill-title { font-size: 15px; color: #303133; }
.drill-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.drill-pager { display: flex; justify-content: flex-end; margin-top: 12px; }
:deep(.row-conflict) { background: #fef0f0 !important; }

@media (max-width: 768px) {
  .hero-main { border-right: none; border-bottom: 1px dashed #ebeef5; padding: 0 0 12px; width: 100%; }
  .hero-stats { min-width: 100%; }
  .data-tip { display: none; }
}
</style>
