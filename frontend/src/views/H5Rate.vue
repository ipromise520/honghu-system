<template>
  <div class="rate-page">
    <!-- 顶部工具栏 -->
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <span class="page-title">📈 H5 参与率通报</span>
          <el-tag type="warning" effect="light" size="small">{{ meta.day_label }} · 装维 H5 满意度参与率</el-tag>
          <el-tag type="info" effect="plain" size="small">{{ meta.source }}</el-tag>
        </div>
        <div class="toolbar-right">
          <el-button size="small" type="primary" :loading="busy1" @click="exportFig1">🖼️ 一键输出图1 通报图片</el-button>
          <el-button size="small" type="primary" :loading="busy2" @click="exportFig2">🖼️ 一键输出图2 通报图片</el-button>
        </div>
      </div>
    </el-card>

    <!-- 图1：洪湖市 人员明细 -->
    <el-card shadow="never" class="report-card">
      <div class="card-head">
        <div class="card-title"><b>图1</b> · 洪湖市 装维人员 H5满意度参与率 明细</div>
        <el-button size="small" type="primary" plain :loading="busy1" @click="exportFig1">导出图片</el-button>
      </div>
      <div class="scroll-x">
        <div ref="fig1Ref" class="shot">
          <table class="rt">
            <caption class="rt-title">
              <span class="cap-left">{{ meta.fig1_title }}</span>
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
                <td class="c-c">{{ fig1.totals.c_back }}</td>
                <td class="c-c">{{ fig1.totals.c_eval }}</td>
                <td class="c-c">{{ pct1(fig1.totals.c_eval / fig1.totals.c_back) }}</td>
                <td class="c-short s-neg">{{ fig1.totals.c_short }}</td>
                <td class="c-sat">{{ fmtScore(fig1.totals.c_score) }}</td>
                <td class="c-name2"></td>
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
          <table v-for="blk in FIG2_BLOCKS" :key="blk.key" class="rt r2">
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
        </div>
      </div>
      <div class="note">
        口径说明：回单量＝回单明细中归属该区县/人员的工单数；测评量＝H5回复详单数；参与率＝测评量÷回单量；
        欠产数＝ROUND(测评量−50%×回单量)；满意度＝0.4×Q24＋0.2×Q25＋0.4×Q26（投诉为 Q27/Q28/Q29）。
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import html2canvas from 'html2canvas'
import rate from '../data/h5_rate.json'

const meta = rate.meta
const fig1 = rate.fig1
const fig2 = rate.fig2
const fig1Ref = ref(null)
const fig2Ref = ref(null)
const busy1 = ref(false)
const busy2 = ref(false)

const FIG1_COLS = [
  { key: 'leader', label: '网格班组长', cls: 'c-grid' },
  { key: 'name', label: '装维人员姓名', cls: 'c-name' },
  { key: 'i_back', label: '装机回单量<br>（当月）', fmt: 'int', cls: 'c-i' },
  { key: 'i_eval', label: '装机H5<br>已测评', fmt: 'int', cls: 'c-i' },
  { key: 'i_rate', label: '参与率', fmt: 'pctInt', cls: 'c-i' },
  { key: 'i_short', label: '50%参与率<br>欠产数', fmt: 'int', cls: 'c-short' },
  { key: 'i_score', label: 'H5装机满意度', fmt: 'score', cls: 'c-sat' },
  { key: 'c_back', label: '投诉回单量<br>（当月）', fmt: 'int', cls: 'c-c' },
  { key: 'c_eval', label: '故障H5<br>已测评', fmt: 'int', cls: 'c-c' },
  { key: 'c_rate', label: '参与率', fmt: 'pctInt', cls: 'c-c' },
  { key: 'c_short', label: '50%参与率<br>欠产数', fmt: 'int', cls: 'c-short' },
  { key: 'c_score', label: 'H5故障满意度', fmt: 'score', cls: 'c-sat' },
  { key: 'name2', src: 'name', label: '装维人员姓名', cls: 'c-name2' },
]

