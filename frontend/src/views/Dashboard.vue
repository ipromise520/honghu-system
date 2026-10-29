<template>
  <div>
    <div class="toolbar">
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" placeholder="选择月份" style="width:150px" @change="load" />
      <el-select v-model="district" style="width:150px" @change="load">
        <el-option v-for="d in districtOptions" :key="d" :label="d" :value="d" />
      </el-select>
      <span class="data-tag" :class="{ wide: hasWide }">{{ hasWide ? '宽表数据' : '演示数据' }} · v{{ buildTime }}</span>
    </div>

    <div class="stat-cards">
      <div v-for="c in cards" :key="c.key" class="stat-card" :class="c.tone">
        <div class="label">{{ c.label }}</div>
        <div class="value">{{ fmt(c.value) }}<span class="unit">{{ c.unit }}</span></div>
        <div v-if="c.sub" class="sub">{{ c.sub }}</div>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :md="10">
        <el-card class="page-card">
          <template #header>
            <div class="card-header">
              <span class="title">
                区县竣工率排名（{{ month }}）
                <el-tag size="small" type="info" style="margin-left:6px">全荆州 {{ districtRanking.length }} 区县 · 不受区县筛选影响</el-tag>
              </span>
            </div>
          </template>
          <div class="rank-table-wrap">
            <el-table :data="districtRanking" size="small" height="100%">
              <el-table-column prop="rank" label="#" width="50" />
              <el-table-column prop="district" label="区县" min-width="90" />
              <el-table-column prop="total" label="受理" min-width="70" />
              <el-table-column prop="completed" label="已施工" min-width="70" />
              <el-table-column label="竣工率" min-width="140">
                <template #default="{ row }">
                  <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="12" />
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="14">
        <el-card class="page-card">
          <template #header><div class="card-header"><span class="title">每日工单趋势（{{ month }}）</span></div></template>
          <div ref="trendRef" class="chart-tall" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="page-card" v-if="hasWide">
      <template #header><div class="card-header"><span class="title">投诉概况（{{ month }}）</span></div></template>
      <el-row :gutter="16">
        <el-col :xs="24" :md="8">
          <div ref="catRef" class="chart-mid" />
        </el-col>
        <el-col :xs="24" :md="8">
          <div ref="cDistRef" class="chart-mid" />
        </el-col>
        <el-col :xs="24" :md="8">
          <el-table :data="complaintDistricts" size="small">
            <el-table-column prop="district" label="区县" width="150" />
            <el-table-column prop="count" label="投诉量" width="90" align="right" />
          </el-table>
        </el-col>
      </el-row>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="detail-header">
          <div class="detail-header-left">
            <el-radio-group v-model="detailType" size="small" @change="onDetailTypeChange">
              <el-radio-button value="install">装机工单明细</el-radio-button>
              <el-radio-button value="complaint">投诉工单明细</el-radio-button>
            </el-radio-group>
            <!-- 勾选项随页签切换：装机页签管「已施工/延伸服务/已退撤/更换光猫」，
                 投诉页签只留「HB-KHBZ / HB-ZHZW」两个单号类开关 -->
            <template v-if="detailType === 'install'">
              <el-checkbox v-model="showDone" size="small" @change="onDetailFilterChange">显示已施工</el-checkbox>
              <el-checkbox v-model="showExt" size="small" @change="onDetailFilterChange">显示延伸服务</el-checkbox>
              <el-checkbox v-model="showRefund" size="small" @change="onDetailFilterChange">显示 已退/撤单</el-checkbox>
              <el-checkbox v-model="showModem" size="small" @change="onDetailFilterChange">显示更换光猫</el-checkbox>
            </template>
            <template v-else>
              <el-checkbox v-model="showHkbz" size="small" @change="onDetailFilterChange">显示HB-KHBZ单</el-checkbox>
              <el-checkbox v-model="showZhzw" size="small" @change="onDetailFilterChange">显示HB-ZHZW工单</el-checkbox>
            </template>
          </div>
          <div class="detail-header-right">
            <!-- 该表数据来自「重要通报」邮件附件，与下方「重要通报」卡片同一数据源 -->
            <template v-if="reportMeta.has_data">
              <el-tag size="small" type="warning" effect="plain">数据 {{ reportMeta.meta.data_date }}</el-tag>
              <el-tag size="small" type="danger" effect="plain">收件 {{ reportMeta.meta.mail_time }}</el-tag>
              <span class="header-tip">拉取 {{ reportMeta.meta.fetched_at }}</span>
            </template>
            <span class="header-tip">共 {{ detailTotal }} 条</span>
            <el-button v-if="isAdmin" size="small" type="danger" plain
                       :loading="reportSyncing" @click="syncReport">同步邮件</el-button>
          </div>
        </div>
      </template>
      <el-table v-if="detailType === 'install'" :data="detailRows" size="small" max-height="440" :empty-text="emptyTip">
        <el-table-column prop="district" label="区县" width="90" sortable />
        <el-table-column prop="no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
        <el-table-column prop="theme" label="业务主题" min-width="120" show-overflow-tooltip sortable />
        <el-table-column prop="broadband_type" label="宽带类型" width="90" sortable />
        <el-table-column prop="is_high_quality" label="是否高品质" width="100" sortable>
          <template #default="{ row }">
            <el-tag v-if="row.is_high_quality === '是'" size="small" type="danger" effect="dark">是</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">否</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_fttr" label="是否FTTR" width="95" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_fttr === '是' ? 'success' : 'info'">{{ row.is_fttr || '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="current_step" label="当前环节" width="110" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="(row.status || '').includes('施工') ? 'success'
              : (row.status || '').includes('退') || (row.status || '').includes('撤') ? 'danger' : 'warning'">
              {{ row.current_step || '处理中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
        <el-table-column prop="appointment_time" label="预约截止时间" width="150" sortable />
        <el-table-column prop="handler" label="回单人员" min-width="90" sortable />
        <el-table-column prop="grid" label="网格" min-width="100" show-overflow-tooltip sortable />
      </el-table>
      <el-table v-else :data="detailRows" size="small" max-height="440" :empty-text="emptyTip">
        <el-table-column prop="district" label="区县" width="90" sortable />
        <el-table-column prop="no" label="工单流水号" min-width="140" show-overflow-tooltip sortable />
        <el-table-column prop="category" label="投诉分类" min-width="150" show-overflow-tooltip sortable />
        <el-table-column label="是否触发H5" width="112" sortable
                         sort-by="row => row.trigger_h5 || ''">
          <template #default="{ row }">
            <!-- 三态口径（app/h5_rule.py）：
                 on=已触发 / wait=暂未触发(完工后会发) / no=不触发(非HB-058) -->
            <el-tooltip :content="row.h5_why || '按单号前缀与工单状态判定'" placement="top" :show-after="250">
              <span class="h5-tag" :class="'h5-' + (row.trigger_h5 || 'no')">{{ row.h5_label || '-' }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="is_high_quality" label="是否高品质" width="100" sortable>
          <template #default="{ row }">
            <!-- 「是」用红色实心标记，与「触发H5」视觉一致，便于一眼扫出 -->
            <el-tag v-if="row.is_high_quality === '是'" size="small" type="danger" effect="dark">是</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">否</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="global_pass" label="全球通标识" width="110" sortable>
          <template #default="{ row }">
            <span :class="{ 'gp-red': /白金|钻石/.test(row.global_pass || '') }">{{ row.global_pass || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="accept_time" label="投诉时间" width="150" sortable />
        <el-table-column prop="appointment_time" label="预约截止时间" width="150" sortable />
        <el-table-column prop="status" label="状态" width="95" sortable show-overflow-tooltip />
        <el-table-column prop="handler" label="处理人" min-width="85" sortable />
        <el-table-column prop="grid" label="网格" min-width="100" show-overflow-tooltip sortable />
        <el-table-column prop="duration" label="时长(h)" width="85" align="right" sortable />
        <el-table-column prop="is_solved" label="是否解决" width="95" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_solved === '已维修' ? 'success' : 'warning'">{{ row.is_solved || '处理中' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination v-if="detailTotal > pageSize"
                     layout="total, sizes, prev, pager, next, jumper"
                     :total="detailTotal" :current-page="detailPage" :page-size="pageSize"
                     :page-sizes="[50, 100, 200, 500]"
                     small background style="margin-top:10px;justify-content:flex-end"
                     @current-change="onDetailPageChange" @size-change="onDetailSizeChange" />

      <!-- 省公司邮件预警（139邮箱每小时快照，仅保留最新） -->
      <el-card class="page-card" style="margin-top:16px">
        <template #header>
          <div class="card-header">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span class="title">省公司预警</span>
              <el-tag v-if="alertMeta.has_data" size="small" type="danger">数据时间：{{ alertMeta.meta.mail_time }}</el-tag>
              <span v-if="alertMeta.has_data" class="header-tip">导出 {{ alertMeta.meta.export_time }} · 拉取 {{ alertMeta.meta.fetched_at }}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <el-select v-model="alertDistrict" size="small" style="width:120px" placeholder="全部区县" clearable @change="onAlertDistrict">
                <el-option v-for="d in districtOptions" :key="d" :label="d" :value="d === '全部' ? '' : d" />
              </el-select>
              <el-radio-group v-model="alertType" size="small" @change="loadAlertOrders">
                <el-radio-button value="install">待装明细</el-radio-button>
                <el-radio-button value="complaint">投诉待处理</el-radio-button>
                <el-radio-button value="book">预约异常</el-radio-button>
                <el-radio-button value="monitor">人员预警</el-radio-button>
              </el-radio-group>
              <el-button v-if="isAdmin" size="small" type="danger" plain :loading="alertSyncing" @click="syncAlert">同步邮件</el-button>
            </div>
          </div>
        </template>
        <template v-if="alertMeta.has_data">
          <div class="stat-cards alert-cards">
            <div class="stat-card blue clickable" :class="{ active: alertType === 'install' }" @click="pickAlert('install')"><div class="label">待装工单</div><div class="value">{{ alertMeta.counts?.install ?? alertMeta.meta.install_total }}<span class="unit">单</span></div><div class="sub">{{ alertInstallTop }}</div></div>
            <div class="stat-card purple clickable" :class="{ active: alertType === 'complaint' }" @click="pickAlert('complaint')"><div class="label">投诉待处理</div><div class="value">{{ alertMeta.counts?.complaint ?? alertMeta.meta.complaint_total }}<span class="unit">单</span></div><div class="sub">{{ alertComplaintTop }}</div></div>
            <div class="stat-card red clickable" :class="{ active: alertType === 'book' }" @click="pickAlert('book')"><div class="label">预约异常</div><div class="value">{{ alertMeta.counts?.book ?? alertMeta.meta.book_abnormal }}<span class="unit">单</span></div><div class="sub">超时/临近/冲突</div></div>
            <div class="stat-card orange clickable" :class="{ active: alertType === 'monitor' && monitorLevel === '黄' }" @click="pickAlert('monitor', '黄')"><div class="label">黄色告警人员</div><div class="value">{{ alertMeta.counts?.warn_yellow ?? alertMeta.meta.warn_yellow }}<span class="unit">人</span></div><div class="sub">单量超负荷预警</div></div>
            <div class="stat-card red clickable" :class="{ active: alertType === 'monitor' && monitorLevel === '红' }" @click="pickAlert('monitor', '红')"><div class="label">红色告警人员</div><div class="value">{{ alertMeta.counts?.warn_red ?? alertMeta.meta.warn_red }}<span class="unit">人</span></div><div class="sub">需立即调单</div></div>
            <div class="stat-card green clickable" @click="openDrill('global', { level: '白金' })"><div class="label">全球通-白金卡</div><div class="value">{{ alertMeta.global_cnt?.platinum ?? 0 }}<span class="unit">单</span></div><div class="sub">当月通报待施工 · 点击明细</div></div>
            <div class="stat-card blue clickable" @click="openDrill('global', { level: '钻石' })"><div class="label">全球通-钻石</div><div class="value">{{ alertMeta.global_cnt?.diamond ?? 0 }}<span class="unit">单</span></div><div class="sub">当月通报待施工 · 点击明细</div></div>
          </div>
          <el-table v-if="alertType === 'install'" :data="alertRows" size="small" max-height="400">
            <el-table-column prop="district" label="区县" width="85" sortable />
            <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
            <el-table-column prop="status" label="工单状态" width="105" sortable>
              <template #default="{ row }"><el-tag size="small" :type="alertStTag(row.status)">{{ row.status || '-' }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="order_type" label="工单类型" width="90" sortable />
            <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
            <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
            <el-table-column prop="visit_time" label="上门时间" width="150" sortable />
            <el-table-column prop="handler" label="处理人" min-width="80" sortable />
            <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
            <el-table-column prop="community" label="小区" min-width="110" show-overflow-tooltip sortable />
            <el-table-column prop="is_fttr" label="FTTR" width="70" sortable />
            <el-table-column prop="is_high_quality" label="高品质" width="70" sortable />
          </el-table>
          <el-table v-else-if="alertType === 'complaint'" :data="alertRows" size="small" max-height="400">
            <el-table-column prop="district" label="区县" width="85" sortable />
            <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
            <el-table-column prop="status" label="工单状态" width="105" sortable>
              <template #default="{ row }"><el-tag size="small" :type="alertStTag(row.status)">{{ row.status || '-' }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="order_type" label="工单类型" width="90" sortable />
            <el-table-column prop="trigger_h5" label="是否触发H5" width="112" sortable>
              <template #default="{ row }">
                <el-tooltip :content="row.h5_why || '按单号前缀与工单状态判定'" placement="top" :show-after="250">
                  <span class="h5-tag" :class="'h5-' + (row.trigger_h5 || 'no')">{{ row.h5_label || '-' }}</span>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column prop="is_high_quality" label="是否高品质" width="100" sortable>
              <template #default="{ row }">
                <el-tag size="small" :type="row.is_high_quality === '是' ? 'success' : 'info'">{{ row.is_high_quality || '否' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
            <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
            <el-table-column prop="handler" label="处理人" min-width="80" sortable />
            <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
            <el-table-column prop="community" label="小区" min-width="110" show-overflow-tooltip sortable />
          </el-table>
          <el-table v-else-if="alertType === 'book'" :data="alertRows" size="small" max-height="400">
            <el-table-column prop="district" label="区县" width="85" sortable />
            <el-table-column prop="book_status" label="预约状态" width="110" sortable>
              <template #default="{ row }">
                <el-tag size="small" :type="(row.book_status || '').includes('超时') || (row.book_status || '').includes('冲突') ? 'danger' : 'warning'">{{ row.book_status || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
            <el-table-column prop="status" label="定单状态" width="105" sortable />
            <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
            <el-table-column prop="visit_time" label="上门时间" width="150" sortable />
            <el-table-column prop="handler" label="处理人" min-width="80" sortable />
            <el-table-column prop="supervise_info" label="督办信息" width="100" sortable />
            <el-table-column prop="supervise_person" label="督办人员" min-width="85" sortable />
            <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
          </el-table>
          <el-table v-else :data="monitorRows" size="small" max-height="400">
            <el-table-column prop="warn_level" label="预警级别" width="100" sortable>
              <template #default="{ row }">
                <el-tag size="small" :type="(row.warn_level || '').includes('红') ? 'danger' : (row.warn_level || '').includes('黄') ? 'warning' : 'info'">{{ row.warn_level || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="handler" label="人员" min-width="85" sortable />
            <el-table-column prop="district" label="区县" width="100" sortable />
            <el-table-column prop="grid" label="网格" min-width="120" show-overflow-tooltip sortable />
            <el-table-column prop="order_count" label="当日工单量" width="100" align="right" sortable />
            <el-table-column prop="warn_time" label="预警时间" width="150" sortable />
          </el-table>
        </template>
        <el-empty v-else description="暂无预警数据，管理员点击右上角「同步邮件」拉取省公司最新邮件" />
      </el-card>
    </el-card>

    <!-- 省公司【重要通报】邮件（区县装维指标快照，随顶部区县筛选联动） -->
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span class="title">重要通报（装维指标）</span>
            <el-tag v-if="reportMeta.has_data" size="small" type="warning">通报日期：{{ reportMeta.meta.data_date }}</el-tag>
            <el-tag v-if="reportMeta.has_data" size="small" type="danger">收件时间：{{ reportMeta.meta.mail_time }}</el-tag>
            <span v-if="reportMeta.has_data" class="header-tip">邮件：{{ reportMeta.meta.mail_subject }} · 拉取 {{ reportMeta.meta.fetched_at }}</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <el-radio-group v-model="reportTab" size="small">
              <el-radio-button value="cards">区县指标</el-radio-button>
              <el-radio-button value="rank">区县排名</el-radio-button>
              <el-radio-button value="timeout">超时盯控明细</el-radio-button>
              <el-radio-button value="h5">H5满意度</el-radio-button>
            </el-radio-group>
            <el-button v-if="isAdmin" size="small" type="warning" plain :loading="reportSyncing" @click="syncReport">同步通报</el-button>
          </div>
        </div>
      </template>
      <template v-if="reportMeta.has_data">
        <div v-show="reportTab === 'cards'" class="stat-cards">
          <div v-for="c in reportCards" :key="c.key" class="stat-card" :class="c.tone">
            <div class="label">{{ c.label }}</div>
            <div class="value">{{ c.value }}<span v-if="c.value !== '-'" class="unit">{{ c.unit }}</span></div>
            <div v-if="c.drill" class="sub clickable" style="color:var(--el-color-primary)" title="点击查看明细"
                 @click="openDrill(c.drill)">{{ c.sub }} ›</div>
            <div v-else class="sub">{{ c.sub }}</div>
          </div>
        </div>
        <el-table v-show="reportTab === 'rank'" :data="reportRanking" size="small" max-height="420">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="district" label="区县" width="100" sortable />
          <el-table-column prop="month_order" label="当月收单" width="90" align="right" sortable />
          <el-table-column prop="month_done" label="当月竣工" width="90" align="right" sortable />
          <el-table-column label="竣工率" min-width="130" sortable sort-by="month_rate">
            <template #default="{ row }">
              <el-progress :percentage="row.month_rate" :color="rateColor(row.month_rate)" :stroke-width="12" />
            </template>
          </el-table-column>
          <el-table-column label="装机及时率" width="105" align="right" sortable sort-by="timely_rate">
            <template #default="{ row }"><span :class="{ 'gp-red': row.timely_rate !== '-' && row.timely_rate < 100 }">{{ row.timely_rate }}%</span></template>
          </el-table-column>
          <el-table-column label="超时工单" width="90" align="right" sortable sort-by="timeout_orders">
            <template #default="{ row }"><span :class="{ 'gp-red': row.timeout_orders > 0 }">{{ row.timeout_orders }}</span></template>
          </el-table-column>
          <el-table-column label="投诉及时率" width="100" align="right" sortable sort-by="hq_normal_rate">
            <template #default="{ row }">{{ row.hq_normal_rate }}%</template>
          </el-table-column>
          <el-table-column label="H5满意度" width="95" align="right" sortable sort-by="h5_satisfaction">
            <template #default="{ row }">{{ row.h5_satisfaction }}</template>
          </el-table-column>
        </el-table>
        <el-table v-show="reportTab === 'timeout'" :data="reportTimeoutRows" size="small" max-height="420"
                  :empty-text="`当前区县暂无超时盯控工单（全通报共 ${reportTimeouts.length} 条）`">
          <el-table-column prop="district" label="区县" width="90" sortable />
          <el-table-column prop="order_no" label="定单编码" min-width="170" show-overflow-tooltip sortable />
          <el-table-column prop="theme" label="定单主题" min-width="130" show-overflow-tooltip sortable />
          <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
          <el-table-column prop="appoint_end" label="预约结束时间" width="150" sortable />
          <el-table-column prop="step" label="当前环节" width="100" sortable>
            <template #default="{ row }">
              <el-tag size="small" type="warning">{{ row.step || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="handler" label="当前处理人" min-width="100" sortable />
          <el-table-column prop="phone" label="处理人电话" width="115" sortable />
          <el-table-column prop="ctrl_type" label="盯控类型" min-width="120" sortable>
            <template #default="{ row }">
              <el-tag size="small" type="danger">{{ row.ctrl_type || '-' }}</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <!-- H5满意度通报（集团大音平台，数据同步页上传） -->
        <div v-show="reportTab === 'h5'">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
            <el-tag v-if="h5Survey.has_data" size="small" type="danger">数据日期：{{ h5Survey.data_date || '-' }}</el-tag>
            <span v-if="h5Survey.has_data" class="header-tip">{{ h5Survey.filename }} · 上传 {{ h5Survey.uploaded_at }}</span>
            <span v-if="isAdmin" class="header-tip">上传入口：数据同步页 → 「H5满意度通报」</span>
          </div>
          <el-table :data="h5DistRows" size="small" max-height="300" v-loading="h5Loading">
            <el-table-column prop="district" label="区县" width="95" sortable />
            <el-table-column label="装机回单" width="85" align="right" sortable sort-by="ib">
              <template #default="{ row }">{{ row.ib }}</template>
            </el-table-column>
            <el-table-column label="装机测评" width="85" align="right" sortable sort-by="isv">
              <template #default="{ row }">{{ row.isv }}</template>
            </el-table-column>
            <el-table-column label="装机参与率" min-width="150" sortable sort-by="ijoin">
              <template #default="{ row }">
                <span :class="{ 'gp-red': row.ijoin !== '-' && row.ijoin < 30 }" style="margin-right:6px">{{ row.ijoin }}%</span>
                <el-progress v-if="row.ijoin !== '-'" :percentage="Math.min(row.ijoin, 100)" :stroke-width="9"
                             :color="row.ijoin < 30 ? '#f56c6c' : '#67c23a'" style="display:inline-block;width:70px" />
              </template>
            </el-table-column>
            <el-table-column label="装机满意度" width="95" align="right" sortable sort-by="isat">
              <template #default="{ row }">{{ row.isat }}</template>
            </el-table-column>
            <el-table-column label="投诉回单" width="85" align="right" sortable sort-by="cb">
              <template #default="{ row }">{{ row.cb }}</template>
            </el-table-column>
            <el-table-column label="投诉测评" width="85" align="right" sortable sort-by="csv">
              <template #default="{ row }">{{ row.csv }}</template>
            </el-table-column>
            <el-table-column label="投诉参与率" min-width="150" sortable sort-by="cjoin">
              <template #default="{ row }">
                <span :class="{ 'gp-red': row.cjoin !== '-' && row.cjoin < 30 }" style="margin-right:6px">{{ row.cjoin }}%</span>
                <el-progress v-if="row.cjoin !== '-'" :percentage="Math.min(row.cjoin, 100)" :stroke-width="9"
                             :color="row.cjoin < 30 ? '#f56c6c' : '#67c23a'" style="display:inline-block;width:70px" />
              </template>
            </el-table-column>
            <el-table-column label="投诉满意度" width="95" align="right" sortable sort-by="csat">
              <template #default="{ row }">{{ row.csat }}</template>
            </el-table-column>
          </el-table>
          <el-row :gutter="16" style="margin-top:12px">
            <el-col :xs="24" :md="12">
              <div class="title" style="margin-bottom:6px">人员测评TOP50（{{ district === '全部' ? '全部区县' : district }} · 按测评总数）</div>
              <el-table :data="h5StaffRows" size="small" max-height="320">
                <el-table-column type="index" label="#" width="45" />
                <el-table-column prop="staff" label="装维人员" min-width="100" sortable />
                <el-table-column prop="district" label="区县" width="90" sortable />
                <el-table-column prop="install" label="装机测评" width="85" align="right" sortable />
                <el-table-column prop="complaint" label="投诉测评" width="85" align="right" sortable />
                <el-table-column prop="total" label="合计" width="70" align="right" sortable />
                <el-table-column prop="low" label="低分" width="65" align="right" sortable>
                  <template #default="{ row }">
                    <span :class="{ 'gp-red': row.low > 0 }">{{ row.low || '-' }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-col>
            <el-col :xs="24" :md="12">
              <div class="title" style="margin-bottom:6px">低分测评核实（{{ h5Survey.lows?.length || 0 }} 条）</div>
              <el-table :data="h5LowRows" size="small" max-height="320"
                        empty-text="当前区县无低分测评工单">
                <el-table-column prop="kind" label="类型" width="65">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.kind === '装机' ? 'primary' : 'warning'">{{ row.kind }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="district" label="区县" width="85" sortable />
                <el-table-column prop="order_no" label="工单号" min-width="150" show-overflow-tooltip />
                <el-table-column prop="score" label="低分" width="60" align="center" />
                <el-table-column prop="person" label="责任人" min-width="90" sortable />
                <el-table-column prop="message" label="用户留言" min-width="120" show-overflow-tooltip />
                <el-table-column prop="note" label="回访备注" min-width="110" show-overflow-tooltip />
              </el-table>
            </el-col>
          </el-row>
        </div>
      </template>
      <el-empty v-else description="暂无通报数据，管理员点击右上角「同步通报」拉取省公司最新邮件" />
    </el-card>

    <!-- AI装维即访-不规范（人员通报，随顶部区县/月份筛选联动；数据同步页上传） -->
    <el-card class="page-card ai-visit-card" shadow="never">
      <template #header>
        <div class="av-head">
          <div class="av-head-left">
            <span class="av-title">AI装维即访 · 不规范</span>
            <span v-if="aiVisit.month" class="av-pill">{{ aiVisit.month }}</span>
            <span v-if="aiVisit.total" class="av-pill danger">共 {{ aiVisit.total }} 条</span>
            <span class="av-head-note">
              {{ district === '全部' ? '全部区县' : district }} · 仅统计「外呼成功」且勾选「不是」的工单 · 按责任人排序
            </span>
          </div>
          <span v-if="isAdmin" class="av-head-note is-admin">上传入口：数据同步页 → 「AI装维即访」</span>
        </div>
      </template>

      <!-- ① 区县规范率矩阵 -->
      <section class="av-section">
        <div class="av-sec-head">
          <span class="av-sec-title">区县规范率</span>
          <span v-if="aiRates.date" class="av-pill light">{{ aiRates.date }}</span>
          <span class="av-sec-note">规范率 =（接通量 − 有问题工单量）/ 接通量 · 低于 99% 标红 · 条形长度按 95–100% 区间映射</span>
          <span v-if="aiRateRows.length" class="av-sec-right">
            全部区县 · <b :class="{ warn: lowRateCells > 0 }">{{ lowRateCells }}</b> 项低于 99%
          </span>
        </div>
        <el-table :data="aiRateRows" size="small" class="av-table av-rate-table"
                  :row-class-name="aiRateRowClass"
                  empty-text="暂无规范率通报，请在数据同步页上传「AI装维即访」回访清单">
          <el-table-column prop="district" label="区县" width="104">
            <template #default="{ row }"><span class="av-dist">{{ row.district }}</span></template>
          </el-table-column>
          <el-table-column label="装机" align="center">
            <el-table-column label="当日" min-width="116" align="center">
              <template #default="{ row }">
                <div v-if="row.iDay" class="av-cell-wrap" :title="`接通 ${row.iDay.connected} · 问题 ${row.iDay.problem} 单`">
                  <span class="av-num" :class="{ low: row.iDay.rate < 99 }">{{ row.iDay.rate }}%</span>
                  <span class="av-track"><i :style="{ width: barW(row.iDay.rate) }"></i></span>
                </div>
                <span v-else class="av-na">—</span>
              </template>
            </el-table-column>
            <el-table-column label="当月" min-width="116" align="center">
              <template #default="{ row }">
                <div v-if="row.iMonth" class="av-cell-wrap" :title="`接通 ${row.iMonth.connected} · 问题 ${row.iMonth.problem} 单`">
                  <span class="av-num" :class="{ low: row.iMonth.rate < 99 }">{{ row.iMonth.rate }}%</span>
                  <span class="av-track"><i :style="{ width: barW(row.iMonth.rate) }"></i></span>
                </div>
                <span v-else class="av-na">—</span>
              </template>
            </el-table-column>
          </el-table-column>
          <el-table-column label="投诉" align="center">
            <el-table-column label="当日" min-width="116" align="center">
              <template #default="{ row }">
                <div v-if="row.cDay" class="av-cell-wrap" :title="`接通 ${row.cDay.connected} · 问题 ${row.cDay.problem} 单`">
                  <span class="av-num" :class="{ low: row.cDay.rate < 99 }">{{ row.cDay.rate }}%</span>
                  <span class="av-track"><i :style="{ width: barW(row.cDay.rate) }"></i></span>
                </div>
                <span v-else class="av-na">—</span>
              </template>
            </el-table-column>
            <el-table-column label="当月" min-width="116" align="center">
              <template #default="{ row }">
                <div v-if="row.cMonth" class="av-cell-wrap" :title="`接通 ${row.cMonth.connected} · 问题 ${row.cMonth.problem} 单`">
                  <span class="av-num" :class="{ low: row.cMonth.rate < 99 }">{{ row.cMonth.rate }}%</span>
                  <span class="av-track"><i :style="{ width: barW(row.cMonth.rate) }"></i></span>
                </div>
                <span v-else class="av-na">—</span>
              </template>
            </el-table-column>
          </el-table-column>
        </el-table>
      </section>

      <!-- ② 不规范明细 -->
      <section class="av-section">
        <div class="av-sec-head">
          <span class="av-sec-title">不规范明细</span>
          <span v-if="aiVisitRows.length" class="av-pill light">{{ aiVisitRows.length }} 条</span>
          <span class="av-sec-note">点击工单号可复制</span>
          <div v-if="aiItemStat.length" class="av-chips">
            <span v-for="c in aiItemStat" :key="c.item" class="av-chip"
                  :class="c.kind === '装机' ? 'k-install' : (c.kind === '投诉' ? 'k-complaint' : 'k-mixed')">
              {{ c.item }}<b>{{ c.count }}</b>
            </span>
          </div>
        </div>
        <el-table :data="aiVisitRows" size="small" max-height="512" v-loading="aiVisitLoading"
                  class="av-table av-issue-table"
                  :default-sort="{ prop: 'handler', order: 'ascending' }">
          <el-table-column v-if="district === '全部'" prop="district" label="区县" width="94" sortable>
            <template #default="{ row }"><span class="av-dist-sm">{{ row.district }}</span></template>
          </el-table-column>
          <el-table-column prop="handler" label="责任人" min-width="156" sortable>
            <template #default="{ row }">
              <div class="av-person">
                <span class="av-avatar">{{ (row.handler || '?').slice(0, 1) }}</span>
                <span class="av-name">{{ row.handler }}</span>
                <span v-if="aiHandlerCount[row.handler] > 1" class="av-repeat"
                      :title="`本表共 ${aiHandlerCount[row.handler]} 条不规范`">×{{ aiHandlerCount[row.handler] }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="item" label="不规范项" min-width="176" sortable>
            <template #default="{ row }">
              <div class="av-item" :class="row.kind === '装机' ? 'k-install' : 'k-complaint'">
                <span class="av-dot"></span>
                <span class="av-item-name">{{ row.item }}</span>
                <span class="av-item-kind">{{ row.kind }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="order_no" label="工单号" min-width="196" sortable>
            <template #default="{ row }">
              <span class="av-order" :title="'点击复制 ' + row.order_no"
                    @click="copyOrder(row.order_no)">{{ row.order_no }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="completion_time" label="回单时间" width="132" sortable>
            <template #default="{ row }">
              <span class="av-time">
                <b>{{ (row.completion_time || '').slice(0, 10) }}</b>
                <i>{{ (row.completion_time || '').slice(11, 16) }}</i>
              </span>
            </template>
          </el-table-column>
          <template #empty>
            <div class="av-empty">当前月份 / 区县暂无「不规范」记录</div>
          </template>
        </el-table>
      </section>
    </el-card>

    <!-- 通报/预警卡片下钻明细对话框 -->
    <el-dialog v-model="drillVisible" :title="drillTitle" width="1080px" top="6vh" destroy-on-close>
      <div v-loading="drillLoading" style="min-height:180px">
        <el-alert v-if="drillKind === 'h5staff'" type="info" :closable="false" style="margin-bottom:8px"
                  title="参评率 = 该装维人员当月投诉工单中完成H5参评的比例；低于30%标红" />
        <el-alert v-if="drillKind === 'dup' && drillRows.length === 0 && !drillLoading" type="success" :closable="false"
                  title="当前区县无广义重复投诉工单" />
        <el-checkbox v-if="drillKind === 'global'" v-model="drillShowDone" @change="reloadDrill"
                     style="margin-bottom:8px">显示已完工</el-checkbox>
        <el-table v-if="drillKind === 'h5staff'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="staff" label="装维人员" min-width="110" sortable />
          <el-table-column prop="district" label="区县" width="100" sortable />
          <el-table-column prop="orders" label="当月投诉工单" width="115" align="right" sortable />
          <el-table-column prop="joined" label="H5参评数" width="100" align="right" sortable />
          <el-table-column label="参评率" width="180">
            <template #default="{ row }">
              <template v-if="row.rate !== null">
                <span :class="{ 'gp-red': row.rate < 30 }" style="margin-right:8px">{{ row.rate }}%</span>
                <el-progress :percentage="Math.min(row.rate, 100)" :stroke-width="10"
                             :color="row.rate < 30 ? '#f56c6c' : '#67c23a'" style="flex:1" />
              </template>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
        <el-table v-else-if="drillKind === 'untimely'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="district" label="区县" width="85" sortable />
          <el-table-column prop="order_no" label="定单编码" min-width="160" show-overflow-tooltip sortable />
          <el-table-column prop="theme" label="业务主题" min-width="120" show-overflow-tooltip sortable />
          <el-table-column prop="broadband_type" label="宽带类型" width="90" sortable />
          <el-table-column prop="accept_time" label="受理时间" width="145" sortable />
          <el-table-column prop="appoint_end" label="预约结束时间" width="145" sortable />
          <el-table-column prop="step" label="当前环节" width="95" sortable />
          <el-table-column prop="handler" label="当前处理人" min-width="90" sortable />
          <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
        </el-table>
        <el-table v-else-if="drillKind === 'timeout'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="district" label="区县" width="85" sortable />
          <el-table-column prop="order_no" label="定单编码" min-width="160" show-overflow-tooltip sortable />
          <el-table-column prop="theme" label="定单主题" min-width="120" show-overflow-tooltip sortable />
          <el-table-column prop="accept_time" label="受理时间" width="145" sortable />
          <el-table-column prop="appoint_end" label="预约结束时间" width="145" sortable />
          <el-table-column prop="step" label="当前环节" width="95" sortable />
          <el-table-column prop="handler" label="当前处理人" min-width="90" sortable />
          <el-table-column prop="phone" label="处理人电话" width="110" sortable />
          <el-table-column prop="ctrl_type" label="盯控类型" min-width="110" sortable />
        </el-table>
        <el-table v-else-if="drillKind === 'complaint'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="district" label="区县" width="85" sortable />
          <el-table-column prop="flow_no" label="工单流水号" min-width="140" show-overflow-tooltip sortable />
          <el-table-column prop="category" label="投诉分类" min-width="120" show-overflow-tooltip sortable />
          <el-table-column prop="theme" label="投诉主题" min-width="130" show-overflow-tooltip sortable />
          <el-table-column prop="accept_time" label="投诉时间" width="145" sortable />
          <el-table-column prop="duration" label="时长(h)" width="80" align="right" sortable />
          <el-table-column prop="status" label="状态" width="95" show-overflow-tooltip sortable />
          <el-table-column prop="handler" label="当前处理人" min-width="90" sortable />
          <el-table-column prop="phone" label="电话" width="110" sortable />
        </el-table>
        <el-table v-else-if="drillKind === 'dup'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="district" label="区县" width="85" sortable />
          <el-table-column prop="order_no" label="工单编号" min-width="140" show-overflow-tooltip sortable />
          <el-table-column prop="account" label="宽带账号" width="100" sortable />
          <el-table-column prop="dup_cnt" label="重复次数" width="85" align="center" sortable />
          <el-table-column prop="first_time" label="第一次投诉时间" width="145" sortable />
          <el-table-column prop="first_reason" label="第一次投诉原因" min-width="130" show-overflow-tooltip />
          <el-table-column prop="second_time" label="第二次投诉时间" width="145" sortable />
          <el-table-column prop="second_reason" label="第二次投诉原因" min-width="130" show-overflow-tooltip />
          <el-table-column prop="handler" label="当前处理人" min-width="95" sortable />
          <el-table-column prop="grid" label="回单网格" min-width="95" show-overflow-tooltip sortable />
        </el-table>
        <el-table v-else-if="drillKind === 'global'" :data="drillRows" size="small" max-height="480">
          <el-table-column prop="district" label="区县" width="85" sortable />
          <el-table-column prop="flow_no" label="工单流水号" min-width="140" show-overflow-tooltip sortable />
          <el-table-column label="全球通标识" width="115" sortable sort-by="global_pass">
            <template #default="{ row }">
              <span :class="{ 'gp-red': /白金|钻石/.test(row.global_pass || '') }">{{ row.global_pass || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="投诉分类" min-width="120" show-overflow-tooltip sortable />
          <el-table-column prop="theme" label="投诉主题" min-width="130" show-overflow-tooltip sortable />
          <el-table-column prop="accept_time" label="投诉时间" width="145" sortable />
          <el-table-column prop="status" label="状态" width="95" show-overflow-tooltip sortable />
          <el-table-column prop="handler" label="当前处理人" min-width="90" sortable />
          <el-table-column prop="duration" label="时长(h)" width="80" align="right" sortable />
        </el-table>
        <el-empty v-if="drillKind !== 'h5staff' && drillKind !== 'dup' && drillRows.length === 0 && !drillLoading"
                  description="当前区县无相关明细" :image-size="70" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, onBeforeUnmount, computed } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, CanvasRenderer])
import api from '../api'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../store'

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)

const month = ref(new Date().toISOString().slice(0, 7))
const buildTime = typeof __BUILD_TIME__ !== 'undefined' ? __BUILD_TIME__ : 'dev'
const district = ref('洪湖市')
const districtOptions = ['洪湖市', '监利市', '公安县', '荆州区', '江陵县', '松滋市', '沙市区', '石首市', '荆州开发区', '全部']
const cards = ref([])
const districtRanking = ref([])
const complaintDistricts = ref([])
const hasWide = ref(false)
const detailType = ref('install')
// 明细数据：筛选与分页全部下推服务端（/metrics/wide/orders），避免一次性拉取上万行
const detailRows = ref([])
const detailTotal = ref(0)
const detailPage = ref(1)
const pageSize = ref(50)
const showDone = ref(false)
const showExt = ref(false)
const showRefund = ref(false)
const showModem = ref(false)
const showHkbz = ref(false)
const showZhzw = ref(false)   // 投诉页签：默认不勾选 = 明细里过滤掉 HB-ZHZW 工单
const emptyTip = computed(() =>
  detailTotal.value === 0 && detailRows.value.length === 0 ? '暂无数据，请先上传对应宽表' : '暂无符合条件的数据')
const trendRef = ref()
const catRef = ref()
const cDistRef = ref()
let chart = null, catChart = null, cDistChart = null

const fmt = v => (typeof v === 'number' ? v.toLocaleString('zh-CN') : v)
const rateColor = r => (r >= 70 ? '#67c23a' : r >= 50 ? '#e6a23c' : '#f56c6c')

// ---------------- AI装维即访（规范率 + 不规范明细，随顶部区县/月份筛选联动） ----------------
const aiVisit = ref({ month: '', total: 0 })
const aiVisitRows = ref([])
const aiVisitLoading = ref(false)
const aiRates = ref({ has_data: false, date: '', rows: [] })
const AI_DIST_ORDER = ['公安县', '洪湖市', '监利市', '江陵县', '荆州开发区', '荆州区', '沙市区', '石首市', '松滋市', '荆州']

// 区县规范率精简表：区县 × (装机·当日/当月, 投诉·当日/当月)
const AI_RATE_KEY = { '装机当日': 'iDay', '装机当月': 'iMonth', '投诉当日': 'cDay', '投诉当月': 'cMonth' }
const aiRateRows = computed(() => {
  const map = {}
  for (const r of aiRates.value.rows) {
    const k = AI_RATE_KEY[r.kind + r.period]
    if (!k) continue
    ;(map[r.district] = map[r.district] || { district: r.district })[k] = r
  }
  let list = AI_DIST_ORDER.filter(d => map[d]).map(d => map[d])
  return list
})

// 规范率矩阵：低于 99% 的格数 / 合计行高亮 / 条形长度映射（95–100% → 6–100%）
const lowRateCells = computed(() => {
  let n = 0
  for (const r of aiRateRows.value) {
    if (r.district === '荆州') continue  // 荆州是合计行，不参与区县口径计数
    for (const k of ['iDay', 'iMonth', 'cDay', 'cMonth']) {
      if (r[k] && typeof r[k].rate === 'number' && r[k].rate < 99) n++
    }
  }
  return n
})
const aiRateRowClass = ({ rowIndex }) => {
  const r = aiRateRows.value[rowIndex]
  return r && r.district === '荆州' ? 'av-sum-row' : ''
}
const barW = r => Math.max(6, Math.min(100, (Number(r) - 95) * 20)) + '%'

// 不规范明细：责任人被通报次数、不规范项分布
const aiHandlerCount = computed(() => {
  const m = {}
  for (const r of aiVisitRows.value) m[r.handler] = (m[r.handler] || 0) + 1
  return m
})
const aiItemStat = computed(() => {
  const m = {}
  for (const r of aiVisitRows.value) {
    const it = r.item || '未分类'
    if (!m[it]) m[it] = { item: it, count: 0, kinds: new Set() }
    m[it].count++
    m[it].kinds.add(r.kind)
  }
  return Object.values(m)
    .map(x => ({ item: x.item, count: x.count, kind: x.kinds.size === 1 ? [...x.kinds][0] : '' }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)
})

function copyOrder(no) {
  if (!no) return
  const ok = () => ElMessage.success('已复制工单号 ' + no)
  const fallback = () => {
    const ta = document.createElement('textarea')
    ta.value = no
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy'); ok() } catch (e) { ElMessage.warning('复制失败，请手动选择') }
    document.body.removeChild(ta)
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(no).then(ok).catch(fallback)
  } else {
    fallback()
  }
}

async function loadAiVisit() {
  aiVisitLoading.value = true
  const dq = district.value === '全部' ? '' : district.value
  // 分开容错：任一请求失败不影响另一块渲染
  const [iss, rates] = await Promise.allSettled([
    api.get('/dashboard/ai-visit-issues', { params: { month: month.value, district: dq } }),
    api.get('/dashboard/ai-visit-rates', { params: { month: month.value } }),
  ])
  if (iss.status === 'fulfilled') {
    aiVisit.value = { month: iss.value.month, total: iss.value.total }
    aiVisitRows.value = iss.value.rows || []
  } else {
    aiVisit.value = { month: '', total: 0 }
    aiVisitRows.value = []
  }
  aiRates.value = rates.status === 'fulfilled' ? rates.value : { has_data: false, date: '', rows: [] }
  aiVisitLoading.value = false
}

async function loadDetail() {
  try {
    const p = { month: month.value, type: detailType.value,
                district: district.value === '全部' ? '' : district.value,
                page: detailPage.value, page_size: pageSize.value }
    if (detailType.value === 'install') {
      // 装机：四个开关全部可控；装机表无 HB-KHBZ / HB-ZHZW 单，不过滤单号
      p.hide_done = !showDone.value
      p.hide_ext = !showExt.value
      p.hide_refund = !showRefund.value
      p.hide_modem = !showModem.value
      p.hide_hkbz = false
      p.hide_zhzw = false
    } else {
      // 投诉：只留 HB-KHBZ / HB-ZHZW 两个开关；
      // 已施工固定隐藏（与历史 299 条口径一致，用户 2026-10-27 确认），
      // 延伸服务 / 更换光猫 / 已退撤 也固定维持原来的隐藏口径
      p.hide_done = true
      p.hide_ext = true
      p.hide_refund = true
      p.hide_modem = true
      p.hide_hkbz = !showHkbz.value
      p.hide_zhzw = !showZhzw.value
    }
    const r = await api.get('/metrics/wide/orders', { params: p })
    detailRows.value = r.rows || []
    detailTotal.value = r.total || 0
  } catch (e) { detailRows.value = []; detailTotal.value = 0 }
}

function onDetailTypeChange() {
  // 切页签时两侧开关全部复位为默认不勾选，避免隐藏着的开关仍在过滤数据
  showDone.value = false
  showExt.value = false
  showRefund.value = false
  showModem.value = false
  showHkbz.value = false
  showZhzw.value = false
  detailPage.value = 1
  loadDetail()
}

function onDetailFilterChange() {
  detailPage.value = 1
  loadDetail()
}

function onDetailPageChange(page) {
  // Element Plus current-change 抛出新页码，必须回写 detailPage，否则永远停在第 1 页
  detailPage.value = page || 1
  loadDetail()
}

function onDetailSizeChange(size) {
  // size-change 抛出新每页条数，回写 pageSize 并回到第 1 页
  pageSize.value = size || 50
  detailPage.value = 1
  loadDetail()
}

function buildCardsFromWide(wide) {
  const i = wide.install, c = wide.complaint
  cards.value = [
    { key: 'accept', label: '当月受理', value: i.total, unit: '单', tone: 'blue', sub: `已施工 ${i.done} / 待装 ${i.pending}` },
    { key: 'rate', label: '竣工率', value: i.rate, unit: '%', tone: 'green', sub: `退撤单 ${i.refund} 单` },
    { key: 'refund', label: '退撤单', value: i.refund, unit: '单', tone: 'orange', sub: i.refund_reasons?.[0] ? `主因：${i.refund_reasons[0].reason}` : '' },
    { key: 't24', label: '24h超时', value: i.timeout_24h, unit: '单', tone: 'red', sub: `8h超时 ${i.timeout_8h}` },
    { key: 'complaint', label: '投诉工单', value: c.total, unit: '单', tone: 'purple', sub: `已解决 ${c.solved}` },
    { key: 'crate', label: '投诉解决率', value: c.rate, unit: '%', tone: 'green', sub: `24h超时 ${c.timeout_24h}` },
  ]
}

// 全荆州标准区县（与后端 _JZ_ORDER 一致）：排名以此为准，缺数据的区县补 0 占位，
// 保证「区县竣工率排名」始终展示完整 9 个区县，不因筛选或数据缺失而减少
const JZ_DISTRICTS = ['沙市区', '荆州区', '公安县', '监利市', '江陵县', '石首市', '洪湖市', '松滋市', '荆州开发区']

function buildRanking(list) {
  const map = new Map((list || []).map(d => [d.district, d]))
  const merged = JZ_DISTRICTS.map(name => {
    const d = map.get(name)
    return d
      ? { ...d, completed: d.done }
      : { district: name, total: 0, done: 0, completed: 0, refund: 0, rate: 0, empty: true }
  })
  // 排序：有数据的按竣工率降序在前，无数据的（total=0）置底
  merged.sort((a, b) => (b.total > 0) - (a.total > 0) || b.rate - a.rate)
  merged.forEach((d, i) => { d.rank = i + 1 })
  return merged
}

function renderTrend(daily) {
  nextTick(() => {
    if (!trendRef.value) return          // 组件已卸载（切走页面）：跳过渲染，避免对已 dispose 实例 setOption
    if (!chart) chart = echarts.init(trendRef.value)
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => {
          const day = ps[0]?.axisValue || ''
          const line = ps.filter(p => p.seriesType === 'line')[0]
          const bars = ps.filter(p => p.seriesType === 'bar')
            .map(p => `${p.marker}${p.seriesName} ${p.value}`).join('<br/>')
          const rateTxt = line ? `<br/>${line.marker}竣工率 <b>${line.value}%</b>` : ''
          return `${day}<br/>${bars}${rateTxt}`
        },
      },
      legend: { data: ['受理', '已施工', '退单', '每日竣工率'], top: 4 },
      grid: { left: 40, right: 48, top: 44, bottom: 30 },
      xAxis: { type: 'category', data: daily.map(d => d.day.slice(5)) },
      // 左轴：工单量（柱）；右轴：竣工率 %（线）
      yAxis: [
        { type: 'value', name: '单', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
        { type: 'value', name: '竣工率%', min: 0, max: 100, nameTextStyle: { fontSize: 10 },
          axisLabel: { fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
      ],
      series: [
        { name: '受理', type: 'bar', data: daily.map(d => d.total), itemStyle: { color: '#1a6fdb' },
          label: { show: true, position: 'top', fontSize: 9, color: '#1a6fdb' },
          labelLayout: { hideOverlap: true } },
        { name: '已施工', type: 'bar', data: daily.map(d => d.done), itemStyle: { color: '#67c23a' },
          label: { show: true, position: 'top', fontSize: 9, color: '#67c23a' },
          labelLayout: { hideOverlap: true } },
        { name: '退单', type: 'bar', data: daily.map(d => d.refund), itemStyle: { color: '#e6a23c' },
          label: { show: true, position: 'top', fontSize: 9, color: '#e6a23c' },
          labelLayout: { hideOverlap: true } },
        { name: '每日竣工率', type: 'line', yAxisIndex: 1, smooth: true,
          data: daily.map(d => d.rate), symbol: 'circle', symbolSize: 5,
          itemStyle: { color: '#f56c6c' }, lineStyle: { width: 2, color: '#f56c6c' },
          label: { show: true, position: 'top', fontSize: 9, color: '#f56c6c', formatter: '{c}%' },
          labelLayout: { hideOverlap: true },
          z: 5 },
      ],
    })
  })
}

function renderComplaintCharts(wide) {
  const c = wide.complaint
  complaintDistricts.value = c.by_district
  nextTick(() => {
    if (!catRef.value || !cDistRef.value) return   // 组件已卸载（切走页面）：跳过渲染
    if (!catChart) catChart = echarts.init(catRef.value)
    const topCat = c.by_category.slice(0, 6)
    catChart.setOption({
      title: { text: '投诉分类 TOP6', left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'item' },
      series: [{ type: 'pie', radius: ['40%', '65%'], data: topCat.map(x => ({ name: x.category.slice(-8), value: x.count })) }],
    })
    if (!cDistChart) cDistChart = echarts.init(cDistRef.value)
    const topD = c.by_district.slice(0, 8)
    cDistChart.setOption({
      title: { text: '区县投诉分布', left: 'center', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      grid: { left: 80, right: 20, top: 40, bottom: 20 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: topD.map(d => d.district).reverse() },
      series: [{ type: 'bar', data: topD.map(d => d.count).reverse(), itemStyle: { color: '#9b59b6' } }],
    })
  })
}

async function load() {
  const dq = district.value === '全部' ? '' : district.value
  detailPage.value = 1
  loadAiVisit()
  try {
    // 卡片/趋势随区县过滤；排名/投诉概况固定全量 9 区县
    // dq 为空（全部）时两次请求参数完全相同，只发一次，省一个往返
    const wa = await api.get('/metrics/wide/summary', { params: { month: month.value } })
    const wf = dq ? await api.get('/metrics/wide/summary', { params: { month: month.value, district: dq } }) : wa
    if (wa.has_data) {
      hasWide.value = true
      const f = wf.has_data ? wf : wa
      buildCardsFromWide(f)
      renderTrend(f.install.daily)
      // 排名固定取全量（wa，不带 district 参数）：始终展示全荆州所有区县，不受筛选影响
      districtRanking.value = buildRanking(wa.install.by_district)
      renderComplaintCharts(wa)
      loadDetail()
      return
    }
  } catch (e) { /* fall through to demo */ }
  // 回退演示数据
  hasWide.value = false
  const [ov, rk, tr] = await Promise.all([
    api.get('/dashboard/overview'),
    api.get('/dashboard/district-ranking'),
    api.get('/dashboard/trend'),
  ])
  cards.value = ov.cards
  districtRanking.value = rk
  renderTrend(tr.map(t => ({ day: t.day, total: t.accepted, done: t.completed, refund: 0 })))
  loadDetail()
}

const onRz = () => { chart?.resize(); catChart?.resize(); cDistChart?.resize() }

// ---------------- 省公司邮件预警 ----------------
const alertMeta = ref({ has_data: false })
const alertType = ref('install')
const alertRows = ref([])
const alertSyncing = ref(false)
const alertDistrict = ref('')
const monitorLevel = ref('')
const alertStTag = s => (s || '').includes('施工') ? 'warning'
  : (s || '').includes('回访') || (s || '').includes('接收') ? 'success' : 'info'
const alertInstallTop = computed(() => {
  const st = alertMeta.value.install_status || []
  return st.length ? `最多：${st[0].status} ${st[0].count}` : ''
})
const alertComplaintTop = computed(() => {
  const st = alertMeta.value.complaint_status || []
  return st.length ? `最多：${st[0].status} ${st[0].count}` : ''
})
const monitorRows = computed(() =>
  monitorLevel.value ? alertRows.value.filter(r => (r.warn_level || '').includes(monitorLevel.value))
    : alertRows.value)

function pickAlert(type, level = '') {
  alertType.value = type
  monitorLevel.value = type === 'monitor' ? level : ''
  loadAlertOrders()
}

function onAlertDistrict() {
  loadAlerts()
}

async function loadAlerts() {
  try {
    const s = await api.get('/alerts/summary', { params: { district: alertDistrict.value } })
    alertMeta.value = s.has_data ? s : { has_data: false }
    if (s.has_data) loadAlertOrders()
  } catch (e) { alertMeta.value = { has_data: false } }
}

async function loadAlertOrders() {
  try {
    const r = await api.get('/alerts/orders', { params: { type: alertType.value, district: alertDistrict.value, limit: 2000 } })
    alertRows.value = r.rows || []
  } catch (e) { alertRows.value = [] }
}

async function syncAlert() {
  alertSyncing.value = true
  try {
    const r = await api.post('/alerts/fetch', null, { timeout: 120000 })
    if (r.ok) {
      ElMessage.success(`已同步预警快照：${r.mail_time}（待装${r.install} · 投诉${r.complaint} · 预约异常${r.book}）`)
      await Promise.all([loadAlerts(), load()])
    } else ElMessage.error(r.error || '同步失败')
  } catch (e) { ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message)) }
  finally { alertSyncing.value = false }
}

// ---------------- 省公司【重要通报】（区县装维指标，随区县筛选联动） ----------------
const reportMeta = ref({ has_data: false })
const reportTab = ref('cards')
const reportSyncing = ref(false)
const reportTimeouts = ref([])

const rAvg = (arr, k) => {
  const v = arr.map(x => Number(x[k])).filter(x => !isNaN(x))
  return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length * 10) / 10 : '-'
}
const rSum = (arr, k) => arr.reduce((a, x) => a + (Number(x[k]) || 0), 0)

const reportCards = computed(() => {
  const km = reportMeta.value
  if (!km.has_data) return []
  const K = km.kinds || {}
  const all = district.value === '全部'
  const one = k => (K[k] || {})[district.value] || {}
  const i = one('install'), c = one('complaint'), d = one('dup')
  const iv = Object.values(K.install || {}), cv = Object.values(K.complaint || {})
  const dash = '-' // 区县无数据占位
  return [
    { key: 'mrate', label: '当月竣工率', tone: 'green', unit: '%',
      value: all ? rAvg(iv, 'month_rate') : (i.month_rate ?? dash),
      sub: all ? `九区县平均 · ${rSum(iv, 'month_order')} 收单 / ${rSum(iv, 'month_done')} 竣工` : `收单 ${i.month_order ?? dash} · 竣工 ${i.month_done ?? dash}` },
    { key: 'trate', label: '装机及时率', tone: 'blue', unit: '%', drill: 'untimely',
      value: all ? rAvg(iv, 'timely_rate') : (i.timely_rate ?? dash),
      sub: all ? '九区县平均 · 全部上门不及时明细' : `不及时 ${i.visit_untimely ?? dash} 单 · 上门及时率 ${i.visit_timely_rate ?? dash}%` },
    { key: 'timeout', label: '装机超时工单', tone: 'red', unit: '单', drill: 'timeout',
      value: all ? rSum(iv, 'timeout_orders') : (i.timeout_orders ?? dash),
      sub: all ? '九区县合计 · 全部超时盯控明细' : `超时占比 ${i.timeout_rate ?? dash}%` },
    { key: 'crate', label: '投诉及时率', tone: 'purple', unit: '%', drill: 'complaint',
      value: all ? rAvg(cv, 'hq_normal_rate') : (c.hq_normal_rate ?? dash),
      sub: all ? '九区县平均（高品质+普通）· 当日超时明细' : `工单 ${c.total ?? dash} · 当日超时 ${c.day_timeout ?? dash} · 未完工 ${c.unfinished ?? dash}` },
    { key: 'h5', label: '投诉H5满意度', tone: 'orange', unit: '', drill: 'h5staff',
      value: all ? rAvg(cv, 'h5_satisfaction') : (c.h5_satisfaction ?? dash),
      sub: all ? '九区县平均 · 全部人员参评率' : `H5参评率 ${c.h5_join_rate ?? dash}%` },
    { key: 'dup', label: '广义重复投诉率', tone: 'blue', unit: '%', drill: 'dup',
      value: all ? rAvg(cv, 'dup_rate') : (c.dup_rate ?? dash),
      sub: all ? '九区县平均 · 全部重复投诉工单' : (d.dup_users != null ? `重复用户 ${d.dup_users} · 工单 ${d.total} · 第${d.rank}名` : '') },
  ]
})

const reportRanking = computed(() => {
  const K = reportMeta.value.kinds || {}
  const ins = K.install || {}, cmp = K.complaint || {}, dup = K.dup || {}
  return Object.keys(ins).map(d => ({
    district: d,
    month_order: ins[d].month_order ?? '-',
    month_done: ins[d].month_done ?? '-',
    month_rate: Number(ins[d].month_rate) || 0,
    timely_rate: ins[d].timely_rate ?? '-',
    timeout_orders: ins[d].timeout_orders ?? 0,
    hq_normal_rate: cmp[d]?.hq_normal_rate ?? '-',
    h5_satisfaction: cmp[d]?.h5_satisfaction ?? '-',
    dup_rate: dup[d]?.dup_rate ?? cmp[d]?.dup_rate ?? '-',
  })).sort((a, b) => b.month_rate - a.month_rate)
})

const reportTimeoutRows = computed(() =>
  district.value === '全部' ? reportTimeouts.value
    : reportTimeouts.value.filter(r => r.district === district.value))

async function loadReport() {
  try {
    const s = await api.get('/alerts/report-summary')
    reportMeta.value = s.has_data ? s : { has_data: false }
    if (s.has_data) {
      const t = await api.get('/alerts/report-timeouts', { params: { limit: 2000 } })
      reportTimeouts.value = t.rows || []
    }
  } catch (e) { reportMeta.value = { has_data: false } }
  // H5满意度通报（大音平台）随通报区块一并加载
  try {
    h5Survey.value = await api.get('/alerts/h5-survey')
  } catch (e) { h5Survey.value = { has_data: false, districts: [], staff: [], lows: [] } }
  finally { h5Loading.value = false }
}

// ---------------- H5满意度通报（集团大音平台） ----------------
const h5Survey = ref({ has_data: false, districts: [], staff: [], lows: [] })
const h5Loading = ref(true)
const h5DistRows = computed(() => {
  const all = h5Survey.value.districts || []
  const d = district.value
  const pick = (kind) => all.filter(x => x.kind === kind && (d === '全部' || x.district === d))
    .reduce((m, x) => (m[x.district] = x, m), {})
  const im = pick('装机'), cm = pick('投诉')
  const f = (v) => (v === 0 || v === undefined || v === null) ? '-' : v
  return Object.keys(Object.keys(im).length ? im : cm).map(k => ({
    district: k,
    ib: f(im[k]?.back_cnt), isv: f(im[k]?.survey_cnt), ijoin: f(im[k]?.join_rate), isat: f(im[k]?.satisfaction),
    cb: f(cm[k]?.back_cnt), csv: f(cm[k]?.survey_cnt), cjoin: f(cm[k]?.join_rate), csat: f(cm[k]?.satisfaction),
  }))
})
const h5StaffRows = computed(() =>
  (h5Survey.value.staff || [])
    .filter(s => district.value === '全部' || s.district === district.value)
    .map(s => ({ ...s, total: s.install + s.complaint })).slice(0, 50))
const h5LowRows = computed(() =>
  (h5Survey.value.lows || []).filter(l => district.value === '全部' || l.district === district.value))

async function syncReport() {
  reportSyncing.value = true
  try {
    const r = await api.post('/alerts/fetch-report', null, { timeout: 120000 })
    if (r.ok) {
      const bs = r.board_sync || {}
      const parts = []
      if (bs.install_replaced?.length) parts.push(`看板装机${bs.install_replaced.join('、')}`)
      if (bs.complaint_replaced?.length) parts.push(`看板投诉${bs.complaint_replaced.join('、')}`)
      if (bs.install_skipped?.length || bs.complaint_skipped?.length) parts.push('较早快照已跳过')
      ElMessage.success(`已同步：${r.subject}（通报日 ${r.data_date}）${parts.length ? '，' + parts.join('；') : ''}`)
      await Promise.all([loadReport(), load()])
    } else ElMessage.error(r.error || '同步失败')
  } catch (e) { ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message)) }
  finally { reportSyncing.value = false }
}

// ---------------- 卡片下钻明细对话框 ----------------
const DRILL_TITLES = {
  untimely: '上门不及时工单明细（装机宽表通报口径）',
  timeout: '超时工单盯控明细',
  complaint: '当日修不及时投诉工单明细',
  h5staff: '装维人员H5参评率（低于30%标红）',
  dup: '广义重复投诉工单明细',
  global: '',
}
const DRILL_LEVELS = { 白金: '全球通-白金卡工单', 钻石: '全球通-钻石卡工单' }
const drillVisible = ref(false)
const drillLoading = ref(false)
const drillKind = ref('timeout')
const drillLevel = ref('')
const drillRows = ref([])
const drillShowDone = ref(false)   // 全球通明细「显示已完工」：默认不勾选（只列待施工）
const drillTitle = computed(() =>
  drillKind.value === 'global'
    ? `${DRILL_LEVELS[drillLevel.value] || '全球通工单'}明细${drillShowDone.value ? '' : '（待施工）'}`
    : DRILL_TITLES[drillKind.value] || '明细')

async function openDrill(kind, opt = {}) {
  drillKind.value = kind
  drillLevel.value = opt.level || ''
  drillShowDone.value = false   // 每次打开重置：默认不勾选「显示已完工」
  // 预警区块的全球通卡片跟随预警区县筛选；通报卡片跟随顶部区县筛选
  const dist = kind === 'global' ? alertDistrict.value
    : (district.value === '全部' ? '' : district.value)
  drillVisible.value = true
  drillLoading.value = true
  drillRows.value = []
  try {
    const r = await api.get('/alerts/report-drill', { params: { kind, level: drillLevel.value, district: dist, show_done: drillShowDone.value, limit: 1000 } })
    drillRows.value = r.rows || []
  } catch (e) { drillRows.value = [] }
  finally { drillLoading.value = false }
}

// 全球通明细：「显示已完工」勾选变化后重拉（跟随预警区县筛选）
async function reloadDrill() {
  if (drillKind.value !== 'global') return
  drillLoading.value = true
  drillRows.value = []
  try {
    const r = await api.get('/alerts/report-drill', { params: { kind: 'global', level: drillLevel.value, district: alertDistrict.value, show_done: drillShowDone.value, limit: 1000 } })
    drillRows.value = r.rows || []
  } catch (e) { drillRows.value = [] }
  finally { drillLoading.value = false }
}

onMounted(() => { load(); loadAlerts(); loadReport(); window.addEventListener('resize', onRz) })
onBeforeUnmount(() => {
  window.removeEventListener('resize', onRz)
  chart?.dispose(); catChart?.dispose(); cDistChart?.dispose()
  chart = catChart = cDistChart = null   // dispose 后置空：防止卸载竞态中的异步回调触达已销毁实例
})
</script>

<style scoped>
/* v1.3.8 明细卡片头部：左控件 + 右同步信息/按钮，窄屏自动换行 */
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.detail-header-left,
.detail-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 左侧占满剩余宽度，右侧才能被推到最右；否则 flex-wrap 换行后右侧会贴左 */
.detail-header-left { flex: 1 1 auto; min-width: 0; }
.detail-header-right { margin-left: auto; justify-content: flex-end; }

.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.data-tag { font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #909399; color: #fff; }
.data-tag.wide { background: #1a6fdb; }
.stat-cards { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px; }
.stat-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); border-left: 4px solid #1a6fdb; }
.stat-card.blue { border-left-color: #1a6fdb; }
.stat-card.green { border-left-color: #67c23a; }
.stat-card.orange { border-left-color: #e6a23c; }
.stat-card.red { border-left-color: #f56c6c; }
.stat-card.purple { border-left-color: #9b59b6; }
.stat-card .label { color: #606266; font-size: 13px; }
.stat-card .value { font-size: 26px; font-weight: 600; margin-top: 6px; color: #303133; }
.stat-card .unit { font-size: 13px; font-weight: 400; margin-left: 2px; }
.stat-card .sub { font-size: 11px; color: #909399; margin-top: 4px; }
.page-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.card-header .title { font-weight: 600; }
.header-tip { font-size: 12px; color: #909399; }
.gp-red { color: #f56c6c; font-weight: 600; }
/* v1.6.3 是否触发H5 三态标签（与 AlertCenter.vue 保持一致）
   绿=已触发 / 橙=暂未触发(完工后会发) / 灰=不触发 */
.h5-tag {
  display: inline-block; padding: 0 7px; height: 20px; line-height: 20px;
  border-radius: 10px; font-size: 12px; font-weight: 600;
  border: 1px solid transparent; cursor: default; white-space: nowrap;
}
.h5-on   { color: #15803d; background: #dcfce7; border-color: #86efac; }
.h5-unknown { color: #b45309; background: #fef3c7; border-color: #fcd34d; }
.h5-no   { color: #909399; background: #f2f3f5; border-color: #dcdfe6; }
.rate-block { margin-bottom: 4px; }
.rate-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-weight: 600; font-size: 14px; margin-bottom: 8px; }
.chart-tall { height: 420px; }
.chart-mid { height: 400px; }
.rank-table-wrap { height: 420px; }
.alert-cards { grid-template-columns: repeat(7, 1fr); margin-bottom: 12px; }
.stat-card.clickable { cursor: pointer; transition: transform .15s, box-shadow .15s; }
.stat-card.clickable:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,.12); }
.stat-card.active { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
@media (max-width: 1200px) { .alert-cards { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 768px) {
  .alert-cards { grid-template-columns: repeat(2, 1fr); }
  .stat-cards { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-card { padding: 12px; }
  .stat-card .value { font-size: 22px; }
  .chart-tall { height: 300px; }
  .chart-mid { height: 260px; }
  .rank-table-wrap { height: auto; }
  .toolbar { flex-wrap: wrap; margin-bottom: 10px; }
  .page-card { margin-bottom: 10px; }
  .page-card :deep(.el-card__body) { padding: 12px; }
}

/* ============ AI装维即访 · 不规范 卡片（v1.3.15 视觉重构） ============ */
.ai-visit-card { border: 1px solid #edf0f5; }
.ai-visit-card :deep(.el-card__header) {
  padding: 12px 16px;
  background: linear-gradient(90deg, #f6faff 0%, #ffffff 62%);
  border-bottom: 1px solid #edf0f5;
}
.av-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.av-head-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.av-title { font-size: 15px; font-weight: 700; color: #1f2d3d; letter-spacing: .3px; }
.av-title::before {
  content: ''; display: inline-block; width: 3px; height: 14px; border-radius: 2px;
  background: #1a6fdb; margin-right: 8px; vertical-align: -2px;
}
.av-pill {
  display: inline-flex; align-items: center; height: 20px; padding: 0 8px; border-radius: 10px;
  font-size: 11.5px; font-weight: 600; background: #eef2f7; color: #5a6474; white-space: nowrap;
}
.av-pill.danger { background: #fef0f0; color: #f56c6c; }
.av-pill.light { background: #eef5ff; color: #1a6fdb; font-weight: 500; }
.av-head-note { font-size: 12px; color: #9aa3af; }

.av-section { margin-top: 14px; }
.av-section + .av-section { margin-top: 16px; padding-top: 14px; border-top: 1px dashed #e9edf3; }
.av-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.av-sec-title { font-size: 13px; font-weight: 700; color: #303133; }
.av-sec-title::before {
  content: ''; display: inline-block; width: 3px; height: 12px; background: #c4ccd8;
  border-radius: 2px; margin-right: 7px; vertical-align: -1px;
}
.av-sec-note { font-size: 11.5px; color: #a8b1bd; }
.av-sec-right { margin-left: auto; font-size: 12px; color: #8a94a6; white-space: nowrap; }
.av-sec-right b { font-size: 13px; color: #5a6474; }
.av-sec-right b.warn { color: #f56c6c; }

.av-table { --el-table-border-color: #eef1f6; font-variant-numeric: tabular-nums; }
.av-table :deep(th.el-table__cell) {
  background: #f7f9fc !important; color: #5f6b7d; font-weight: 600; font-size: 12px;
  letter-spacing: .3px; padding: 7px 0; border-bottom: 1px solid #e6ebf2;
}
.av-table :deep(.el-table__row > td) { border-bottom: 1px solid #f2f5f9; }
.av-table :deep(.el-table__row--striped > td) { background: #fafcfe; }
.av-table :deep(.el-table__row:hover > td) { background: #eef6ff !important; }
.av-table :deep(.el-table__inner-wrapper::before) { display: none; }
.av-table :deep(.el-table__empty-block) { min-height: 120px; }

/* ① 规范率矩阵 */
.av-rate-table :deep(.el-table__header tr:first-child th) {
  background: #eef2f8 !important; color: #445066; font-size: 12.5px; font-weight: 700;
}
.av-rate-table :deep(.el-table__cell) { padding: 5px 0; }
.av-dist { font-weight: 600; color: #303133; font-size: 12.5px; padding-left: 10px; }
.av-cell-wrap {
  display: flex; align-items: center; gap: 10px; padding: 0 18px;
}
.av-num {
  flex: none; width: 50px; text-align: right; font-size: 12.5px; font-weight: 700;
  color: #2f3a4a; line-height: 1.2;
}
.av-num.low { color: #f56c6c; }
.av-track { flex: 1; min-width: 40px; height: 5px; border-radius: 3px; background: #eef1f6; overflow: hidden; display: block; }
.av-track i { display: block; height: 100%; border-radius: 2px; background: linear-gradient(90deg, #9cc4f5, #1a6fdb); transition: width .3s; }
.av-num.low ~ .av-track i { background: linear-gradient(90deg, #ffb8b8, #f56c6c); }
.av-na { color: #dcdfe6; }
.av-rate-table :deep(.av-sum-row > td) { background: #f2f7ff !important; }
.av-rate-table :deep(.av-sum-row) .av-dist { color: #1a6fdb; }
.av-rate-table :deep(.av-sum-row) .av-num { color: #1a6fdb; }
.av-rate-table :deep(.av-sum-row) .av-num.low { color: #f56c6c; }

/* ② 不规范明细 */
.av-issue-table :deep(.el-table__cell) { padding: 7px 0; }
.av-dist-sm { font-size: 12px; color: #6b7688; padding-left: 6px; }
.av-person { display: flex; align-items: center; gap: 8px; padding-left: 6px; }
.av-avatar {
  width: 22px; height: 22px; border-radius: 50%; background: #eaf0f8; color: #5a6474;
  font-size: 11px; font-weight: 700; display: inline-flex; align-items: center;
  justify-content: center; flex: none;
}
.av-name { font-weight: 600; color: #303133; font-size: 12.5px; }
.av-repeat {
  font-size: 10.5px; font-weight: 700; color: #6b7688; background: #eef2f7;
  border-radius: 8px; padding: 0 5px; line-height: 16px;
}
.av-item { display: flex; align-items: center; gap: 7px; padding-left: 6px; }
.av-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.av-item.k-install .av-dot { background: #1a6fdb; }
.av-item.k-complaint .av-dot { background: #e6a23c; }
.av-item-name { font-weight: 600; color: #303133; font-size: 12.5px; }
.av-item-kind { font-size: 11px; padding: 0 6px; border-radius: 8px; line-height: 16px; }
.av-item.k-install .av-item-kind { color: #1a6fdb; background: #eef5ff; }
.av-item.k-complaint .av-item-kind { color: #b8791c; background: #fdf6ec; }
.av-order {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px;
  color: #5a6474; letter-spacing: -.2px; cursor: pointer; border-bottom: 1px dashed transparent;
}
.av-order:hover { color: #1a6fdb; border-bottom-color: #b3d0f5; }
.av-time { display: flex; flex-direction: column; line-height: 1.35; padding-left: 4px; }
.av-time b { font-weight: 600; font-size: 12px; color: #303133; }
.av-time i { font-style: normal; font-size: 11px; color: #9aa3af; }
.av-empty { padding: 28px 0; text-align: center; color: #b3bac5; font-size: 12.5px; }

.av-chips { display: flex; gap: 6px; margin-left: auto; flex-wrap: wrap; }
.av-chip {
  display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 9px;
  border-radius: 11px; background: #f5f7fa; font-size: 11.5px; color: #5f6b7d;
}
.av-chip b { font-weight: 700; color: #2f3a4a; }
.av-chip.k-install { background: #eef5ff; }
.av-chip.k-complaint { background: #fdf6ec; }
.av-chip.k-mixed { background: #f2f4f8; color: #6b7688; }

@media (max-width: 1280px) {
  .av-head-note.is-admin { display: none; }
}
@media (max-width: 900px) {
  .av-sec-note, .av-chips { display: none; }
  .av-head-note { display: none; }
  .av-cell-wrap { padding: 0 8px; }
}
</style>
