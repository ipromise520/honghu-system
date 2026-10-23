<template>
  <div class="rate-page" v-loading="loading">
    <!-- 顶部工具栏 -->
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <span class="page-title">📈 H5 参与率通报</span>
          <el-tag type="warning" effect="light" size="small">{{ meta.day_label }} · 装维 H5 满意度参与率</el-tag>
          <el-tag type="info" effect="plain" size="small">{{ meta.source }}</el-tag>
          <el-tooltip content="勾选后显示：装机预计奖励、故障预计奖励、奖励合计；取消勾选则隐藏（默认隐藏）" placement="bottom">
            <el-checkbox v-model="showReward" size="small" class="reward-toggle">显示奖励列</el-checkbox>
          </el-tooltip>
        </div>
        <div class="toolbar-right">
          <el-button size="small" type="primary" :loading="busy1" @click="exportFig1">🖼️ 一键输出图1 通报图片</el-button>
          <el-button size="small" type="primary" :loading="busy2" @click="exportFig2">🖼️ 一键输出图2 通报图片</el-button>
        </div>
      </div>
    </el-card>

    <el-empty v-if="!loading && !hasData" description="暂无数据：请先在「数据同步 → H5满意度通报」上传平台导出文件并确认" />

    <template v-if="hasData">
    <!-- 图1：洪湖市 人员明细 -->
    <el-card shadow="never" class="report-card">
      <div class="card-head">
        <div class="card-title"><b>图1</b> · 洪湖市 装维人员 H5满意度参与率 明细<span v-if="meta.reward_note" class="reward-note">{{ meta.reward_note }}</span></div>
        <el-button size="small" type="primary" plain :loading="busy1" @click="exportFig1">导出图片</el-button>
      </div>
      <!-- 无人员明细（旧版导出无人员粒度数据）时给出明确指引，替代空白表格 -->
      <el-alert v-if="!fig1.rows.length" :title="fig1Hint || '当前无人员明细数据'"
                type="warning" :closable="false" show-icon class="fig1-empty" />
      <div v-else class="scroll-x">
        <div ref="fig1Ref" class="shot">
          <table class="rt">
            <caption class="rt-title">
              <span class="cap-left">{{ meta.fig1_title }}<span v-if="meta.reward_note" class="reward-note">{{ meta.reward_note }}</span></span>
              <span class="cap-right">{{ meta.day_label }}</span>
            </caption>
            <thead>
              <tr>
                <th v-for="c in FIG1_COLS" :key="c.label + c.key" :class="'h ' + c.cls">
                  <span v-html="c.label"></span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in fig1.rows" :key="i">
                <td v-for="c in FIG1_COLS" :key="c.key" :class="[c.cls, cellCls(c, r)]">
                  <span v-html="fmt(c.fmt, r[c.src || c.key], r, c)"></span>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td class="sum-label" colspan="2">合计</td>
                <td class="c-i">{{ fig1.totals.i_back }}</td>
                <td class="c-i">{{ fig1.totals.i_eval }}</td>
                <td class="c-i">{{ pct1(fig1.totals.i_eval / fig1.totals.i_back) }}</td>
                <td class="c-short s-neg">{{ fig1.totals.i_short }}</td>
                <td class="c-sat">{{ fmtScore(fig1.totals.i_score) }}</td>
                <td v-if="showReward" class="c-rw" :class="numCls(fig1.totals.i_reward)">{{ fig1.totals.i_reward }}</td>
                <td class="c-c">{{ fig1.totals.c_back }}</td>
                <td class="c-c">{{ fig1.totals.c_eval }}</td>
                <td class="c-c">{{ pct1(fig1.totals.c_eval / fig1.totals.c_back) }}</td>
                <td class="c-short s-neg">{{ fig1.totals.c_short }}</td>
                <td class="c-sat">{{ fmtScore(fig1.totals.c_score) }}</td>
                <td v-if="showReward" class="c-rw" :class="numCls(fig1.totals.c_reward)">{{ fig1.totals.c_reward }}</td>
                <td class="c-name2"></td>
                <td v-if="showReward" class="c-rw" :class="numCls(fig1.totals.rw_total)">{{ fig1.totals.rw_total }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </el-card>

    <!-- 图2：区县汇总（18日 + 当月 两张） -->
    <el-card shadow="never" class="report-card">
      <div class="card-head">
        <div class="card-title"><b>图2</b> · 区县 H5满意度参与率 汇总（{{ meta.day_label }} / {{ meta.month_label }}当月）</div>
        <el-button size="small" type="primary" plain :loading="busy2" @click="exportFig2">导出图片</el-button>
      </div>
      <div class="scroll-x">
        <div ref="fig2Ref" class="shot">
          <template v-for="blk in FIG2_BLOCKS" :key="blk.key">
            <table v-if="fig2[blk.key].rows.length" class="rt r2">
            <caption class="rt-title split">
              <span class="cap-left">{{ blk.title1 }}</span>
              <span class="cap-right">{{ blk.title2 }}</span>
            </caption>
            <thead>
              <tr>
                <th class="h c-dist" rowspan="2">区县</th>
                <th class="h c-i" :colspan="G1.length">{{ blk.title1 }}</th>
                <th class="h c-c" :colspan="G2.length">{{ blk.title2 }}</th>
              </tr>
              <tr>
                <th v-for="c in G1" :key="'a' + c.key" :class="'h ' + c.cls">{{ c.label }}</th>
                <th v-for="c in G2" :key="'b' + c.key" :class="'h ' + c.cls">{{ c.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in fig2[blk.key].rows" :key="i">
                <td class="c-dist">{{ r.district }}</td>
                <td v-for="c in G1" :key="'a' + c.key" :class="[c.cls, cellCls(c, r)]">{{ fmt2(c, r) }}</td>
                <td v-for="c in G2" :key="'b' + c.key" :class="[c.cls, cellCls(c, r)]">{{ fmt2(c, r) }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td class="sum-label c-dist">{{ fig2[blk.key].totals.district }}</td>
                <td v-for="c in G1" :key="'a' + c.key" :class="[c.cls, cellCls(c, fig2[blk.key].totals)]">{{ fmt2(c, fig2[blk.key].totals) }}</td>
                <td v-for="c in G2" :key="'b' + c.key" :class="[c.cls, cellCls(c, fig2[blk.key].totals)]">{{ fmt2(c, fig2[blk.key].totals) }}</td>
              </tr>
            </tfoot>
            </table>
            <!-- 当日块无数据（旧版导出仅当月汇总）时隐藏空表并说明，避免展示全 0 的误导表格 -->
            <div v-else class="note blk-empty">{{ blk.title1 }} / {{ blk.title2 }}：当前快照无当日数据（旧版导出仅含当月汇总）。</div>
          </template>
        </div>
      </div>
      <div class="note">
        数据来源：「数据同步 → H5满意度通报」最新确认快照（新版平台导出，后端本地聚合）。
        口径说明：回单量＝回单明细中归属该区县/人员的工单数；测评量＝H5回复详单数；参与率＝测评量÷回单量；
        欠产数＝ROUND(测评量−50%×回单量)；满意度＝0.4×Q24＋0.2×Q25＋0.4×Q26（投诉为 Q27/Q28/Q29）；
        FTTR 满意度新版导出无明细数据，显示「—」。<br>
        预计奖励规则（参数以后端 <code>h5.py</code> 顶部常量为准，可调整）：参与率 ≥ {{ (meta.reward?.rate_min ?? 0.5) * 100 }}%（达标）时，
        「装机预计奖励」＝装机H5已测评 × {{ meta.reward?.install_unit ?? 20 }} 元/件，「故障预计奖励」＝故障H5已测评 × {{ meta.reward?.complaint_unit ?? 30 }} 元/件；
        参与率未达标时，「装机预计奖励」＝装机50%参与率欠产数 × {{ meta.reward?.install_unit ?? 20 }} 元/件，「故障预计奖励」＝投诉50%参与率欠产数 × {{ meta.reward?.complaint_unit ?? 30 }} 元/件
        （欠产数为负即扣罚）；无回单不计奖；「奖励合计」＝装机预计奖励＋故障预计奖励。
        奖励列（装机预计奖励、故障预计奖励、奖励合计）默认隐藏，勾选页面顶部「显示奖励列」后展示（图1/图2 同步生效，导出图片同步包含）。
        网格班组长由花名册（extension_members）及洪湖网格长 mappings 补齐。
      </div>
    </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import html2canvas from 'html2canvas'
import api from '../api'

// 数据来自「数据同步 → H5满意度通报」最新确认快照（后端本地聚合），不再使用静态 JSON
const payload = ref(null)
const loading = ref(false)
const meta = computed(() => payload.value?.meta || {})
const fig1 = computed(() => payload.value?.fig1 || { rows: [], totals: {} })
// 图1 空态说明（后端 /h5/rate 返回）：旧版导出无人员粒度数据时提示应传哪种导出
const fig1Hint = computed(() => payload.value?.fig1_hint || '')
const fig2 = computed(() => payload.value?.fig2 || { day: { rows: [], totals: {} }, month: { rows: [], totals: {} } })
const hasData = computed(() => !!payload.value?.has_data)

async function loadRate() {
  loading.value = true
  try {
    payload.value = await api.get('/h5/rate')
  } catch (e) { /* 错误提示由 api 拦截器统一处理 */ }
  finally { loading.value = false }
}
onMounted(loadRate)

const fig1Ref = ref(null)
const fig2Ref = ref(null)
const busy1 = ref(false)
const busy2 = ref(false)
// 「显示奖励列」勾选框：控制图1/图2 的奖励列（装机预计奖励、故障预计奖励、奖励合计）显隐，默认不勾选（隐藏）
const showReward = ref(false)
const REWARD_COL_KEYS = ['i_reward', 'c_reward', 'rw_total']
const _noReward = cols => cols.filter(c => !REWARD_COL_KEYS.includes(c.key))

const FIG1_COLS_ALL = [
  { key: 'leader', label: '网格班组长', cls: 'c-grid' },
  { key: 'name', label: '装维人员姓名', cls: 'c-name' },
  { key: 'i_back', label: '装机回单量<br>（当月）', fmt: 'int', cls: 'c-i' },
  { key: 'i_eval', label: '装机H5<br>已测评', fmt: 'int', cls: 'c-i' },
  { key: 'i_rate', label: '参与率', fmt: 'pctInt', cls: 'c-i' },
  { key: 'i_short', label: '50%参与率<br>欠产数', fmt: 'int', cls: 'c-short' },
  { key: 'i_score', label: 'H5装机满意度', fmt: 'score', cls: 'c-sat' },
  { key: 'i_reward', label: '装机预计奖励', fmt: 'reward', cls: 'c-rw' },
  { key: 'c_back', label: '投诉回单量<br>（当月）', fmt: 'int', cls: 'c-c' },
  { key: 'c_eval', label: '故障H5<br>已测评', fmt: 'int', cls: 'c-c' },
  { key: 'c_rate', label: '参与率', fmt: 'pctInt', cls: 'c-c' },
  { key: 'c_short', label: '50%参与率<br>欠产数', fmt: 'int', cls: 'c-short' },
  { key: 'c_score', label: 'H5故障满意度', fmt: 'score', cls: 'c-sat' },
  { key: 'c_reward', label: '故障预计奖励', fmt: 'reward', cls: 'c-rw' },
  { key: 'name2', src: 'name', label: '装维人员姓名', cls: 'c-name2' },
  { key: 'rw_total', label: '奖励合计', fmt: 'reward', cls: 'c-rw' },
]
const FIG1_COLS = computed(() => showReward.value ? FIG1_COLS_ALL : _noReward(FIG1_COLS_ALL))

const G1_ALL = [
  { key: 'i_back', label: '回单量', fmt: 'int', cls: 'c-i' },
  { key: 'i_eval', label: 'H5装机测评数', fmt: 'int', cls: 'c-i' },
  { key: 'i_sat', label: '装机满意度', fmt: 'score2', cls: 'c-sat' },
  { key: 'q24', label: 'Q24上门及时性40%', fmt: 'score2', cls: 'c-q' },
  { key: 'q25', label: 'Q25人员服务20%', fmt: 'score2', cls: 'c-q' },
  { key: 'q26', label: 'Q26安装效果40%', fmt: 'score2', cls: 'c-q' },
  { key: 'fttr', label: 'FTTR满意度', fmt: 'score2', cls: 'c-q' },
  { key: 'i_rate', label: '装机参与率', fmt: 'pct2', cls: 'c-i' },
  { key: 'i_reward', label: '装机预计奖励', fmt: 'reward', cls: 'c-rw' },
  { key: 'i_affect', label: '装机影响客户感知工单数量', fmt: 'int', cls: 'c-af' },
  { key: 'i_affect_rate', label: '装机影响客户感知工单数占比0.3%', fmt: 'pct2', cls: 'c-af' },
  { key: 'i_diff', label: '差额', fmt: 'int', cls: 'c-diff' },
  { key: 'bm', label: '安装上门及时方面不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bn', label: '工作人员服务不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bo', label: '服务安装效果不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bp', label: '服务人员形象提升', fmt: 'int', cls: 'c-no' },
]
const G2_ALL = [
  { key: 'c_back', label: '回单量', fmt: 'int', cls: 'c-c' },
  { key: 'c_eval', label: 'H5投诉测评数', fmt: 'int', cls: 'c-c' },
  { key: 'c_sat', label: '投诉满意度', fmt: 'score2', cls: 'c-sat' },
  { key: 'q27', label: 'Q27上门及时性40%', fmt: 'score2', cls: 'c-q' },
  { key: 'q28', label: 'Q28人员服务20%', fmt: 'score2', cls: 'c-q' },
  { key: 'q29', label: 'Q29问题解决情况40%', fmt: 'score2', cls: 'c-q' },
  { key: 'c_rate', label: '投诉参与率', fmt: 'pct2', cls: 'c-c' },
  { key: 'c_reward', label: '故障预计奖励', fmt: 'reward', cls: 'c-rw' },
  { key: 'c_affect', label: '投诉影响客户感知工单数量', fmt: 'int', cls: 'c-af' },
  { key: 'c_affect_rate', label: '投诉影响客户感知工单数占比0.3%', fmt: 'pct2', cls: 'c-af' },
  { key: 'c_diff', label: '差额', fmt: 'int', cls: 'c-diff' },
]
const G1 = computed(() => showReward.value ? G1_ALL : _noReward(G1_ALL))
const G2 = computed(() => showReward.value ? G2_ALL : _noReward(G2_ALL))
// 图2 两张表标题随数据日期动态生成（如「10月装机H5满意度（18日）」/「10月截止18日装机H5满意度（当月）」）
const FIG2_BLOCKS = computed(() => {
  const day = meta.value.day_label || ''
  const mm = day.replace(/^(\d+月).*$/, '$1')
  const dd = day.replace(/^\d+月/, '')
  const month = meta.value.month_label || ''
  return [
    { key: 'day', title1: `${mm}装机H5满意度（${dd}）`, title2: `${mm}故障H5满意度（${dd}）` },
    { key: 'month', title1: `${month}装机H5满意度（当月）`, title2: `${month}故障H5满意度（当月）` },
  ]
})

function pct1(v) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  const s = (v * 100).toFixed(2).replace(/\.?0+$/, '')
  return s + '%'
}
function pct2(v) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  return (v * 100).toFixed(2) + '%'
}
// 图1 明细的参与率按整数百分比展示（与通报参考口径一致）
function pctInt(v) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  return Math.round(v * 100) + '%'
}
function fmtScore(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(2) + '%'
}
function fmt(fmtType, v, row, col) {
  if (fmtType === 'int') {
    // 无装机/投诉数据的空档显示为 0（与通报口径一致）
    return v === null || v === undefined ? '0' : String(v)
  }
  if (fmtType === 'reward') return v === null || v === undefined ? '0' : String(v)
  if (fmtType === 'pct1') return pct1(v)
  if (fmtType === 'pctInt') return pctInt(v)
  if (fmtType === 'score') return fmtScore(v)
  return v === null || v === undefined ? '—' : String(v)
}
function fmt2(col, row) {
  const v = row[col.key]
  if (col.fmt === 'int') return v === null || v === undefined ? '0' : String(v)
  if (col.fmt === 'reward') return v === null || v === undefined ? '0' : String(v)
  if (col.fmt === 'pct2') return pct2(v)
  if (col.fmt === 'score2') return fmtScore(v)
  return v
}
// 数值正负配色：负红、正绿（用于欠产数与预计奖励列）
function numCls(v) {
  if (v === null || v === undefined) return ''
  return v < 0 ? 's-neg' : (v > 0 ? 's-pos' : '')
}
// 负欠产/负奖励标红
function cellCls(col, row) {
  if (col.key.endsWith('_short') || REWARD_COL_KEYS.includes(col.key)) {
    return numCls(row[col.key])
  }
  return ''
}

async function shot(el, name) {
  // html2canvas 宽表导出加固（避免勾选「显示奖励列」后导出的图片缺少右侧奖励列）：
  // 1) windowWidth/Height 显式按元素滚动尺寸指定——默认按 window.innerWidth 布局，宽表右侧列会被裁掉；
  // 2) onclone 放开祖先滚动容器 overflow——防止 .scroll-x 的横向裁剪；
  // 3) 导出期间把表格切为 fixed 布局并按当前列宽固化（colgroup）——消除克隆文档中 auto 布局重排导致的末列丢失；
  // 4) scrollX/Y 归零，避免窗口滚动偏移。
  el.setAttribute('data-shot-root', '1')
  const tables = Array.from(el.querySelectorAll('table'))
  const backups = tables.map(t => {
    const ths = Array.from(t.querySelectorAll('thead th'))
    const colWs = ths.map(th => th.offsetWidth)
    const tblW = t.offsetWidth
    const cg = document.createElement('colgroup')
    colWs.forEach(w => {
      const col = document.createElement('col')
      col.style.width = w + 'px'
      cg.appendChild(col)
    })
    t.insertBefore(cg, t.firstChild)
    return { t, cg, tblW, layout: t.style.tableLayout, width: t.style.width }
  })
  backups.forEach(b => { b.t.style.tableLayout = 'fixed'; b.t.style.width = b.tblW + 'px' })
  try {
    const canvas = await html2canvas(el, {
      backgroundColor: '#ffffff', scale: 2, useCORS: true,
      width: el.scrollWidth, height: el.scrollHeight,
      windowWidth: el.scrollWidth, windowHeight: el.scrollHeight,
      scrollX: 0, scrollY: 0,
      onclone: (doc) => {
        const root = doc.querySelector('[data-shot-root]')
        let p = root && root.parentElement
        while (p && p !== doc.body) {
          p.style.overflowX = 'visible'
          p.style.overflowY = 'visible'
          p.style.maxWidth = 'none'
          p.style.width = 'auto'
          p = p.parentElement
        }
      },
    })
    const a = document.createElement('a')
    a.download = name
    a.href = canvas.toDataURL('image/png')
    a.click()
  } finally {
    backups.forEach(b => {
      b.cg.remove()
      b.t.style.tableLayout = b.layout
      b.t.style.width = b.width
    })
    el.removeAttribute('data-shot-root')
  }
}
async function exportFig1() {
  if (!fig1.value.rows.length) {
    ElMessage.warning(fig1Hint.value || '当前无人员明细数据，无法导出')
    return
  }
  if (!fig1Ref.value) return
  busy1.value = true
  try {
    await shot(fig1Ref.value, `H5参与率通报_图1_洪湖市明细_${meta.value.day_label}.png`)
    ElMessage.success('图1 通报图片已导出')
  } catch (e) { ElMessage.error('导出失败：' + (e?.message || e)) }
  finally { busy1.value = false }
}
async function exportFig2() {
  if (!fig2Ref.value) return
  busy2.value = true
  try {
    await shot(fig2Ref.value, `H5参与率通报_图2_区县汇总_${meta.value.day_label}.png`)
    ElMessage.success('图2 通报图片已导出')
  } catch (e) { ElMessage.error('导出失败：' + (e?.message || e)) }
  finally { busy2.value = false }
}
</script>

<style scoped>
.rate-page { display: flex; flex-direction: column; gap: 12px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.reward-toggle :deep(.el-checkbox__label) { font-size: 12px; }
.page-title { font-size: 17px; font-weight: 700; }
.toolbar-right { display: flex; align-items: center; gap: 8px; }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.card-title { font-size: 14px; color: #303133; }
.reward-note { margin-left: 10px; font-size: 12px; color: #e02020; font-weight: 700; white-space: nowrap; }
.note { margin-top: 10px; font-size: 12px; color: #909399; line-height: 1.7; }
.note code { background: #f2f2f2; padding: 0 4px; border-radius: 3px; font-size: 11px; }
.scroll-x { overflow-x: auto; }
.fig1-empty { margin-bottom: 10px; }
.fig1-empty :deep(.el-alert__title) { font-size: 13px; line-height: 1.6; }
.blk-empty { margin: 4px 0 14px; }

/* 截图表 —— 使用独立表格样式，保证 html2canvas 导出保真 */
.shot { width: max-content; }
table.rt {
  border-collapse: collapse;
  font-size: 12px;
  color: #1f2d3d;
  background: #fff;
  font-family: 'Microsoft YaHei', 'PingFang SC', 'Heiti SC', Arial, sans-serif;
}
table.rt caption.rt-title {
  caption-side: top;
  background: #ffc000;
  color: #1f4e79;
  font-size: 16px;
  font-weight: 700;
  padding: 7px 12px;
  border: 1px solid #d9a300;
  display: table-caption;
  text-align: center;
  position: relative;
}
table.rt caption .cap-right {
  position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
  color: #ff0000; font-weight: 700; font-size: 14px;
}
table.rt.r2 caption { font-size: 13px; }
table.rt.r2 caption .cap-left { position: absolute; left: 40%; }
table.rt.r2 caption .cap-right { left: 78%; right: auto; color: #1f4e79; }

table.rt th, table.rt td {
  border: 1px solid #9bb7d4;
  padding: 3px 6px;
  text-align: center;
  white-space: nowrap;
  line-height: 1.35;
}
table.rt th.h { background: #bdd7ee; color: #1f4e79; font-weight: 700; vertical-align: middle; }
table.rt th.c-sat { background: #c6efce; color: #1f5c2e; }
table.rt th.c-short { background: #fce4d6; color: #974706; }
table.rt th.c-c { background: #f8cbad; color: #833c00; }
table.rt td.c-grid, table.rt th.c-grid { background: #f2f2f2; }
table.rt td.c-name { text-align: left; }
table.rt td.c-name2 { text-align: left; background: #eef3fb; }
table.rt td.c-sat { background: #ebf7ec; }
table.rt td.c-short { background: #fdf3ee; }
table.rt td.c-c { background: #fdf2ec; }
table.rt td.c-rw { background: #fff7e0; }
table.rt th.c-rw { background: #ffe6a3; color: #7a5b00; }
table.rt tfoot td.c-rw { background: #ffd966; color: inherit; }
table.rt td.c-q { background: #f2f7fd; }
table.rt td.c-af { background: #f7f7f7; }
table.rt td.c-no { background: #fbfbfb; }
table.rt tfoot td { background: #ffd966; font-weight: 700; }
table.rt tfoot td.sum-label { background: #ffd966; }
table.rt .s-neg { color: #e02020; font-weight: 700; }
table.rt .s-pos { color: #0f9d58; font-weight: 700; }
:deep(.el-card__body) { padding: 14px; }
</style>
