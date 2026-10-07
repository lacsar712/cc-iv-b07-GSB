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
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读旁观" }}）</p>
      <nav class="tabs">
        <button :class="{ active: tab === 'logs' }" @click="tab = 'logs'">扫描台账</button>
        <button :class="{ active: tab === 'fold' }" @click="tab = 'fold'">等效曲线</button>
        <button :class="{ active: tab === 'folds' }" @click="tab = 'folds'; fetchFolds()">折算流水</button>
        <span class="spacer"></span>
        <button class="secondary" @click="refresh">刷新列表</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- 扫描台账 -->
      <template v-if="tab === 'logs'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <label>光照类型</label>
          <select v-model="lightType">
            <option value="high">高照</option>
            <option value="low">低照</option>
          </select>
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>光照</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ lightLabel(row.light_type) }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <!-- 等效曲线折算专页 -->
      <template v-if="tab === 'fold'">
        <p class="sub">把两笔以上<strong>已办结</strong>扫描拖进折算筐，拖条调权重后点折算；只接受同一光照类型（高照/低照不混选）。折算成功后生成一条等效新单入队等候处理。</p>
        <div class="fold-grid">
          <section>
            <h3>已办结扫描（拖入折算筐）</h3>
            <p class="hint">未办结单据不参与折算；不同光照类型不能混入同一筐。</p>
            <div
              v-for="row in doneScans"
              :key="row.id"
              class="scan-card"
              :class="{ disabled: !canAdd(row.id) }"
              draggable="true"
              @dragstart="onDragStart($event, row.id)"
            >
              <div class="card-main">
                <strong>#{{ row.id }} {{ row.string_code }}</strong>
                <span class="tag" :class="row.light_type === 'high' ? 'ok' : 'low'">{{ lightLabel(row.light_type) }}</span>
                <span class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span>
              </div>
              <div class="card-meta">Voc {{ row.voc_v }} ｜ Isc {{ row.isc_a }} ｜ FF {{ row.fill_factor }}</div>
              <button class="mini" :disabled="!canAdd(row.id)" @click="addScan(row.id)">加入</button>
            </div>
            <p v-if="doneScans.length === 0" class="hint">暂无可参与折算的已办结扫描。</p>
          </section>

          <section>
            <h3>折算筐</h3>
            <div
              class="dropzone"
              :class="{ over: dragOver }"
              @dragover.prevent="dragOver = true"
              @dragleave="dragOver = false"
              @drop.prevent="onDrop"
            >
              <p v-if="basket.length === 0" class="hint">拖两笔以上已办结扫描到这里</p>
              <div v-for="(item, idx) in basket" :key="item.id" class="basket-row">
                <div>
                  <strong>#{{ item.id }} {{ logsById[item.id]?.string_code }}</strong>
                  <span class="tag" :class="logsById[item.id]?.light_type === 'high' ? 'ok' : 'low'">{{ lightLabel(logsById[item.id]?.light_type) }}</span>
                  <button class="mini danger" @click="removeScan(item.id)">移出</button>
                </div>
                <div class="slider-row">
                  <input type="range" min="0" max="100" step="1" v-model.number="basket[idx].weight" />
                  <span class="weight">权重 {{ item.weight }}（占比 {{ sharePct(item.id) }}%）</span>
                </div>
              </div>
            </div>

            <div v-if="foldError" class="err">拒收：{{ foldError }}</div>

            <div v-if="preview" class="preview">
              <h3>等效曲线预览</h3>
              <p>
                <span class="tag" :class="preview.light_type === 'high' ? 'ok' : 'low'">{{ preview.light_label }}</span>
                来源 {{ preview.source_count }} 笔
              </p>
              <table>
                <tbody>
                  <tr><td>等效开路电压</td><td>{{ preview.voc_v }} V</td></tr>
                  <tr><td>等效短路电流</td><td>{{ preview.isc_a }} A</td></tr>
                  <tr><td>等效填充因子</td><td><strong>{{ preview.fill_factor }}</strong></td></tr>
                </tbody>
              </table>
            </div>
            <p v-else-if="!foldError && basket.length < 2" class="hint">至少两笔同光照类型的已办结扫描才能折算。</p>

            <template v-if="isWriter">
              <button :disabled="loading || !preview" @click="fold">折算并入队</button>
            </template>
            <p v-else class="hint">只读旁观账号可查看预览，但不能替人点折算。</p>
            <p v-if="foldOk" class="ok-text">{{ foldOk }}</p>
          </section>
        </div>
      </template>

      <!-- 折算流水 -->
      <template v-if="tab === 'folds'">
        <section>
          <table v-if="folds.length">
            <thead>
              <tr><th>流水</th><th>光照</th><th>来源笔数与权重</th><th>等效FF</th><th>新单</th><th>新单状态</th><th>操作人</th><th>时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in folds" :key="f.id">
                <td>#{{ f.id }}</td>
                <td><span class="tag" :class="f.light_type === 'high' ? 'ok' : 'low'">{{ f.light_label }}</span></td>
                <td>
                  <span v-for="s in f.sources" :key="s.scan_id" class="src-chip">
                    #{{ s.scan_id }} {{ s.string_code }}：{{ pct(s.weight_share) }}%
                  </span>
                </td>
                <td>{{ f.fill_factor }}</td>
                <td>#{{ f.new_scan_id }} {{ f.new_string_code }}</td>
                <td>
                  <span class="tag" :class="f.new_status === 'pending' ? 'pending' : 'ok'">
                    {{ f.new_status === 'pending' ? '待处理' : '已完成' }}
                  </span>
                  <span v-if="f.new_verdict" class="tag" :class="f.new_verdict === '合格' ? 'ok' : 'bad'">{{ f.new_verdict }}</span>
                </td>
                <td>{{ f.created_by }}</td>
                <td>{{ formatTime(f.created_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="hint">还没有折算流水。</p>
        </section>
      </template>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
const session = ref(null);
const logs = ref([]);
const folds = ref([]);
const tab = ref("logs");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const lightType = ref("high");
const error = ref("");
const loading = ref(false);

const basket = ref([]);
const dragOver = ref(false);
const preview = ref(null);
const foldError = ref("");
const foldOk = ref("");
let previewTimer = null;
let timer;

const LIGHT_LABELS = { high: "高照", low: "低照" };
const lightLabel = (v) => LIGHT_LABELS[v] || v;
const pct = (x) => Math.round(x * 1000) / 10;
const isWriter = computed(() => session.value?.role === "writer");
const doneScans = computed(() => logs.value.filter((r) => r.status === "done"));
const logsById = computed(() => Object.fromEntries(logs.value.map((r) => [r.id, r])));
const basketItems = computed(() =>
  basket.value
    .map((b) => ({ ...b, row: logs.value.find((r) => r.id === b.id) }))
    .filter((b) => b.row)
);
const basketLight = computed(() => basketItems.value[0]?.row.light_type || null);

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function formatTime(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
  if (tab.value === "folds") fetchFolds();
}
async function fetchFolds() {
  if (!session.value) return;
  const res = await fetch("/api/folds", { headers: headers() });
  if (res.ok) folds.value = await res.json();
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
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  basket.value = [];
  preview.value = null;
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
        light_type: lightType.value,
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

function onDragStart(e, id) {
  e.dataTransfer.setData("text/scan-id", String(id));
  e.dataTransfer.setData("text/plain", String(id));
  e.dataTransfer.effectAllowed = "copy";
}
function canAdd(id) {
  const row = logs.value.find((r) => r.id === id);
  if (!row || row.status !== "done") return false;
  if (basket.value.some((b) => b.id === id)) return false;
  if (basketLight.value && basketLight.value !== row.light_type) return false;
  return true;
}
function addScan(id) {
  foldOk.value = "";
  const row = logs.value.find((r) => r.id === id);
  if (!row) { foldError.value = "扫描单据不存在"; return; }
  if (row.status !== "done") { foldError.value = `#${id} 尚未办结，未办结单据不能参与折算`; return; }
  if (basket.value.some((b) => b.id === id)) { foldError.value = `#${id} 已在折算筐中`; return; }
  if (basketLight.value && basketLight.value !== row.light_type) {
    foldError.value = "高照与低照两类不能混选，请先清空折算筐再换另一类";
    return;
  }
  foldError.value = "";
  basket.value.push({ id, weight: 50 });
}
function onDrop(e) {
  dragOver.value = false;
  const raw = e.dataTransfer.getData("text/scan-id") || e.dataTransfer.getData("text/plain");
  const id = Number(raw);
  if (id) addScan(id);
}
function removeScan(id) {
  basket.value = basket.value.filter((b) => b.id !== id);
}
function sharePct(id) {
  const s = preview.value?.sources?.find((x) => x.scan_id === id);
  return s ? pct(s.weight_share) : "—";
}

watch(
  basket,
  () => {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 250);
  },
  { deep: true }
);

async function runPreview() {
  foldError.value = "";
  if (basket.value.length === 0) { preview.value = null; return; }
  if (basket.value.length < 2) { preview.value = null; return; }
  try {
    const res = await fetch("/api/folds/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        ids: basket.value.map((b) => b.id),
        weights: Object.fromEntries(basket.value.map((b) => [b.id, b.weight])),
      }),
    });
    const data = await res.json();
    if (!res.ok) { preview.value = null; foldError.value = data.detail || "预览失败"; return; }
    preview.value = data;
  } catch {
    preview.value = null;
    foldError.value = "预览时网络异常";
  }
}

