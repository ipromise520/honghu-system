<template>
  <div class="sheet" v-if="!loading">
    <div class="bar-top">
      <button class="icon-btn" @click="$router.back()"><Icon name="back" /></button>
      <h1>质检整改</h1>
      <button class="icon-btn" @click="load"><Icon name="refresh" /></button>
    </div>

    <div class="body">
      <div class="sum">
        <div class="sum-item">
          <b class="danger">{{ pending }}</b>
          <span>待整改</span>
        </div>
        <div class="sum-sep" />
        <div class="sum-item">
          <b class="ok">{{ fixed }}</b>
          <span>已提交</span>
        </div>
        <div class="sum-sep" />
        <div class="sum-item">
          <b>{{ total }}</b>
          <span>累计</span>
        </div>
      </div>

      <div class="hint" v-if="total">
        整改要求：核对质检不合格项 → 补齐照片/资料 → 在本页提交整改说明，市公司质检复核后闭环。
      </div>

      <div v-for="r in rows" :key="r.id" class="qc" :class="{ done: r.fix_status === 'fixed' }">
        <div class="qc-h">
          <span class="cat">{{ r.category }}</span>
          <span class="date">{{ r.date }}</span>
          <span class="st" :class="r.fix_status === 'fixed' ? 'st-ok' : 'st-wait'">
            {{ r.fix_status === 'fixed' ? '已提交' : '待整改' }}
          </span>
        </div>

        <div class="qc-item">{{ r.ng_item }}</div>
        <div class="qc-no">{{ r.order_no }}</div>
        <div class="qc-detail" v-if="r.ng_detail">{{ r.ng_detail }}</div>

        <div class="qc-meta">
          <span>{{ r.district }} · {{ r.grid || '—' }}</span>
          <span>质检人：{{ r.qc_person || '—' }}</span>
          <span v-if="r.penalty">考核 {{ Math.abs(r.penalty) }} 元</span>
          <span v-if="r.no_settle">不结算 {{ Math.abs(r.no_settle) }} 元</span>
        </div>

        <!-- 已整改 -->
        <div v-if="r.fix_status === 'fixed'" class="qc-done">
          <Icon name="check" :size="14" /> {{ r.fix_at }} 已提交：{{ r.fix_note || '—' }}
        </div>

        <!-- 待整改 -->
        <template v-else>
          <button v-if="editing !== r.id" class="btn-fix" @click="editing = r.id; note = ''">
            填写整改说明
          </button>
          <div v-else class="fix-box">
            <textarea v-model="note" rows="3" maxlength="200"
                      placeholder="例：已补拍尾纤占用照片，现场核对资源清查结果与图片一致" />
            <div class="fix-btns">
              <button class="btn-ghost" @click="editing = 0">取消</button>
              <button class="btn-primary" :disabled="submitting || !note.trim()" @click="submit(r)">
                {{ submitting ? '提交中…' : '提交整改' }}
              </button>
            </div>
          </div>
        </template>
      </div>

      <div v-if="!total" class="empty">
        <Icon name="inbox" :size="40" />
        <div>暂无质检整改任务</div>
        <div class="empty-sub">区县质检抽检出现不规范时会推送到这里</div>
      </div>
    </div>
  </div>

  <div class="sheet" v-else>
    <div class="bar-top"><span style="width:34px"></span><h1>质检整改</h1><span style="width:34px"></span></div>
    <div class="skeleton"><div class="sk" v-for="n in 4" :key="n"></div></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { mp } from '../api'
import Icon from '../components/Icon.vue'

