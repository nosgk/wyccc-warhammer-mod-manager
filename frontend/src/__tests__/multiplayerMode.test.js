import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }))
vi.mock('../bridge', () => ({ invoke: invokeMock }))

import App from '../App.vue'
import { useAppStore } from '../store'
import { applyInterfaceLanguage, hasTranslation, LANGUAGE_OPTIONS, t } from '../languages'

beforeEach(() => {
  setActivePinia(createPinia())
  invokeMock.mockReset()
})

describe('multiplayer mode', () => {
  it('translates the switch and its explanation in every built-in language', () => {
    for (const key of ['app.multiplayerMode', 'app.multiplayerModeHelp']) {
      expect(hasTranslation(key)).toBe(true)
      const messages = LANGUAGE_OPTIONS.map(({ code }) => {
        applyInterfaceLanguage(code)
        return t(key)
      })
      expect(new Set(messages).size).toBe(6)
      expect(messages).not.toContain(key)
    }
    applyInterfaceLanguage('zh-CN')
  })

  it('saves the switch without clearing MOD selection or changing patch preferences', async () => {
    const store = useAppStore()
    store.settings = { language: 'zh-CN', multiplayer_mode: false, skip_intro_movies: true }
    store.mods = [{ id: 'user', pack_name: 'user.pack' }]
    store.activeIds = ['user']
    store.orderToken = 'original'
    store.dirty = true
    invokeMock.mockResolvedValue({ settings: { ...store.settings, multiplayer_mode: true } })
    await store.setMultiplayerMode(true)
    expect(invokeMock).toHaveBeenCalledExactlyOnceWith('set_multiplayer_mode', true)
    expect(store.settings.multiplayer_mode).toBe(true)
    expect(store.settings.skip_intro_movies).toBe(true)
    expect(store.mods).toHaveLength(1)
    expect(store.activeIds).toEqual(['user'])
    expect(store.orderToken).toBe('original')
    expect(store.dirty).toBe(true)
  })

  it('keeps the saved mode when a change fails', async () => {
    const store = useAppStore()
    store.settings = { multiplayer_mode: true }
    invokeMock.mockRejectedValue(new Error('save failed'))
    await expect(store.setMultiplayerMode(false)).rejects.toThrow('save failed')
    expect(store.settings.multiplayer_mode).toBe(true)
    expect(store.busy).toBe('')
  })

  it('places an operable switch immediately left of launch history and locks it while running', async () => {
    const store = useAppStore()
    store.settings = { selected_game: 'warhammer3', language: 'zh-CN', multiplayer_mode: true }
    store.pathHealth = { game_ready: true }
    vi.spyOn(store, 'bootstrap').mockResolvedValue({})
    vi.spyOn(store, 'refreshRuntime').mockResolvedValue({})
    invokeMock.mockImplementation(async (method, enabled) => ({
      settings: { ...store.settings, multiplayer_mode: enabled },
    }))
    const wrapper = mount(App, { global: { stubs: {
      ModList: true, ModDetails: true, SettingsModal: true, ModDiagnosticsModal: true,
    } } })
    try {
      await flushPromises()
      const control = wrapper.get('[data-testid="multiplayer-mode-toggle"]')
      expect(control.attributes('role')).toBe('switch')
      expect(control.element.checked).toBe(true)
      expect(control.element.closest('label').textContent).toContain('联机模式')
      expect(control.element.closest('label').nextElementSibling.dataset.testid).toBe('launch-history-button')
      await control.setValue(false)
      await flushPromises()
      expect(store.settings.multiplayer_mode).toBe(false)
      expect(control.element.checked).toBe(false)
      invokeMock.mockRejectedValueOnce(new Error('save failed'))
      await control.setValue(true)
      await flushPromises()
      expect(store.settings.multiplayer_mode).toBe(false)
      expect(control.element.checked).toBe(false)
      store.busy = 'saving'
      await flushPromises()
      expect(control.element.disabled).toBe(true)
      store.busy = ''
      store.runtime = { running: true }
      await flushPromises()
      expect(control.element.disabled).toBe(true)
    } finally { wrapper.unmount() }
  })
})
