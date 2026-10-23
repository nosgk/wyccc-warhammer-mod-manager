import { localizeBackendMessage, t } from './languages'
import { formatVoiceCompatibilityError } from './voiceCompatibilityErrors'

const waitForDesktopBridge = () => new Promise((resolve, reject) => {
  if (window.pywebview?.api?.call) {
    resolve(window.pywebview.api)
    return
  }
  let settled = false
  const finish = () => {
    if (settled) return
    settled = true
    if (window.pywebview?.api?.call) resolve(window.pywebview.api)
    else reject(new Error(t('common.backendFailure')))
  }
  window.addEventListener('pywebviewready', finish, { once: true })
  window.setTimeout(finish, 5000)
})

export async function invoke(method, ...args) {
  const api = await waitForDesktopBridge()
  const response = await api.call(method, args, {})
  if (!response?.ok) {
    const voiceMessage = formatVoiceCompatibilityError(response?.error?.voice_compatibility)
    const error = new Error(voiceMessage || localizeBackendMessage(response?.error?.message, 'common.backendFailure'))
    if (voiceMessage) error.voiceCompatibility = response.error.voice_compatibility
    throw error
  }
  return response.data
}