const loading = ref(true)
const rows = ref([])
const total = ref(0)
const pending = ref(0)
const fixed = ref(0)
const editing = ref(0)
const note = ref('')
const submitting = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await mp.qc()
    rows.value = d.rows || []
    total.value = d.total || 0
    pending.value = d.pending || 0
    fixed.value = d.fixed || 0
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function submit(r) {
  if (!note.value.trim()) return
  submitting.value = true
  try {
    await mp.qcFix(r.id, note.value.trim())
    editing.value = 0
    note.value = ''
    await load()
  } catch (e) {
    alert(e.message || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sum {
  display: flex; align-items: center;
  background: linear-gradient(158deg, rgba(22, 40, 70, .72), rgba(9, 18, 34, .84));
  border: 1px solid rgba(96, 165, 250, .16);
  border-radius: 14px;
  padding: 14px 8px; box-shadow: var(--shadow); margin-bottom: 10px;
}
.sum-item { flex: 1; text-align: center; }
.sum-item b { display: block; font-size: 22px; line-height: 1.2; color: var(--ink); font-variant-numeric: tabular-nums; }
.sum-item b.danger { color: #f87171; }
.sum-item b.ok { color: #34d399; }
.sum-item span { font-size: 11.5px; color: var(--ink-3); }
.sum-sep { width: 1px; height: 26px; background: var(--line); }

.hint {
  font-size: 12px; color: var(--ink-2); line-height: 1.7;
  background: rgba(56, 189, 248, .10); border: 1px solid rgba(56, 189, 248, .18);
  border-radius: 12px; padding: 10px 12px; margin-bottom: 10px;
}

.qc {
  background: linear-gradient(158deg, rgba(22, 40, 70, .72), rgba(9, 18, 34, .84));
  border: 1px solid rgba(96, 165, 250, .14);
  border-radius: 14px; padding: 13px 14px; margin-bottom: 10px;
  box-shadow: var(--shadow); border-left: 3px solid #f87171;
}
.qc.done { border-left-color: #34d399; }
.qc-h { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.cat {
  font-size: 11.5px; font-weight: 600; color: #7dd3fc;
  background: rgba(56, 189, 248, .12); border: 1px solid rgba(56, 189, 248, .20);
  border-radius: 7px; padding: 2px 7px;
}
.date { font-size: 11.5px; color: var(--ink-3); }
.st { margin-left: auto; font-size: 11.5px; font-weight: 600; border-radius: 7px; padding: 2px 8px; }
.st-wait { color: #fca5a5; background: rgba(248, 113, 113, .14); border: 1px solid rgba(248, 113, 113, .28); }
.st-ok { color: #6ee7b7; background: rgba(52, 211, 153, .14); border: 1px solid rgba(52, 211, 153, .26); }

.qc-item { font-size: 15px; font-weight: 700; color: #fca5a5; line-height: 1.45; }
.qc.done .qc-item { color: var(--ink); }
.qc-no { font-size: 12px; color: var(--ink-3); margin-top: 3px; font-family: ui-monospace, Menlo, monospace; }
.qc-detail {
  font-size: 12.5px; color: var(--ink-2); line-height: 1.65; margin-top: 6px;
  background: rgba(255, 255, 255, .03); border: 1px solid rgba(96, 165, 250, .10);
  border-radius: 9px; padding: 8px 10px;
}
.qc-meta {
  display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11.5px;
  color: var(--ink-3); margin-top: 8px;
}

.btn-fix {
  width: 100%; margin-top: 10px; height: 38px; border: 1px solid rgba(56, 189, 248, .22); border-radius: 11px;
  background: rgba(56, 189, 248, .12); color: #7dd3fc; font-size: 14px; font-weight: 600;
}
.btn-fix:active { background: rgba(56, 189, 248, .24); }
.fix-box { margin-top: 10px; }
.fix-box textarea {
  width: 100%; border: 1px solid var(--line); border-radius: 11px; padding: 10px;
  font-size: 13.5px; line-height: 1.6; font-family: inherit; color: var(--ink);
  resize: none; outline: none; background: rgba(6, 14, 28, .7);
}
.fix-box textarea::placeholder { color: var(--ink-3); }
.fix-box textarea:focus { border-color: rgba(56, 189, 248, .7); background: rgba(7, 18, 36, .92); box-shadow: 0 0 0 3px rgba(56, 189, 248, .12); }
.fix-btns { display: flex; gap: 8px; margin-top: 8px; }
.btn-ghost {
  flex: 0 0 84px; height: 40px; border: 1px solid var(--line); background: rgba(125, 211, 252, .05);
  border-radius: 11px; color: var(--ink-2); font-size: 14px;
}
.fix-btns .btn-primary { flex: 1; height: 40px; }

.qc-done {
  margin-top: 10px; font-size: 12px; color: #6ee7b7; line-height: 1.7;
  background: rgba(52, 211, 153, .10); border: 1px solid rgba(52, 211, 153, .22);
  border-radius: 9px; padding: 8px 10px;
  display: flex; align-items: flex-start; gap: 6px;
}
/* Icon.vue 的 svg 不带宽高属性（尺寸历来由各页 CSS 给），
   这里不给就会撑满整块 —— 截图里那个巨大的绿勾就是这么来的 */
.qc-done :deep(svg) { width: 14px; height: 14px; flex: 0 0 14px; margin-top: 3px; }
.empty :deep(svg) { width: 40px; height: 40px; color: var(--ink-3); }

.empty { text-align: center; padding: 60px 0; color: var(--ink-3); }
.empty div { margin-top: 10px; font-size: 14px; }
.empty .empty-sub { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
</style>
