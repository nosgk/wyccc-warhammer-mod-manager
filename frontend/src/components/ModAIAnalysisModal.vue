<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { currentLocale, t } from '../languages'
import { useAppStore } from '../store'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close', 'settings'])
const store = useAppStore()
const session = ref({ status: 'idle', running: false, results: [] })
const pending = ref(false)
const error = ref('')
const search = ref('')
const page = ref(1)
const resultViewport = ref(null)
const collapsedConclusions = ref(new Set())
const commentPages = ref(new Map())
const MODS_PER_PAGE = 5
const COMMENTS_PER_PAGE = 10
let revision = 0
let timer = 0

const configured = computed(() => !!(store.settings.ai_enabled && store.settings.ai_model && store.settings.ai_base_url))
const visibleComments = item => (item.comments || []).filter(comment => comment.is_issue && comment.category === 'issue')
const allResults = computed(() => session.value.results || [])
const results = computed(() => allResults.value.filter(item => visibleComments(item).length))
const normalizeSearch = value => String(value || '').normalize('NFKC').toLowerCase()
const filteredResults = computed(() => {
  const terms = normalizeSearch(search.value).trim().split(/\s+/).filter(Boolean)
  return results.value.filter(item => {
    const names = normalizeSearch([item.name, item.title, item.workshop_id, ...(item.pack_names || [])].join(' '))
    return terms.every(term => names.includes(term))
  })
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredResults.value.length / MODS_PER_PAGE)))
const pagedResults = computed(() => filteredResults.value.slice((page.value - 1) * MODS_PER_PAGE, page.value * MODS_PER_PAGE))
const failures = computed(() => allResults.value.filter(item => item.error && !visibleComments(item).length))
const resultKey = item => item.mod_ids.join(',')
const conclusionToggled = (item, event) => {
  const next = new Set(collapsedConclusions.value)
  if (event.target.open) next.delete(resultKey(item))
  else next.add(resultKey(item))
  collapsedConclusions.value = next
}
const commentPageCount = item => Math.max(1, Math.ceil(visibleComments(item).length / COMMENTS_PER_PAGE))
const commentPage = item => Math.min(commentPages.value.get(resultKey(item)) || 1, commentPageCount(item))
const pagedComments = item => visibleComments(item).slice((commentPage(item) - 1) * COMMENTS_PER_PAGE, commentPage(item) * COMMENTS_PER_PAGE)
const changeCommentPage = (item, offset, event) => {
  const evidence = event.currentTarget.closest('.ai-analysis-evidence')
  const next = new Map(commentPages.value)
  next.set(resultKey(item), Math.min(commentPageCount(item), Math.max(1, commentPage(item) + offset)))
  commentPages.value = next
  void nextTick(() => evidence.querySelector('.ai-analysis-comments')?.scrollIntoView?.({ block: 'start' }))
}
const cacheSize = computed(() => ((Number(session.value.cache_bytes) || 0) / 1024 / 1024).toFixed(1))
const progress = computed(() => Math.min(100, Math.max(0, Number(session.value.progress) || 0)))
const phaseLabel = computed(() => {
  const state = session.value
  if (!state.running) return state.status === 'idle' ? '' : t(`aiAnalysis.${state.status}`)
  return t(`aiAnalysis.${state.phase}`)
})
const date = value => value ? new Date(value).toLocaleString(currentLocale()) : '—'
const itemLabel = item => t('aiAnalysis.issues', { count: visibleComments(item).length })
const openSource = async url => {
  try { await store.openExternalUrl(url) } catch (failure) { error.value = failure.message }
}
const clearTimer = () => { window.clearTimeout(timer); timer = 0 }

const schedulePoll = currentRevision => {
  clearTimer()
  if (!props.open || !session.value.running || currentRevision !== revision) return
  timer = window.setTimeout(async () => {
    try {
      const state = await store.getModAIAnalysis(session.value.id)
      if (currentRevision !== revision) return
      session.value = state
      error.value = ''
    } catch (failure) {
      if (currentRevision !== revision) return
      error.value = failure.message
    }
    schedulePoll(currentRevision)
  }, 1000)
}