const G1 = [
  { key: 'i_back', label: '回单量', fmt: 'int', cls: 'c-i' },
  { key: 'i_eval', label: 'H5装机测评数', fmt: 'int', cls: 'c-i' },
  { key: 'i_sat', label: '装机满意度', fmt: 'score2', cls: 'c-sat' },
  { key: 'q24', label: 'Q24上门及时性40%', fmt: 'score2', cls: 'c-q' },
  { key: 'q25', label: 'Q25人员服务20%', fmt: 'score2', cls: 'c-q' },
  { key: 'q26', label: 'Q26安装效果40%', fmt: 'score2', cls: 'c-q' },
  { key: 'fttr', label: 'FTTR满意度', fmt: 'score2', cls: 'c-q' },
  { key: 'i_rate', label: '装机参与率', fmt: 'pct2', cls: 'c-i' },
  { key: 'i_affect', label: '装机影响客户感知工单数量', fmt: 'int', cls: 'c-af' },
  { key: 'i_affect_rate', label: '装机影响客户感知工单数占比0.3%', fmt: 'pct2', cls: 'c-af' },
  { key: 'i_diff', label: '差额', fmt: 'int', cls: 'c-diff' },
  { key: 'bm', label: '安装上门及时方面不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bn', label: '工作人员服务不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bo', label: '服务安装效果不满意', fmt: 'int', cls: 'c-no' },
  { key: 'bp', label: '服务人员形象提升', fmt: 'int', cls: 'c-no' },
]
const G2 = [
  { key: 'c_back', label: '回单量', fmt: 'int', cls: 'c-c' },
  { key: 'c_eval', label: 'H5投诉测评数', fmt: 'int', cls: 'c-c' },
  { key: 'c_sat', label: '投诉满意度', fmt: 'score2', cls: 'c-sat' },
  { key: 'q27', label: 'Q27上门及时性40%', fmt: 'score2', cls: 'c-q' },
  { key: 'q28', label: 'Q28人员服务20%', fmt: 'score2', cls: 'c-q' },
  { key: 'q29', label: 'Q29问题解决情况40%', fmt: 'score2', cls: 'c-q' },
  { key: 'c_rate', label: '投诉参与率', fmt: 'pct2', cls: 'c-c' },
  { key: 'c_affect', label: '投诉影响客户感知工单数量', fmt: 'int', cls: 'c-af' },
  { key: 'c_affect_rate', label: '投诉影响客户感知工单数占比0.3%', fmt: 'pct2', cls: 'c-af' },
  { key: 'c_diff', label: '差额', fmt: 'int', cls: 'c-diff' },
]
const FIG2_BLOCKS = [
  { key: 'day', title1: '10月装机H5满意度（18日）', title2: '10月故障H5满意度（18日）' },
  { key: 'month', title1: '10月截止18日装机H5满意度（当月）', title2: '10月截止18日故障H5满意度（当月）' },
]

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
  if (fmtType === 'pct1') return pct1(v)
  if (fmtType === 'pctInt') return pctInt(v)
  if (fmtType === 'score') return fmtScore(v)
  return v === null || v === undefined ? '—' : String(v)
}
function fmt2(col, row) {
  const v = row[col.key]
  if (col.fmt === 'int') return v === null || v === undefined ? '0' : String(v)
  if (col.fmt === 'pct2') return pct2(v)
  if (col.fmt === 'score2') return fmtScore(v)
  return v
}
// 负欠产标红
function cellCls(col, row) {
  if (col.key.endsWith('_short')) {
    const v = row[col.key]
    return v < 0 ? 's-neg' : (v > 0 ? 's-pos' : '')
  }
  return ''
}

async function shot(el, name) {
  const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2, useCORS: true })
  const a = document.createElement('a')
  a.download = name
  a.href = canvas.toDataURL('image/png')
  a.click()
}
async function exportFig1() {
  if (!fig1Ref.value) return
  busy1.value = true
  try {
    await shot(fig1Ref.value, `H5参与率通报_图1_洪湖市明细_${meta.day_label}.png`)
    ElMessage.success('图1 通报图片已导出')
  } catch (e) { ElMessage.error('导出失败：' + (e?.message || e)) }
  finally { busy1.value = false }
}
async function exportFig2() {
  if (!fig2Ref.value) return
  busy2.value = true
  try {
    await shot(fig2Ref.value, `H5参与率通报_图2_区县汇总_${meta.day_label}.png`)
    ElMessage.success('图2 通报图片已导出')
  } catch (e) { ElMessage.error('导出失败：' + (e?.message || e)) }
  finally { busy2.value = false }
}
</script>

<style scoped>
.rate-page { display: flex; flex-direction: column; gap: 12px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.page-title { font-size: 17px; font-weight: 700; }
.toolbar-right { display: flex; align-items: center; gap: 8px; }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.card-title { font-size: 14px; color: #303133; }
.note { margin-top: 10px; font-size: 12px; color: #909399; line-height: 1.7; }
.scroll-x { overflow-x: auto; }

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
table.rt td.c-q { background: #f2f7fd; }
table.rt td.c-af { background: #f7f7f7; }
table.rt td.c-no { background: #fbfbfb; }
table.rt tfoot td { background: #ffd966; font-weight: 700; }
table.rt tfoot td.sum-label { background: #ffd966; }
table.rt .s-neg { color: #e02020; font-weight: 700; }
table.rt .s-pos { color: #0f9d58; font-weight: 700; }
:deep(.el-card__body) { padding: 14px; }
</style>
