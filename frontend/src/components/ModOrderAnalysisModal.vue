<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { currentLocale, t } from '../languages'
import { useAppStore } from '../store'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])
const store = useAppStore()

const idleState = () => ({
  id: '',
  status: 'idle',
  running: false,
  phase: 'idle',
  progress: 0,
  total_mods: 0,
  completed_mods: 0,
  current_mod: '',
  ai_enabled: false,
  degraded: false,
  baseline: 'current',
  warnings: [],
  error: '',
  result: null,
  events: [],
  stats: {},
})

const session = ref(idleState())
const baseline = ref('current')
const history = ref([])
const historyBusy = ref(false)
const historyError = ref('')
const viewingHistoryId = ref('')
const pending = ref(false)
const error = ref('')
let revision = 0
let timer = 0

const result = computed(() => session.value.result || null)
const items = computed(() => result.value?.items || [])
const conflicts = computed(() => result.value?.conflicts || [])
const warnings = computed(() => result.value?.warnings || [])
const itemById = computed(() => new Map(items.value.map((item) => [item.mod_id, item])))
const progress = computed(() => Math.min(100, Math.max(0, Number(session.value.progress) || 0)))
const phaseKey = computed(() => {
  const phase = session.value.phase
  if (phase === 'cancelling') return 'aiOrder.cancel'
  if (['collecting', 'evaluating', 'analyzing', 'global', 'done', 'cancelled', 'error'].includes(phase)) {
    return `aiOrder.${phase}`
  }
  return session.value.running ? 'aiOrder.collecting' : 'aiOrder.done'
})
const canStart = computed(() => !pending.value && !session.value.running && store.activeIds.length > 0)
const canApply = computed(() => !pending.value && session.value.status === 'ready' && !!result.value)
const events = computed(() => session.value.events || [])
const stats = computed(() => session.value.stats || {})
const recommended = computed(() => {
  if (!result.value) return []
  return result.value.recommended_order.map(
    (modId) => itemById.value.get(modId) || { mod_id: modId, name: modId },
  )
})
const current = computed(() => {
  if (!result.value) return []
  const order = [...items.value].sort((left, right) => (left.current_index ?? 0) - (right.current_index ?? 0))
  return order
})

const clearTimer = () => {
  window.clearTimeout(timer)
  timer = 0
}

const schedulePoll = (currentRevision) => {
  clearTimer()
  if (!props.open || !session.value.running || currentRevision !== revision) return
  timer = window.setTimeout(async () => {
    try {
      const state = await store.getModOrderAnalysis(session.value.id)
      if (currentRevision !== revision) return
      session.value = state
      error.value = ''
    } catch (failure) {
      if (currentRevision !== revision) return
      error.value = failure.message
    }
    schedulePoll(currentRevision)
  }, 800)
}

const start = async () => {
  if (!canStart.value) return
  const currentRevision = ++revision
  pending.value = true
  error.value = ''
  viewingHistoryId.value = ''
  clearTimer()
  try {
    const state = await store.startModOrderAnalysis(null, baseline.value)
    if (currentRevision !== revision) {
      if (state.running) void store.cancelModOrderAnalysis(state.id).catch(() => {})
      return
    }
    session.value = state
    schedulePoll(currentRevision)
  } catch (failure) {
    if (currentRevision === revision) error.value = failure.message
  } finally {
    if (currentRevision === revision) pending.value = false
  }
}

const cancel = async () => {
  if (!session.value.running || pending.value) return
  const currentRevision = revision
  pending.value = true
  try {
    const state = await store.cancelModOrderAnalysis(session.value.id)
    if (currentRevision === revision) session.value = state
  } catch (failure) {
    if (currentRevision === revision) error.value = failure.message
  } finally {
    if (currentRevision === revision) pending.value = false
  }
}