const start = async () => {
  if (pending.value || session.value.running || !configured.value || !store.activeIds.length) return
  const currentRevision = ++revision
  pending.value = true
  error.value = ''
  clearTimer()
  try {
    const state = await store.startModAIAnalysis()
    if (currentRevision !== revision) {
      if (state.running) void store.cancelModAIAnalysis(state.id).catch(() => {})
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
    const state = await store.cancelModAIAnalysis(session.value.id)
    if (currentRevision === revision) session.value = state
  } catch (failure) {
    if (currentRevision === revision) error.value = failure.message
  } finally {
    if (currentRevision === revision) pending.value = false
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
  if (session.value.running) void store.cancelModAIAnalysis(session.value.id).catch(() => {})
  session.value = { status: 'idle', running: false, results: [] }
  search.value = ''
  page.value = 1
  collapsedConclusions.value = new Set()
  commentPages.value = new Map()
  pending.value = false
  error.value = ''
  emit('close')
}
watch(() => props.open, open => {
  if (!open) { clearTimer(); return }
  search.value = ''
  page.value = 1
  if (session.value.running) schedulePoll(revision)
  else void start()
}, { immediate: true })
watch([() => store.gameContextRevision, () => store.settings.language], reset)
watch(search, () => { page.value = 1 })
watch(totalPages, pages => { page.value = Math.min(page.value, pages) })
watch([page, search], () => { if (resultViewport.value) resultViewport.value.scrollTop = 0 })
onBeforeUnmount(reset)
</script>

<template>
  <div v-if="open" class="modal-backdrop ai-analysis-backdrop" @mousedown.self="close">
    <section class="modal-card ai-analysis-modal" role="dialog" aria-modal="true"
      :aria-label="t('aiAnalysis.title')" data-testid="ai-analysis-modal">
      <header class="modal-header">
        <h2>{{ t('aiAnalysis.title') }}</h2>
        <button type="button" class="icon-button" :disabled="pending || session.running"
          :aria-label="t('common.close')" @click="close">×</button>
      </header>
      <div class="ai-analysis-overview">
        <div class="ai-analysis-overview-meta">
          <p class="ai-analysis-muted ai-analysis-intro">{{ t('aiAnalysis.intro') }}</p>
          <div class="ai-analysis-model-cache">
            <p class="ai-analysis-model">{{ t('aiAnalysis.model', { model: session.model || store.settings.ai_model || '—' }) }}</p>
            <p class="ai-analysis-muted" data-testid="ai-analysis-workers">{{ t('aiAnalysis.workers', { active: session.active_workers || 0, limit: session.max_workers || 5 }) }}</p>
            <p class="ai-analysis-muted" data-testid="ai-analysis-cache-info">{{ t('aiAnalysis.cacheInfo', { size: cacheSize }) }}</p>
          </div>
        </div>
        <p v-if="!configured" role="alert">{{ t('aiAnalysis.configureAI') }}</p>
        <p v-else-if="!store.activeIds.length && session.status === 'idle'">{{ t('aiAnalysis.noEnabledMods') }}</p>
        <p v-if="error || session.error" class="ai-analysis-error" role="alert">{{ error || t(session.error) }}</p>
        <div v-if="session.id || pending" class="ai-analysis-progress" aria-live="polite">
          <div class="ai-analysis-progress-label">
            <strong>{{ phaseLabel || t('aiAnalysis.fetching') }}</strong>
            <span>{{ t('aiAnalysis.progress', { completed: session.completed_mods || 0,
              total: session.total_mods || store.activeIds.length, percent: progress }) }}</span>
          </div>
          <progress :value="progress" max="100" :aria-label="t('aiAnalysis.title')" data-testid="ai-analysis-progress" />
          <p v-if="session.running && session.current_mod" class="ai-analysis-current-mod">{{ session.current_mod }}</p>
          <p class="ai-analysis-muted">{{ t('aiAnalysis.counts', { fetched: session.fetched_comments || 0, analyzed: session.analyzed_comments || 0 }) }}
            <span v-if="session.running && session.batch_total"> · {{ t('aiAnalysis.batch', { current: session.batch_current, total: session.batch_total }) }}</span>
          </p>
          <p v-if="session.cached_comments || session.cached_summaries" class="ai-analysis-muted" data-testid="ai-analysis-cache-hits">
            {{ t('aiAnalysis.cacheHits', { comments: session.cached_comments || 0, summaries: session.cached_summaries || 0 }) }}
          </p>
        </div>
      </div>
      <div v-if="results.length" class="ai-analysis-toolbar">
        <label class="ai-analysis-search">
          <span>{{ t('aiAnalysis.search') }}</span>
          <input v-model="search" type="search" :placeholder="t('aiAnalysis.searchPlaceholder')"
            data-testid="ai-analysis-search" />
        </label>
        <span class="ai-analysis-muted">{{ t('aiAnalysis.matchingMods', { count: filteredResults.length }) }}</span>
      </div>
      <div ref="resultViewport" class="modal-body ai-analysis-body">
        <p v-if="session.status === 'completed' && !results.length" class="ai-analysis-muted">{{ t('aiAnalysis.noReports') }}</p>
        <section v-if="failures.length" class="ai-analysis-failures" role="alert">
          <h3>{{ t('aiAnalysis.failures') }}</h3>
          <p v-for="item in failures" :key="item.mod_ids.join(',')" class="ai-analysis-error">
            <strong>{{ item.name }}</strong> · {{ t(item.error) }}
          </p>
        </section>
        <p v-if="results.length && !filteredResults.length" class="ai-analysis-muted" data-testid="ai-analysis-no-matches">{{ t('aiAnalysis.noMatches') }}</p>
        <article v-for="item in pagedResults" :key="resultKey(item)" class="ai-analysis-result" :data-status="item.status">
          <header class="ai-analysis-result-header">
            <h3 class="ai-analysis-mod-title">{{ item.name }}</h3>
            <span class="ai-analysis-issue-count">{{ itemLabel(item) }}</span>
          </header>
          <div class="ai-analysis-result-body">
            <p v-if="item.updated_at" class="ai-analysis-muted">{{ t('aiAnalysis.updated', { date: date(item.updated_at), count: item.comment_count }) }}</p>
            <p v-if="item.error" class="ai-analysis-error" role="alert">{{ t(item.error) }}</p>
            <details v-if="item.summary" class="ai-analysis-conclusion" :open="!collapsedConclusions.has(resultKey(item))"
              data-testid="ai-analysis-conclusion" @toggle="conclusionToggled(item, $event)">
              <summary>{{ t('aiAnalysis.conclusion') }}</summary>
              <p class="ai-analysis-text ai-analysis-conclusion-text">{{ item.summary }}</p>
            </details>
            <div class="ai-analysis-mod-actions">
              <button v-if="item.url" type="button" class="secondary-button" @click="openSource(item.url)">{{ t('aiAnalysis.source') }}</button>
            </div>
            <details class="ai-analysis-evidence">
              <summary>{{ t('aiAnalysis.evidence', { count: visibleComments(item).length }) }}</summary>
              <div class="ai-analysis-comments">
                <article v-for="comment in pagedComments(item)" :key="comment.id" class="ai-analysis-comment">
                  <div class="ai-analysis-comment-meta"><span>{{ comment.author }}</span><time>{{ date(comment.posted_at) }}</time></div>
                  <strong class="ai-analysis-comment-title">{{ comment.summary }}</strong>
                  <p class="ai-analysis-text ai-analysis-translation" data-testid="ai-analysis-translation">{{ comment.translation }}</p>
                  <details class="ai-analysis-original"><summary>{{ t('aiAnalysis.original') }}</summary><p class="ai-analysis-text">{{ comment.text }}</p></details>
                  <button type="button" class="secondary-button" @click="openSource(comment.url)">{{ t('aiAnalysis.source') }}</button>
                </article>
              </div>
              <nav v-if="commentPageCount(item) > 1" class="ai-analysis-comment-pagination" :aria-label="t('aiAnalysis.evidence', { count: visibleComments(item).length })">
                <button type="button" class="secondary-button" :disabled="commentPage(item) <= 1"
                  data-testid="ai-analysis-comment-prev" @click="changeCommentPage(item, -1, $event)">{{ t('aiAnalysis.prevPage') }}</button>
                <span class="ai-analysis-muted">{{ t('aiAnalysis.pageInfo', { page: commentPage(item), pages: commentPageCount(item) }) }}</span>
                <button type="button" class="secondary-button" :disabled="commentPage(item) >= commentPageCount(item)"
                  data-testid="ai-analysis-comment-next" @click="changeCommentPage(item, 1, $event)">{{ t('aiAnalysis.nextPage') }}</button>
              </nav>
            </details>
          </div>
        </article>
      </div>
      <footer class="modal-footer ai-analysis-footer">
        <nav v-if="results.length" class="ai-analysis-pagination" :aria-label="t('aiAnalysis.pagination')">
          <button type="button" class="secondary-button" :disabled="page <= 1" data-testid="ai-analysis-prev" @click="page -= 1">{{ t('aiAnalysis.prevPage') }}</button>
          <span class="ai-analysis-muted" data-testid="ai-analysis-page-info">{{ t('aiAnalysis.pageInfo', { page, pages: totalPages }) }}</span>
          <button type="button" class="secondary-button" :disabled="page >= totalPages" data-testid="ai-analysis-next" @click="page += 1">{{ t('aiAnalysis.nextPage') }}</button>
        </nav>
        <div class="ai-analysis-footer-actions">
        <button v-if="!configured || error" type="button" class="secondary-button" :disabled="pending || session.running"
          @click="emit('settings')">{{ t('aiAnalysis.configure') }}</button>
        <button v-if="session.running" type="button" class="secondary-button" :disabled="pending || session.phase === 'cancelling'"
          data-testid="ai-analysis-cancel" @click="cancel">{{ t('aiAnalysis.cancel') }}</button>
        <button v-else type="button" class="primary-button" :disabled="pending || !configured || !store.activeIds.length"
          data-testid="ai-analysis-start" @click="start">{{ t('aiAnalysis.start') }}</button>
        <button type="button" class="secondary-button" :disabled="pending || session.running" @click="close">{{ t('common.close') }}</button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.ai-analysis-backdrop { padding: 0; background: var(--bg); backdrop-filter: none; }
.ai-analysis-modal {
  --analysis-accent: #df9f61;
  --analysis-heading: #d6c5af;
  --analysis-conclusion: #ceb99c;
  --analysis-body: #bdb4ab;
  --analysis-muted: #988d84;
  width: 100%; height: 100%; max-height: none; border: 0; border-radius: 0;
  background: var(--panel); box-shadow: none; color: var(--analysis-body);
}
.ai-analysis-modal .modal-header { flex-basis: 56px; padding: 0 24px; }
.ai-analysis-modal .modal-header h2 { color: var(--analysis-heading); }
.ai-analysis-overview { flex: 0 0 auto; padding: 14px 24px; border-bottom: 1px solid var(--line); }
.ai-analysis-overview-meta { display: flex; gap: 24px; align-items: flex-start; justify-content: space-between; }
.ai-analysis-overview p, .ai-analysis-body p, .ai-analysis-body h3 { margin: 0; }
.ai-analysis-intro { max-width: 90ch; }
.ai-analysis-model-cache { flex-shrink: 0; max-width: 42%; text-align: right; }
.ai-analysis-muted, .ai-analysis-comment-meta { color: var(--analysis-muted); font-size: 12px; line-height: 1.6; }
.ai-analysis-model { color: var(--analysis-heading); font-size: 13px; overflow-wrap: anywhere; }
.ai-analysis-progress { margin-top: 12px; padding: 12px 16px; border: 1px solid var(--line); border-radius: 5px; background: rgba(0, 0, 0, .18); }
.ai-analysis-progress-label { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; color: var(--analysis-heading); font-size: 13px; }
.ai-analysis-current-mod { color: var(--analysis-accent); font-size: 13px; padding-top: 5px; }
.ai-analysis-progress progress { appearance: none; width: 100%; height: 8px; border: 0; border-radius: 5px; overflow: hidden; }
.ai-analysis-progress progress::-webkit-progress-bar { background: #302722; }
.ai-analysis-progress progress::-webkit-progress-value { background: linear-gradient(90deg, #957143, #dfbd7c); transition: width .3s; }
.ai-analysis-progress progress::-moz-progress-bar { background: #dfbd7c; }
.ai-analysis-toolbar { display: flex; flex: 0 0 auto; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 24px; border-bottom: 1px solid var(--line); }
.ai-analysis-search { display: flex; align-items: center; gap: 12px; width: min(600px, 70%); font-size: 13px; }
.ai-analysis-search span { flex-shrink: 0; color: var(--analysis-heading); }
.ai-analysis-search input { flex: 1; min-width: 0; height: 34px; padding: 0 12px; font-size: 13px; }
.ai-analysis-search input::placeholder { color: var(--analysis-muted); }
.ai-analysis-body { flex: 1; display: flex; flex-direction: column; gap: 16px; padding: 18px 24px; }
.ai-analysis-text { max-width: 110ch; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; line-height: 1.8; }
.ai-analysis-conclusion-text { color: var(--analysis-conclusion); font-weight: 500; }
.ai-analysis-error { color: #ff9090; }
.ai-analysis-failures { display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
.ai-analysis-result { flex-shrink: 0; border: 1px solid var(--line); border-radius: 5px; background: rgba(0, 0, 0, .08); }
.ai-analysis-result-header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 16px 18px 10px; }
.ai-analysis-mod-title { color: var(--analysis-accent); font-size: 16px; font-weight: 800; overflow-wrap: anywhere; }
.ai-analysis-issue-count { flex-shrink: 0; color: var(--analysis-muted); font-size: 12px; }
.ai-analysis-result-body { padding: 0 18px 16px; display: flex; flex-direction: column; gap: 10px; }
.ai-analysis-conclusion > summary { color: var(--analysis-heading); font-size: 13px; font-weight: 600; }
.ai-analysis-conclusion[open] > p { padding: 8px 0 0 16px; }
.ai-analysis-mod-actions .secondary-button, .ai-analysis-comment .secondary-button { font-size: 12px; min-height: 28px; padding: 5px 10px; color: var(--analysis-muted); }
.ai-analysis-evidence { border-top: 1px solid var(--line); padding-top: 10px; }
.ai-analysis-evidence > summary { color: var(--analysis-heading); font-size: 13px; }
.ai-analysis-conclusion > summary, .ai-analysis-evidence > summary { cursor: pointer; }
.ai-analysis-comments { display: flex; flex-direction: column; gap: 12px; padding-top: 12px; }
.ai-analysis-comment { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border-left: 2px solid #80643a; background: rgba(0, 0, 0, .16); }
.ai-analysis-comment > button { align-self: flex-start; }
.ai-analysis-comment-meta { display: flex; gap: 12px; justify-content: space-between; flex-wrap: wrap; }
.ai-analysis-comment-title { color: var(--analysis-heading); font-size: 13px; font-weight: 600; }
.ai-analysis-translation { color: var(--analysis-body); font-weight: 400; }
.ai-analysis-original > summary { cursor: pointer; color: var(--analysis-muted); font-size: 12px; }
.ai-analysis-original p { padding-top: 8px; color: var(--analysis-muted); font-size: 13px; }
.ai-analysis-footer { flex: 0 0 auto; min-height: 64px; justify-content: space-between; padding: 12px 24px; flex-wrap: wrap; }
.ai-analysis-footer-actions, .ai-analysis-pagination, .ai-analysis-comment-pagination { display: flex; align-items: center; gap: 10px; }
.ai-analysis-footer-actions { margin-left: auto; }
.ai-analysis-comment-pagination { padding-top: 12px; }
@media (max-width: 1100px) {
  .ai-analysis-overview-meta { gap: 16px; }
  .ai-analysis-model-cache { max-width: 40%; }
  .ai-analysis-modal .modal-header, .ai-analysis-overview, .ai-analysis-toolbar, .ai-analysis-footer { padding-left: 18px; padding-right: 18px; }
  .ai-analysis-body { padding: 16px 18px; }
}
</style>
