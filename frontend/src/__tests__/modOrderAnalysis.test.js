import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const invoke = vi.fn()

vi.mock('../bridge', () => ({
  invoke: (...args) => invoke(...args),
}))

import { useAppStore } from '../store'

describe('MOD order analysis store actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    invoke.mockReset()
  })

  it('starts with the current enabled MOD ids', async () => {
    const store = useAppStore()
    store.activeIds = ['a', 'b']
    invoke.mockResolvedValue({ id: 'session', running: true })

    const state = await store.startModOrderAnalysis()

    expect(invoke).toHaveBeenCalledWith('start_mod_order_analysis', ['a', 'b'], 'current')
    expect(state.id).toBe('session')
  })

  it('passes an explicit baseline to the backend', async () => {
    const store = useAppStore()
    store.activeIds = ['a', 'b']
    invoke.mockResolvedValue({ id: 'session', running: true })

    await store.startModOrderAnalysis(null, 'default')

    expect(invoke).toHaveBeenCalledWith('start_mod_order_analysis', ['a', 'b'], 'default')
  })

  it('uses an explicit selection when provided', async () => {
    const store = useAppStore()
    store.activeIds = ['a']
    invoke.mockResolvedValue({ id: 'session', running: true })

    await store.startModOrderAnalysis(['b', 'c'])

    expect(invoke).toHaveBeenCalledWith('start_mod_order_analysis', ['b', 'c'], 'current')
  })

  it('applies the recommended order to the active list and current playset', async () => {
    const store = useAppStore()
    store.activeIds = ['a', 'b']
    store.orderToken = 'old'
    store.currentPlaysetId = 'p1'
    store.playsets = [{ id: 'p1', mod_ids: ['a', 'b'] }]
    invoke.mockResolvedValue({
      ordered_mod_ids: ['b', 'a'],
      order_token: 'new',
      backup: { id: 1 },
    })

    await store.applyModOrderAnalysis('session')

    expect(invoke).toHaveBeenCalledWith('apply_mod_order_analysis', 'session')
    expect(store.activeIds).toEqual(['b', 'a'])
    expect(store.orderToken).toBe('new')
    expect(store.playsets[0].mod_ids).toEqual(['b', 'a'])
    expect(store.backups[0]).toEqual({ id: 1 })
  })

  it('lists, loads, applies and deletes history records', async () => {
    const store = useAppStore()
    store.activeIds = ['a', 'b']
    store.orderToken = 'old'
    store.currentPlaysetId = 'p1'
    store.playsets = [{ id: 'p1', mod_ids: ['a', 'b'] }]

    invoke.mockResolvedValueOnce([{ id: 'r1', total_mods: 2 }])
    expect(await store.listModOrderHistory()).toEqual([{ id: 'r1', total_mods: 2 }])
    expect(invoke).toHaveBeenCalledWith('list_mod_order_history')

    invoke.mockResolvedValueOnce({ id: 'r1', recommended_order: ['b', 'a'] })
    expect(await store.getModOrderHistory('r1')).toEqual({ id: 'r1', recommended_order: ['b', 'a'] })
    expect(invoke).toHaveBeenCalledWith('get_mod_order_history', 'r1')

    invoke.mockResolvedValueOnce({
      ordered_mod_ids: ['b', 'a'],
      order_token: 'new',
      backup: { id: 2 },
    })
    await store.applyModOrderHistory('r1')
    expect(invoke).toHaveBeenCalledWith('apply_mod_order_history', 'r1')
    expect(store.activeIds).toEqual(['b', 'a'])
    expect(store.orderToken).toBe('new')

    invoke.mockResolvedValueOnce({ deleted: true })
    expect(await store.deleteModOrderHistory('r1')).toEqual({ deleted: true })
    expect(invoke).toHaveBeenCalledWith('delete_mod_order_history', 'r1')
  })
})
