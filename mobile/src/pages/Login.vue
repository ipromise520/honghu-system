<template>
  <div class="login">
    <div class="bg-grid"></div>
    <div class="aurora a1"></div>
    <div class="aurora a2"></div>

    <main class="panel">
      <div class="brand">
        <div class="brand-mark"><Icon name="plug" :size="22" /></div>
        <div class="brand-text">
          <div class="kicker"><i class="live"></i>SECURE ACCESS</div>
          <h1>装维人员端</h1>
        </div>
      </div>
      <p class="sub">洪湖支撑服务中心 · 工单与绩效随身看</p>

      <form @submit.prevent="submit">
        <div class="field">
          <label for="lg-user">手机号 / 账号</label>
          <div class="control">
            <span class="ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                   stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
                <path d="M4.5 20c1.2-3.6 4.1-5.4 7.5-5.4s6.3 1.8 7.5 5.4" />
              </svg>
            </span>
            <input id="lg-user" v-model.trim="username" type="text" inputmode="numeric" placeholder="请输入账号" autocomplete="username" />
          </div>
        </div>
        <div class="field">
          <label for="lg-pass">密码</label>
          <div class="control">
            <span class="ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                   stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="4.5" y="10.5" width="15" height="10" rx="2.5" />
                <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
              </svg>
            </span>
            <input id="lg-pass" v-model="password" type="password" placeholder="请输入密码" autocomplete="current-password" />
          </div>
        </div>
        <p v-if="err" class="err">{{ err }}</p>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>

      <button class="ghost" type="button" @click="fillDemo">填入演示账号</button>
      <div class="meta-line"><span>v1.7.2</span><i></i><span>HONGHU MOBILE OPS</span></div>
    </main>

    <p class="tip">
      演示账号 <code>15171201240</code> / <code>Aa123456</code><br />
      微信内打开请使用「不校验合法域名」调试模式
    </p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'
import Icon from '../components/Icon.vue'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const err = ref('')

function fillDemo() {
  username.value = '15171201240'
  password.value = 'Aa123456'
}

