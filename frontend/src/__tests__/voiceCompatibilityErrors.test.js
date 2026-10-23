// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { invoke } from '../bridge'
import { applyInterfaceLanguage, t } from '../languages'
import { useAppStore } from '../store'
import { formatVoiceCompatibilityError } from '../voiceCompatibilityErrors'

const details = {
  reason: 'argumentMismatch', event_id: 165328208,
  sources: [
    { role: 'mod', source: 'Example Voice [one.pack] (Workshop 123)', bank: 'audio\\wwise\\one.bnk' },
    { role: 'vanilla', source: 'audio_en_bnk.pack', bank: 'audio\\wwise\\english(uk)\\core.bnk' },
  ],
}

afterEach(() => {
  applyInterfaceLanguage('zh-CN')
  delete window.pywebview
  vi.useRealTimers()
})

describe('voice compatibility failure delivery', () => {
  it('retains MOD identity, bank paths and vanilla identity through the real bridge in all six languages', async () => {
    window.pywebview = { api: { call: vi.fn().mockResolvedValue({
      ok: false, error: { message: '语音事件 165328208 的参数结构不一致，无法合并', voice_compatibility: details },
    }) } }
    for (const language of ['zh-CN', 'en-US', 'ko-KR', 'ru-RU', 'ja-JP', 'es-ES']) {
      applyInterfaceLanguage(language)
      const error = await invoke('launch_game').catch(error => error)
      expect(error.voiceCompatibility).toEqual(details)
      expect(error.message).toContain('165328208')
      expect(error.message).toContain('Example Voice [one.pack] (Workshop 123)')
      expect(error.message).toContain('audio\\wwise\\one.bnk')
      expect(error.message).toContain(t('voiceError.vanilla'))
      expect(error.message).toContain('audio_en_bnk.pack')
      expect(error.message).not.toContain('voiceError.')
    }
  })

  it('localizes every voice failure reason', () => {
    for (const reason of ['argumentMismatch', 'ambiguousCopies', 'treeMismatch', 'mixedLayout',
      'bankConflict', 'invalidBank', 'encodeFailed']) {
      expect(formatVoiceCompatibilityError({ ...details, reason, event_id: null })).toContain(t(`voiceError.${reason}`))
    }
    expect(formatVoiceCompatibilityError({ reason: 'unknown' })).toBe('')
  })

  it('keeps a voice failure readable until dismissed instead of hiding it after 3.2 seconds', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    const store = useAppStore()
    const error = Object.assign(new Error(formatVoiceCompatibilityError(details)), { voiceCompatibility: details })
    store.notify(error.message, 'error')
    await expect(store.withBusy('launch', async () => { throw error })).rejects.toBe(error)
    vi.advanceTimersByTime(60000)
    expect(store.toast.persistent).toBe(true)
    expect(store.toast.message).toContain('one.pack')
    store.toast = null
    expect(store.toast).toBeNull()
  })
})