async function fold() {
  if (!preview.value) return;
  foldError.value = "";
  foldOk.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/folds", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        ids: basket.value.map((b) => b.id),
        weights: Object.fromEntries(basket.value.map((b) => [b.id, b.weight])),
      }),
    });
    const data = await res.json();
    if (!res.ok) { foldError.value = data.detail || "折算失败"; return; }
    foldOk.value = `折算完成：新单 #${data.id}（FF ${data.fill_factor}）已入队，等候工人处理，未直接办结。流水号 #${data.fold_id}。`;
    basket.value = [];
    preview.value = null;
    await refresh();
    fetchFolds();
  } catch {
    foldError.value = "折算时网络异常";
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h3 { margin-top: 0; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.hint { color: #86efac; opacity: 0.75; font-size: 0.85rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
button.secondary { background: #365314; }
button.mini { padding: 0.2rem 0.6rem; font-size: 0.78rem; background: #15803d; float: right; }
button.mini.danger { background: #7f1d1d; }
.err { color: #fecaca; }
.ok-text { color: #bbf7d0; }
.tabs { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 1rem; }
.tabs .spacer { flex: 1; }
.tabs button.active { background: #22c55e; color: #052e16; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; margin-right: 0.25rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.low { background: #1e3a8a; color: #bfdbfe; }
.pending { background: #854d0e; color: #fde68a; }
.fold-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.scan-card { border: 1px solid #166534; border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; background: #052e16; cursor: grab; }
.scan-card.disabled { opacity: 0.45; cursor: not-allowed; }
.card-main { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
.card-meta { font-size: 0.82rem; color: #a7f3d0; margin: 0.35rem 0; }
.dropzone { border: 2px dashed #4ade80; border-radius: 8px; padding: 0.75rem; min-height: 120px; margin-bottom: 0.75rem; }
.dropzone.over { border-color: #fde68a; background: #166534; }
.basket-row { border-bottom: 1px solid #166534; padding: 0.5rem 0; }
.slider-row { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.35rem; }
.slider-row input[type=range] { margin: 0; }
.weight { font-size: 0.82rem; white-space: nowrap; color: #a7f3d0; }
.preview { background: #022c22; border: 1px solid #4ade80; border-radius: 6px; padding: 0.5rem 0.9rem; margin-bottom: 0.75rem; }
.src-chip { display: inline-block; background: #022c22; border: 1px solid #166534; border-radius: 4px; padding: 0.1rem 0.4rem; margin: 0.1rem 0.25rem 0.1rem 0; font-size: 0.8rem; }
</style>
