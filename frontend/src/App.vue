<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交/折算" : "只读旁观" }}）</p>
      <nav class="topbar">
        <button :class="{ active: page === 'scan' }" @click="page = 'scan'">扫描台</button>
        <button :class="{ active: page === 'equiv' }" @click="goEquiv">等效曲线</button>
        <span class="spacer"></span>
        <button class="secondary" @click="refreshAll">刷新</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- 扫描台 -->
      <div v-show="page === 'scan'">
        <section v-if="isWriter">
          <h2>提交扫描</h2>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <label>光照类型</label>
          <select v-model="irrClass">
            <option value="高照">高照</option>
            <option value="低照">低照</option>
          </select>
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <h2>扫描台账</h2>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>光照</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td><span class="tag" :class="row.irr_class === '高照' ? 'irr-hi' : 'irr-lo'">{{ row.irr_class }}</span></td>
                <td>{{ fmt(row.voc_v) }}</td>
                <td>{{ fmt(row.isc_a) }}</td>
                <td>{{ fmt(row.fill_factor) }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- 等效曲线专页 -->
      <div v-show="page === 'equiv'">
        <p class="sub">把两笔以上<strong>已办结</strong>的同类（高照/低照）扫描拖进折算篮，拖条调权重，预览等效曲线后再点折算；折算新单以“待处理”重新入队，并同时记一笔折算流水。</p>
        <div class="equiv-grid">
          <section>
            <h2>已办结扫描（可拖入）</h2>
            <p v-if="doneLogs.length === 0" class="hint">暂无已办结扫描。</p>
            <div
              v-for="row in doneLogs"
              :key="row.id"
              class="scan-card"
              :class="{ dim: basketClass && basketClass !== row.irr_class }"
              draggable="true"
              @dragstart="onDragStart($event, row)"
            >
              <div>
                <strong>#{{ row.id }} {{ row.string_code }}</strong>
                <span class="tag" :class="row.irr_class === '高照' ? 'irr-hi' : 'irr-lo'">{{ row.irr_class }}</span>
                <span class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span>
              </div>
              <div class="muted">Voc {{ fmt(row.voc_v) }} ｜ Isc {{ fmt(row.isc_a) }} ｜ FF {{ fmt(row.fill_factor) }}</div>
              <button class="mini" @click="addToBasket(row)">加入折算篮</button>
            </div>
          </section>

          <section
            class="dropzone"
            :class="{ over: dragOver }"
            @dragover.prevent="dragOver = true"
            @dragleave.prevent="dragOver = false"
            @drop.prevent="onDrop"
          >
            <h2>折算篮</h2>
            <p v-if="basket.length === 0" class="hint">把已办结扫描拖到这里，至少两笔。</p>
            <div v-for="b in basket" :key="b.id" class="basket-item">
              <div class="basket-head">
                <strong>#{{ b.id }} {{ b.string_code }}</strong>
                <span class="tag" :class="b.irr_class === '高照' ? 'irr-hi' : 'irr-lo'">{{ b.irr_class }}</span>
                <button class="mini danger" @click="removeFromBasket(b.id)">移出</button>
              </div>
              <div class="weight-row">
                <input type="range" min="1" max="100" step="1" v-model.number="weights[b.id]" @input="schedulePreview" />
                <span class="weight-val">权重 {{ weights[b.id] }}（占比 {{ sharePct(b.id) }}%）</span>
              </div>
            </div>

            <div v-if="basket.length > 0" class="preview-box">
              <h3>等效曲线预览{{ previewLoading ? "（计算中…）" : "" }}</h3>
              <table class="preview-table">
                <tr><td>光照类型</td><td>{{ basketClass }}</td></tr>
                <tr><td>参与笔数</td><td>{{ basket.length }}</td></tr>
                <tr><td>等效 Voc</td><td>{{ preview ? fmt(preview.voc_v) : "—" }}</td></tr>
                <tr><td>等效 Isc</td><td>{{ preview ? fmt(preview.isc_a) : "—" }}</td></tr>
                <tr><td>加权填充因子</td><td><strong>{{ preview ? fmt(preview.fill_factor) : "—" }}</strong></td></tr>
              </table>
              <button v-if="isWriter" :disabled="basket.length < 2 || previewLoading || converting" @click="convert">
                {{ converting ? "折算入账中…" : "按以上权重折算并入队" }}
              </button>
              <p v-else class="hint">只读账号可查看预览，折算请由扫描员操作。</p>
            </div>
            <p v-if="equivError" class="err">{{ equivError }}</p>
            <p v-if="equivOk" class="ok-text">{{ equivOk }}</p>
          </section>
        </div>

        <section>
          <h2>折算流水</h2>
          <p v-if="records.length === 0" class="hint">还没有折算记录。</p>
          <table>
            <thead>
              <tr><th>流水#</th><th>新单#</th><th>光照</th><th>笔数</th><th>Voc</th><th>Isc</th><th>加权FF</th><th>新单状态</th><th>来源(权重)</th><th>操作人</th><th>时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in records" :key="r.id">
                <td>{{ r.id }}</td>
                <td>{{ r.result_scan_id }}</td>
                <td><span class="tag" :class="r.irr_class === '高照' ? 'irr-hi' : 'irr-lo'">{{ r.irr_class }}</span></td>
                <td>{{ r.item_count }}</td>
                <td>{{ fmt(r.voc_v) }}</td>
                <td>{{ fmt(r.isc_a) }}</td>
                <td>{{ fmt(r.fill_factor) }}</td>
                <td>
                  <span class="tag" :class="r.result_status === 'pending' ? 'pending' : 'ok'">{{ r.result_status === 'pending' ? '待处理' : '已完成' }}</span>
                  <span v-if="r.result_verdict" class="tag" :class="r.result_verdict === '合格' ? 'ok' : 'bad'">{{ r.result_verdict }}</span>
                </td>
                <td class="muted">{{ r.items.map(i => '#' + i.source_scan_id + '×' + fmt(i.weight)).join('，') }}</td>
                <td>{{ r.created_by }}</td>
                <td class="muted">{{ fmtTime(r.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const records = ref([]);
const page = ref("scan");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const irrClass = ref("高照");
const error = ref("");
const loading = ref(false);
let timer;

// 折算专页状态
const basket = ref([]);           // 选中的已办结扫描
const weights = ref({});          // id -> 拖条权重
const preview = ref(null);
const previewLoading = ref(false);
const converting = ref(false);
const equivError = ref("");
const equivOk = ref("");
const dragOver = ref(false);
let previewTimer;

const isWriter = computed(() => session.value?.role === "writer");
const doneLogs = computed(() => logs.value.filter(r => r.status === "done"));
const basketClass = computed(() => basket.value[0]?.irr_class || "");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(v) {
  return typeof v === "number" ? Math.round(v * 1000) / 1000 : v;
}
function fmtTime(t) {
  return t ? new Date(t).toLocaleString("zh-CN", { hour12: false }) : "";
}
function sharePct(id) {
  const total = basket.value.reduce((s, b) => s + Number(weights.value[b.id] || 0), 0);
  return total > 0 ? Math.round((weights.value[id] / total) * 100) : 0;
}

async function refreshLogs() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshRecords() {
  const res = await fetch("/api/equiv/records", { headers: headers() });
  if (res.ok) records.value = await res.json();
}
async function refreshAll() {
  await refreshLogs();
  if (page.value === "equiv") await refreshRecords();
}
function goEquiv() {
  page.value = "equiv";
  refreshRecords();
  schedulePreview();
}

async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshLogs();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  if (previewTimer) clearTimeout(previewTimer);
  session.value = null;
  logs.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
        irr_class: irrClass.value,
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshLogs();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

// ---- 折算篮：拖拽 / 点击两种加入方式 ----
function onDragStart(ev, row) {
  ev.dataTransfer.setData("text/plain", String(row.id));
  ev.dataTransfer.effectAllowed = "copy";
}
function onDrop(ev) {
  dragOver.value = false;
  const id = Number(ev.dataTransfer.getData("text/plain"));
  const row = logs.value.find(r => r.id === id);
  if (!row) return;
  addToBasket(row);
}
function addToBasket(row) {
  equivError.value = "";
  if (row.status !== "done") {
    equivError.value = `#${row.id} 尚未办结，未办结扫描不能拖入折算`;
    return;
  }
  if (basket.value.some(b => b.id === row.id)) return;
  if (basketClass.value && basketClass.value !== row.irr_class) {
    equivError.value = `折算篮已是${basketClass.value}曲线，高照与低照两类不能混选`;
    return;
  }
  basket.value = [...basket.value, row];
  weights.value = { ...weights.value, [row.id]: 50 };
  schedulePreview();
}
function removeFromBasket(id) {
  basket.value = basket.value.filter(b => b.id !== id);
  const next = { ...weights.value };
  delete next[id];
  weights.value = next;
  equivError.value = "";
  schedulePreview();
}

function previewPayload() {
  return {
    items: basket.value.map(b => ({ id: b.id, weight: Number(weights.value[b.id] || 1) })),
  };
}
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer);
  if (basket.value.length < 2) { preview.value = null; return; }
  previewLoading.value = true;
  previewTimer = setTimeout(fetchPreview, 250);
}
async function fetchPreview() {
  try {
    const res = await fetch("/api/equiv/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(previewPayload()),
    });
    const data = await res.json();
    if (!res.ok) {
      preview.value = null;
      equivError.value = data.detail || "预览失败";
      return;
    }
    equivError.value = "";
    preview.value = data;
  } catch {
    preview.value = null;
  } finally {
    previewLoading.value = false;
  }
}
async function convert() {
  equivError.value = "";
  equivOk.value = "";
  if (basket.value.length < 2) { equivError.value = "至少选择两笔已办结扫描"; return; }
  converting.value = true;
  try {
    const res = await fetch("/api/equiv/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(previewPayload()),
    });
    const data = await res.json();
    if (!res.ok) { equivError.value = data.detail || "折算失败"; return; }
    equivOk.value = `已折算：新单 #${data.id}（FF=${fmt(data.fill_factor)}）以“待处理”入队，流水 #${data.record.id} 同次入账。`;
    basket.value = [];
    weights.value = {};
    preview.value = null;
    await refreshLogs();
    await refreshRecords();
  } catch {
    equivError.value = "折算时网络异常";
  } finally {
    converting.value = false;
  }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshLogs();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
  if (previewTimer) clearTimeout(previewTimer);
});
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { color: #bbf7d0; font-size: 1.05rem; margin: 0 0 0.75rem; }
h3 { color: #bbf7d0; font-size: 0.95rem; margin: 0.5rem 0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.hint { color: #86efac99; font-size: 0.88rem; }
.muted { color: #a7f3d0aa; font-size: 0.85rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
button.secondary { background: #365314; }
button.mini { padding: 0.2rem 0.6rem; font-size: 0.78rem; margin-top: 0.4rem; background: #15803d; }
button.mini.danger { background: #7f1d1d; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar button.active { background: #22c55e; color: #052e16; }
.topbar .spacer { flex: 1; }
.err { color: #fecaca; }
.ok-text { color: #bbf7d0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; margin-right: 0.25rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.irr-hi { background: #1e3a8a; color: #bfdbfe; }
.irr-lo { background: #4c1d95; color: #ddd6fe; }
.equiv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
@media (max-width: 860px) { .equiv-grid { grid-template-columns: 1fr; } }
.scan-card { border: 1px dashed #4ade80; border-radius: 8px; padding: 0.6rem 0.8rem; margin-bottom: 0.6rem; background: #052e16; cursor: grab; }
.scan-card.dim { opacity: 0.35; }
.dropzone { border: 2px dashed #4ade80; min-height: 220px; transition: background 0.15s; }
.dropzone.over { background: #166534; }
.basket-item { background: #052e16; border-radius: 8px; padding: 0.6rem 0.8rem; margin-bottom: 0.6rem; }
.basket-head { display: flex; align-items: center; gap: 0.5rem; }
.weight-row { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.3rem; }
.weight-row input[type=range] { flex: 1; margin: 0; accent-color: #4ade80; }
.weight-val { white-space: nowrap; font-size: 0.85rem; color: #a7f3d0; }
.preview-box { border-top: 1px solid #166534; margin-top: 0.75rem; padding-top: 0.5rem; }
.preview-table td { border: none; padding: 0.2rem 0.5rem; }
</style>
