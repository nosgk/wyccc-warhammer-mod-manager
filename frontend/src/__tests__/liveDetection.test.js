// @vitest-environment jsdom

import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }))

vi.mock('../bridge', () => ({ invoke: invokeMock }))

import { useAppStore } from '../store'
import App from '../App.vue'
import ModList from '../components/ModList.vue'
import ModContextMenu from '../components/ModContextMenu.vue'

const emptyScan = revision => ({
  mods: [],
  enabled_order: [],
  missing_enabled_ids: [],
  warnings: [],
  mod_revision: revision,
})

describe('live MOD detection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    invokeMock.mockReset()
  })

  it('runs one quiet background scan when the filesystem revision advances', async () => {
    invokeMock.mockImplementation(async method => {
      if (method === 'get_runtime_status') return { running: false, mod_revision: 3 }
      if (method === 'scan_mods') return emptyScan(3)
      throw new Error(`Unexpected RPC: ${method}`)
    })
    const store = useAppStore()
    store.settings = { live_mod_detection: true }
    store.modRevision = 2

    await store.refreshRuntime()

    expect(invokeMock).toHaveBeenCalledWith('scan_mods', false)
    expect(store.modRevision).toBe(3)
    expect(store.liveModRefreshing).toBe(false)
  })

  it('refreshes Steam metadata when a newly subscribed Workshop MOD appears', async () => {
    const withoutMetadata = {
      ...emptyScan(3),
      mods: [{ id: 'steam:123:new.pack', source: 'workshop', workshop_id: '123', pack_name: 'new.pack' }],
    }
    const withMetadata = {
      ...withoutMetadata,
      mods: [{
        ...withoutMetadata.mods[0],
        display_name: 'Steam MOD name',
        author: 'Steam author',
      }],
    }
    invokeMock.mockImplementation(async (method, refreshWorkshop) => {
      if (method === 'get_runtime_status') return { running: false, mod_revision: 3 }
      if (method === 'scan_mods') return withoutMetadata
      if (method === 'refresh_workshop_metadata') return withMetadata
      throw new Error(`Unexpected RPC: ${method}`)
    })
    const store = useAppStore()
    store.settings = { live_mod_detection: true, fetch_workshop_metadata: true }
    store.modRevision = 2

    await store.refreshRuntime()
    await flushPromises()

    expect(invokeMock.mock.calls.filter(([method]) => method === 'scan_mods')).toEqual([
      ['scan_mods', false],
    ])
    expect(invokeMock).toHaveBeenCalledWith('refresh_workshop_metadata', ['123'], 'warhammer3')
    expect(store.mods[0].author).toBe('Steam author')
  })

  it('shows a newly downloaded MOD before its slow online metadata request finishes', async () => {
    const freshMod = { id: 'steam:123:new.pack', source: 'workshop', workshop_id: '123', pack_name: 'new.pack',
      effective_name: 'New downloaded MOD' }
    const localScan = { ...emptyScan(3), mods: [freshMod] }
    let finishMetadata
    invokeMock.mockImplementation(async (method, refreshWorkshop) => {
      if (method === 'get_runtime_status') return { running: false, mod_revision: 3 }
      if (method === 'scan_mods' && !refreshWorkshop) return localScan
      if (method === 'refresh_workshop_metadata') return new Promise(resolve => { finishMetadata = resolve })
      if (method === 'update_playset') return { playsets: [], current_playset: { id: 'test-playset' } }
      if (method === 'save_load_order') return { order_token: 'saved-order' }
      throw new Error(`Unexpected RPC: ${method}`)
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAppStore()
    store.settings = { live_mod_detection: true, fetch_workshop_metadata: true }
    store.modRevision = 2
    store.pathHealth = { game_ready: true }
    vi.spyOn(store, 'bootstrap').mockResolvedValue({})
    const wrapper = shallowMount(App, { global: { plugins: [pinia], stubs: { ModList: false } } })
    const pending = store.refreshRuntime()
    try {
      await vi.waitFor(() => expect(finishMetadata).toBeTypeOf('function'))
      expect(store.mods.map(mod => mod.id)).toContain(freshMod.id)
      expect(store.busy).toBe('')
      expect(store.liveModRefreshing).toBe(false)
      const inactiveList = wrapper.findAllComponents(ModList).find(list => !list.props('active'))
      expect(inactiveList.text()).toContain('New downloaded MOD')
      await inactiveList.get('.enable-button').trigger('click')
      expect(store.activeIds).toContain(freshMod.id)
    } finally {
      finishMetadata({ ...localScan, mods: [{ ...freshMod, display_name: 'Steam MOD name' }] })
      await pending
      await flushPromises()
      wrapper.unmount()
    }
    expect(store.activeIds).toContain(freshMod.id)
  })

  it('does not scan while the game is running in low-consumption mode', async () => {
    invokeMock.mockResolvedValue({ running: true, mod_revision: 4 })
    const store = useAppStore()
    store.settings = { live_mod_detection: true }
    store.modRevision = 3

    await store.refreshRuntime()

    expect(invokeMock).toHaveBeenCalledTimes(1)
    expect(invokeMock).toHaveBeenCalledWith('get_runtime_status')
  })

  it('continues live scanning while the game is running when low-consumption mode is disabled', async () => {
    invokeMock.mockImplementation(async method => {
      if (method === 'get_runtime_status') return { running: true, mod_revision: 4 }
      if (method === 'scan_mods') return emptyScan(4)
      throw new Error(`Unexpected RPC: ${method}`)
    })
    const store = useAppStore()
    store.settings = { live_mod_detection: true, auto_low_consumption_mode: false }
    store.modRevision = 3

    await store.refreshRuntime()

    expect(invokeMock.mock.calls).toEqual([
      ['get_runtime_status'],
      ['scan_mods', false],
    ])
  })

  it('ends the game process and immediately clears the running state', async () => {
    invokeMock.mockResolvedValue({
      process_ids: [42],
      runtime: { running: false, mod_revision: 4 },
    })
    const store = useAppStore()
    store.runtime = { running: true, mod_revision: 4 }

    await store.terminateGame()

    expect(invokeMock).toHaveBeenCalledWith('terminate_game')
    expect(store.runtime).toEqual({ running: false, mod_revision: 4 })
    expect(store.busy).toBe('')
  })

  it('refreshes Workshop metadata without taking the global busy lock', async () => {
    let finish
    invokeMock.mockImplementation(method => {
      if (method !== 'refresh_workshop_metadata') throw new Error(`Unexpected RPC: ${method}`)
      return new Promise(resolve => { finish = resolve })
    })
    const store = useAppStore()
    store.runtime = { running: false }

    const pending = store.refreshWorkshopInBackground()
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
    expect(store.workshopRefreshing).toBe(true)
    expect(invokeMock).toHaveBeenCalledWith('refresh_workshop_metadata', null, 'warhammer3', true)
    expect(store.busy).toBe('')
    finish(emptyScan(0))
    await pending
    expect(store.workshopRefreshing).toBe(false)
  })

  it('keeps manual Workshop refresh as an explicit full refresh', async () => {
    invokeMock.mockResolvedValue(emptyScan(0))
    const store = useAppStore()
    await store.refreshWorkshopInBackground(null, true)
    expect(invokeMock).toHaveBeenCalledWith('refresh_workshop_metadata', null, 'warhammer3')
  })

  it('keeps discovering MODs and queues their metadata while another metadata request is slow', async () => {
    const first = { id: 'first', source: 'workshop', workshop_id: '111', pack_name: 'first.pack' }
    const second = { id: 'second', source: 'workshop', workshop_id: '222', pack_name: 'second.pack' }
    let finishFirst
    const metadataCalls = []
    invokeMock.mockImplementation(async (method, ids) => {
      if (method === 'refresh_workshop_metadata') {
        metadataCalls.push(ids)
        if (ids[0] === '111') return new Promise(resolve => { finishFirst = resolve })
        return { ...emptyScan(4), mods: [first, { ...second, display_name: 'Second MOD title' }] }
      }
      if (method === 'scan_mods') return { ...emptyScan(4), mods: [first, second] }
      return {}
    })
    const store = useAppStore()
    store.settings = { selected_game: 'warhammer3', fetch_workshop_metadata: true }
    store.mods = [first]
    const pending = store.refreshWorkshopInBackground(['111'])
    await vi.waitFor(() => expect(finishFirst).toBeTypeOf('function'))
    await store.refreshModsInBackground()
    expect(store.mods.map(mod => mod.id)).toEqual(['first', 'second'])
    expect(store.liveModRefreshing).toBe(false)
    expect(metadataCalls).toEqual([['111']])
    finishFirst({ ...emptyScan(3), mods: [first] })
    await pending
    expect(metadataCalls).toEqual([['111'], ['222']])
    expect(store.mods.map(mod => mod.id)).toEqual(['first', 'second'])
    expect(store.mods[1].display_name).toBe('Second MOD title')
    expect(store.workshopRefreshing).toBe(false)
  })

  it('ignores a stale Workshop ownership response after a newer menu request', async () => {
    let resolveFirst
    let resolveSecond
    invokeMock.mockImplementation((method, modIds) => {
      if (method !== 'get_workshop_update_eligibility') throw new Error(`Unexpected RPC: ${method}`)
      return new Promise(resolve => {
        if (modIds[0] === 'first') resolveFirst = resolve
        else resolveSecond = resolve
      })
    })
    const store = useAppStore()

    const first = store.refreshWorkshopUpdateEligibility(['first'])
    const second = store.refreshWorkshopUpdateEligibility(['second'])
    resolveSecond({ eligible_mod_ids: ['second'], checked_mod_ids: ['second'] })
    await second
    resolveFirst({ eligible_mod_ids: ['first'], checked_mod_ids: ['first'] })
    await first

    expect([...store.workshopUpdateEligibility]).toEqual(['second'])
  })

  it('reuses a successful Workshop ownership response for repeated menu openings', async () => {
    invokeMock.mockResolvedValue({ eligible_mod_ids: ['owned'], checked_mod_ids: ['owned'] })
    const store = useAppStore()

    await store.refreshWorkshopUpdateEligibility(['owned'])
    await store.refreshWorkshopUpdateEligibility(['owned'])

    expect(invokeMock).toHaveBeenCalledTimes(1)
    expect([...store.workshopUpdateEligibility]).toEqual(['owned'])
  })

  it('shows update for owned MODs at positions 526, 528 and 529 in a 658-item scan', async () => {
    const mods = Array.from({ length: 658 }, (_, index) => ({
      id: `steam:${index + 1}:own.pack`,
      workshop_id: String(index + 1),
      source: 'workshop',
    }))
    const owned = [mods[525], mods[527], mods[528]]
    const ownedIds = new Set(owned.map(mod => mod.id))
    invokeMock.mockImplementation(async (method, ids) => {
      expect(method).toBe('get_workshop_update_eligibility')
      const checked = ids.slice(0, 500)
      return { checked_mod_ids: checked, eligible_mod_ids: checked.filter(id => ownedIds.has(id)) }
    })
    const store = useAppStore()
    store.mods = mods
    await store.refreshWorkshopUpdateEligibility(mods.map(mod => mod.id))

    for (const mod of owned) {
      await store.refreshWorkshopUpdateEligibility([mod.id])
      const wrapper = mount(ModContextMenu, {
        props: { open: true, mod, selectedModIds: [mod.id], eligibleUpdateIds: [...store.workshopUpdateEligibility] },
      })
      expect(wrapper.find('[data-testid="context-publish-update"]').exists()).toBe(true)
      wrapper.unmount()
    }
    expect(invokeMock.mock.calls.map(([, ids]) => ids.length)).toEqual([500, 158])
    expect(store.workshopEligibilityKnownIds.size).toBe(658)
  })

  it('retries a MOD that the backend did not actually check', async () => {
    invokeMock.mockResolvedValueOnce({ checked_mod_ids: ['other'], eligible_mod_ids: [] })
      .mockResolvedValueOnce({ checked_mod_ids: ['owned'], eligible_mod_ids: ['owned'] })
    const store = useAppStore()
    await store.refreshWorkshopUpdateEligibility(['other', 'owned'])
    expect(store.workshopEligibilityKnownIds.has('owned')).toBe(false)
    await store.refreshWorkshopUpdateEligibility(['owned'])
    expect([...store.workshopUpdateEligibility]).toEqual(['owned'])
    expect(invokeMock).toHaveBeenCalledTimes(2)
  })

  it.each([500, 501, 1001])('checks all %i MODs across the batch boundary', async count => {
    const ids = Array.from({ length: count }, (_, index) => `mod:${index}`)
    invokeMock.mockImplementation(async (method, requested) => ({
      checked_mod_ids: requested.slice(0, 500),
      eligible_mod_ids: requested.slice(0, 500),
    }))
    const store = useAppStore()
    expect(await store.refreshWorkshopUpdateEligibility(ids)).toEqual(ids)
    expect(invokeMock).toHaveBeenCalledTimes(Math.ceil(count / 500))
    expect(store.workshopEligibilityKnownIds.size).toBe(count)
  })

  it('keeps update available when a large-library MOD switches between Data and Workshop copies', async () => {
    const mods = Array.from({ length: 658 }, (_, index) => ({
      id: `steam:${index + 1}:mod.pack`, workshop_id: String(index + 1), source: 'workshop',
    }))
    const workshopMod = mods[525]
    const dataMod = { ...workshopMod, id: 'local:data:own', source: 'data', sources: ['data', 'workshop'] }
    const merged = mods.map(mod => mod.id === workshopMod.id ? dataMod : mod)
    const owned = new Set([workshopMod.id, dataMod.id])
    invokeMock.mockImplementation(async (method, requested) => {
      const checked = requested.slice(0, 500)
      return { checked_mod_ids: checked, eligible_mod_ids: checked.filter(id => owned.has(id)) }
    })
    const store = useAppStore()
    vi.spyOn(store, 'loadPreview').mockResolvedValue()
    vi.spyOn(store, 'loadThumbnails').mockResolvedValue()
    store.mods = merged
    await store.refreshWorkshopUpdateEligibility(merged.map(mod => mod.id))
    for (const snapshot of [merged, mods, merged]) {
      await store.applyExternalScan({ mods: snapshot })
      const selected = snapshot[525].id
      await store.refreshWorkshopUpdateEligibility([selected])
      expect(store.workshopUpdateEligibility.has(selected)).toBe(true)
    }
  })

  it('keeps a failed later batch retryable without losing the checked first batch', async () => {
    const ids = Array.from({ length: 501 }, (_, index) => `mod:${index}`)
    invokeMock.mockResolvedValueOnce({ checked_mod_ids: ids.slice(0, 500), eligible_mod_ids: [] })
      .mockRejectedValueOnce(new Error('Steam offline'))
      .mockResolvedValueOnce({ checked_mod_ids: [ids[500]], eligible_mod_ids: [ids[500]] })
    const store = useAppStore()
    await store.refreshWorkshopUpdateEligibility(ids)
    expect(store.workshopEligibilityKnownIds.size).toBe(500)
    expect(store.workshopEligibilityKnownIds.has(ids[500])).toBe(false)
    expect(await store.refreshWorkshopUpdateEligibility([ids[500]])).toEqual([ids[500]])
  })
})
