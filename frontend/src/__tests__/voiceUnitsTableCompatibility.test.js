// @vitest-environment jsdom
import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }))
vi.mock('../bridge', () => ({ invoke: invokeMock }))

import App from '../App.vue'
import ModContextMenu from '../components/ModContextMenu.vue'
import ModList from '../components/ModList.vue'
import { applyInterfaceLanguage, hasTranslation, t } from '../languages'
import { useAppStore } from '../store'

const mod = { id: 'a', effective_name: 'Voice', pack_name: 'voice.pack', source: 'data' }
const selector = '[data-testid="context-voice-units-table-compatibility"]'

beforeEach(() => {
  setActivePinia(createPinia())
  invokeMock.mockReset()
  applyInterfaceLanguage('zh-CN')
})

describe('voice / unit table compatibility', () => {
  it('adds an unchecked checkbox at the bottom with the requested tooltip', async () => {
    const wrapper = mount(ModContextMenu, { props: { open: true, mod } })
    const checkbox = wrapper.get(selector)
    expect(checkbox.attributes('role')).toBe('menuitemcheckbox')
    expect(checkbox.attributes('aria-checked')).toBe('false')
    expect(checkbox.text()).toBe('语音-单位表兼容')
    expect(checkbox.attributes('title')).toBe('有的语音替换MOD，修改了原单位的units_table表，这往往会导致兼容问题，勾选此项后，启动器将该MOD对units_table的修改视为最低优先级，但是对语音字段的修改设置为最高优先级')
    expect(wrapper.findAll('nav button').at(-1).attributes('data-testid')).toBe('context-voice-units-table-compatibility')
    await checkbox.trigger('click')
    expect(wrapper.emitted('action')[0][0]).toMatchObject({ action: 'toggle-voice-units-table-compatibility', mod })
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.setProps({ mod: { ...mod, voice_units_table_compatibility: true } })
    expect(checkbox.attributes('aria-checked')).toBe('true')
    expect(checkbox.classes()).toContain('checked')
    await wrapper.setProps({ busy: true })
    expect(checkbox.element.disabled).toBe(true)
    await wrapper.setProps({ busy: false, voiceUnitsTableCompatibilitySupported: false })
    expect(wrapper.find(selector).exists()).toBe(false)
    await wrapper.setProps({ voiceUnitsTableCompatibilitySupported: true })
    await wrapper.setProps({ busy: false, packActions: false })
    expect(wrapper.find(selector).exists()).toBe(false)
    wrapper.unmount()
  })

  it('saves the switch without changing enablement, selection or load order', async () => {
    const store = useAppStore()
    store.mods = [mod]
    store.activeIds = ['a']
    store.selectedIds = ['a']
    store.dirty = true
    invokeMock.mockResolvedValue({ ...mod, voice_units_table_compatibility: true })
    await store.setVoiceUnitsTableCompatibility('a', true)
    expect(invokeMock).toHaveBeenCalledWith('set_voice_units_table_compatibility', 'a', true)
    expect(store.modMap.get('a').voice_units_table_compatibility).toBe(true)
    expect(store.activeIds).toEqual(['a'])
    expect(store.selectedIds).toEqual(['a'])
    expect(store.dirty).toBe(true)
    expect(store.busy).toBe('')
  })

  it('routes mixed batch selections through App and updates the open checkbox', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAppStore()
    store.settings = { selected_game: 'warhammer3', language: 'zh-CN' }
    store.pathHealth = { game_ready: true }
    store.mods = [mod, { ...mod, id: 'b', voice_units_table_compatibility: true }]
    store.selectedIds = ['a', 'b']
    store.selectedId = 'a'
    vi.spyOn(store, 'bootstrap').mockResolvedValue({})
    vi.spyOn(store, 'refreshRuntime').mockResolvedValue({})
    invokeMock.mockImplementation(async (method, id, enabled) => (
      method === 'set_voice_units_table_compatibility'
        ? { ...store.modMap.get(id), voice_units_table_compatibility: enabled }
        : {}
    ))
    const wrapper = shallowMount(App, { global: { plugins: [pinia], stubs: { ModContextMenu: false } } })
    try {
      await flushPromises()
      wrapper.findAllComponents(ModList)[0].vm.$emit('context-menu', { mod, x: 20, y: 20 })
      await wrapper.vm.$nextTick()
      await wrapper.get(selector).trigger('click')
      await flushPromises()
      expect(invokeMock.mock.calls.filter(([method]) => method === 'set_voice_units_table_compatibility')).toEqual([
        ['set_voice_units_table_compatibility', 'a', true],
      ])
      expect(store.mods.every(item => item.voice_units_table_compatibility)).toBe(true)
      expect(wrapper.get(selector).attributes('aria-checked')).toBe('true')
      await wrapper.get(selector).trigger('click')
      await flushPromises()
      expect(invokeMock).toHaveBeenCalledWith('set_voice_units_table_compatibility', 'a', false)
      expect(invokeMock).toHaveBeenCalledWith('set_voice_units_table_compatibility', 'b', false)
      expect(store.mods.every(item => !item.voice_units_table_compatibility)).toBe(true)
      expect(wrapper.get(selector).attributes('aria-checked')).toBe('false')
    } finally { wrapper.unmount() }
  })

  it('does not replace another game’s MOD after a delayed reply', async () => {
    const store = useAppStore()
    store.mods = [mod]
    let resolveReply
    invokeMock.mockImplementation(() => new Promise(resolve => { resolveReply = resolve }))
    const pending = store.setVoiceUnitsTableCompatibility('a', true)
    store.gameContextRevision += 1
    store.mods = [{ ...mod, effective_name: 'Other game' }]
    resolveReply({ ...mod, voice_units_table_compatibility: true })
    expect(await pending).toBeNull()
    expect(store.mods[0].effective_name).toBe('Other game')
    expect(store.mods[0].voice_units_table_compatibility).toBeUndefined()
  })

  it('translates the title, explanation and notifications in all six languages', () => {
    const keys = ['context.voiceUnitsTableCompatibility', 'context.voiceUnitsTableCompatibilityHint',
      'app.voiceUnitsTableCompatibilityEnabled', 'app.voiceUnitsTableCompatibilityDisabled']
    for (const key of keys) expect(hasTranslation(key), key).toBe(true)
    for (const language of ['zh-CN', 'en-US', 'ko-KR', 'ru-RU', 'ja-JP', 'es-ES']) {
      applyInterfaceLanguage(language)
      expect(t(keys[1])).toContain('units_table')
      expect(t(keys[2], { count: 3 })).toContain('3')
      expect(t(keys[3], { count: 3 })).toContain('3')
    }
    applyInterfaceLanguage('zh-CN')
  })
})
