import { t } from './languages'

const reasons = new Set([
  'argumentMismatch', 'ambiguousCopies', 'treeMismatch', 'mixedLayout',
  'bankConflict', 'invalidBank', 'encodeFailed',
])

export const formatVoiceCompatibilityError = details => {
  if (!details || !reasons.has(details.reason) || !Array.isArray(details.sources)) return ''
  const reason = t(`voiceError.${details.reason}`)
  const title = details.event_id == null
    ? t('voiceError.title', { reason })
    : t('voiceError.eventTitle', { event: details.event_id, reason })
  const sources = details.sources.map(source => t('voiceError.source', {
    role: t(source.role === 'vanilla' ? 'voiceError.vanilla' : 'voiceError.mod'),
    name: source.source,
    bank: source.bank,
  }))
  return [title, ...new Set(sources), t('voiceError.help')].join('\n')
}