const apply = async () => {
  if (!canApply.value) return
  const currentRevision = revision
  pending.value = true
  error.value = ''
  try {
    if (viewingHistoryId.value) {
      await store.applyModOrderHistory(viewingHistoryId.value)
    } else {
      await store.applyModOrderAnalysis(session.value.id)
    }
    if (currentRevision === revision) {
      store.notify(t('aiOrder.applied'), 'success')
      await loadHistory()
      close()
    }
  } catch (failure) {
    if (currentRevision === revision) {
      error.value = t('aiOrder.applyFailed', { error: failure.message || String(failure) })
    }
  } finally {
    if (currentRevision === revision) pending.value = false
  }
}

const loadHistory = async () => {
  try {
    const records = await store.listModOrderHistory()
    history.value = Array.isArray(records) ? records : []
    historyError.value = ''
  } catch (failure) {
    historyError.value = failure.message || String(failure)
  }
}

const viewHistory = async (recordId) => {
  if (pending.value || session.value.running) return
  historyBusy.value = true
  historyError.value = ''
  try {
    const record = await store.getModOrderHistory(recordId)
    session.value = {
      ...idleState(),
      id: record.id,
      status: 'ready',
      running: false,
      phase: 'done',
      progress: 100,
      total_mods: record.total_mods,
      completed_mods: record.total_mods,
      ai_enabled: record.ai_enabled,
      degraded: record.degraded,
      baseline: record.baseline,
      warnings: record.warnings || [],
      result: record.result,
    }
    viewingHistoryId.value = recordId
  } catch (failure) {
    historyError.value = failure.message || String(failure)
  } finally {
    historyBusy.value = false
  }
}

const applyHistory = async (recordId) => {
  if (historyBusy.value) return
  historyBusy.value = true
  historyError.value = ''
  try {
    await store.applyModOrderHistory(recordId)
    store.notify(t('aiOrder.applied'), 'success')
    await loadHistory()
    close()
  } catch (failure) {
    historyError.value = failure.message || String(failure)
  } finally {
    historyBusy.value = false
  }
}

const deleteHistory = async (recordId) => {
  if (historyBusy.value) return
  historyBusy.value = true
  historyError.value = ''
  try {
    await store.deleteModOrderHistory(recordId)
    if (viewingHistoryId.value === recordId) {
      viewingHistoryId.value = ''
      session.value = idleState()
    }
    await loadHistory()
  } catch (failure) {
    historyError.value = failure.message || String(failure)
  } finally {
    historyBusy.value = false
  }
}

const close = () => {
  if (pending.value || session.value.running) return
  revision += 1
  clearTimer()
  emit('close')
}

const reset = () => {
  revision += 1
  clearTimer()
  if (session.value.running) void store.cancelModOrderAnalysis(session.value.id).catch(() => {})
  session.value = idleState()
  viewingHistoryId.value = ''
  historyError.value = ''
  pending.value = false
  error.value = ''
}

const formatTime = (value) => (value ? new Date(value).toLocaleTimeString(currentLocale()) : '')
const formatDate = (value) => (value ? new Date(value).toLocaleString(currentLocale()) : '')

const moveLabel = (item) => {
  const move = Number(item.move) || 0
  if (move > 0) return t('aiOrder.moveDown', { count: move })
  if (move < 0) return t('aiOrder.moveUp', { count: -move })
  return t('aiOrder.noChange')
}

const confidence = (item) => Math.round((Number(item.confidence) || 0) * 100)
const conflictNames = (conflict) =>
  conflict.mods.map((modId) => itemById.value.get(modId)?.name || modId).join(' · ')

watch(
  () => props.open,
  (open) => {
    if (!open) {
      clearTimer()
      return
    }
    reset()
    void loadHistory()
  },
  { immediate: true },
)

watch([() => store.gameContextRevision, () => store.settings.language], reset)
onBeforeUnmount(reset)
</script>

