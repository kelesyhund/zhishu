import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getCurrentUser, type CurrentUser } from '../api'

export const useAuthStore = defineStore('auth', () => {
  localStorage.removeItem('token')
  const currentUser = ref<CurrentUser | null>(null)
  const username = computed(() => currentUser.value?.username || '')
  const authInitializing = ref(false)
  const initialized = ref(false)
  const loggedIn = computed(() => Boolean(currentUser.value))

  function setAuth(user: CurrentUser) {
    currentUser.value = user
    initialized.value = true
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('active_workspace_id')
  }

  function logout() {
    currentUser.value = null
    initialized.value = true
    localStorage.removeItem('token')
    localStorage.removeItem('active_workspace_id')
  }

  async function initialize(force = false) {
    if ((initialized.value && !force) || authInitializing.value) return
    authInitializing.value = true
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    try {
      currentUser.value = await getCurrentUser()
    } catch {
      currentUser.value = null
    } finally {
      initialized.value = true
      authInitializing.value = false
    }
  }

  return { currentUser, username, loggedIn, authInitializing, initialized, setAuth, logout, initialize }
})
