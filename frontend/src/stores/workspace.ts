import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  getMeContext,
  type OrganizationItem,
  type WorkspaceItem,
} from '../api'

export const useWorkspaceStore = defineStore('workspace', () => {
  const organizations = ref<OrganizationItem[]>([])
  const workspaces = ref<WorkspaceItem[]>([])
  const activeWorkspaceId = ref<number | null>(null)
  const loading = ref(false)
  const loaded = ref(false)
  const activeWorkspace = computed(
    () => workspaces.value.find((item) => item.id === activeWorkspaceId.value) || null,
  )
  const capabilities = computed(() => new Set(activeWorkspace.value?.capabilities || []))

  async function initialize(force = false) {
    if ((loaded.value && !force) || loading.value) return
    loading.value = true
    try {
      const context = await getMeContext()
      organizations.value = context.organizations
      workspaces.value = context.workspaces
      const stored = Number(localStorage.getItem('active_workspace_id'))
      const selected = workspaces.value.some((item) => item.id === stored)
        ? stored
        : context.active_workspace_id || workspaces.value[0]?.id || null
      activeWorkspaceId.value = selected
      if (selected) localStorage.setItem('active_workspace_id', String(selected))
      else localStorage.removeItem('active_workspace_id')
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function can(capability: string) {
    return capabilities.value.has(capability)
  }

  function switchWorkspace(workspaceId: number) {
    if (workspaceId === activeWorkspaceId.value) return
    if (!workspaces.value.some((item) => item.id === workspaceId)) return
    activeWorkspaceId.value = workspaceId
    localStorage.setItem('active_workspace_id', String(workspaceId))
    window.location.assign('/knowledge')
  }

  function reset() {
    organizations.value = []
    workspaces.value = []
    activeWorkspaceId.value = null
    loaded.value = false
    localStorage.removeItem('active_workspace_id')
  }

  return {
    organizations, workspaces, activeWorkspaceId, activeWorkspace, capabilities,
    loading, loaded, initialize, can, switchWorkspace, reset,
  }
})