<template>
  <div v-if="open" class="modal-backdrop ai-order-backdrop" @mousedown.self="close">
    <section
      class="modal-card ai-order-modal"
      role="dialog"
      aria-modal="true"
      :aria-label="t('aiOrder.title')"
      data-testid="ai-order-modal"
    >
      <header class="modal-header">
        <h2>{{ t('aiOrder.title') }}</h2>
        <button
          type="button"
          class="icon-button"
          :disabled="pending || session.running"
          :aria-label="t('common.close')"
          @click="close"
        >
          ×
        </button>
      </header>

      <div class="ai-order-overview">
        <p class="ai-order-intro">{{ t('aiOrder.intro') }}</p>
        <p class="ai-order-muted">
          {{ session.ai_enabled ? t('aiOrder.aiDisclosure') : t('aiOrder.aiDisabled') }}
        </p>
        <p class="ai-order-muted">{{ t('aiOrder.analyzingHint') }}</p>
        <p class="ai-order-warning">{{ t('aiOrder.noAutoSubscribe') }}</p>
        <div class="ai-order-baseline">
          <label>
            <span>{{ t('aiOrder.baseline') }}</span>
            <select v-model="baseline" :disabled="session.running || pending">
              <option value="current">{{ t('aiOrder.baselineCurrent') }}</option>
              <option value="default">{{ t('aiOrder.baselineDefault') }}</option>
            </select>
          </label>
          <p class="ai-order-muted">{{ t('aiOrder.baselineHelp') }}</p>
        </div>
        <p v-if="viewingHistoryId" class="ai-order-warning">{{ t('aiOrder.viewingHistory') }}</p>
        <p v-if="!store.activeIds.length && session.status === 'idle'" class="ai-order-error" role="alert">
          {{ t('aiOrder.empty') }}
        </p>
        <p v-if="error || session.error" class="ai-order-error" role="alert">{{ error || session.error }}</p>
        <p v-if="session.degraded" class="ai-order-warning" role="status">{{ t('aiOrder.degraded') }}</p>
        <div v-if="session.id || pending" class="ai-order-progress" aria-live="polite">
          <div class="ai-order-progress-label">
            <strong>{{ t(phaseKey) }}</strong>
            <span>{{
              t('aiOrder.progress', {
                current: session.completed_mods || 0,
                total: session.total_mods || store.activeIds.length,
              })
            }}</span>
          </div>
          <progress
            :value="progress"
            max="100"
            :aria-label="t('aiOrder.title')"
            data-testid="ai-order-progress"
          />
          <p v-if="session.running && session.current_mod" class="ai-order-current">
            {{ session.current_mod }}
          </p>
        </div>
      </div>

      <div class="modal-body ai-order-body">
        <template v-if="result">
          <div class="ai-order-columns">
            <section class="ai-order-column">
              <h3>{{ t('aiOrder.currentOrder') }}</h3>
              <ol class="ai-order-list">
                <li v-for="item in current" :key="item.mod_id" class="ai-order-item">
                  <span class="ai-order-name">{{ item.name }}</span>
                  <span class="ai-order-meta">{{ item.category }}</span>
                </li>
              </ol>
            </section>
            <section class="ai-order-column">
              <h3>{{ t('aiOrder.recommendedOrder') }}</h3>
              <ol class="ai-order-list">
                <li
                  v-for="item in recommended"
                  :key="item.mod_id"
                  class="ai-order-item"
                  :class="{ moved: Number(item.move) }"
                >
                  <div class="ai-order-item-head">
                    <span class="ai-order-name">{{ item.name }}</span>
                    <span class="ai-order-meta"
                      >{{ moveLabel(item) }} ·
                      {{ t('aiOrder.confidence', { value: confidence(item) }) }}</span
                    >
                  </div>
                  <ul v-if="item.reasons?.length" class="ai-order-reasons">
                    <li v-for="reason in item.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                  <p v-for="warning in item.warnings || []" :key="warning" class="ai-order-warning">
                    {{ warning }}
                  </p>
                </li>
              </ol>
            </section>
          </div>

          <section v-if="events.length" class="ai-order-section">
            <h3>{{ t('aiOrder.process') }}</h3>
            <ol class="ai-order-events">
              <li v-for="event in events" :key="`${event.at}-${event.message}`">
                <time>{{ formatTime(event.at) }}</time>
                <span class="ai-order-event-phase">{{ event.phase }}</span>
                <span>{{ event.message }}</span>
              </li>
            </ol>
          </section>

          <section v-if="stats && Object.keys(stats).length" class="ai-order-section">
            <h3>{{ t('aiOrder.stats') }}</h3>
            <div class="ai-order-stats">
              <span>{{ t('aiOrder.statsRelations', { count: stats.relations_total || 0 }) }}</span>
              <span>{{ t('aiOrder.statsConflicts', { count: stats.conflicts || 0 }) }}</span>
              <span>{{
                t('aiOrder.statsAiBatches', {
                  local: stats.ai_local_batches || 0,
                  global: stats.ai_global_batches || 0,
                })
              }}</span>
              <span>{{ t('aiOrder.statsRetries', { count: stats.ai_retries || 0 }) }}</span>
              <span>{{ t('aiOrder.statsCache', { count: stats.cache_hits || 0 }) }}</span>
            </div>
          </section>

          <section v-if="conflicts.length" class="ai-order-section">
            <h3>{{ t('aiOrder.conflicts') }}</h3>
            <ul class="ai-order-conflicts">
              <li v-for="conflict in conflicts" :key="`${conflict.kind}:${conflict.resource}`">
                <strong>{{ conflictNames(conflict) }}</strong>
                <span>{{ conflict.kind }} · {{ conflict.resource }} · {{ conflict.severity }}</span>
              </li>
            </ul>
          </section>

          <section v-if="warnings.length" class="ai-order-section">
            <h3>{{ t('aiOrder.warnings') }}</h3>
            <ul class="ai-order-warnings">
              <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
            </ul>
          </section>
        </template>

        <section class="ai-order-section">
          <h3>{{ t('aiOrder.history') }}</h3>
          <p v-if="historyError" class="ai-order-error" role="alert">{{ historyError }}</p>
          <p v-if="!history.length" class="ai-order-muted">{{ t('aiOrder.historyEmpty') }}</p>
          <ul v-else class="ai-order-history">
            <li v-for="record in history" :key="record.id">
              <div class="ai-order-history-meta">
                <strong>{{ formatDate(record.completed_at) }}</strong>
                <span class="ai-order-meta">
                  {{ record.total_mods }} · {{ record.baseline }} ·
                  {{ record.degraded ? t('aiOrder.degradedShort') : 'AI' }}
                </span>
                <span v-if="record.applied_at" class="ai-order-meta">
                  {{ t('aiOrder.historyApplied') }}
                </span>
              </div>
              <div class="ai-order-history-actions">
                <button
                  type="button"
                  class="secondary-button"
                  :disabled="historyBusy || session.running"
                  @click="viewHistory(record.id)"
                >
                  {{ t('aiOrder.historyView') }}
                </button>
                <button
                  type="button"
                  class="secondary-button"
                  :disabled="historyBusy || session.running"
                  @click="applyHistory(record.id)"
                >
                  {{ t('aiOrder.historyApply') }}
                </button>
                <button
                  type="button"
                  class="secondary-button"
                  :disabled="historyBusy || session.running"
                  @click="deleteHistory(record.id)"
                >
                  {{ t('common.delete') }}
                </button>
              </div>
            </li>
          </ul>
        </section>
      </div>

      <footer class="modal-footer ai-order-footer">
        <button
          v-if="session.running"
          type="button"
          class="secondary-button"
          :disabled="pending || session.phase === 'cancelling'"
          @click="cancel"
        >
          {{ t('aiOrder.cancel') }}
        </button>
        <button
          v-else
          type="button"
          class="primary-button"
          :disabled="!canStart"
          data-testid="ai-order-start"
          @click="start"
        >
          {{ t('aiOrder.start') }}
        </button>
        <button
          type="button"
          class="primary-button"
          :disabled="!canApply"
          data-testid="ai-order-apply"
          @click="apply"
        >
          {{ pending ? t('aiOrder.applying') : t('aiOrder.apply') }}
        </button>
        <button type="button" class="secondary-button" :disabled="pending || session.running" @click="close">
          {{ t('common.close') }}
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.ai-order-backdrop {
  padding: 0;
  background: var(--bg);
  backdrop-filter: none;
}
.ai-order-modal {
  width: 100%;
  height: 100%;
  max-height: none;
  border: 0;
  border-radius: 0;
  background: var(--panel);
  box-shadow: none;
  color: var(--text);
}
.ai-order-modal .modal-header {
  flex-basis: 56px;
  padding: 0 24px;
}
.ai-order-overview {
  flex: 0 0 auto;
  padding: 14px 24px;
  border-bottom: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-order-overview p {
  margin: 0;
}
.ai-order-intro {
  max-width: 110ch;
  line-height: 1.7;
}
.ai-order-muted {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
.ai-order-error {
  color: #ff9090;
}
.ai-order-warning {
  color: #dfbd7c;
  font-size: 13px;
}
.ai-order-progress {
  margin-top: 6px;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: rgba(0, 0, 0, 0.18);
}
.ai-order-progress-label {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 13px;
}
.ai-order-current {
  color: #df9f61;
  font-size: 13px;
  padding-top: 5px;
}
.ai-order-progress progress {
  appearance: none;
  width: 100%;
  height: 8px;
  border: 0;
  border-radius: 5px;
  overflow: hidden;
}
.ai-order-progress progress::-webkit-progress-bar {
  background: #302722;
}
.ai-order-progress progress::-webkit-progress-value {
  background: linear-gradient(90deg, #957143, #dfbd7c);
  transition: width 0.3s;
}
.ai-order-progress progress::-moz-progress-bar {
  background: #dfbd7c;
}
.ai-order-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 18px 24px;
  overflow: auto;
}
.ai-order-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  align-items: start;
}
.ai-order-column h3,
.ai-order-section h3 {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--text-soft);
}
.ai-order-list {
  margin: 0;
  padding-left: 22px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-order-item {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.08);
}
.ai-order-item.moved {
  border-color: #80643a;
}
.ai-order-item-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
}
.ai-order-name {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.ai-order-meta {
  color: var(--muted);
  font-size: 12px;
  flex-shrink: 0;
}
.ai-order-reasons {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
.ai-order-section {
  border-top: 1px solid var(--line);
  padding-top: 14px;
}
.ai-order-conflicts,
.ai-order-warnings {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
.ai-order-conflicts li {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ai-order-conflicts span {
  color: var(--muted);
  font-size: 12px;
}
.ai-order-footer {
  flex: 0 0 auto;
  min-height: 64px;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 24px;
}
.ai-order-baseline {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.ai-order-baseline label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.ai-order-baseline select {
  height: 32px;
  padding: 0 8px;
}
.ai-order-baseline p {
  margin: 0;
}
.ai-order-events {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow: auto;
  font-size: 12px;
}
.ai-order-events li {
  display: flex;
  gap: 10px;
  align-items: baseline;
}
.ai-order-events time {
  color: var(--muted);
  flex-shrink: 0;
}
.ai-order-event-phase {
  color: #df9f61;
  flex-shrink: 0;
  text-transform: uppercase;
  font-size: 10px;
  letter-spacing: 0.04em;
}
.ai-order-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  font-size: 12px;
  color: var(--muted);
}
.ai-order-history {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-order-history li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 4px;
}
.ai-order-history-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}
.ai-order-history-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
@media (max-width: 1100px) {
  .ai-order-columns {
    grid-template-columns: 1fr;
  }
  .ai-order-modal .modal-header,
  .ai-order-overview,
  .ai-order-footer {
    padding-left: 18px;
    padding-right: 18px;
  }
  .ai-order-body {
    padding: 16px 18px;
  }
}
</style>