async function submit() {
  if (!username.value || !password.value) {
    err.value = '请填写账号和密码'
    return
  }
  err.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    router.replace('/')
  } catch (e) {
    err.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* 装维端登录页：深色科技风，仅作用于本页，不污染其它移动端页面 */
.login {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: calc(28px + env(safe-area-inset-top, 0px)) 20px calc(28px + env(safe-area-inset-bottom, 0px));
  overflow: hidden;
  background:
    radial-gradient(120% 78% at 12% -8%, rgba(29, 78, 216, .55) 0%, rgba(29, 78, 216, 0) 58%),
    radial-gradient(100% 70% at 96% 108%, rgba(8, 145, 178, .42) 0%, rgba(8, 145, 178, 0) 62%),
    linear-gradient(168deg, #060c1b 0%, #081327 48%, #04091a 100%);
  color: #e6f1ff;
}

.bg-grid {
  position: absolute;
  inset: -1px;
  background-image:
    linear-gradient(rgba(96, 165, 250, .07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(96, 165, 250, .07) 1px, transparent 1px);
  background-size: 30px 30px;
  mask-image: radial-gradient(90% 65% at 50% 30%, #000 0%, transparent 78%);
  -webkit-mask-image: radial-gradient(90% 65% at 50% 30%, #000 0%, transparent 78%);
  pointer-events: none;
}

.aurora {
  position: absolute;
  border-radius: 50%;
  filter: blur(46px);
  opacity: .55;
  pointer-events: none;
  animation: drift 16s ease-in-out infinite alternate;
}
.aurora.a1 { width: 220px; height: 220px; top: -70px; left: -60px; background: rgba(56, 189, 248, .40); }
.aurora.a2 { width: 260px; height: 260px; bottom: -110px; right: -70px; background: rgba(99, 102, 241, .38); animation-duration: 21s; animation-direction: alternate-reverse; }
@keyframes drift {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to { transform: translate3d(26px, 18px, 0) scale(1.12); }
}

.panel {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 420px;
  padding: 26px 22px 18px;
  border-radius: 22px;
  background: linear-gradient(155deg, rgba(23, 42, 76, .78) 0%, rgba(8, 17, 34, .86) 100%);
  border: 1px solid rgba(96, 165, 250, .22);
  box-shadow:
    0 28px 64px -34px rgba(0, 0, 0, .95),
    0 0 0 1px rgba(255, 255, 255, .03) inset,
    0 1px 0 rgba(255, 255, 255, .08) inset;
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  animation: rise .55s cubic-bezier(.22, 1, .36, 1) both;
}
.panel::before {
  content: "";
  position: absolute;
  left: 18%; right: 18%; top: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(56, 189, 248, .85), rgba(129, 140, 248, .85), transparent);
}
@keyframes rise {
  from { opacity: 0; transform: translateY(18px) scale(.985); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 46px; height: 46px; flex: 0 0 46px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 14px;
  color: #dff3ff;
  background: linear-gradient(145deg, rgba(14, 165, 233, .95), rgba(67, 56, 202, .9));
  box-shadow: 0 10px 22px -10px rgba(14, 165, 233, .9), 0 0 0 1px rgba(255, 255, 255, .12) inset;
}
.brand-text { min-width: 0; }
.kicker {
  display: flex; align-items: center; gap: 6px;
  font: 600 9.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 2.2px;
  color: #7dd3fc;
  margin-bottom: 7px;
}
.kicker .live {
  width: 5px; height: 5px; border-radius: 50%;
  background: #22d3ee;
  box-shadow: 0 0 0 0 rgba(34, 211, 238, .6);
  animation: pulse 2.1s ease-out infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(34, 211, 238, .55); }
  70% { box-shadow: 0 0 0 7px rgba(34, 211, 238, 0); }
  100% { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0); }
}
.login h1 {
  margin: 0;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: 1.5px;
  background: linear-gradient(100deg, #f1f8ff 0%, #a5d8ff 55%, #7dd3fc 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.sub {
  margin: 14px 0 22px;
  font-size: 12px;
  letter-spacing: .4px;
  color: #8299b8;
}

.field { margin-bottom: 14px; }
.field label {
  display: block;
  margin-bottom: 7px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 1.1px;
  color: #8ba6c9;
}
.control {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 50px;
  padding: 0 14px;
  border-radius: 13px;
  background: rgba(5, 12, 25, .78);
  border: 1px solid rgba(94, 140, 190, .28);
  box-shadow: 0 1px 0 rgba(255, 255, 255, .04) inset, 0 2px 10px -6px rgba(0, 0, 0, .9) inset;
  transition: border-color .2s, box-shadow .2s, background .2s;
}
.control:focus-within {
  border-color: rgba(56, 189, 248, .68);
  background: rgba(7, 18, 36, .92);
  box-shadow: 0 0 0 3px rgba(56, 189, 248, .12), 0 0 22px -4px rgba(56, 189, 248, .38);
}
.ico { display: flex; color: #5b7ba8; }
.ico svg { width: 17px; height: 17px; }
.control:focus-within .ico { color: #38bdf8; }

.field input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: 0;
  background: transparent;
  color: #e8f2ff;
  font: inherit;
  font-size: 15px;
  letter-spacing: .3px;
  outline: none;
  padding: 0;
}
.field input::placeholder { color: rgba(139, 166, 201, .55); }
.field input:-webkit-autofill,
.field input:-webkit-autofill:focus {
  -webkit-text-fill-color: #e8f2ff;
  transition: background-color 100000s ease-in-out 0s;
}

.err {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 0 12px;
  padding: 9px 11px;
  border-radius: 10px;
  font-size: 12.5px;
  color: #fecaca;
  background: rgba(239, 68, 68, .12);
  border: 1px solid rgba(239, 68, 68, .32);
}

.btn-primary {
  position: relative;
  overflow: hidden;
  width: 100%;
  height: 50px;
  margin-top: 6px;
  border: 0;
  border-radius: 13px;
  color: #fff;
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 3px;
  background: linear-gradient(100deg, #0ea5e9 0%, #2563eb 52%, #4f46e5 100%);
  box-shadow: 0 14px 30px -14px rgba(37, 99, 235, .95), 0 1px 0 rgba(255, 255, 255, .22) inset;
  transition: transform .14s ease, box-shadow .2s, filter .2s;
}
.btn-primary::after {
  content: "";
  position: absolute;
  top: 0; bottom: 0; left: -60%;
  width: 55%;
  background: linear-gradient(100deg, transparent, rgba(255, 255, 255, .34), transparent);
  transform: skewX(-18deg);
  animation: sweep 3.6s ease-in-out infinite;
}
@keyframes sweep {
  0%, 62% { left: -60%; }
  100% { left: 118%; }
}
.btn-primary:active { transform: scale(.985); filter: brightness(1.05); }
.btn-primary[disabled] { opacity: .65; pointer-events: none; }

.ghost {
  width: 100%;
  margin-top: 13px;
  padding: 10px;
  border: 1px dashed rgba(125, 211, 252, .26);
  border-radius: 11px;
  background: rgba(125, 211, 252, .04);
  color: #96b9dc;
  font: inherit;
  font-size: 12.5px;
  letter-spacing: .5px;
  transition: border-color .2s, color .2s, background .2s;
}
.ghost:active { border-color: rgba(125, 211, 252, .55); background: rgba(125, 211, 252, .10); color: #cfe8ff; }

.meta-line {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  margin-top: 16px;
  font: 500 9.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 1.6px;
  color: rgba(125, 155, 190, .62);
}
.meta-line i { width: 3px; height: 3px; border-radius: 50%; background: rgba(125, 155, 190, .45); }

.tip {
  position: relative;
  z-index: 1;
  margin: 20px 4px 0;
  text-align: center;
  font-size: 11.5px;
  line-height: 1.9;
  color: rgba(148, 178, 210, .82);
}
.tip code {
  padding: 1px 6px;
  border-radius: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #b9e2ff;
  background: rgba(125, 211, 252, .10);
  border: 1px solid rgba(125, 211, 252, .16);
}

@media (max-width: 360px) {
  .panel { padding: 22px 18px 16px; border-radius: 19px; }
  .login h1 { font-size: 19px; letter-spacing: 1.2px; }
}

@media (prefers-reduced-motion: reduce) {
  .panel, .aurora, .kicker .live, .btn-primary::after { animation: none !important; }
}
</style>