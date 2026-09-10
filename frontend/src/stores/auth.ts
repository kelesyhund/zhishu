import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const loggedIn = computed(() => Boolean(token.value))

  function setAuth(nextToken: string, nextUsername: string) {
    token.value = nextToken
    username.value = nextUsername
    localStorage.setItem('token', nextToken)
    localStorage.setItem('username', nextUsername)
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('active_workspace_id')
  }

  return { token, username, loggedIn, setAuth, logout }
})
